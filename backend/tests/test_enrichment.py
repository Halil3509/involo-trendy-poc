from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes import FakeDatabase
from provider_doubles import (
    FakeMediaProvider,
    FakeTranscriptionProvider,
    FixtureMetadataProvider,
)

from app.core.errors import TransientError
from app.providers.media import StoredMedia
from app.providers.metadata import (
    ChainedMetadataProvider,
    ContentMetadata,
    GraphApiMetadataProvider,
    MetadataProvider,
    OfficialMetaMetadataProvider,
    YtdlpMetadataProvider,
)
from app.providers.scraper import noop_emit
from app.providers.transcription import TranscriptionProvider
from app.schemas.trends import TranscriptResult
from app.services.enrichment import EnrichmentService
from app.services.scoring import ScoreWeights

FIXTURES = Path(__file__).parent / "fixtures"


def _service(
    db: FakeDatabase,
    *,
    threshold: float,
    min_views: int,
    emit: Any = noop_emit,
) -> EnrichmentService:
    return EnrichmentService(
        db,  # type: ignore[arg-type]
        FixtureMetadataProvider(FIXTURES / "metadata.json"),
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        weights=ScoreWeights(),
        viral_threshold=threshold,
        transcribe_min_views=min_views,
        emit=emit,
    )


@pytest.mark.asyncio
async def test_high_engagement_content_is_enriched_and_transcribed() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_A1", "processing_status": "discovered"}
    )
    counters = await _service(db, threshold=1.0, min_views=0).run()
    doc = db.trend_content.docs[0]
    assert counters["processed"] == 1
    assert counters["scored"] == 1
    assert counters["enriched"] == 1
    assert counters["transcribed"] == 1
    assert doc["processing_status"] == "enriched"
    assert doc["viral_score"] > 0
    assert "hidden coastal towns" in doc["transcript"]
    assert doc["combined_text"].startswith("A deterministic travel")
    assert doc["metrics"]["view_count"] == 500000


@pytest.mark.asyncio
async def test_below_threshold_is_stored_without_transcript() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_B2", "processing_status": "discovered"}
    )
    counters = await _service(db, threshold=99.0, min_views=0).run()
    doc = db.trend_content.docs[0]
    assert counters["skipped_threshold"] == 1
    assert counters["transcribed"] == 0
    assert doc["processing_status"] == "stored"
    assert doc["transcript"] == ""
    assert doc["viral_score"] >= 0


@pytest.mark.asyncio
async def test_cost_prefilter_skips_transcription_below_min_views() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_B2", "processing_status": "discovered"}
    )
    counters = await _service(db, threshold=0.0, min_views=10_000).run()
    doc = db.trend_content.docs[0]
    assert counters["transcribed"] == 0
    assert doc["processing_status"] == "enriched"
    assert doc["transcript"] == ""


@pytest.mark.asyncio
async def test_missing_metadata_marks_document_failed() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "unknown", "processing_status": "discovered"}
    )
    counters = await _service(db, threshold=0.0, min_views=0).run()
    assert counters["failed"] == 1
    assert db.trend_content.docs[0]["processing_status"] == "failed"


@pytest.mark.asyncio
async def test_stored_content_is_re_scored_and_enriched() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_A1", "processing_status": "stored"}
    )
    counters = await _service(db, threshold=1.0, min_views=0).run()
    doc = db.trend_content.docs[0]
    assert counters["processed"] == 1
    assert counters["scored"] == 1
    assert counters["enriched"] == 1
    assert doc["processing_status"] == "enriched"
    assert doc["viral_score"] > 0


@pytest.mark.asyncio
async def test_incomplete_official_metadata_falls_back_to_provider() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {
            "_id": 1,
            "shortcode": "Fixture_A1",
            "processing_status": "discovered",
            "metadata": {
                "official_source": True,
                "caption_text": "Official caption",
                "like_count": 100,
                "comment_count": 5,
            },
        }
    )
    counters = await _service(db, threshold=1.0, min_views=0).run()
    doc = db.trend_content.docs[0]
    assert counters["enriched"] == 1
    assert doc["processing_status"] == "enriched"
    assert doc["metrics"]["view_count"] == 500000
    assert doc["caption_text"] == "A deterministic travel fixture caption"


@pytest.mark.asyncio
async def test_enrichment_emits_progress_logs() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_A1", "processing_status": "discovered"}
    )
    messages: list[str] = []

    async def emit(message: str, **kwargs: Any) -> None:
        messages.append(message)

    counters = await _service(db, threshold=1.0, min_views=0, emit=emit).run()
    assert counters["processed"] == 1
    assert any("Starting enrichment" in msg for msg in messages)
    assert any("Enriching item 1/1" in msg for msg in messages)
    assert any("Metadata resolved" in msg for msg in messages)
    assert any("viral_score" in msg for msg in messages)
    assert any("Transcribing" in msg for msg in messages)
    assert any("Transcription complete" in msg for msg in messages)


