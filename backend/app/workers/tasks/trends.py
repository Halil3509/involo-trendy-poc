from functools import partial
from typing import Any

from app.infrastructure.resources import Resources
from app.providers.embedding import build_embedding_provider
from app.providers.media import build_media_provider
from app.providers.metadata import build_metadata_provider
from app.providers.scraper import EmitFn, NeedsInterventionError, build_scraper, noop_emit
from app.providers.transcription import build_transcription_provider
from app.providers.vision import build_vision_provider
from app.schemas.trends import ScraperConfig
from app.services.enrichment import EnrichmentService
from app.services.meta_token import MetaTokenError, build_meta_token_service
from app.services.multimodal import MultimodalService
from app.services.scoring import ScoreWeights
from app.services.scraper import ScraperService
from app.workers.celery_app import RETRY_KWARGS, celery_app, settings
from app.workers.runtime import (
    PIPELINE_LOCK,
    SCRAPER_LOCK,
    execute_job,
    log_bus,
    run_locked,
)


async def _publish(
    resources: Resources,
    task_id: str,
    message: str,
    *,
    level: str = "info",
    step: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    bus = log_bus(resources)
    if bus is None:
        return
    await bus.publish(task_id, message, level=level, step=step, **(data or {}))


def _score_weights() -> ScoreWeights:
    return ScoreWeights(
        distribution_weight=settings.score_distribution_weight,
        engagement_weight=settings.score_engagement_weight,
        velocity_weight=settings.score_velocity_weight,
        comment_weight=settings.score_comment_weight,
        share_weight=settings.score_share_weight,
        distribution_ratio_divisor=settings.score_distribution_ratio_divisor,
        engagement_rate_multiplier=settings.score_engagement_rate_multiplier,
        velocity_log_divisor=settings.score_velocity_log_divisor,
    )


async def _config_value(resources: Resources, key: str, default: Any) -> Any:
    assert resources.db is not None
    config = await resources.db.scraper_config.find_one({"key": "default"})
    if not config or config.get(key) is None:
        return default
    return config[key]


async def _scrape(resources: Resources, keywords: list[str], task_id: str) -> dict[str, int]:
    assert resources.db is not None
    limit = await _config_value(resources, "reels_per_keyword", ScraperConfig().reels_per_keyword)
    headless = await _config_value(resources, "headless", ScraperConfig().headless)
    scrape_settings = settings.model_copy(update={"scraper_headless": bool(headless)})
    bus = log_bus(resources)
    emit: EmitFn = partial(bus.publish, task_id) if bus is not None else noop_emit

    if settings.scraper_adapter == "meta":
        try:
            access_token = await build_meta_token_service(
                settings, resources.db, redis=resources.redis
            ).get_valid_token()
        except MetaTokenError as exc:
            raise NeedsInterventionError(str(exc)) from exc
    else:
        access_token = None

    service = ScraperService(
        resources.db,
        build_scraper(scrape_settings, access_token=access_token, redis=resources.redis),
    )
    return await service.run(keywords, int(limit), emit, job_id=task_id)


@celery_app.task(bind=True, name="app.tasks.scrape_instagram", **RETRY_KWARGS)  # type: ignore[untyped-decorator]
def scrape_instagram(self: Any, keywords: list[str]) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "scrape",
        SCRAPER_LOCK,
        {"discovered": 0, "inserted": 0, "updated": 0},
        execute_job(task_id, "scrape", lambda r: _scrape(r, keywords, task_id)),
    )


