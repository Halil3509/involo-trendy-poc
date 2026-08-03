import asyncio
import logging
import random
from typing import Any

from bson import ObjectId

from app.core.errors import TransientError
from app.infrastructure.resources import Resources
from app.providers.creator_profile import build_creator_profile_provider
from app.providers.embedding import build_embedding_provider
from app.providers.media import build_media_provider
from app.providers.profile_summary import build_profile_summary_provider
from app.providers.scraper import EmitFn, NeedsInterventionError, noop_emit
from app.providers.transcription import build_transcription_provider
from app.providers.vision import build_vision_provider
from app.services.creator_tracking import CreatorTrackingService
from app.services.meta_token import MetaTokenError, build_meta_token_service
from app.services.multimodal import MultimodalService
from app.workers.celery_app import celery_app, settings
from app.workers.runtime import execute_job, log_bus, run_locked

logger = logging.getLogger(__name__)

CREATOR_TRACKING_KIND = "creator_track"
CREATOR_TRACK_ALL_KIND = "creator_track_all"


def _retry_countdown(exc: TransientError, retries: int = 0) -> float:
    """Exponential backoff with jitter; honor Retry-After as the base."""
    base: float = max(exc.retry_after or 60.0, 60.0)
    backoff: float = min(base * (2 ** retries), float(settings.task_retry_backoff_max))
    if retries > 0:
        jitter = random.uniform(0, max(1.0, backoff * 0.1))
        backoff += jitter
    return float(backoff)


def _emit(bus: Any, task_id: str) -> EmitFn:
    if bus is None:
        return noop_emit

    async def emit(message: str, *, level: str = "info", **kwargs: Any) -> None:
        await bus.publish(task_id, message, level=level, **kwargs)

    return emit


async def _service(resources: Resources, task_id: str) -> CreatorTrackingService:
    assert resources.db is not None
    assert resources.qdrant is not None
    bus = log_bus(resources)
    emit = _emit(bus, task_id)
    settings = resources.settings

    if settings.creator_tracking_provider in {"fixture", "playwright"}:
        access_token: str | None = None
    else:
        try:
            access_token = await build_meta_token_service(
                settings, resources.db, redis=resources.redis
            ).get_valid_token()
        except MetaTokenError as exc:
            raise NeedsInterventionError(str(exc)) from exc

    multimodal = MultimodalService(
        resources.db,
        resources.qdrant,
        settings,
        build_media_provider(settings),
        build_vision_provider(settings),
        build_embedding_provider(settings),
        emit=emit,
    )
    return CreatorTrackingService(
        resources.db,
        resources.qdrant,
        settings,
        build_creator_profile_provider(
            settings, access_token=access_token, redis=resources.redis
        ),
        build_transcription_provider(settings),
        multimodal,
        build_profile_summary_provider(settings),
        emit=emit,
    )


async def _run_creator(resources: Resources, creator_id: ObjectId, task_id: str) -> dict[str, int]:
    service = await _service(resources, task_id)
    return await service.run(creator_id)


async def _run_all(resources: Resources, task_id: str) -> dict[str, int]:
    assert resources.db is not None
    bus = log_bus(resources)
    emit = _emit(bus, task_id)
    creators = await resources.db.tracked_creators.find(
        {"status": {"$ne": "not_found"}}
    ).to_list(None)
    totals = {"creators": 0, "snapshotted": 0, "new_posts": 0, "updated_posts": 0, "failed": 0}
    service = await _service(resources, task_id)
    delay_seconds = resources.settings.creator_tracking_batch_delay_ms / 1000.0
    for index, creator in enumerate(creators):
        totals["creators"] += 1
        username = creator.get("username", "?")
        try:
            await emit(f"Tracking creator @{username}...", step="creator", creator=username)
            counters = await service.run(creator["_id"])
            for key in ("snapshotted", "new_posts", "updated_posts"):
                totals[key] += counters.get(key, 0)
        except Exception as exc:  # noqa: BLE001 - isolate per-creator failures
            totals["failed"] += 1
            await emit(
                f"Creator @{username} tracking failed: {exc}",
                level="error",
                step="creator",
                creator=username,
            )
        if index < len(creators) - 1 and delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
    return totals


@celery_app.task(bind=True, name="creator.track", max_retries=settings.task_max_retries)  # type: ignore[untyped-decorator]
def track_creator(self: Any, creator_id: str) -> dict[str, int]:
    task_id = self.request.id
    lock_key = f"involo:creator:{creator_id}"
    try:
        return run_locked(
            task_id,
            CREATOR_TRACKING_KIND,
            lock_key,
            {},
            execute_job(
                task_id,
                CREATOR_TRACKING_KIND,
                lambda resources: _run_creator(resources, ObjectId(creator_id), task_id),
            ),
        )
    except TransientError as exc:
        raise self.retry(
            exc=exc, countdown=_retry_countdown(exc, self.request.retries)
        ) from exc


@celery_app.task(bind=True, name="creator.track_all", max_retries=settings.task_max_retries)  # type: ignore[untyped-decorator]
def track_all_creators(self: Any) -> dict[str, int]:
    task_id = self.request.id
    try:
        return run_locked(
            task_id,
            CREATOR_TRACK_ALL_KIND,
            "involo:creator:track-all",
            {},
            execute_job(
                task_id,
                CREATOR_TRACK_ALL_KIND,
                lambda resources: _run_all(resources, task_id),
            ),
        )
    except TransientError as exc:
        raise self.retry(
            exc=exc, countdown=_retry_countdown(exc, self.request.retries)
        ) from exc
