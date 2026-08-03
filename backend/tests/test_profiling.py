import hashlib
import hmac
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fakes import FakeDatabase
from fastapi import HTTPException
from provider_doubles import (
    FakeEmbeddingProvider,
    FakeMultimodalProcessor,
    FakeProfileSummaryProvider,
    FakeTranscriptionProvider,
)

from app.api.routes.profiling import (
    instagram_webhook_event,
    instagram_webhook_verify,
    start_oauth,
)
from app.core.config import Settings
from app.core.token_crypto import TokenCipher
from app.infrastructure.resources import utcnow
from app.providers.instagram_profile import (
    InstagramAccount,
    InstagramGraphError,
    InstagramMedia,
    InstagramProfileProvider,
    TokenBundle,
)
from app.services.instagram_webhook import InstagramWebhookService
from app.services.profiling import ProfilingService, average_and_dispersion

FIXTURES = Path(__file__).parent / "fixtures"


class StubInstagramProvider(InstagramProfileProvider):
    def authorization_url(self, state: str) -> str:
        return f"https://example.test/oauth?state={state}"

    async def exchange_code(self, code: str) -> TokenBundle:
        return TokenBundle("token", utcnow() + timedelta(days=60))

    async def refresh_token(self, access_token: str) -> TokenBundle:
        return TokenBundle(access_token, utcnow() + timedelta(days=60))

    async def fetch_account(self, access_token: str) -> InstagramAccount:
        return InstagramAccount("ig-1", "fixture_creator", 25000)

    async def fetch_recent_media(
        self, access_token: str, account_id: str, *, now: Any
    ) -> list[InstagramMedia]:
        return [
            InstagramMedia(
                id="media-1",
                shortcode="Fixture_A1",
                caption="Travel ideas",
                media_type="REELS",
                media_url="https://example.invalid/video.mp4",
                permalink="https://instagram.com/reel/Fixture_A1/",
                taken_at=now - timedelta(days=2),
                like_count=500,
                comment_count=20,
                view_count=10000,
                share_count=40,
                insights_available=True,
            ),
            InstagramMedia(
                id="media-2",
                shortcode="Fixture_B2",
                caption="Quick recipe",
                media_type="REELS",
                media_url="https://example.invalid/video-2.mp4",
                permalink="https://instagram.com/reel/Fixture_B2/",
                taken_at=now - timedelta(days=4),
                like_count=300,
                comment_count=10,
                view_count=8000,
                share_count=15,
                insights_available=True,
            ),
        ]


class FakeQdrant:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, Any]] = []

    async def upsert(self, *, collection_name: str, points: Any) -> None:
        self.upserts.append((collection_name, points))


def test_average_and_dispersion_is_zero_for_identical_vectors() -> None:
    average, dispersion = average_and_dispersion([[1.0, 2.0], [1.0, 2.0]])
    assert average == [1.0, 2.0]
    assert dispersion == pytest.approx(0.0)


