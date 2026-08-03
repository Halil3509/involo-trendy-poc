"""Admin controls for scheduled bulk user profiling."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from croniter import croniter
from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import AdminUser, resources, settings
from app.api.responses import job_response
from app.infrastructure.resources import utcnow
from app.schemas.jobs import JobResponse
from app.schemas.profiling import ProfilingConfig, ProfilingEstimate
from app.tasks import profile_all_users

router = APIRouter(prefix="/admin/profiling", tags=["admin", "profiling"])


async def _load_config(request: Request) -> ProfilingConfig:
    document = await resources(request).db.profiling_config.find_one({"key": "default"})
    if document:
        return ProfilingConfig.model_validate(document)
    cfg = settings(request)
    return ProfilingConfig(
        enabled=cfg.profiling_schedule_enabled,
        schedule_cron=cfg.profiling_schedule_cron,
    )


@router.get("/config", response_model=ProfilingConfig)
async def get_config(request: Request, _: AdminUser) -> ProfilingConfig:
    return await _load_config(request)


@router.put("/config", response_model=ProfilingConfig)
async def update_config(
    payload: ProfilingConfig, request: Request, _: AdminUser
) -> ProfilingConfig:
    await resources(request).db.profiling_config.update_one(
        {"key": "default"},
        {"$set": {**payload.model_dump(), "updated_at": utcnow()}},
        upsert=True,
    )
    return payload


@router.post("/runs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_all(request: Request, _: AdminUser) -> JobResponse:
    task_id = uuid4().hex
    document = {
        "task_id": task_id,
        "kind": "profile_all",
        "state": "queued",
        "counters": {},
        "created_at": utcnow(),
    }
    await resources(request).db.job_runs.insert_one(document)
    profile_all_users.apply_async(args=[], task_id=task_id)
    return job_response(document)


@router.get("/runs/latest", response_model=JobResponse)
async def latest_run(request: Request, _: AdminUser) -> JobResponse:
    document = await resources(request).db.job_runs.find_one(
        {"kind": "profile_all"}, sort=[("created_at", -1)]
    )
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profiling jobs found")
    return job_response(document)


@router.get("/estimate", response_model=ProfilingEstimate)
async def estimate(request: Request, _: AdminUser) -> ProfilingEstimate:
    db = resources(request).db
    cfg = await _load_config(request)
    connected = await db.instagram_connections.count_documents({"status": {"$ne": "needs_reauth"}})
    sample_size = settings(request).profiling_estimate_sample_size
    cursor = (
        db.job_runs.find({"kind": "profile_all", "state": "succeeded"})
        .sort("created_at", -1)
        .limit(sample_size)
    )
    per_user: list[float] = []
    async for job in cursor:
        users = int((job.get("counters") or {}).get("users", 0))
        duration = float(job.get("duration_seconds", 0) or 0)
        if users > 0 and duration > 0:
            per_user.append(duration / users)
    average = (
        sum(per_user) / len(per_user)
        if per_user
        else settings(request).profiling_default_seconds_per_user
    )
    duration = average * connected
    start_at: datetime | None = None
    finish_at: datetime | None = None
    if cfg.enabled and cfg.schedule_cron:
        start_at = croniter(cfg.schedule_cron, utcnow()).get_next(datetime)
        finish_at = start_at + timedelta(seconds=duration)
    return ProfilingEstimate(
        connected_users=connected,
        average_seconds_per_user=round(average, 2),
        estimated_duration_seconds=round(duration, 2),
        estimated_start_at=start_at,
        estimated_finish_at=finish_at,
    )