async def _enrich(resources: Resources, limit: int | None, task_id: str) -> dict[str, int]:

    assert resources.db is not None
    emit = partial(_publish, resources, task_id, step="enrich")
    threshold = await _config_value(resources, "viral_threshold", ScraperConfig().viral_threshold)
    min_views = await _config_value(
        resources, "transcribe_min_views", ScraperConfig().transcribe_min_views
    )

    access_token: str | None = None
    try:
        access_token = await build_meta_token_service(
            settings, resources.db, redis=resources.redis
        ).get_valid_token()
    except MetaTokenError:
        access_token = None

    service = EnrichmentService(
        resources.db,
        build_metadata_provider(
            settings, access_token=access_token, redis=resources.redis
        ),
        build_transcription_provider(settings),
        weights=_score_weights(),
        viral_threshold=float(threshold),
        transcribe_min_views=int(min_views),
        media_provider=build_media_provider(settings),
        emit=emit,
        max_zero_score_retries=settings.max_zero_score_enrichment_retries,
        zero_score_cooldown_minutes=settings.zero_score_enrichment_cooldown_minutes,
    )
    result = await service.run(limit)
    await emit(
        (
            f"Enrichment complete: {result.get('enriched', 0)} enriched, "
            f"{result.get('transcribed', 0)} transcribed, "
            f"{result.get('skipped_threshold', 0)} skipped, "
            f"{result.get('failed', 0)} failed."
        ),
        data=result,
    )
    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.enrich_trend_content", **RETRY_KWARGS
)
def enrich_trend_content(self: Any, limit: int | None = None) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "enrich",
        PIPELINE_LOCK,
        {"processed": 0},
        execute_job(task_id, "enrich", lambda r: _enrich(r, limit, task_id)),
    )


async def _embed(resources: Resources, limit: int | None, task_id: str) -> dict[str, int]:
    assert resources.db is not None
    assert resources.qdrant is not None
    emit = partial(_publish, resources, task_id, step="embed")
    service = MultimodalService(
        resources.db,
        resources.qdrant,
        settings,
        build_media_provider(settings),
        build_vision_provider(settings),
        build_embedding_provider(settings),
        emit=emit,
    )
    result = await service.run_eligible(limit)
    await emit(
        (
            f"Embedding complete: {result.get('embedded', 0)} embedded, "
            f"{result.get('failed', 0)} failed."
        ),
        data=result,
    )
    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.embed_trend_content", **RETRY_KWARGS
)
def embed_trend_content(self: Any, limit: int | None = None) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "embed",
        PIPELINE_LOCK,
        {"processed": 0},
        execute_job(task_id, "embed", lambda r: _embed(r, limit, task_id)),
    )


async def _run_pipeline(resources: Resources, task_id: str) -> dict[str, int]:
    """Run the full enrich → embed sequence for trend content."""
    await _publish(
        resources,
        task_id,
        "Starting full pipeline: enrich → embed.",
        step="pipeline",
    )
    counters: dict[str, int] = {"enriched": 0, "embedded": 0}

    enrich_result = await _enrich(resources, limit=None, task_id=task_id)
    counters["enriched"] = enrich_result.get("enriched", 0)
    await _publish(
        resources,
        task_id,
        f"Enrich stage finished: {counters['enriched']} items enriched.",
        step="pipeline",
    )

    embed_result = await _embed(resources, limit=None, task_id=task_id)
    counters["embedded"] = embed_result.get("embedded", 0)
    await _publish(
        resources,
        task_id,
        f"Embed stage finished: {counters['embedded']} items embedded.",
        step="pipeline",
    )

    await _publish(
        resources,
        task_id,
        (
            f"Full pipeline complete: {counters['enriched']} enriched, "
            f"{counters['embedded']} embedded."
        ),
        step="pipeline",
        data=counters,
    )
    return counters


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.run_pipeline"
)
def run_pipeline(self: Any) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "pipeline",
        PIPELINE_LOCK,
        {"enriched": 0, "embedded": 0},
        execute_job(task_id, "pipeline", lambda r: _run_pipeline(r, task_id)),
    )


async def _recluster(resources: Resources, task_id: str) -> dict[str, int]:
    await _publish(
        resources,
        task_id,
        "Clustering skipped: service not available.",
        step="cluster",
    )
    return {"points": 0, "clusters": 0, "noise": 0}


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.recluster_trend_content", **RETRY_KWARGS
)
def recluster_trend_content(self: Any) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "cluster",
        PIPELINE_LOCK,
        {"points": 0},
        execute_job(task_id, "cluster", lambda r: _recluster(r, task_id)),
    )