def test_average_and_dispersion_uses_rms_distance() -> None:
    average, dispersion = average_and_dispersion([[0.0, 0.0], [2.0, 0.0]])
    assert average == [1.0, 0.0]
    assert dispersion == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_profile_pipeline_processes_every_item_and_writes_average() -> None:
    db = FakeDatabase()
    qdrant = FakeQdrant()
    user_id = ObjectId()
    cipher = TokenCipher("a sufficiently long test encryption secret")
    db.instagram_connections.docs.append(
        {
            "_id": 1,
            "user_id": user_id,
            "access_token_encrypted": cipher.encrypt("token"),
            "token_expires_at": utcnow() + timedelta(days=30),
            "status": "connected",
        }
    )
    db.user_preferences.docs.append(
        {
            "user_id": user_id,
            "target_countries": ["TR"],
            "target_cities": ["Istanbul"],
            "content_languages": ["tr"],
            "niches": ["travel", "food"],
            "goals": ["saves"],
            "constraints": ["indoor only"],
            "timezone": "Europe/Istanbul",
        }
    )
    settings = Settings(
        vector_size=8,
        instagram_token_encryption_key="a sufficiently long test encryption secret",
    )
    service = ProfilingService(
        db,
        qdrant,  # type: ignore[arg-type]
        settings,
        StubInstagramProvider(),
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        FakeMultimodalProcessor(qdrant, FakeEmbeddingProvider(8), settings.vector_schema_version),
        FakeProfileSummaryProvider(),
        cipher,
    )

    counters = await service.run(user_id)

    assert counters == {"processed": 2, "transcribed": 2, "embedded": 2, "failed": 0}
    assert len(db.user_content.docs) == 2
    assert all(doc["processing_status"] == "embedded" for doc in db.user_content.docs)
    assert all(doc["visual_analysis"] for doc in db.user_content.docs)
    assert all(doc["keyframes"] for doc in db.user_content.docs)
    assert all("fused_vector" in doc for doc in db.user_content.docs)
    assert all(doc["performance_score"]["cohort"]["size"] == 2 for doc in db.user_content.docs)
    assert len(db.user_profiles.docs) == 1
    profile = db.user_profiles.docs[0]
    assert profile["content_count_analyzed"] == 2
    assert profile["average_vector_id"]
    assert "@fixture_creator" in profile["ai_profile_summary"]
    assert all(
        pillar["id"].startswith("semantic:")
        for pillar in profile["structured_profile"]["pillars"]
    )
    assert profile["structured_profile"]["target_markets"] == ["TR", "Istanbul"]
    assert profile["structured_profile"]["constraints"] == ["indoor only"]
    assert db.instagram_connections.docs[0]["status"] == "ready"
    assert [name for name, _ in qdrant.upserts].count("user_content_v2") == 2
    assert [name for name, _ in qdrant.upserts].count("user_profiles_v2") == 1
    content_upserts = [name for name, _ in qdrant.upserts].count("user_content_v2")
    assert content_upserts == 2
    vectors = qdrant.upserts[0][1][0].vector
    assert vectors["text"] != vectors["audio_video"]
    assert vectors["fused"] != vectors["text"]


@pytest.mark.asyncio
async def test_profile_pipeline_reuses_cached_media_and_avoids_re_embedding() -> None:
    db = FakeDatabase()
    qdrant = FakeQdrant()
    user_id = ObjectId()
    cipher = TokenCipher("a sufficiently long test encryption secret")
    db.instagram_connections.docs.append(
        {
            "_id": 1,
            "user_id": user_id,
            "access_token_encrypted": cipher.encrypt("token"),
            "token_expires_at": utcnow() + timedelta(days=30),
            "status": "connected",
        }
    )
    db.user_preferences.docs.append(
        {
            "user_id": user_id,
            "target_countries": ["TR"],
            "target_cities": ["Istanbul"],
            "content_languages": ["tr"],
            "niches": ["travel", "food"],
            "goals": ["saves"],
            "constraints": ["indoor only"],
            "timezone": "Europe/Istanbul",
        }
    )
    settings = Settings(
        vector_size=8,
        instagram_token_encryption_key="a sufficiently long test encryption secret",
    )
    service = ProfilingService(
        db,
        qdrant,  # type: ignore[arg-type]
        settings,
        StubInstagramProvider(),
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        FakeMultimodalProcessor(
            qdrant, FakeEmbeddingProvider(8), settings.vector_schema_version
        ),
        FakeProfileSummaryProvider(),
        cipher,
    )

    first = await service.run(user_id)
    assert first == {"processed": 2, "transcribed": 2, "embedded": 2, "failed": 0}
    assert all("fused_vector" in doc for doc in db.user_content.docs)
    content_upserts = [name for name, _ in qdrant.upserts].count("user_content_v2")
    assert content_upserts == 2

    second = await service.run(user_id)
    assert second["processed"] == 2
    assert second["embedded"] == 2
    assert second["failed"] == 0
    # No new user_content_v2 upserts because cached vectors are reused.
    assert [name for name, _ in qdrant.upserts].count("user_content_v2") == content_upserts
    # Profile is updated again.
    assert [name for name, _ in qdrant.upserts].count("user_profiles_v2") == 2