class _RecordingMediaProvider(FakeMediaProvider):
    def __init__(self) -> None:
        self.ingest_calls: list[tuple[str, str]] = []

    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        self.ingest_calls.append((source_url, content_id))
        return await super().ingest(source_url, content_id)


class _ExpiredMediaProvider(FakeMediaProvider):
    async def ingest(self, source_url: str, content_id: str) -> StoredMedia:
        request = httpx.Request("GET", source_url)
        response = httpx.Response(403, request=request)
        cause = httpx.HTTPStatusError("403 Forbidden", request=request, response=response)
        raise TransientError("media download failed") from cause


class _RecordingTranscriptionProvider(TranscriptionProvider):
    def __init__(self, inner: FakeTranscriptionProvider) -> None:
        self.inner = inner
        self.urls: list[str | None] = []

    async def transcribe(self, shortcode: str, video_url: str | None) -> TranscriptResult:
        self.urls.append(video_url)
        return await self.inner.transcribe(shortcode, video_url)


@pytest.mark.asyncio
async def test_above_threshold_media_is_persisted_and_transcribed_from_storage() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_A1", "processing_status": "discovered"}
    )
    media = _RecordingMediaProvider()
    transcription = _RecordingTranscriptionProvider(
        FakeTranscriptionProvider(FIXTURES / "transcripts.json")
    )
    service = EnrichmentService(
        db,  # type: ignore[arg-type]
        FixtureMetadataProvider(FIXTURES / "metadata.json"),
        transcription,
        weights=ScoreWeights(),
        viral_threshold=1.0,
        transcribe_min_views=0,
        media_provider=media,  # type: ignore[arg-type]
    )

    counters = await service.run()

    doc = db.trend_content.docs[0]
    assert counters["enriched"] == 1
    assert counters["transcribed"] == 1
    assert media.ingest_calls == [("https://example.invalid/video-a1.mp4", "1")]
    assert doc["media_asset"]["key"] == "media/1.mp4"
    # Transcription reads the persisted S3 asset, not the expiring CDN URL.
    assert transcription.urls == ["https://example.com/media/1.mp4"]


@pytest.mark.asyncio
async def test_below_threshold_media_is_not_ingested() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_B2", "processing_status": "discovered"}
    )
    media = _RecordingMediaProvider()
    service = EnrichmentService(
        db,  # type: ignore[arg-type]
        FixtureMetadataProvider(FIXTURES / "metadata.json"),
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        weights=ScoreWeights(),
        viral_threshold=99.0,
        transcribe_min_views=0,
        media_provider=media,  # type: ignore[arg-type]
    )

    counters = await service.run()

    assert counters["skipped_threshold"] == 1
    assert media.ingest_calls == []
    assert "media_asset" not in db.trend_content.docs[0]


@pytest.mark.asyncio
async def test_expired_cdn_url_marks_media_expired_and_leaves_retry_loop() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_A1", "processing_status": "discovered"}
    )
    service = EnrichmentService(
        db,  # type: ignore[arg-type]
        FixtureMetadataProvider(FIXTURES / "metadata.json"),
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        weights=ScoreWeights(),
        viral_threshold=1.0,
        transcribe_min_views=0,
        media_provider=_ExpiredMediaProvider(),  # type: ignore[arg-type]
    )

    counters = await service.run()

    doc = db.trend_content.docs[0]
    assert counters["media_expired"] == 1
    assert counters["failed"] == 0
    assert doc["processing_status"] == "media_expired"
    assert "media download failed" in doc["enrichment_error"]

    # media_expired is terminal: a re-run must not retry the same stale URL.
    second = await service.run()
    assert second["processed"] == 0


class _AdaptiveMetadataProvider(MetadataProvider):
    """Return an empty metadata record on the first fetch, then the fixture."""

    def __init__(self, zero_shortcodes: set[str], fallback: MetadataProvider) -> None:
        self.zero_shortcodes = zero_shortcodes
        self.fallback = fallback

    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        retry = (context or {}).get("zero_score_retry", 0)
        if retry == 0 and shortcode in self.zero_shortcodes:
            return ContentMetadata(shortcode=shortcode)
        return await self.fallback.fetch(
            shortcode, discovered_metadata, context=context
        )


