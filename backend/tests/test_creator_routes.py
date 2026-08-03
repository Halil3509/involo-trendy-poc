from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId
from fakes import FakeDatabase
from fastapi import HTTPException

from app.api.routes.creators import (
    add_creator,
    analyze_creator,
    creator_content,
    creator_detail,
    creator_followers,
    list_creators,
    remove_creator,
)
from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.providers.creator_profile import (
    CreatorProfileProvider,
    CreatorProfileSnapshot,
)
from app.schemas.creators import TrackCreatorRequest


def _request(db: FakeDatabase, settings: Settings | None = None) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resources=SimpleNamespace(
                    db=db, redis=None, settings=settings or Settings()
                )
            )
        )
    )


class FakeCreatorProfileProvider(CreatorProfileProvider):
    async def exists(self, username: str) -> bool:
        return True

    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        return CreatorProfileSnapshot(
            username=username,
            display_name=username,
            bio="",
            avatar_url=None,
            follower_count=0,
            following_count=0,
            media_count=0,
            is_private=False,
            posts=[],
        )


@pytest.fixture(autouse=True)
def fake_creator_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def _build(
        settings: Settings,
        *,
        access_token: str | None = None,
        redis: Any | None = None,
    ) -> CreatorProfileProvider:
        return FakeCreatorProfileProvider()

    monkeypatch.setattr(
        "app.api.routes.creators.build_creator_profile_provider", _build
    )


class FakeTask:
    def __init__(self) -> None:
        self.calls: list[tuple[list[Any], str]] = []

    def apply_async(self, *, args: list[Any], task_id: str) -> None:
        self.calls.append((args, task_id))


@pytest.fixture
def fake_task(monkeypatch: pytest.MonkeyPatch) -> FakeTask:
    task = FakeTask()
    monkeypatch.setattr("app.api.routes.creators.track_creator", task)
    return task


@pytest.mark.asyncio
async def test_add_creator_creates_global_creator_and_link(fake_task: FakeTask) -> None:
    db = FakeDatabase()
    user = {"_id": ObjectId()}
    result = await add_creator(
        _request(db), TrackCreatorRequest(username="@Excalibur"), user
    )

    assert result.username == "excalibur"
    assert len(db.tracked_creators.docs) == 1
    assert len(db.user_tracked_creators.docs) == 1
    assert len(fake_task.calls) == 1  # initial tracking job queued


@pytest.mark.asyncio
async def test_second_user_adding_same_creator_reuses_global_data(
    fake_task: FakeTask,
) -> None:
    db = FakeDatabase()
    user_a = {"_id": ObjectId()}
    user_b = {"_id": ObjectId()}
    await add_creator(_request(db), TrackCreatorRequest(username="excalibur"), user_a)
    result = await add_creator(
        _request(db), TrackCreatorRequest(username="excalibur"), user_b
    )

    assert len(db.tracked_creators.docs) == 1  # no duplicate scraping target
    assert len(db.user_tracked_creators.docs) == 2
    assert len(fake_task.calls) == 2
    assert result.id == str(db.tracked_creators.docs[0]["_id"])


