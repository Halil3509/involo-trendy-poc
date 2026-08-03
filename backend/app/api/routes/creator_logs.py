"""WebSocket endpoint that streams live creator tracking logs to the creator owner."""

from __future__ import annotations

from typing import cast

from bson import ObjectId
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.config import Settings
from app.core.security import ACCESS_COOKIE, decode_access_token
from app.infrastructure.log_bus import JobLogBus

router = APIRouter(tags=["creators"])

# 1008 == policy violation (RFC 6455); used when auth fails on a WebSocket.
POLICY_VIOLATION = 1008


async def _authorize_creator_websocket(
    websocket: WebSocket,
    creator_id: str,
) -> bool:
    """Validate the access cookie and confirm the user tracks this creator."""
    resources = websocket.app.state.resources
    settings = cast(Settings, websocket.app.state.settings)
    token = websocket.cookies.get(ACCESS_COOKIE)
    if not token:
        return False
    try:
        payload = decode_access_token(token, settings)
        user_id = ObjectId(payload["sub"])
    except Exception:  # noqa: BLE001 - auth failures are mapped to close
        return False
    if resources.db is None:
        return False
    user = await resources.db.users.find_one(
        {"_id": user_id, "disabled": {"$ne": True}}
    )
    if not user:
        return False
    try:
        object_id = ObjectId(creator_id)
    except Exception:  # noqa: BLE001
        return False
    link = await resources.db.user_tracked_creators.find_one(
        {"user_id": user_id, "creator_id": object_id}
    )
    return bool(link)


@router.websocket("/creators/{creator_id}/analyze/{task_id}/logs")
async def creator_analysis_logs(
    websocket: WebSocket, creator_id: str, task_id: str
) -> None:
    try:
        if not await _authorize_creator_websocket(websocket, creator_id):
            await websocket.close(code=POLICY_VIOLATION)
            return

        await websocket.accept()
        settings = cast(Settings, websocket.app.state.settings)
        resources = websocket.app.state.resources
        bus = JobLogBus(
            resources.redis,
            max_lines=settings.scraper_log_max_lines,
            ttl_seconds=settings.scraper_log_ttl_seconds,
        )

        history = await bus.history(task_id)
        for event in history:
            await websocket.send_json(event)
        if history and history[-1].get("terminal"):
            await websocket.close()
            return

        async for event in bus.subscribe(task_id):
            await websocket.send_json(event)
            if event.get("terminal"):
                break
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except RuntimeError:
                pass
