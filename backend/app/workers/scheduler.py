import asyncio
from typing import Any
from uuid import uuid4

from app.core.schedule import cron_due
from app.infrastructure.resources import Resources, utcnow
from app.schemas.trends import ScraperConfig
from app.workers.celery_app import celery_app, settings
from app.workers.tasks import (
    capture_metric_snapshots,
    capture_recommendation_outcomes,
    capture_topic_signals,
    embed_trend_content,
    enrich_trend_content,
    profile_all_users,
    scrape_instagram,
    track_all_creators,
)

_TASK_BY_KIND: dict[str, Any] = {
    "scrape": scrape_instagram,
    "enrich": enrich_trend_content,
    "embed": embed_trend_content,
    "profile_all": profile_all_users,
    "metric_snapshot": capture_metric_snapshots,
    "recommendation_outcome": capture_recommendation_outcomes,
    "topic_signals": capture_topic_signals,
    "creator_track_all": track_all_creators,
}


async def _queue_job(resources: Resources, kind: str, args: list[Any]) -> None:
    assert resources.db is not None
    task_id = uuid4().hex
    await resources.db.job_runs.insert_one(
        {
            "task_id": task_id,
            "kind": kind,
            "state": "queued",
            "counters": {},
            "created_at": utcnow(),
            "scheduled": True,
        }
    )
    _TASK_BY_KIND[kind].apply_async(args=args, task_id=task_id)


async def _scheduled_dispatch() -> dict[str, int]:
    resources = Resources(settings)
    await resources.connect(init_qdrant=False)
    assert resources.db is not None
    try:
        dispatched = 0
        config = await resources.db.scraper_config.find_one({"key": "default"})
        if config:
            due = cron_due(config.get("schedule_cron"), utcnow(), config.get("last_scheduled_run"))
            if due is not None:
                await resources.db.scraper_config.update_one(
                    {"key": "default"}, {"$set": {"last_scheduled_run": due}}
                )
                keywords = ScraperConfig.model_validate(config).keywords
                if config.get("enabled", True) and keywords:
                    await _queue_job(resources, "scrape", [keywords])
                if config.get("schedule_pipeline"):
                    for kind in ("enrich", "embed"):
                        await _queue_job(resources, kind, [])
                dispatched += 1

        profile_config = await resources.db.profiling_config.find_one({"key": "default"})
        profile_cron = (
            profile_config.get("schedule_cron")
            if profile_config
            else settings.profiling_schedule_cron
        )
        profile_enabled = (
            profile_config.get("enabled", False)
            if profile_config
            else settings.profiling_schedule_enabled
        )
        profile_due = cron_due(
            profile_cron,
            utcnow(),
            profile_config.get("last_scheduled_run") if profile_config else None,
        )
        if profile_enabled and profile_due is not None:
            await resources.db.profiling_config.update_one(
                {"key": "default"},
                {"$set": {"last_scheduled_run": profile_due}},
                upsert=True,
            )
            await _queue_job(resources, "profile_all", [])
            dispatched += 1

        # Creator tracking: fixed daily snapshot time (default 00:00 UTC = 03:00 UTC+3),
        # not user-configurable. Last-run state is kept in creator_tracking_config.
        if settings.creator_tracking_schedule_enabled:
            creator_config = await resources.db.creator_tracking_config.find_one(
                {"key": "default"}
            )
            creator_due = cron_due(
                settings.creator_tracking_schedule_cron,
                utcnow(),
                creator_config.get("last_scheduled_run") if creator_config else None,
            )
            if creator_due is not None:
                await resources.db.creator_tracking_config.update_one(
                    {"key": "default"},
                    {"$set": {"last_scheduled_run": creator_due}},
                    upsert=True,
                )
                await _queue_job(resources, "creator_track_all", [])
                dispatched += 1
        assert resources.redis is not None
        hour_key = f"involo:intelligence:hour:{utcnow().strftime('%Y%m%d%H')}"
        if await resources.redis.set(hour_key, "1", ex=7200, nx=True):
            await _queue_job(resources, "metric_snapshot", [])
            if settings.topic_signals_schedule_enabled:
                await _queue_job(resources, "topic_signals", [])
            for offset in settings.outcome_offsets_hours:
                await _queue_job(resources, "recommendation_outcome", [offset])
            dispatched += (
                1
                + int(settings.topic_signals_schedule_enabled)
                + len(settings.outcome_offsets_hours)
            )
        return {"dispatched": dispatched}
    finally:
        await resources.close()


@celery_app.task(name="app.tasks.scheduled_dispatch")  # type: ignore[untyped-decorator]
def scheduled_dispatch() -> dict[str, int]:
    return asyncio.run(_scheduled_dispatch())
