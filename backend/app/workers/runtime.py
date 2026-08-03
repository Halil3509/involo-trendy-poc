import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any, cast

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

from app.infrastructure.log_bus import JobLogBus
from app.infrastructure.resources import Resources, utcnow
from app.providers.scraper import NeedsInterventionError
from app.workers.celery_app import settings

SCRAPER_LOCK = "involo:scraper:single-job"
PIPELINE_LOCK = "involo:pipeline:single-job"
PROFILE_ALL_LOCK = "involo:profiling:all"

CANCEL_KEY_PREFIX = "involo:job:cancel"
CANCEL_TTL_SECONDS = 3600
CANCEL_POLL_SECONDS = 1.0

INTERVENTION_REQUEST_KEY_PREFIX = "involo:job:intervention"
INTERVENTION_RESPONSE_KEY_PREFIX = "involo:job:intervention_response"
INTERVENTION_TTL_SECONDS = 600
INTERVENTION_POLL_SECONDS = 1.0


def cancel_key(task_id: str) -> str:
    return f"{CANCEL_KEY_PREFIX}:{task_id}"


async def request_cancel(redis: AsyncRedis | None, task_id: str) -> bool:
    if redis is None:
        return False
    await redis.set(cancel_key(task_id), "1", ex=CANCEL_TTL_SECONDS)
    return True


async def is_cancel_requested(redis: AsyncRedis | None, task_id: str) -> bool:
    if redis is None:
        return False
    return bool(await redis.exists(cancel_key(task_id)))


def intervention_request_key(task_id: str) -> str:
    return f"{INTERVENTION_REQUEST_KEY_PREFIX}:{task_id}"


def intervention_response_key(task_id: str) -> str:
    return f"{INTERVENTION_RESPONSE_KEY_PREFIX}:{task_id}"


async def submit_intervention_response(
    redis: AsyncRedis,
    task_id: str,
    payload: dict[str, Any],
) -> None:
    """Store the admin's response to an intervention request."""
    await redis.set(
        intervention_response_key(task_id),
        json.dumps(payload),
        ex=INTERVENTION_TTL_SECONDS,
    )


async def request_intervention(
    resources: Resources,
    task_id: str,
    prompt: str,
    fields: list[str],
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    """Pause the job, ask the admin for input via the log bus, and wait for it.

    Publishes an ``intervention`` log event, updates the job state, and polls
    Redis for a response. Raises ``asyncio.CancelledError`` if the job is
    cancelled, or ``NeedsInterventionError`` if the timeout expires.
    """
    if resources.redis is None:
        raise NeedsInterventionError("Redis is required for intervention requests")

    response_key = intervention_response_key(task_id)
    await resources.redis.delete(response_key)

    if resources.db is not None:
        await resources.db.job_runs.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "state": "needs_intervention",
                    "intervention": {
                        "prompt": prompt,
                        "fields": fields,
                        "requested_at": utcnow().isoformat(),
                    },
                    "finished_at": None,
                }
            },
        )

    bus = log_bus(resources)
    if bus is not None:
        await bus.publish(
            task_id,
            prompt,
            level="intervention",
            step="needs_input",
            data={"fields": fields, "prompt": prompt},
        )

    loop = asyncio.get_event_loop()
    started = loop.time()
    while True:
        if await is_cancel_requested(resources.redis, task_id):
            raise asyncio.CancelledError()

        raw = await resources.redis.get(response_key)
        if raw:
            try:
                return cast(dict[str, Any], json.loads(raw))
            except (ValueError, TypeError) as exc:
                raise NeedsInterventionError(
                    f"Invalid intervention response: {exc}"
                ) from exc

        if loop.time() - started > timeout_seconds:
            raise NeedsInterventionError(
                f"Intervention timed out after {timeout_seconds} seconds"
            )

        await asyncio.sleep(INTERVENTION_POLL_SECONDS)


def log_bus(resources: Resources) -> JobLogBus | None:
    if resources.redis is None:
        return None
    return JobLogBus(
        resources.redis,
        max_lines=settings.scraper_log_max_lines,
        ttl_seconds=settings.scraper_log_ttl_seconds,
    )


