"""Compatibility exports for the reorganized Celery worker package."""

from app.workers.celery_app import celery_app
from app.workers.scheduler import scheduled_dispatch
from app.workers.tasks import (
    analyze_brand,
    capture_metric_snapshots,
    capture_recommendation_outcomes,
    capture_topic_signals,
    cleanup_stale_jobs,
    embed_trend_content,
    enrich_trend_content,
    multimodal_backfill,
    profile_all_users,
    profile_user,
    refresh_meta_trend_token,
    run_pipeline,
    scrape_instagram,
    track_all_creators,
    track_creator,
)

__all__ = [
    "celery_app",
    "analyze_brand",
    "capture_metric_snapshots",
    "capture_recommendation_outcomes",
    "capture_topic_signals",
    "cleanup_stale_jobs",
    "embed_trend_content",
    "enrich_trend_content",
    "profile_all_users",
    "profile_user",
    "multimodal_backfill",
    "refresh_meta_trend_token",
    "run_pipeline",
    "scheduled_dispatch",
    "scrape_instagram",
    "track_all_creators",
    "track_creator",
]
