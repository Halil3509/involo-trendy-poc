from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from app.api.dependencies import AdminUser, authorize_admin_websocket, resources
from app.api.responses import job_response
from app.api.statistics import compute_pipeline_stats
from app.core.config import Settings
from app.infrastructure.log_bus import JobLogBus
from app.infrastructure.resources import utcnow
from app.schemas.jobs import JobResponse
from app.schemas.trends import PipelineStats
from app.tasks import (
    embed_trend_content,
    enrich_trend_content,
    multimodal_backfill,
    run_pipeline,
)

router = APIRouter(prefix="/admin/pipeline", tags=["admin", "pipeline"])



_TASKS = {
    "enrich": enrich_trend_content,
    "embed": embed_trend_content,
    "multimodal_backfill": multimodal_backfill,
}


async def _dispatch(request: Request, kind: str) -> JobResponse:
    now = utcnow()
    task_id = uuid4().hex
    document: dict[str, Any] = {
        "task_id": task_id,
        "kind": kind,
        "state": "queued",
        "counters": {},
        "created_at": now,
    }
    await resources(request).db.job_runs.insert_one(document)
    _TASKS[kind].apply_async(args=[], task_id=task_id)
    return job_response(document)


@router.post("/enrich", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_enrich(request: Request, _: AdminUser) -> JobResponse:
    return await _dispatch(request, "enrich")


@router.post("/embed", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_embed(request: Request, _: AdminUser) -> JobResponse:
    return await _dispatch(request, "embed")


@router.post(
    "/multimodal-backfill",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_multimodal_backfill(request: Request, _: AdminUser) -> JobResponse:
    return await _dispatch(request, "multimodal_backfill")


@router.get("/runs/latest", response_model=JobResponse)
async def latest_run(
    request: Request,
    _: AdminUser,
    kind: str | None = Query(default=None),
) -> JobResponse:
    query = {"kind": kind} if kind else {}
    document = await resources(request).db.job_runs.find_one(query, sort=[("created_at", -1)])
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pipeline jobs found")
    return job_response(document)


@router.get("/runs/{task_id}", response_model=JobResponse)
async def get_run(task_id: str, request: Request, _: AdminUser) -> JobResponse:
    document = await resources(request).db.job_runs.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pipeline job not found")
    return job_response(document)


@router.post("/run", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_pipeline_run(request: Request, _: AdminUser) -> JobResponse:
    now = utcnow()
    task_id = uuid4().hex
    document: dict[str, Any] = {
        "task_id": task_id,
        "kind": "pipeline",
        "state": "queued",
        "counters": {},
        "created_at": now,
    }
    await resources(request).db.job_runs.insert_one(document)
    run_pipeline.apply_async(args=[], task_id=task_id)
    return job_response(document)


@router.get("/stats", response_model=PipelineStats)
async def pipeline_stats(request: Request, _: AdminUser) -> PipelineStats:
    return await compute_pipeline_stats(resources(request).db)


# 1008 == policy violation (RFC 6455); used when auth fails on a WebSocket.
POLICY_VIOLATION = 1008


@router.websocket("/runs/{task_id}/logs")
async def pipeline_logs(websocket: WebSocket, task_id: str) -> None:
    """Stream live log events for a pipeline run to authenticated admins."""
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
