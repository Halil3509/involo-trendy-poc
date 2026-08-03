from typing import Any

import pytest
from fakes import FakeDatabase

from app.core.config import Settings
from app.providers.embedding import EmbeddingProvider
from app.providers.media import Keyframe, MediaProcessingError, StoredMedia, VideoSegment
from app.schemas.intelligence import VisualAnalysis
from app.services.multimodal import (
    MultimodalService,
    deterministic_media_point_id,
)


def media(key: str, content_type: str) -> StoredMedia:
    return StoredMedia("media", key, f"s3://media/{key}", content_type, key, 10)


class StubMediaProvider:
    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        return media(f"media/{content_id}.mp4", "video/mp4")

    async def extract_keyframes(
        self, stored: StoredMedia, content_id: str, offsets: list[float]
    ) -> list[Keyframe]:
        return [Keyframe(0, media(f"frames/{content_id}.jpg", "image/jpeg"))]

    async def segment_video(
        self, stored: StoredMedia, content_id: str, segment_seconds: int
    ) -> list[VideoSegment]:
        assert segment_seconds == 15
        return [
            VideoSegment(0, 15, media(f"segments/{content_id}-0.mp4", "video/mp4")),
            VideoSegment(15, 23, media(f"segments/{content_id}-1.mp4", "video/mp4")),
        ]

    async def prepare_embedding_media(self, stored: StoredMedia) -> StoredMedia:
        return stored


class StubVision:
    async def analyze(
        self, stored: StoredMedia, frames: list[Keyframe], *, caption: str
    ) -> VisualAnalysis:
        return VisualAnalysis(opening_frame="Creator opens the video", confidence=1)


class StubEmbedding(EmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(2)

    async def embed(self, text: str) -> list[float]:
        return [1, 0]

    async def embed_media(self, s3_uri: str, *, purpose: str = "GENERIC_INDEX") -> list[float]:
        if s3_uri.endswith("-0.mp4"):
            return [1, 0]
        if s3_uri.endswith("-1.mp4"):
            return [0, 1]
        return [0.5, 0.5]


class StubQdrant:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, Any]] = []

    async def upsert(self, *, collection_name: str, points: Any) -> None:
        self.upserts.append((collection_name, points))


@pytest.mark.asyncio
async def test_segments_are_pooled_and_stored_separately_from_keyframes() -> None:
    qdrant = StubQdrant()
    db = FakeDatabase()
    settings = Settings(vector_size=2, segment_seconds=15)
    service = MultimodalService(
        db,
        qdrant,  # type: ignore[arg-type]
        settings,
        StubMediaProvider(),  # type: ignore[arg-type]
        StubVision(),  # type: ignore[arg-type]
        StubEmbedding(),
    )

    result = await service.process_asset(
        source_url="https://example.test/video.mp4",
        content_id="mongo-1",
        caption="caption",
        combined_text="caption transcript",
        collection="user_content_v2",
        payload={"content_type": "user_content", "user_id": "user-1"},
    )

    assert result.media_vector == pytest.approx([2**-0.5, 2**-0.5])
    segment_points = [
        call[1][0]
        for call in qdrant.upserts
        if call[0] == settings.qdrant_segment_collection
    ]
    assert [point.payload["type"] for point in segment_points] == [
        "video_segment",
        "video_segment",
        "keyframe",
    ]
    assert segment_points[0].payload["start_seconds"] == 0
    assert segment_points[1].payload["end_seconds"] == 23
    assert len({str(point.id) for point in segment_points}) == 3
    assert {run["stage"] for run in db.provider_runs.docs} == {
        "vision",
        "embedding_text",
        "embedding_video",
        "embedding_image",
    }
    assert result.processing_regions == {
        "vision": "us-east-1",
        "text_embedding": "us-east-1",
        "media_embedding": "us-east-1",
    }
    assert {run["region"] for run in db.provider_runs.docs} == {"us-east-1"}


def test_segment_ids_include_collection_content_type_and_owner() -> None:
    common = {
        "content_id": "same-mongo-id",
        "asset_type": "video_segment",
        "position": "0",
        "schema_version": "nova-mm-v2",
    }
    trend = deterministic_media_point_id(
        collection="trend_content_v2",
        content_type="trend_content",
        owner_id="meta",
        **common,
    )
    user = deterministic_media_point_id(
        collection="user_content_v2",
        content_type="user_content",
        owner_id="user-123",
        **common,
    )

    assert trend != user


@pytest.mark.asyncio
async def test_default_embed_path_only_marks_full_multimodal_trends_ready() -> None:
    db = FakeDatabase()
    db.trend_content.docs.extend(
        [
            {
                "_id": "eligible",
                "processing_status": "enriched",
                "video_url": "https://example.test/eligible.mp4",
                "caption_text": "caption",
                "combined_text": "caption transcript",
                "source": "meta",
            },
            {
                "_id": "not-eligible",
                "processing_status": "stored",
                "video_url": "https://example.test/stored.mp4",
            },
        ]
    )
    service = MultimodalService(
        db,
        StubQdrant(),  # type: ignore[arg-type]
        Settings(vector_size=2),
        StubMediaProvider(),  # type: ignore[arg-type]
        StubVision(),  # type: ignore[arg-type]
        StubEmbedding(),
    )

    counters = await service.run_eligible()

    assert counters == {"processed": 1, "embedded": 1, "failed": 0}
    eligible = db.trend_content.docs[0]
    assert eligible["processing_status"] == "embedded"
    assert eligible["visual_analysis"]["opening_frame"]
    assert len(eligible["video_segments"]) == 2
    assert "embedding_schema_version" not in db.trend_content.docs[1]


