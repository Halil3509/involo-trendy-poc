"""Tests for the creator-side live log WebSocket endpoint."""

from __future__ import annotations

import json
from typing import Any

import pytest
from bson import ObjectId
from fakes import FakeDatabase
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes import creator_logs
from app.core.config import Settings
from app.core.security import ACCESS_COOKIE, create_access_token


class FakeResources:
    def __init__(self) -> None:
        self.settings = Settings()
        self.db = FakeDatabase()
        self.redis: Any = None


class FakeJobLogBus:
    def __init__(self, *_: Any, **__: Any) -> None:
        self.history_events: list[dict[str, Any]] = []

    async def history(self, task_id: str) -> list[dict[str, Any]]:
        return list(self.history_events)

    async def subscribe(self, task_id: str) -> Any:
        # No live events in these tests; route will close after history.
        if False:
            yield {}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(creator_logs, "JobLogBus", FakeJobLogBus)
    app = FastAPI()
    app.state.settings = Settings()
    app.state.resources = FakeResources()
    app.include_router(creator_logs.router, prefix="/api/v1")
    return TestClient(app)


def _seed_link(resources: FakeResources, user_id: ObjectId, creator_id: ObjectId) -> None:
    resources.db.users.docs.append(
        {"_id": user_id, "username": "testuser", "disabled": False}
    )
    resources.db.tracked_creators.docs.append({"_id": creator_id, "username": "fixture"})
    resources.db.user_tracked_creators.docs.append(
        {"user_id": user_id, "creator_id": creator_id}
    )


def test_logs_require_auth_cookie(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/v1/creators/123abc/analyze/task-1/logs"
        ):
            pass
    assert exc_info.value.code == creator_logs.POLICY_VIOLATION


def test_logs_require_linked_creator(client: TestClient) -> None:
    resources = client.app.state.resources
    user_id = ObjectId()
    creator_id = ObjectId()
    resources.db.users.docs.append(
        {"_id": user_id, "username": "testuser", "disabled": False}
    )
    resources.db.tracked_creators.docs.append({"_id": creator_id, "username": "fixture"})

    token = create_access_token(str(user_id), "user", resources.settings)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/api/v1/creators/{creator_id}/analyze/task-1/logs",
            cookies={ACCESS_COOKIE: token},
        ):
            pass
    assert exc_info.value.code == creator_logs.POLICY_VIOLATION


def test_logs_stream_history_for_linked_creator(client: TestClient) -> None:
    resources = client.app.state.resources
    user_id = ObjectId()
    creator_id = ObjectId()
    _seed_link(resources, user_id, creator_id)

    # Pre-populate the fake log bus history.
    bus = FakeJobLogBus()
    bus.history_events = [
        {"ts": "2026-07-30T00:00:00Z", "level": "info", "message": "started"},
        {"ts": "2026-07-30T00:00:01Z", "level": "success", "message": "done", "terminal": True},
    ]
    creator_logs.JobLogBus = lambda *args, **kwargs: bus  # type: ignore[misc]

    token = create_access_token(str(user_id), "user", resources.settings)

    with client.websocket_connect(
        f"/api/v1/creators/{creator_id}/analyze/task-1/logs",
        cookies={ACCESS_COOKIE: token},
    ) as websocket:
        messages: list[dict[str, Any]] = []
        while True:
            message = websocket.receive()
            if message["type"] == "websocket.close":
                break
            messages.append(json.loads(message["text"]))

    assert len(messages) == 2
    assert messages[0]["message"] == "started"
    assert messages[1]["message"] == "done"
