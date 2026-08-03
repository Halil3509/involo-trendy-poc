"""WebSocket endpoint that streams live scraper log events to admins."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.dependencies import authorize_admin_websocket
from app.core.config import Settings
from app.infrastructure.log_bus import JobLogBus

router = APIRouter(tags=["admin", "scraper"])

# 1008 == policy violation (RFC 6455); used when auth fails on a WebSocket.
POLICY_VIOLATION = 1008


@router.websocket("/admin/scraper/runs/{task_id}/logs")
async def scraper_logs(websocket: WebSocket, task_id: str) -> None:
    try:
        if not await authorize_admin_websocket(websocket):
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
