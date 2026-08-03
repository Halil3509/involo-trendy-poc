"""Tests for job cancellation runtime and admin endpoint."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import FakeDatabase

from app.api.routes.admin_stats import stop_job
from app.infrastructure.resources import utcnow
from app.workers.runtime import (
    CANCEL_TTL_SECONDS,
    cancel_key,
    execute_job,
    is_cancel_requested,
    mark_skipped,
    request_cancel,
)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.lists: dict[str, list[str]] = {}
        self.published: list[tuple[str, str]] = []
        self.expirations: dict[str, int] = {}

    async def set(self, key: str, value: Any, *, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is not None:
            self.expirations[key] = ex

    async def exists(self, key: str) -> int:
        return 1 if key in self.data else 0

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                count += 1
        return count

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> None:
        data = self.lists.get(key, [])
        stop = None if end == -1 else end + 1
        self.lists[key] = data[start:stop]

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl

    async def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, data))
        return 1

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        data = self.lists.get(key, [])
        stop = None if end == -1 else end + 1
        return data[start:stop]

    async def aclose(self) -> None:
        pass

    async def ping(self) -> bool:
        return True


class FakeResources:
    instances: list[FakeResources] = []

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.db: FakeDatabase = FakeDatabase()
        self.redis = FakeRedis()
        self.qdrant: Any | None = None
        self.mongo_client: Any | None = None
        self.instances.append(self)

    async def connect(self, *, init_qdrant: bool = True) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_request_cancel_and_check() -> None:
    redis = FakeRedis()
    assert await is_cancel_requested(redis, "task-1") is False
    assert await request_cancel(redis, "task-1") is True
    assert await is_cancel_requested(redis, "task-1") is True
    assert redis.expirations[cancel_key("task-1")] == CANCEL_TTL_SECONDS


@pytest.mark.asyncio
async def test_execute_job_records_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeResources.instances = []
    monkeypatch.setattr("app.workers.runtime.Resources", FakeResources)

    async def runner(resources: FakeResources) -> dict[str, int]:
        return {"processed": 3}

    result = await execute_job("task-1", "enrich", runner)

    resources = FakeResources.instances[-1]
    assert result == {"processed": 3}
    document = await resources.db.job_runs.find_one({"task_id": "task-1"})
    assert document is not None
    assert document["state"] == "succeeded"
    assert document["counters"] == {"processed": 3}
    assert "created_at" in document
    assert document["created_at"] == document["started_at"]


@pytest.mark.asyncio
async def test_execute_job_cancels_when_cancel_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeResources.instances = []
    monkeypatch.setattr("app.workers.runtime.Resources", FakeResources)
    monkeypatch.setattr("app.workers.runtime.CANCEL_POLL_SECONDS", 0.05)

    async def runner(resources: FakeResources) -> dict[str, int]:
        await request_cancel(resources.redis, "task-1")
        await asyncio.sleep(2.0)
        return {"processed": 99}

    result = await execute_job("task-1", "enrich", runner)

    resources = FakeResources.instances[-1]
    assert result == {}
    document = await resources.db.job_runs.find_one({"task_id": "task-1"})
    assert document is not None
    assert document["state"] == "cancelled"
    assert document["error"] == "enrich job cancelled by user"
    assert cancel_key("task-1") not in resources.redis.data
    assert "created_at" in document


@pytest.mark.asyncio
async def test_mark_skipped_records_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeResources.instances = []
    monkeypatch.setattr("app.workers.runtime.Resources", FakeResources)

    await mark_skipped("task-1", "scrape")

    resources = FakeResources.instances[-1]
    document = await resources.db.job_runs.find_one({"task_id": "task-1"})
    assert document is not None
    assert document["state"] == "skipped_locked"
    assert "created_at" in document
    assert document["finished_at"] is not None


@pytest.mark.asyncio
async def test_stop_job_activates_cancel_and_revokes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    db = FakeDatabase()
    db.job_runs.docs.append(
        {
            "task_id": "task-1",
            "kind": "enrich",
            "state": "running",
            "created_at": utcnow(),
        }
    )
    redis = AsyncMock()
    revoke_mock = MagicMock()
    monkeypatch.setattr(
        "app.api.routes.admin_stats.celery_app.control.revoke",
        revoke_mock,
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(resources=SimpleNamespace(db=db, redis=redis))
        )
    )

    result = await stop_job("task-1", request, {})

    assert result.state == "cancelled"
    document = await db.job_runs.find_one({"task_id": "task-1"})
    assert document["state"] == "cancelled"
    assert document["error"] == "Stopped by user"
    redis.set.assert_awaited_once_with(
        cancel_key("task-1"), "1", ex=CANCEL_TTL_SECONDS
    )
    revoke_mock.assert_called_once_with("task-1", terminate=False)


@pytest.mark.asyncio
async def test_stop_job_404_when_missing() -> None:
    from types import SimpleNamespace

    db = FakeDatabase()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(resources=SimpleNamespace(db=db, redis=AsyncMock()))
        )
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await stop_job("missing", request, {})
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_stop_job_409_when_not_active() -> None:
    from types import SimpleNamespace

    db = FakeDatabase()
    db.job_runs.docs.append(
        {"task_id": "task-1", "kind": "enrich", "state": "succeeded"}
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(resources=SimpleNamespace(db=db, redis=AsyncMock()))
        )
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await stop_job("task-1", request, {})
    assert exc_info.value.status_code == 409
