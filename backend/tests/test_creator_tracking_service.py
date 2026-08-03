from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeDatabase
from provider_doubles import (
    FakeEmbeddingProvider,
    FakeMultimodalProcessor,
    FakeProfileSummaryProvider,
    FakeTranscriptionProvider,
)

from app.core.config import Settings
from app.core.errors import TransientError
from app.infrastructure.resources import utcnow
from app.providers.creator_profile import (
    CreatorPost,
    CreatorProfileProvider,
    CreatorProfileSnapshot,
    PlaywrightCreatorProfileProvider,
)
from app.providers.scraper import NeedsInterventionError
from app.services.creator_tracking import CreatorTrackingService
from app.workers.tasks.creator_tracking import _retry_countdown

FIXTURES = Path(__file__).parent / "fixtures"


class StubCreatorProvider(CreatorProfileProvider):
    def __init__(self, snapshot: CreatorProfileSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        self.calls += 1
        return self.snapshot


class FailingCreatorProvider(CreatorProfileProvider):
    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        raise NeedsInterventionError("instagram requires verification")


class RateLimitedCreatorProvider(CreatorProfileProvider):
    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        raise TransientError("upstream returned 429", retry_after=120.0)


def _post(shortcode: str, days_ago: int, **overrides: Any) -> CreatorPost:
    return CreatorPost(
        shortcode=shortcode,
        caption=overrides.get("caption", f"travel reel {shortcode} istanbul food"),
        media_type=overrides.get("media_type", "REELS"),
        permalink=f"https://www.instagram.com/p/{shortcode}/",
        taken_at=utcnow() - timedelta(days=days_ago),
        like_count=overrides.get("like_count", 1000),
        comment_count=overrides.get("comment_count", 50),
        view_count=overrides.get("view_count", 20000),
        media_url=overrides.get("media_url", "https://example.invalid/video.mp4"),
        thumbnail_url="https://example.invalid/thumb.jpg",
    )


def _snapshot(posts: list[CreatorPost]) -> CreatorProfileSnapshot:
    return CreatorProfileSnapshot(
        username="fixture_creator",
        display_name="Fixture Creator",
        bio="Travel and food",
        avatar_url="https://example.invalid/avatar.jpg",
        follower_count=42000,
        following_count=350,
        media_count=210,
        is_private=False,
        posts=posts,
    )


class FakeQdrant:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, Any]] = []

    async def upsert(self, *, collection_name: str, points: Any) -> None:
        self.upserts.append((collection_name, points))


def _service(
    db: FakeDatabase, provider: CreatorProfileProvider
) -> CreatorTrackingService:
    settings = Settings(vector_size=8)
    qdrant = FakeQdrant()
    return CreatorTrackingService(
        db,
        qdrant,  # type: ignore[arg-type]
        settings,
        provider,
        FakeTranscriptionProvider(FIXTURES / "transcripts.json"),
        FakeMultimodalProcessor(qdrant, FakeEmbeddingProvider(8), settings.vector_schema_version),
        FakeProfileSummaryProvider(),
    )


async def _seed_creator(db: FakeDatabase) -> Any:
    result = await db.tracked_creators.insert_one(
        {"username": "fixture_creator", "status": "active", "created_at": utcnow()}
    )
    return result.inserted_id


@pytest.mark.asyncio
async def test_run_snapshots_and_processes_new_posts() -> None:
    db = FakeDatabase()
    creator_id = await _seed_creator(db)
    provider = StubCreatorProvider(_snapshot([_post("AAA1", 1), _post("BBB2", 3)]))
    service = _service(db, provider)

    counters = await service.run(creator_id)

    assert counters == {"snapshotted": 1, "new_posts": 2, "updated_posts": 0, "embedded": 2}
    snapshots = db.creator_snapshots.docs
    assert len(snapshots) == 1
    assert snapshots[0]["follower_count"] == 42000
    contents = db.creator_content.docs
    assert len(contents) == 2
    assert all(doc["is_new"] for doc in contents)
    assert all(doc["processing_status"] == "embedded" for doc in contents)
    assert all(doc["viral_score"] > 0 for doc in contents)
    creator = await db.tracked_creators.find_one({"_id": creator_id})
    assert creator["status"] == "active"
    assert creator["follower_count"] == 42000
    assert creator["trend_score"] > 0
    profile = await db.creator_profiles.find_one({"creator_id": creator_id})
    assert profile is not None
    assert profile["ai_summary"]
    assert profile["structured_profile"]["pillars"]