async def finalize_logs(resources: Resources, task_id: str, state: str, message: str) -> None:
    """Publish a terminal event and persist the recent log tail for review."""
    bus = log_bus(resources)
    if bus is None or resources.db is None:
        return
    level = "success" if state == "succeeded" else "error"
    try:
        await bus.publish(task_id, message, level=level, step="job", terminal=True, state=state)
        history = await bus.history(task_id)
        if history and settings.scraper_log_persist_lines > 0:
            await resources.db.job_runs.update_one(
                {"task_id": task_id},
                {"$set": {"logs": history[-settings.scraper_log_persist_lines :]}},
            )
    except Exception:  # noqa: BLE001 - logging must never mask the job result
        pass


async def _watch_cancel(
    resources: Resources,
    task_id: str,
    task: asyncio.Task[dict[str, int]],
) -> None:
    if resources.redis is None:
        return
    key = cancel_key(task_id)
    try:
        while True:
            await asyncio.sleep(CANCEL_POLL_SECONDS)
            if await resources.redis.exists(key):
                task.cancel()
                return
    except asyncio.CancelledError:
        return


async def execute_job(
    task_id: str,
    kind: str,
    runner: Callable[[Resources], Coroutine[Any, Any, dict[str, int]]],
) -> dict[str, int]:
    resources = Resources(settings)
    await resources.connect()
    assert resources.db is not None
    started_at = utcnow()
    try:
        await resources.db.job_runs.update_one(
            {"task_id": task_id},
            {
                "$set": {"state": "running", "kind": kind, "started_at": started_at},
                "$setOnInsert": {"created_at": started_at},
            },
            upsert=True,
        )
        if resources.redis is not None:
            await resources.redis.delete(cancel_key(task_id))
        bus = log_bus(resources)
        if bus is not None:
            await bus.publish(task_id, f"{kind} job started", level="info", step="job")

        runner_task: asyncio.Task[dict[str, int]] = asyncio.create_task(runner(resources))
        monitor_task = asyncio.create_task(_watch_cancel(resources, task_id, runner_task))
        try:
            counters = await runner_task
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        await resources.db.job_runs.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "state": "succeeded",
                    "counters": counters,
                    "finished_at": utcnow(),
                    "duration_seconds": max(0.0, (utcnow() - started_at).total_seconds()),
                    "error": None,
                }
            },
        )
        await finalize_logs(resources, task_id, "succeeded", f"{kind} job succeeded.")
        return counters
    except asyncio.CancelledError:
        await resources.db.job_runs.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "state": "cancelled",
                    "finished_at": utcnow(),
                    "duration_seconds": max(0.0, (utcnow() - started_at).total_seconds()),
                    "error": f"{kind} job cancelled by user",
                }
            },
        )
        await finalize_logs(resources, task_id, "cancelled", f"{kind} job cancelled by user.")
        return {}
    except NeedsInterventionError as exc:
        await resources.db.job_runs.update_one(
            {"task_id": task_id},
            {"$set": {"state": "needs_intervention", "error": str(exc), "finished_at": utcnow()}},
        )
        await finalize_logs(
            resources, task_id, "needs_intervention", f"{kind} job needs intervention: {exc}"
        )
        raise
    except Exception as exc:
        await resources.db.job_runs.update_one(
            {"task_id": task_id},
            {"$set": {"state": "failed", "error": str(exc), "finished_at": utcnow()}},
        )
        await finalize_logs(resources, task_id, "failed", f"{kind} job failed: {exc}")
        raise
    finally:
        if resources.redis is not None:
            await resources.redis.delete(cancel_key(task_id))
            await resources.redis.delete(intervention_response_key(task_id))
        await resources.close()


def run_locked(
    task_id: str,
    kind: str,
    lock_key: str,
    empty: dict[str, int],
    coro: Coroutine[Any, Any, dict[str, int]],
) -> dict[str, int]:
    redis = SyncRedis.from_url(settings.redis_url)
    lock = redis.lock(lock_key, timeout=60 * 60, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        coro.close()
        asyncio.run(mark_skipped(task_id, kind))
        return empty
    try:
        return asyncio.run(coro)
    finally:
        try:
            lock.release()
        finally:
            redis.close()


async def mark_skipped(task_id: str, kind: str = "scrape") -> None:
    resources = Resources(settings)
    await resources.connect()
    assert resources.db is not None
    try:
        await resources.db.job_runs.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "state": "skipped_locked",
                    "kind": kind,
                    "finished_at": utcnow(),
                    "counters": {},
                },
                "$setOnInsert": {"created_at": utcnow()},
            },
            upsert=True,
        )
    finally:
        await resources.close()
