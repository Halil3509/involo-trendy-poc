from typing import Any

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings
from app.core.errors import TransientError

settings = get_settings()
celery_app = Celery("involo", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "pipeline-scheduler": {
            "task": "app.tasks.scheduled_dispatch",
            "schedule": 60.0,
        },
        "meta-token-refresh": {
            "task": "app.tasks.refresh_meta_trend_token",
            "schedule": crontab(hour=0, minute=0),
        },
        "stale-job-cleanup": {
            "task": "app.tasks.cleanup_stale_jobs",
            "schedule": crontab(minute=0),
        },
    },
)

RETRY_KWARGS: dict[str, Any] = {
    "autoretry_for": (TransientError,),
    "retry_backoff": True,
    "retry_backoff_max": settings.task_retry_backoff_max,
    "retry_jitter": True,
    "max_retries": settings.task_max_retries,
}