@pytest.mark.asyncio
async def test_zero_score_is_re_enriched_and_retry_count_tracked() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_A1", "processing_status": "discovered"}
    )
    provider = _AdaptiveMetadataProvider(
        {"Fixture_A1"}, FixtureMetadataProvider(FIXTURES / "metadata.json")
    )
    service = EnrichmentService(
        db,  # type: ignore[arg-type]
        provider,
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        weights=ScoreWeights(),
        viral_threshold=1.0,
        transcribe_min_views=0,
    )

    first = await service.run()
    doc = db.trend_content.docs[0]
    assert first["processed"] == 1
    assert first["enriched"] == 0
    assert first["skipped_threshold"] == 1
    assert doc["processing_status"] == "stored"
    assert doc["viral_score"] == 0.0
    assert doc["zero_score_retry_count"] == 1

    second = await service.run()
    doc = db.trend_content.docs[0]
    assert second["processed"] == 1
    assert second["enriched"] == 1
    assert doc["processing_status"] == "enriched"
    assert doc["viral_score"] > 0
    assert doc["zero_score_retry_count"] == 0


@pytest.mark.asyncio
async def test_zero_score_retry_limit_stops_re_enrichment() -> None:
    db = FakeDatabase()
    db.trend_content.docs.append(
        {"_id": 1, "shortcode": "Fixture_A1", "processing_status": "discovered"}
    )
    provider = _AdaptiveMetadataProvider(
        {"Fixture_A1"}, FixtureMetadataProvider(FIXTURES / "metadata.json")
    )
    service = EnrichmentService(
        db,  # type: ignore[arg-type]
        provider,
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        weights=ScoreWeights(),
        viral_threshold=1.0,
        transcribe_min_views=0,
        max_zero_score_retries=1,
    )

    first = await service.run()
    assert first["processed"] == 1
    assert db.trend_content.docs[0]["zero_score_retry_count"] == 1

    second = await service.run()
    assert second["processed"] == 0


@pytest.mark.asyncio
async def test_chained_provider_falls_back_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import metadata as metadata_module

    async def fake_ytdlp(shortcode: str) -> dict[str, Any] | None:
        return {
            "view_count": 1000,
            "video_duration": 15.0,
            "like_count": 50,
            "taken_at": 1700000000,
        }

    monkeypatch.setattr(metadata_module, "_fetch_ytdlp_post", fake_ytdlp)

    provider = ChainedMetadataProvider(
        OfficialMetaMetadataProvider(), YtdlpMetadataProvider()
    )
    discovered = {"caption_text": "discovered caption"}
    result = await provider.fetch(
        "abc", discovered, context={"zero_score_retry": 1}
    )

    assert result.view_count == 1000
    assert result.video_duration == 15.0
    assert result.like_count == 50
    assert result.caption_text == "discovered caption"


@pytest.mark.asyncio
async def test_chained_provider_skips_fallback_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers import metadata as metadata_module

    calls: list[str] = []

    async def fake_ytdlp(shortcode: str) -> dict[str, Any] | None:
        calls.append(shortcode)
        return {"view_count": 1000, "video_duration": 15.0}

    monkeypatch.setattr(metadata_module, "_fetch_ytdlp_post", fake_ytdlp)

    provider = ChainedMetadataProvider(
        OfficialMetaMetadataProvider(), YtdlpMetadataProvider()
    )
    discovered = {"view_count": 0, "owner_follower_count": 0}
    result = await provider.fetch("abc", discovered)

    assert result.view_count == 0
    assert result.video_duration is None
    assert calls == []


