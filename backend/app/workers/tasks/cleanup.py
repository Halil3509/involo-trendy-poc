"""Maintenance tasks for job queue hygiene."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.infrastructure.resources import Resources, utcnow
from app.workers.celery_app import celery_app, settings

logger = logging.getLogger(__name__)


async def _cleanup_stale_jobs() -> dict[str, int]:
    """Cancel jobs that have been queued or running for too long."""
    resources = Resources(settings)
    await resources.connect(init_qdrant=False)
    assert resources.db is not None

    cutoff = utcnow() - timedelta(hours=settings.stale_job_cleanup_hours)
    try:
        result = await resources.db.job_runs.update_many(
            {
                "state": {"$in": ["queued", "running"]},
                "created_at": {"$lt": cutoff},
            },
            {
                "$set": {
                    "state": "cancelled",
                    "finished_at": utcnow(),
                    "error": f"Stale job cleaned up after {settings.stale_job_cleanup_hours}h",
                }
            },
        )
        cleaned = result.modified_count
        logger.info(
            "cleaned up %d stale queued/running jobs older than %dh",
            cleaned,
            settings.stale_job_cleanup_hours,
        )
        return {"cleaned": cleaned}
    finally:
        await resources.close()


@celery_app.task(name="app.tasks.cleanup_stale_jobs")  # type: ignore[untyped-decorator]
def cleanup_stale_jobs() -> dict[str, int]:
    return asyncio.run(_cleanup_stale_jobs())
