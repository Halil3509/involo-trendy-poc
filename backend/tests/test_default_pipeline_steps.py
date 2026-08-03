"""Default enrichment and embedding step tests with wild-score verification.

These tests exercise the standard ``enrich`` and ``embed`` pipeline stages using
fixture/fake providers so they run offline and deterministically. They assert
that:

* ``viral_score`` ("wild score") is calculated correctly for known fixture metadata.
* Raw score components are persisted alongside the normalized score.
* Transcripts, captions, and combined text are stored in MongoDB.
* Multimodal embeddings are stored in Qdrant with the expected named vectors and
  payload fields.
* ``trend_content`` documents transition through ``discovered`` → ``enriched`` →
  ``embedded`` and keep an ``embedding_vector_id`` reference.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from bson import ObjectId
from fakes import FakeDatabase
from provider_doubles import (
    FakeEmbeddingProvider,
    FakeMediaProvider,
    FakeTranscriptionProvider,
    FakeVisionProvider,
    FixtureMetadataProvider,
    FixtureScraper,
)

from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.schemas.trends import ContentMetadata
from app.services.enrichment import EnrichmentService
from app.services.multimodal import MultimodalService
from app.services.scoring import ScoreWeights, compute_viral_score
from app.services.scraper import ScraperService

VECTOR_SIZE = 8
FIXTURES = Path(__file__).parent / "fixtures"


class FakeQdrant:
    """Minimal async Qdrant double supporting the ops used across services."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def _col(self, name: str) -> dict[str, dict[str, Any]]:
        return self.collections.setdefault(name, {})

    async def upsert(self, *, collection_name: str, points: list[Any]) -> None:
        col = self._col(collection_name)
        for point in points:
            col[str(point.id)] = {
                "vector": dict(point.vector),
                "payload": dict(point.payload or {}),
            }

    async def retrieve(
        self, *, collection_name: str, ids: list[Any], with_vectors: bool = False
    ) -> list[Any]:
        col = self._col(collection_name)
        result = []
        for identifier in ids:
            stored = col.get(str(identifier))
            if stored is not None:
                result.append(
                    SimpleNamespace(
                        id=str(identifier),
                        vector=stored["vector"],
                        payload=stored["payload"],
                    )
                )
        return result

    async def scroll(
        self,
        *,
        collection_name: str,
        limit: int,
        offset: Any = None,
        with_payload: bool = True,
        with_vectors: Any = None,
        scroll_filter: Any = None,
    ) -> tuple[list[Any], Any]:
        col = self._col(collection_name)
        points = [
            SimpleNamespace(id=key, vector=value["vector"], payload=value["payload"])
            for key, value in col.items()
        ]
        return points, None

    async def set_payload(
        self, *, collection_name: str, payload: dict[str, Any], points: list[Any]
    ) -> None:
        col = self._col(collection_name)
        for identifier in points:
            stored = col.get(str(identifier))
            if stored is not None:
                stored["payload"].update(payload)

    async def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        using: str,
        limit: int,
        with_payload: bool = True,
        query_filter: Any = None,
    ) -> Any:
        col = self._col(collection_name)
        query_vec = np.asarray(query, dtype=float)
        scored = []
        for key, value in col.items():
            vector = np.asarray(value["vector"].get(using, []), dtype=float)
            denom = float(np.linalg.norm(query_vec) * np.linalg.norm(vector))
            score = float(np.dot(query_vec, vector) / denom) if denom else 0.0
            scored.append(SimpleNamespace(id=key, score=score, payload=value["payload"]))
        scored.sort(key=lambda item: item.score, reverse=True)
        return SimpleNamespace(points=scored[:limit])


def _settings() -> Settings:
    return Settings(
        vector_size=VECTOR_SIZE,
        transcribe_min_views=0,
        qdrant_trend_collection="trend_content_test_v2",
        qdrant_user_collection="user_profiles_test_v2",
        qdrant_user_content_collection="user_content_test_v2",
        qdrant_segment_collection="content_segments_test_v2",
    )


