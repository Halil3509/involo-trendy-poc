"""End-to-end pipeline: scrape -> enrich -> embed -> recommend.

Uses fixtures + fake providers + an in-memory Qdrant double so the whole flow
runs offline and deterministically.
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
    FakeRecommendationProvider,
    FakeTranscriptionProvider,
    FakeVisionProvider,
    FixtureMetadataProvider,
    FixtureScraper,
)

from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.services.enrichment import EnrichmentService
from app.services.multimodal import MultimodalService
from app.services.profiling import user_average_point_id
from app.services.recommendations import RecommendationService
from app.services.scoring import ScoreWeights
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
        recommendation_retrieval_top_k=2,
        recommendation_retrieval_pool=2,
        recommendation_dedupe_threshold=0.999,
        recommendation_max_attempts=2,
    )


@pytest.mark.asyncio
async def test_full_pipeline_scrape_to_recommendation() -> None:
    settings = _settings()
    db = FakeDatabase()
    qdrant = FakeQdrant()

    # 1. Scrape (fixture adapter) -> discovered documents
    scraper = ScraperService(db, FixtureScraper(FIXTURES / "instagram.json"))
    scrape_counters = await scraper.run(["travel", "food"], 10)
    assert scrape_counters["discovered"] == 2
    assert await db.trend_content.count_documents({"processing_status": "discovered"}) == 2
    scraped = {document["shortcode"]: document for document in db.trend_content.docs}
    assert scraped["Fixture_A1"]["canonical_url"] == "https://www.instagram.com/reel/Fixture_A1/"
    assert scraped["Fixture_A1"]["caption_text"] == "A deterministic travel fixture"
    assert scraped["Fixture_B2"]["canonical_url"] == "https://www.instagram.com/reel/Fixture_B2/"
    assert scraped["Fixture_B2"]["caption_text"] == "A deterministic food fixture"

    # The in-memory double assigns integer ids; the recommendation retrieval path
    # expects Mongo ObjectId strings, so normalize ids as real Mongo would.
    for doc in db.trend_content.docs:
        doc["_id"] = ObjectId()

    # 2. Enrich -> metadata, viral score, transcript
    enrichment = EnrichmentService(
        db,
        FixtureMetadataProvider(FIXTURES / "metadata.json"),
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        weights=ScoreWeights(),
        viral_threshold=0.0,
        transcribe_min_views=settings.transcribe_min_views,
    )
    enrich_counters = await enrichment.run()
    assert enrich_counters["scored"] == 2
    assert enrich_counters["transcribed"] == 2
    assert await db.trend_content.count_documents({"processing_status": "enriched"}) == 2
    for document in db.trend_content.docs:
        assert document["viral_score"] > 0
        assert document["transcript"]
        assert document["combined_text"] == "\n\n".join(
            (document["caption_text"], document["transcript"])
        )

    # 3. Full multimodal embed -> S3 media, vision, segments, and fused vectors
    vectors = MultimodalService(
        db,
        qdrant,
        settings,
        FakeMediaProvider(),
        FakeVisionProvider(),
        FakeEmbeddingProvider(VECTOR_SIZE),
    )
    embed_counters = await vectors.run_eligible()
    assert embed_counters["embedded"] == 2
    assert len(qdrant.collections[settings.qdrant_trend_collection]) == 2

    # 4. Prepare a ready user profile + average vector
    user_id = ObjectId()
    now = utcnow()
    db.instagram_connections.docs.append(
        {"user_id": user_id, "status": "ready", "instagram_username": "creator"}
    )
    db.user_profiles.docs.append(
        {
            "user_id": user_id,
            "average_vector_id": user_average_point_id(str(user_id)),
            "ai_profile_summary": "Travel-focused creator with a warm, practical tone.",
            "updated_at": now,
        }
    )
    average = await FakeEmbeddingProvider(VECTOR_SIZE).embed("travel coastal towns")
    await qdrant.upsert(
        collection_name=settings.qdrant_user_collection,
        points=[
            SimpleNamespace(
                id=user_average_point_id(str(user_id)),
                vector={"average": average},
                payload={},
            )
        ],
    )

    # 5. Recommend
    service = RecommendationService(
        db, qdrant, settings, FakeRecommendationProvider(), FakeEmbeddingProvider(VECTOR_SIZE)
    )
    document = await service.generate(user_id, 2)
    assert len(document["recommendations"]) == 2
    assert len(db.recommendations.docs) == 1
    assert document["retrieval"]["top_k"] == 2