@pytest.mark.asyncio
async def test_add_creator_rejects_invalid_username(fake_task: FakeTask) -> None:
    db = FakeDatabase()
    with pytest.raises(HTTPException) as exc:
        await add_creator(
            _request(db), TrackCreatorRequest(username="not a user!"), {"_id": ObjectId()}
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_list_creators_only_returns_linked(fake_task: FakeTask) -> None:
    db = FakeDatabase()
    user_a = {"_id": ObjectId()}
    user_b = {"_id": ObjectId()}
    await add_creator(_request(db), TrackCreatorRequest(username="excalibur"), user_a)
    await add_creator(_request(db), TrackCreatorRequest(username="other"), user_b)

    result = await list_creators(_request(db), user_a)

    assert [creator.username for creator in result.creators] == ["excalibur"]


async def _linked(db: FakeDatabase, user: dict[str, Any]) -> dict[str, Any]:
    await add_creator(_request(db), TrackCreatorRequest(username="excalibur"), user)
    creator = await db.tracked_creators.find_one({"username": "excalibur"})
    assert creator is not None
    # Real Mongo assigns ObjectId; the in-memory fake uses ints, so normalize.
    creator["_id"] = ObjectId()
    db.user_tracked_creators.docs[0]["creator_id"] = creator["_id"]
    return creator


@pytest.mark.asyncio
async def test_detail_includes_ai_profile(fake_task: FakeTask) -> None:
    db = FakeDatabase()
    user = {"_id": ObjectId()}
    creator = await _linked(db, user)
    await db.creator_profiles.insert_one(
        {
            "creator_id": creator["_id"],
            "ai_summary": "Travel niche creator",
            "structured_profile": {"pillars": [{"name": "travel"}]},
            "average_viral_score": 55.0,
        }
    )

    detail = await creator_detail(_request(db), str(creator["_id"]), user)

    assert detail.ai_summary == "Travel niche creator"
    assert detail.structured_profile is not None


@pytest.mark.asyncio
async def test_detail_404_for_unlinked_user(fake_task: FakeTask) -> None:
    db = FakeDatabase()
    creator = await _linked(db, {"_id": ObjectId()})
    with pytest.raises(HTTPException) as exc:
        await creator_detail(_request(db), str(creator["_id"]), {"_id": ObjectId()})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_followers_filters_by_range_and_computes_delta(
    fake_task: FakeTask,
) -> None:
    db = FakeDatabase()
    user = {"_id": ObjectId()}
    creator = await _linked(db, user)
    now = utcnow()
    db.creator_snapshots.docs.extend(
        [
            {
                "creator_id": creator["_id"],
                "captured_at": now - timedelta(days=100),
                "follower_count": 100,
            },
            {
                "creator_id": creator["_id"],
                "captured_at": now - timedelta(days=2),
                "follower_count": 150,
            },
            {
                "creator_id": creator["_id"],
                "captured_at": now - timedelta(days=1),
                "follower_count": 180,
            },
        ]
    )

    week = await creator_followers(_request(db), str(creator["_id"]), user, range="week")
    assert [point.follower_count for point in week.points] == [150, 180]
    assert week.delta == 30

    year = await creator_followers(_request(db), str(creator["_id"]), user, range="year")
    assert len(year.points) == 3
    assert year.delta == 80


@pytest.mark.asyncio
async def test_content_sorts_and_counts_new(fake_task: FakeTask) -> None:
    db = FakeDatabase()
    user = {"_id": ObjectId()}
    creator = await _linked(db, user)
    now = utcnow()
    db.creator_content.docs.extend(
        [
            {
                "creator_id": creator["_id"],
                "shortcode": "OLD1",
                "taken_at": now - timedelta(days=5),
                "viral_score": 10.0,
                "is_new": False,
            },
            {
                "creator_id": creator["_id"],
                "shortcode": "NEW1",
                "taken_at": now,
                "viral_score": 80.0,
                "is_new": True,
            },
        ]
    )

    viral = await creator_content(
        _request(db), str(creator["_id"]), user, sort="viral", limit=30
    )
    assert [item.shortcode for item in viral.items] == ["NEW1", "OLD1"]
    assert viral.new_count == 1


@pytest.mark.asyncio
async def test_analyze_queues_job_for_linked_creator(fake_task: FakeTask) -> None:
    db = FakeDatabase()
    user = {"_id": ObjectId()}
    creator = await _linked(db, user)

    job = await analyze_creator(_request(db), str(creator["_id"]), user)

    assert job.kind == "creator_track"
    assert job.state == "queued"
    assert fake_task.calls[-1][0] == [str(creator["_id"])]


@pytest.mark.asyncio
async def test_remove_unlinks_user_but_keeps_global_creator(
    fake_task: FakeTask,
) -> None:
    db = FakeDatabase()
    user = {"_id": ObjectId()}
    creator = await _linked(db, user)

    await remove_creator(_request(db), str(creator["_id"]), user)

    assert len(db.user_tracked_creators.docs) == 0
    assert len(db.tracked_creators.docs) == 1  # shared data is retained
