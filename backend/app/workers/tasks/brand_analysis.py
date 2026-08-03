"""Celery task for brand reference analysis."""

from __future__ import annotations

from functools import partial
from typing import Any

from app.core.config import get_settings
from app.infrastructure.resources import Resources
from app.providers.brand_analysis import (
    BrandAnalysisError,
    build_brand_analysis_provider,
)
from app.providers.brand_caption import build_brand_caption_analyzer
from app.providers.brand_report import build_brand_analysis_report_provider
from app.providers.media import build_media_provider
from app.providers.scraper import NeedsInterventionError
from app.providers.vision import build_vision_provider
from app.services.brand_analysis import BrandAnalysisService
from app.services.meta_token import MetaTokenError, build_meta_token_service
from app.workers.celery_app import RETRY_KWARGS, celery_app
from app.workers.runtime import execute_job, is_cancel_requested, log_bus, run_locked

BRAND_ANALYSIS_LOCK = "lock:brand_analysis"


async def _analyze(
    resources: Resources,
    task_id: str,
    username_or_url: str,
    max_posts: int,
) -> dict[str, int]:
    assert resources.db is not None
    settings = get_settings()
    bus = log_bus(resources)
    emit = partial(bus.publish, task_id) if bus is not None else _noop_emit

    access_token = ""
    business_account_id = settings.meta_instagram_business_account_id

    if settings.brand_analysis_provider != "fake":
        try:
            access_token = await build_meta_token_service(
                settings, resources.db, redis=resources.redis
            ).get_valid_token()
        except MetaTokenError as exc:
            raise NeedsInterventionError(str(exc)) from exc

        if not business_account_id:
            if not access_token:
                raise BrandAnalysisError("meta_trend_access_token_not_configured")
            connection = await resources.db.instagram_connections.find_one(
                {"status": {"$in": ["connected", "ready"]}},
                sort=[("last_synced_at", -1)],
            )
            if not connection or not connection.get("instagram_user_id"):
                raise BrandAnalysisError(
                    "instagram_business_account_id_not_configured"
                )
            business_account_id = str(connection["instagram_user_id"])

    provider = build_brand_analysis_provider(
        settings,
        access_token,
        business_account_id=business_account_id,
        redis=resources.redis,
    )
    media = build_media_provider(settings)
    vision = build_vision_provider(settings)
    caption_analyzer = build_brand_caption_analyzer(settings)
    report_provider = build_brand_analysis_report_provider(settings)
    service = BrandAnalysisService(
        resources.db,
        settings,
        provider,
        media,
        vision,
        caption_analyzer,
        report_provider,
        is_cancelled=partial(is_cancel_requested, resources.redis, task_id),
    )
    return await service.run(
        task_id,
        username_or_url,
        max_posts,
        emit=emit,
    )


async def _noop_emit(message: str, **kwargs: Any) -> None:
    pass


@celery_app.task(bind=True, name="app.tasks.analyze_brand", **RETRY_KWARGS)  # type: ignore[untyped-decorator]
def analyze_brand(
    self: Any,
    job_id: str,
    username_or_url: str,
    max_posts: int = 10,
) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "brand_analysis",
        BRAND_ANALYSIS_LOCK,
        {"resolved": 0, "fetched": 0, "failed": 0},
        execute_job(
            task_id,
            "brand_analysis",
            lambda r: _analyze(r, task_id, username_or_url, max_posts),
        ),
    )