@pytest.mark.asyncio
async def test_process_asset_emits_progress_logs() -> None:
    messages: list[str] = []

    async def emit(message: str, **kwargs: Any) -> None:
        messages.append(message)

    db = FakeDatabase()
    settings = Settings(vector_size=2, segment_seconds=15)
    service = MultimodalService(
        db,
        StubQdrant(),  # type: ignore[arg-type]
        settings,
        StubMediaProvider(),  # type: ignore[arg-type]
        StubVision(),  # type: ignore[arg-type]
        StubEmbedding(),
        emit=emit,
    )

    result = await service.process_asset(
        source_url="https://example.test/video.mp4",
        content_id="mongo-1",
        caption="caption",
        combined_text="caption transcript",
        collection="user_content_v2",
        payload={"content_type": "user_content", "user_id": "user-1"},
    )

    assert result.point_id
    assert any("Downloading media" in msg for msg in messages)
    assert any("Media downloaded" in msg for msg in messages)
    assert any("Extracted" in msg and "keyframes" in msg for msg in messages)
    assert any("Vision analysis complete" in msg for msg in messages)
    assert any("Text embedding complete" in msg for msg in messages)
    assert any("Qdrant upsert complete" in msg for msg in messages)


class FailingMediaProvider(StubMediaProvider):
    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        raise MediaProcessingError("S3 bucket missing")


@pytest.mark.asyncio
async def test_failed_embed_keeps_status_enriched() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {
            "_id": "fail",
            "processing_status": "enriched",
            "video_url": "https://example.test/fail.mp4",
            "caption_text": "caption",
            "combined_text": "caption transcript",
            "source": "meta",
        }
    )
    service = MultimodalService(
        db,
        StubQdrant(),  # type: ignore[arg-type]
        Settings(vector_size=2),
        FailingMediaProvider(),  # type: ignore[arg-type]
        StubVision(),  # type: ignore[arg-type]
        StubEmbedding(),
    )

    counters = await service.run_eligible()

    assert counters == {"processed": 1, "embedded": 0, "failed": 1}
    doc = db.trend_content.docs[0]
    assert doc["processing_status"] == "enriched"
    assert doc["processing_error_stage"] == "multimodal"
    assert "S3 bucket missing" in doc["processing_error"]


class CountingMediaProvider(StubMediaProvider):
    def __init__(self) -> None:
        self.ingest_calls = 0

    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        self.ingest_calls += 1
        return await super().ingest(source_url, content_id)


@pytest.mark.asyncio
async def test_existing_media_asset_skips_download() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {
            "_id": "reuse",
            "processing_status": "enriched",
            "video_url": "https://example.test/stale.mp4",
            "caption_text": "caption",
            "combined_text": "caption transcript",
            "source": "meta",
            "media_asset": {
                "bucket": "media",
                "key": "media/reuse.mp4",
                "uri": "s3://media/media/reuse.mp4",
                "content_type": "video/mp4",
                "sha256": "abc",
                "size_bytes": 10,
                "region": "us-east-1",
            },
        }
    )
    media = CountingMediaProvider()
    service = MultimodalService(
        db,
        StubQdrant(),  # type: ignore[arg-type]
        Settings(vector_size=2),
        media,  # type: ignore[arg-type]
        StubVision(),  # type: ignore[arg-type]
        StubEmbedding(),
    )

    counters = await service.run_eligible()

    assert counters == {"processed": 1, "embedded": 1, "failed": 0}
    assert media.ingest_calls == 0
    assert db.trend_content.docs[0]["processing_status"] == "embedded"


class ExpiredMediaProvider(StubMediaProvider):
    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        import httpx

        from app.core.errors import TransientError

        request = httpx.Request("GET", source_url)
        response = httpx.Response(403, request=request)
        cause = httpx.HTTPStatusError("403 Forbidden", request=request, response=response)
        raise TransientError("media download failed") from cause


@pytest.mark.asyncio
async def test_expired_media_download_marks_media_expired_and_leaves_retry_loop() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {
            "_id": "stale",
            "processing_status": "enriched",
            "video_url": "https://example.test/stale.mp4",
            "caption_text": "caption",
            "combined_text": "caption transcript",
            "source": "meta",
        }
    )
    service = MultimodalService(
        db,
        StubQdrant(),  # type: ignore[arg-type]
        Settings(vector_size=2),
        ExpiredMediaProvider(),  # type: ignore[arg-type]
        StubVision(),  # type: ignore[arg-type]
        StubEmbedding(),
    )

    counters = await service.run_eligible()

    assert counters == {"processed": 1, "embedded": 0, "failed": 1}
    doc = db.trend_content.docs[0]
    assert doc["processing_status"] == "media_expired"
    assert "media download failed" in doc["processing_error"]

    # Terminal state: no longer eligible until the scraper refreshes the URL.
    second = await service.run_eligible()
    assert second["processed"] == 0