class _FakeResponse:
    def __init__(self, ok: bool, payload: dict[str, Any]) -> None:
        self.is_success = ok
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_graph_api_metadata_provider_fetches_public_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    oembed_calls: list[str] = []

    async def fake_get(
        self: Any, url: str | Any, *, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> _FakeResponse:
        url_str = str(url)
        if "instagram_oembed" in url_str:
            oembed_calls.append(url_str)
            return _FakeResponse(True, {"media_id": "123", "shortcode": "abc"})
        if "/123" in url_str:
            return _FakeResponse(
                True,
                {
                    "timestamp": "2026-08-01T12:00:00+0000",
                    "like_count": 500,
                    "comments_count": 20,
                    "view_count": 10_000,
                    "caption": "Graph caption",
                    "media_url": "https://cdn.example/video.mp4",
                    "permalink": "https://instagram.com/p/abc/",
                    "media_type": "REELS",
                    "username": "graph_user",
                },
            )
        return _FakeResponse(False, {})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    provider = GraphApiMetadataProvider("token")
    # When source_id is already available, oEmbed should be skipped and the media
    # endpoint called directly.
    result = await provider.fetch(
        "abc",
        {"source_id": "123"},
        context={"zero_score_retry": 0},
    )

    assert result.like_count == 500
    assert result.comment_count == 20
    assert result.view_count == 10_000
    assert result.video_duration is None
    assert result.video_url == "https://cdn.example/video.mp4"
    assert result.owner_username == "graph_user"
    assert result.taken_at is not None
    assert not oembed_calls


@pytest.mark.asyncio
async def test_chained_provider_prefers_graph_then_ytdlp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers import metadata as metadata_module

    async def fake_ytdlp(shortcode: str) -> dict[str, Any] | None:
        return {
            "view_count": 10_000,
            "video_duration": 25.0,
            "like_count": 1000,
            "taken_at": 1700000000,
        }

    monkeypatch.setattr(metadata_module, "_fetch_ytdlp_post", fake_ytdlp)

    import httpx

    oembed_calls: list[str] = []

    async def fake_get(
        self: Any, url: str | Any, *, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> _FakeResponse:
        url_str = str(url)
        if "instagram_oembed" in url_str:
            oembed_calls.append(url_str)
            return _FakeResponse(True, {"media_id": "123"})
        if "/123" in url_str:
            return _FakeResponse(
                True,
                {
                    "timestamp": "2026-08-01T12:00:00+0000",
                    "like_count": 500,
                    "comments_count": 20,
                    "media_url": "https://cdn.example/video.mp4",
                    "media_type": "REELS",
                    "username": "graph_user",
                },
            )
        return _FakeResponse(False, {})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    provider = ChainedMetadataProvider(
        OfficialMetaMetadataProvider(),
        ChainedMetadataProvider(
            GraphApiMetadataProvider("token"), YtdlpMetadataProvider()
        ),
    )
    # The source_id from discovery lets GraphApi skip oEmbed and hit /{media_id}
    # directly; yt-dlp then fills the missing view_count and video_duration.
    result = await provider.fetch(
        "abc",
        {
            "source_id": "123",
            "view_count": 0,
            "owner_follower_count": 0,
            "like_count": 0,
        },
        context={"zero_score_retry": 0},
    )

    assert not oembed_calls

    assert result.view_count == 10_000
    assert result.video_duration == 25.0
    # Official Graph API engagement wins; yt-dlp only fills missing/zero fields.
    assert result.like_count == 500
    assert result.comment_count == 20
    assert result.owner_username == "graph_user"


@pytest.mark.asyncio
async def test_chained_provider_continues_past_failed_graph_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: GraphApi returning incomplete metadata must not abort the chain.

    When the Instagram oEmbed/media endpoints fail (real-world 400s), the
    GraphApiMetadataProvider must return an incomplete record so the outer
    ChainedMetadataProvider can still try yt-dlp and produce a non-zero score.
    """
    from app.providers import metadata as metadata_module

    async def fake_ytdlp(shortcode: str) -> dict[str, Any] | None:
        return {
            "view_count": 55_000,
            "video_duration": 30.0,
            "like_count": 2500,
            "comment_count": 120,
            "owner_follower_count": 15_000,
            "taken_at": 1700000000,
        }

    monkeypatch.setattr(metadata_module, "_fetch_ytdlp_post", fake_ytdlp)

    import httpx

    async def fake_get(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        # Simulate every Graph API call failing (oEmbed + media endpoint).
        return _FakeResponse(False, {})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    provider = ChainedMetadataProvider(
        OfficialMetaMetadataProvider(),
        ChainedMetadataProvider(
            GraphApiMetadataProvider("token"), YtdlpMetadataProvider()
        ),
    )
    result = await provider.fetch(
        "abc",
        {"view_count": 0, "owner_follower_count": 0, "like_count": 0},
        context={"zero_score_retry": 0},
    )

    assert result.view_count == 55_000
    assert result.like_count == 2500
    assert result.comment_count == 120
    assert result.video_duration == 30.0
    assert result.owner_follower_count == 15_000


@pytest.mark.asyncio
async def test_ytdlp_extracts_photo_metadata_without_video_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: yt-dlp must not abort on photo/carousel posts without video.

    Instagram posts without a video file previously caused "There is no video in
    this post". With ``ignore_no_formats_error`` and the generic ``/p/`` permalink,
    yt-dlp should still return like_count, comment_count and username so the
    viral score is not forced to zero.
    """
    import yt_dlp

    from app.providers.metadata import _fetch_ytdlp_post

    class FakeYDL:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def extract_info(self, url: str, *, download: bool = False) -> dict[str, Any]:
            return {
                "timestamp": 1_700_000_000,
                "uploader": "chef_user",
                "description": "Yummy photo",
                "thumbnail": "https://cdn.example/img.jpg",
                "like_count": 800,
                "comment_count": 45,
                "duration": None,
                "url": "https://cdn.example/img.jpg",
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    result = await _fetch_ytdlp_post("Dbfrhg6knME")

    assert result is not None
    assert result["like_count"] == 800
    assert result["comment_count"] == 45
    assert result["owner_username"] == "chef_user"
    assert result.get("video_url") is None
    assert "video_duration" not in result
