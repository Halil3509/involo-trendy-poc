"""Admin overview statistics and job queue monitoring endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pymongo.asynchronous.database import AsyncDatabase

from app.api.dependencies import AdminUser, resources
from app.api.responses import job_response
from app.api.statistics import compute_pipeline_stats
from app.infrastructure.resources import utcnow
from app.schemas.jobs import JobResponse
from app.schemas.trends import AdminOverview
from app.workers.celery_app import celery_app
from app.workers.runtime import CANCEL_TTL_SECONDS, cancel_key

router = APIRouter(prefix="/admin", tags=["admin", "stats"])

ATTENTION_STATES = ("failed", "needs_intervention")


async def _jobs_by_state(db: AsyncDatabase[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state in await db.job_runs.distinct("state"):
        if state is None:
            continue
        counts[str(state)] = await db.job_runs.count_documents({"state": state})
    return counts


@router.get("/overview", response_model=AdminOverview)
async def overview(request: Request, _: AdminUser) -> AdminOverview:
    db = resources(request).db
    pipeline = await compute_pipeline_stats(db)
    jobs_by_state = await _jobs_by_state(db)
    attention_jobs = sum(jobs_by_state.get(state, 0) for state in ATTENTION_STATES)

    return AdminOverview(
        total_users=await db.users.count_documents({}),
        admin_users=await db.users.count_documents({"role": "admin"}),
        connected_instagram=await db.instagram_connections.count_documents({}),
        needs_reauth=await db.instagram_connections.count_documents({"status": "needs_reauth"}),
        trend_content_total=await db.trend_content.count_documents({}),
        pipeline=pipeline,
        user_content_total=await db.user_content.count_documents({}),
        user_profiles_ready=await db.user_profiles.count_documents({}),
        recommendation_batches=await db.recommendations.count_documents({}),
        jobs_by_state=jobs_by_state,
        attention_jobs=attention_jobs,
    )


@router.get("/jobs", response_model=list[JobResponse])
async def recent_jobs(
    request: Request,
    _: AdminUser,
    limit: int = Query(default=20, ge=1, le=100),
    state: str | None = Query(default=None),
    kind: str | None = Query(default=None),
) -> list[JobResponse]:
    query: dict[str, str] = {}
    if state:
        query["state"] = state
    if kind:
        query["kind"] = kind
    cursor = resources(request).db.job_runs.find(query).sort("created_at", -1).limit(limit)
    return [job_response(document) async for document in cursor]


@router.post("/jobs/{task_id}/stop", response_model=JobResponse)
async def stop_job(
    task_id: str,
    request: Request,
    _: AdminUser,
) -> JobResponse:
    db = resources(request).db
    redis = resources(request).redis
    document = await db.job_runs.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if document["state"] not in ("queued", "running"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not active")
    if redis is not None:
        await redis.set(cancel_key(task_id), "1", ex=CANCEL_TTL_SECONDS)
    await asyncio.to_thread(
        celery_app.control.revoke,
        task_id,
        terminate=False,
    )
    now = utcnow()
    await db.job_runs.update_one(
        {"task_id": task_id},
        {"$set": {"state": "cancelled", "finished_at": now, "error": "Stopped by user"}},
    )
    return job_response(await db.job_runs.find_one({"task_id": task_id}))
