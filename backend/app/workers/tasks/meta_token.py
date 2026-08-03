"""Celery task that keeps the managed Meta trend token refreshed."""

from __future__ import annotations

import logging
from typing import Any

from app.infrastructure.resources import Resources
from app.providers.scraper import NeedsInterventionError
from app.services.meta_token import MetaTokenError, build_meta_token_service
from app.workers.celery_app import RETRY_KWARGS, celery_app, settings
from app.workers.runtime import execute_job, run_locked

logger = logging.getLogger(__name__)

META_TOKEN_REFRESH_LOCK = "lock:meta_trend_token_refresh"
META_TOKEN_REFRESH_KIND = "meta_trend_token_refresh"


async def _refresh_meta_trend_token(resources: Resources, task_id: str) -> dict[str, int]:
    assert resources.db is not None
    try:
        token = await build_meta_token_service(
            settings, resources.db, redis=resources.redis
        ).get_valid_token()
    except MetaTokenError as exc:
        raise NeedsInterventionError(str(exc)) from exc
    return {"refreshed": 1 if token else 0}


@celery_app.task(bind=True, name="app.tasks.refresh_meta_trend_token", **RETRY_KWARGS)  # type: ignore[untyped-decorator]
def refresh_meta_trend_token(self: Any) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        META_TOKEN_REFRESH_KIND,
        META_TOKEN_REFRESH_LOCK,
        {"refreshed": 0},
        execute_job(
            task_id,
            META_TOKEN_REFRESH_KIND,
            lambda resources: _refresh_meta_trend_token(resources, task_id),
        ),
    )
