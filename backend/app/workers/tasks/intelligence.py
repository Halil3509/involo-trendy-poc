from typing import Any

from app.core.token_crypto import TokenCipher
from app.infrastructure.resources import Resources
from app.providers.embedding import build_embedding_provider
from app.providers.instagram_profile import build_instagram_profile_provider
from app.providers.media import build_media_provider
from app.providers.topic_signals import (
    GoogleTrendsProvider,
    RedditTopicSignalProvider,
    TopicSignalProvider,
    YouTubeTopicSignalProvider,
)
from app.providers.vision import build_vision_provider
from app.services.intelligence import OutcomeService
from app.services.multimodal import MultimodalService
from app.services.snapshots import SnapshotService
from app.services.topic_signals import TopicSignalService
from app.workers.celery_app import RETRY_KWARGS, celery_app, settings
from app.workers.runtime import PIPELINE_LOCK, execute_job, run_locked


async def _capture_snapshots(resources: Resources) -> dict[str, int]:
    assert resources.db is not None
    return await SnapshotService(resources.db).capture_due(settings.snapshot_offsets_hours)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.capture_metric_snapshots", **RETRY_KWARGS
)
def capture_metric_snapshots(self: Any) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "metric_snapshot",
        f"{PIPELINE_LOCK}:snapshot",
        {"processed": 0},
        execute_job(
            task_id,
            "metric_snapshot",
            _capture_snapshots,
        ),
    )


async def _capture_outcomes(resources: Resources, offset_hours: int) -> dict[str, int]:
    assert resources.db is not None
    return await OutcomeService(
        resources.db,
        build_instagram_profile_provider(settings, redis=resources.redis),
        TokenCipher(settings.instagram_token_encryption_key.get_secret_value()),
    ).capture_due(offset_hours=offset_hours)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.capture_recommendation_outcomes", **RETRY_KWARGS
)
def capture_recommendation_outcomes(self: Any, offset_hours: int) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "recommendation_outcome",
        f"{PIPELINE_LOCK}:outcome:{offset_hours}",
        {"processed": 0},
        execute_job(
            task_id,
            "recommendation_outcome",
            lambda resources: _capture_outcomes(resources, offset_hours),
        ),
    )


async def _multimodal_backfill(
    resources: Resources, limit: int | None
) -> dict[str, int]:
    assert resources.db is not None
    assert resources.qdrant is not None
    service = MultimodalService(
        resources.db,
        resources.qdrant,
        settings,
        build_media_provider(settings),
        build_vision_provider(settings),
        build_embedding_provider(settings),
    )
    return await service.backfill(limit)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.multimodal_backfill", **RETRY_KWARGS
)
def multimodal_backfill(self: Any, limit: int | None = None) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "multimodal_backfill",
        f"{PIPELINE_LOCK}:multimodal",
        {"processed": 0},
        execute_job(
            task_id,
            "multimodal_backfill",
            lambda resources: _multimodal_backfill(resources, limit),
        ),
    )


async def _capture_topic_signals(resources: Resources) -> dict[str, int]:
    assert resources.db is not None
    config = await resources.db.scraper_config.find_one({"key": "default"})
    topics = list((config or {}).get("keywords", []))
    providers: list[TopicSignalProvider] = []
    if settings.google_trends_enabled:
        providers.append(GoogleTrendsProvider(settings))
    if settings.youtube_signals_enabled:
        providers.append(YouTubeTopicSignalProvider(settings))
    if settings.reddit_signals_enabled:
        providers.append(RedditTopicSignalProvider(settings))
    return await TopicSignalService(resources.db, providers).capture(topics)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.capture_topic_signals", **RETRY_KWARGS
)
def capture_topic_signals(self: Any) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "topic_signals",
        f"{PIPELINE_LOCK}:topic-signals",
        {"signals": 0},
        execute_job(task_id, "topic_signals", _capture_topic_signals),
    )