@pytest.mark.asyncio
async def test_start_oauth_maps_instagram_graph_error_to_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    redis = AsyncMock()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings, resources=SimpleNamespace(redis=redis)
            )
        )
    )

    def _fake_build(_: Settings, **kwargs: Any) -> InstagramProfileProvider:
        raise InstagramGraphError("provider not configured")

    monkeypatch.setattr(
        "app.api.routes.profiling.build_instagram_profile_provider", _fake_build
    )

    with pytest.raises(HTTPException) as excinfo:
        await start_oauth(request, {"_id": ObjectId()})  # type: ignore[arg-type]

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "instagram_oauth_unavailable"


def _webhook_request(settings: Settings, db: Any | None = None) -> Any:
    if db is None:
        db = AsyncMock()
    resources = SimpleNamespace(db=db)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=settings, resources=resources)
        ),
        query_params={},
    )


@pytest.mark.asyncio
async def test_webhook_verify_returns_challenge_when_token_matches() -> None:
    settings = Settings(instagram_webhook_verify_token="test-token")
    request = _webhook_request(settings)

    response = await instagram_webhook_verify(
        request,
        "subscribe",
        "test-token",
        "challenge-123",
    )

    assert response.status_code == 200
    assert response.body.decode() == "challenge-123"


@pytest.mark.asyncio
async def test_webhook_verify_rejects_wrong_token() -> None:
    settings = Settings(instagram_webhook_verify_token="test-token")
    request = _webhook_request(settings)

    with pytest.raises(HTTPException) as excinfo:
        await instagram_webhook_verify(request, "subscribe", "wrong-token", "challenge")

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "instagram_webhook_verification_failed"


@pytest.mark.asyncio
async def test_webhook_verify_returns_503_when_not_configured() -> None:
    settings = Settings(instagram_webhook_verify_token=None)
    request = _webhook_request(settings)

    with pytest.raises(HTTPException) as excinfo:
        await instagram_webhook_verify(request, "subscribe", "token", "challenge")

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_webhook_event_accepts_valid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "app-secret"
    body = b'{"test": "payload"}'
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    settings = Settings(instagram_app_secret=secret)
    request = _webhook_request(settings)
    handle_mock = AsyncMock()
    monkeypatch.setattr(InstagramWebhookService, "handle_event", handle_mock)

    response = await instagram_webhook_event(request, expected, body)

    assert response.status_code == 200
    handle_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_event_rejects_invalid_signature() -> None:
    secret = "app-secret"
    body = b'{"test": "payload"}'
    settings = Settings(instagram_app_secret=secret)
    request = _webhook_request(settings)

    with pytest.raises(HTTPException) as excinfo:
        await instagram_webhook_event(request, "sha256=badsignature", body)

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid_webhook_signature"


@pytest.mark.asyncio
async def test_webhook_service_dispatches_sync_for_known_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(instagram_app_secret="secret")
    db = AsyncMock()
    db.instagram_connections.find_one.return_value = {
        "_id": "conn-1",
        "user_id": "user123",
    }
    profile_mock = MagicMock()
    monkeypatch.setattr(
        "app.services.instagram_webhook.profile_user",
        profile_mock,
    )
    service = InstagramWebhookService(settings)
    payload = b'{"object": "instagram", "entry": [{"id": "12345", "changes": []}]}'

    await service.handle_event(db, payload)

    db.instagram_webhook_events.insert_one.assert_awaited_once()
    db.instagram_connections.find_one.assert_awaited_once()
    db.instagram_connections.update_one.assert_awaited_once()
    profile_mock.apply_async.assert_called_once_with(args=["user123"])
