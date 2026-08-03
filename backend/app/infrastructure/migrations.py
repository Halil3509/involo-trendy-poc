"""Small, versioned Mongo migration runner.

Migrations are additive and idempotent. Destructive cleanup is intentionally not
performed during application startup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pymongo import ASCENDING, DESCENDING, IndexModel

Migration = Callable[[Any], Awaitable[None]]


async def _v1_content_intelligence(db: Any) -> None:
    collections = {
        "content_metric_snapshots",
        "audience_snapshots",
        "user_preferences",
        "recommendation_events",
        "recommendation_post_links",
        "recommendation_experiments",
        "provider_runs",
        "topic_signal_snapshots",
        "topic_signal_aggregates",
        "schema_migrations",
    }
    existing = set(await db.list_collection_names())
    for name in collections - existing:
        await db.create_collection(name)
    await db.content_metric_snapshots.create_indexes(
        [
            IndexModel(
                [
                    ("subject_type", ASCENDING),
                    ("subject_id", ASCENDING),
                    ("offset_hours", ASCENDING),
                ],
                unique=True,
                name="snapshot_subject_offset",
            ),
            IndexModel([("captured_at", DESCENDING)]),
        ]
    )
    await db.audience_snapshots.create_indexes(
        [
            IndexModel([("user_id", ASCENDING), ("captured_at", DESCENDING)]),
            IndexModel([("provider_snapshot_id", ASCENDING)], unique=True, sparse=True),
        ]
    )
    await db.user_preferences.create_index([("user_id", ASCENDING)], unique=True)
    await db.recommendation_events.create_indexes(
        [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel(
                [("user_id", ASCENDING), ("idempotency_key", ASCENDING)], unique=True
            ),
            IndexModel([("recommendation_id", ASCENDING), ("created_at", DESCENDING)]),
        ]
    )
    await db.recommendation_post_links.create_indexes(
        [
            IndexModel([("user_id", ASCENDING), ("recommendation_id", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING), ("media_id", ASCENDING)], unique=True),
        ]
    )
    await db.recommendation_experiments.create_indexes(
        [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("recommendation_id", ASCENDING)]),
        ]
    )
    await db.provider_runs.create_indexes(
        [
            IndexModel([("provider", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("state", ASCENDING), ("created_at", DESCENDING)]),
        ]
    )
    await db.content_metric_snapshots.create_index(
        [("user_id", ASCENDING), ("captured_at", DESCENDING)]
    )
    await db.job_runs.create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)]
    )
    ttl_name = "created_at_-1"
    indexes = await db.job_runs.list_indexes()
    async for idx in indexes:
        if (
            idx.get("name") == ttl_name
            and idx.get("expireAfterSeconds") != 30 * 24 * 60 * 60
        ):
            await db.job_runs.drop_index(ttl_name)
            break
    await db.job_runs.create_index(
        [("created_at", DESCENDING)], expireAfterSeconds=30 * 24 * 60 * 60
    )
    await db.topic_signal_snapshots.create_indexes(
        [
            IndexModel([("topic", ASCENDING), ("captured_at", DESCENDING)]),
            IndexModel([("source", ASCENDING), ("captured_at", DESCENDING)]),
        ]
    )
    await db.topic_signal_aggregates.create_index([("topic", ASCENDING)], unique=True)


async def _v2_topic_signals(db: Any) -> None:
    existing = set(await db.list_collection_names())
    for name in ("topic_signal_snapshots", "topic_signal_aggregates"):
        if name not in existing:
            await db.create_collection(name)
    await db.topic_signal_snapshots.create_indexes(
        [
            IndexModel([("topic", ASCENDING), ("captured_at", DESCENDING)]),
            IndexModel([("source", ASCENDING), ("captured_at", DESCENDING)]),
        ]
    )
    await db.topic_signal_aggregates.create_index([("topic", ASCENDING)], unique=True)


async def _v3_evaluation_and_erasure(db: Any) -> None:
    existing = set(await db.list_collection_names())
    for name in ("ranking_predictions", "evaluation_runs"):
        if name not in existing:
            await db.create_collection(name)
    await db.ranking_predictions.create_indexes(
        [
            IndexModel(
                [
                    ("model_version", ASCENDING),
                    ("predicted_at", DESCENDING),
                ]
            ),
            IndexModel([("user_id", ASCENDING), ("predicted_at", DESCENDING)]),
        ]
    )
    await db.evaluation_runs.create_indexes(
        [
            IndexModel([("model_version", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("passed", ASCENDING), ("created_at", DESCENDING)]),
        ]
    )
    await db.provider_runs.create_indexes(
        [
            IndexModel(
                [
                    ("provider", ASCENDING),
                    ("model_id", ASCENDING),
                    ("stage", ASCENDING),
                    ("created_at", DESCENDING),
                ]
            ),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
        ]
    )


async def _v4_provider_regions(db: Any) -> None:
    await db.provider_runs.create_index(
        [
            ("provider", ASCENDING),
            ("model_id", ASCENDING),
            ("stage", ASCENDING),
            ("region", ASCENDING),
            ("created_at", DESCENDING),
        ],
        name="provider_model_stage_region_created",
    )


async def _v5_creator_tracking(db: Any) -> None:
    existing = set(await db.list_collection_names())
    for name in (
        "tracked_creators",
        "creator_snapshots",
        "creator_content",
        "creator_profiles",
        "user_tracked_creators",
        "creator_tracking_config",
    ):
        if name not in existing:
            await db.create_collection(name)
    await db.tracked_creators.create_index([("username", ASCENDING)], unique=True)
    await db.creator_snapshots.create_indexes(
        [
            IndexModel(
                [("creator_id", ASCENDING), ("day", ASCENDING)],
                unique=True,
                name="creator_snapshot_day",
            ),
            IndexModel([("creator_id", ASCENDING), ("captured_at", DESCENDING)]),
        ]
    )
    await db.creator_content.create_indexes(
        [
            IndexModel(
                [("creator_id", ASCENDING), ("shortcode", ASCENDING)], unique=True
            ),
            IndexModel([("creator_id", ASCENDING), ("taken_at", DESCENDING)]),
        ]
    )
    await db.creator_profiles.create_index([("creator_id", ASCENDING)], unique=True)
    await db.user_tracked_creators.create_indexes(
        [
            IndexModel(
                [("user_id", ASCENDING), ("creator_id", ASCENDING)], unique=True
            ),
            IndexModel([("user_id", ASCENDING), ("added_at", DESCENDING)]),
        ]
    )
    await db.creator_tracking_config.create_index([("key", ASCENDING)], unique=True)


MIGRATIONS: tuple[tuple[int, Migration], ...] = (
    (1, _v1_content_intelligence),
    (2, _v2_topic_signals),
    (3, _v3_evaluation_and_erasure),
    (4, _v4_provider_regions),
    (5, _v5_creator_tracking),
)


async def run_migrations(db: Any) -> None:
    existing = set(await db.list_collection_names())
    if "schema_migrations" not in existing:
        await db.create_collection("schema_migrations")
    await db.schema_migrations.create_index([("version", ASCENDING)], unique=True)
    for version, migration in MIGRATIONS:
        if await db.schema_migrations.find_one({"version": version}):
            continue
        await migration(db)
        await db.schema_migrations.update_one(
            {"version": version},
            {"$setOnInsert": {"version": version}},
            upsert=True,
        )