@pytest.mark.asyncio
async def test_second_run_updates_existing_posts_without_reembedding() -> None:
    db = FakeDatabase()
    creator_id = await _seed_creator(db)
    provider = StubCreatorProvider(_snapshot([_post("AAA1", 1)]))
    service = _service(db, provider)
    await service.run(creator_id)

    provider.snapshot = _snapshot([_post("AAA1", 2, like_count=2500), _post("CCC3", 0)])
    counters = await service.run(creator_id)

    assert counters["updated_posts"] == 1
    assert counters["new_posts"] == 1
    assert counters["embedded"] == 1
    assert len(db.creator_content.docs) == 2
    updated = next(d for d in db.creator_content.docs if d["shortcode"] == "AAA1")
    assert updated["like_count"] == 2500
    assert updated["is_new"] is False
    new = next(d for d in db.creator_content.docs if d["shortcode"] == "CCC3")
    assert new["is_new"] is True
    # Same-day snapshot is overwritten, not duplicated.
    assert len(db.creator_snapshots.docs) == 1


@pytest.mark.asyncio
async def test_needs_intervention_maps_status_and_reraises() -> None:
    db = FakeDatabase()
    creator_id = await _seed_creator(db)
    service = _service(db, FailingCreatorProvider())

    with pytest.raises(NeedsInterventionError):
        await service.run(creator_id)

    creator = await db.tracked_creators.find_one({"_id": creator_id})
    assert creator["status"] == "needs_intervention"
    assert creator["last_error"]


@pytest.mark.asyncio
async def test_transient_error_keeps_status_for_celery_retry() -> None:
    db = FakeDatabase()
    creator_id = await _seed_creator(db)
    service = _service(db, RateLimitedCreatorProvider())

    with pytest.raises(TransientError):
        await service.run(creator_id)

    creator = await db.tracked_creators.find_one({"_id": creator_id})
    assert creator["status"] == "tracking"  # not "failed"; Celery will retry


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [(None, 60.0), (5.0, 60.0), (120.0, 120.0), (99999.0, 300.0)],
)
def test_retry_countdown_honors_retry_after(
    retry_after: float | None, expected: float
) -> None:
    exc = TransientError("upstream returned 429", retry_after=retry_after)
    assert _retry_countdown(exc) == expected


@pytest.mark.asyncio
async def test_weekly_growth_uses_snapshot_older_than_seven_days() -> None:
    db = FakeDatabase()
    creator_id = await _seed_creator(db)
    await db.creator_snapshots.insert_one(
        {
            "creator_id": creator_id,
            "day": (utcnow() - timedelta(days=8)).date().isoformat(),
            "captured_at": utcnow() - timedelta(days=8),
            "follower_count": 40000,
        }
    )
    service = _service(db, StubCreatorProvider(_snapshot([])))

    growth = await service._weekly_growth_pct(creator_id, 42000)

    assert growth == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_playwright_provider_runs_without_meta_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = {
        "username": "playwright_creator",
        "full_name": "Playwright Creator",
        "biography": "travel",
        "profile_pic_url": "https://cdn.invalid/avatar.jpg",
        "edge_followed_by": {"count": 50000},
        "edge_follow": {"count": 100},
        "edge_owner_to_timeline_media": {
            "count": 1,
            "edges": [
                {
                    "node": {
                        "__typename": "GraphVideo",
                        "shortcode": "PW01",
                        "is_video": True,
                        "taken_at_timestamp": int((utcnow() - timedelta(days=1)).timestamp()),
                        "edge_media_to_caption": {
                            "edges": [{"node": {"text": "istanbul food"}}]
                        },
                        "edge_media_preview_like": {"count": 1000},
                        "edge_media_to_comment": {"count": 50},
                        "video_view_count": 20000,
                        "video_url": "https://cdn.invalid/pw01.mp4",
                        "display_url": "https://cdn.invalid/pw01.jpg",
                    }
                }
            ],
        },
        "is_private": False,
    }

    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        return user

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )

    db = FakeDatabase()
    creator_id = await _seed_creator(db)
    settings = Settings(
        creator_tracking_provider="playwright",
        creator_tracking_max_posts=1,
        vector_size=8,
    )
    provider = PlaywrightCreatorProfileProvider(settings)
    service = _service(db, provider)

    counters = await service.run(creator_id)

    assert counters["snapshotted"] == 1
    assert counters["new_posts"] == 1
    assert counters["embedded"] == 1
    creator = await db.tracked_creators.find_one({"_id": creator_id})
    assert creator["status"] == "active"
    assert creator["follower_count"] == 50000
    snapshots = db.creator_snapshots.docs
    assert len(snapshots) == 1
    assert snapshots[0]["follower_count"] == 50000
    contents = db.creator_content.docs
    assert len(contents) == 1
    assert contents[0]["shortcode"] == "PW01"