def _expected_scores(now: Any) -> dict[str, float]:
    """Return the exact viral scores for the two fixture records."""
    metadata_a1 = ContentMetadata(
        shortcode="Fixture_A1",
        like_count=12000,
        comment_count=340,
        view_count=500000,
        share_count=800,
        owner_follower_count=25000,
        taken_at=now.replace(year=2026, month=7, day=10),
    )
    metadata_b2 = ContentMetadata(
        shortcode="Fixture_B2",
        like_count=40,
        comment_count=2,
        view_count=900,
        share_count=1,
        owner_follower_count=8000,
        taken_at=now.replace(year=2026, month=5, day=1),
    )
    weights = ScoreWeights()
    score_a1, _ = compute_viral_score(metadata_a1, now, weights)
    score_b2, _ = compute_viral_score(metadata_b2, now, weights)
    return {"Fixture_A1": score_a1, "Fixture_B2": score_b2}


@pytest.mark.asyncio
async def test_default_enrichment_and_embedding_steps() -> None:
    """Run the default pipeline end-to-end and verify persistence + wild score."""
    settings = _settings()
    db = FakeDatabase()
    qdrant = FakeQdrant()

    # 1. Discover fixture content.
    scraper = ScraperService(
        db,  # type: ignore[arg-type]
        FixtureScraper(FIXTURES / "instagram.json"),
    )
    scrape_counters = await scraper.run(["travel", "food"], 10)
    assert scrape_counters["discovered"] == 2
    assert await db.trend_content.count_documents({"processing_status": "discovered"}) == 2

    scraped = {document["shortcode"]: document for document in db.trend_content.docs}
    assert scraped["Fixture_A1"]["canonical_url"] == "https://www.instagram.com/reel/Fixture_A1/"
    assert scraped["Fixture_B2"]["canonical_url"] == "https://www.instagram.com/reel/Fixture_B2/"

    # Real Mongo assigns ObjectIds; do the same so downstream id references behave.
    for doc in db.trend_content.docs:
        doc["_id"] = ObjectId()

    # 2. Enrich: metadata + viral score + transcript.
    enrichment = EnrichmentService(
        db,  # type: ignore[arg-type]
        FixtureMetadataProvider(FIXTURES / "metadata.json"),
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        weights=ScoreWeights(),
        viral_threshold=0.0,
        transcribe_min_views=0,
    )
    enrich_counters = await enrichment.run()
    assert enrich_counters["scored"] == 2
    assert enrich_counters["enriched"] == 2
    assert enrich_counters["transcribed"] == 2
    assert await db.trend_content.count_documents({"processing_status": "enriched"}) == 2

    # 3. Verify wild score correctness and data saved in MongoDB.
    now = utcnow()
    expected = _expected_scores(now)
    for document in db.trend_content.docs:
        shortcode = document["shortcode"]
        assert document["processing_status"] == "enriched"
        assert document["viral_score"] > 0
        assert document["viral_score"] == pytest.approx(expected[shortcode], abs=0.1)
        assert "score_components" in document
        assert document["score_components"]["raw_score"] > 0
        assert document["transcript"]
        assert document["combined_text"] == "\n\n".join(
            (document["caption_text"], document["transcript"])
        )
        assert "enriched_at" in document

    # 4. Embed: generate multimodal vectors and store in Qdrant.
    vectors = MultimodalService(
        db,
        qdrant,  # type: ignore[arg-type]
        settings,
        FakeMediaProvider(),  # type: ignore[arg-type]
        FakeVisionProvider(),  # type: ignore[arg-type]
        FakeEmbeddingProvider(VECTOR_SIZE),
    )
    embed_counters = await vectors.run_eligible()
    assert embed_counters["embedded"] == 2
    assert len(qdrant.collections[settings.qdrant_trend_collection]) == 2

    # 5. Verify data persisted in Qdrant and MongoDB.
    for document in db.trend_content.docs:
        shortcode = document["shortcode"]
        assert document["processing_status"] == "embedded"
        assert document["embedding_vector_id"]
        assert document["embedded_at"]

        point = qdrant.collections[settings.qdrant_trend_collection][
            document["embedding_vector_id"]
        ]
        vector = point["vector"]
        assert {"text", "audio_video", "fused"} <= set(vector.keys())
        assert len(vector["fused"]) == VECTOR_SIZE
        assert point["payload"]["shortcode"] == shortcode
        assert point["payload"]["viral_score"] == document["viral_score"]
        assert point["payload"]["schema_version"] == settings.vector_schema_version
