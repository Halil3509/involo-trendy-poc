from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.database import AsyncDatabase
from qdrant_client import AsyncQdrantClient, models
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.rate_limit import build_graph_rate_limiter
from app.infrastructure.migrations import run_migrations
from app.infrastructure.provider_readiness import ProviderReadinessProber, probe_result


class Resources:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mongo_client: AsyncMongoClient[dict[str, Any]] | None = None
        self.db: AsyncDatabase[dict[str, Any]] | None = None
        self.redis: Redis | None = None
        self.qdrant: AsyncQdrantClient | None = None
        self.provider_readiness = ProviderReadinessProber(settings)

    async def connect(self, *, init_qdrant: bool = True) -> None:
        self.mongo_client = AsyncMongoClient(
            self.settings.mongo_uri,
            tz_aware=True,
            maxIdleTimeMS=1200000,
            heartbeatFrequencyMS=10000,
            connectTimeoutMS=30000,
            serverSelectionTimeoutMS=30000,
            retryWrites=True,
        )
        self.db = self.mongo_client[self.settings.mongo_database]
        self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        self.provider_readiness.graph_limiter = build_graph_rate_limiter(
            self.redis, self.settings
        )
        if init_qdrant:
            self.qdrant = AsyncQdrantClient(
                url=self.settings.qdrant_url,
                api_key=(
                    self.settings.qdrant_api_key.get_secret_value()
                    if self.settings.qdrant_api_key
                    else None
                ),
            )
            await self._init_mongo()
            await self._init_qdrant()
        else:
            await self._init_mongo()

    async def _drop_conflicting_ttl_index(
        self, collection_name: str, index_name: str, expire_after_seconds: int
    ) -> None:
        assert self.db is not None
        collection = self.db[collection_name]
        indexes = await collection.list_indexes()
        async for idx in indexes:
            if (
                idx.get("name") == index_name
                and idx.get("expireAfterSeconds") != expire_after_seconds
            ):
                await collection.drop_index(index_name)
                break

    async def _init_mongo(self) -> None:
        assert self.db is not None
        existing = set(await self.db.list_collection_names())
        for name in (
            "users",
            "auth_sessions",
            "scraper_config",
            "trend_content",
            "job_runs",
            "instagram_connections",
            "user_content",
            "user_profiles",
            "profiling_config",
            "recommendations",
            "content_metric_snapshots",
            "audience_snapshots",
            "user_preferences",
            "recommendation_events",
            "recommendation_post_links",
            "recommendation_experiments",
            "provider_runs",
            "topic_signal_snapshots",
            "topic_signal_aggregates",
            "ranking_predictions",
            "evaluation_runs",
            "brand_analysis_posts",
            "brand_analysis_reports",
            "meta_access_tokens",
            "schema_migrations",
        ):
            if name not in existing:
                await self.db.create_collection(name)
        await self.db.users.create_indexes(
            [
                IndexModel([("email", ASCENDING)], unique=True),
                IndexModel([("role", ASCENDING)]),
            ]
        )
        await self.db.auth_sessions.create_indexes(
            [
                IndexModel([("token_hash", ASCENDING)], unique=True),
                IndexModel([("user_id", ASCENDING), ("revoked_at", ASCENDING)]),
                IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0),
            ]
        )
        await self.db.scraper_config.create_index([("key", ASCENDING)], unique=True)
        await self.db.trend_content.create_indexes(
            [
                IndexModel([("canonical_url", ASCENDING)], unique=True),
                IndexModel([("shortcode", ASCENDING)], sparse=True),
                IndexModel([("last_seen_at", DESCENDING)]),
            ]
        )
        await self._drop_conflicting_ttl_index(
            "job_runs", "created_at_-1", 30 * 24 * 60 * 60
        )
        await self.db.job_runs.create_indexes(
            [
                IndexModel([("task_id", ASCENDING)], unique=True),
                IndexModel(
                    [("created_at", DESCENDING)],
                    expireAfterSeconds=30 * 24 * 60 * 60,
                ),
                IndexModel([("kind", ASCENDING), ("created_at", DESCENDING)]),
            ]
        )
        await self.db.instagram_connections.create_indexes(
            [
                IndexModel([("user_id", ASCENDING)], unique=True),
                IndexModel([("instagram_user_id", ASCENDING)], unique=True, sparse=True),
            ]
        )
        await self.db.user_content.create_indexes(
            [
                IndexModel([("user_id", ASCENDING), ("media_id", ASCENDING)], unique=True),
                IndexModel([("user_id", ASCENDING), ("taken_at", DESCENDING)]),
            ]
        )
        await self.db.user_profiles.create_index([("user_id", ASCENDING)], unique=True)
        await self.db.profiling_config.create_index([("key", ASCENDING)], unique=True)
        await self.db.recommendations.create_index(
            [("user_id", ASCENDING), ("created_at", DESCENDING)]
        )
        await self.db.brand_analysis_posts.create_indexes(
            [
                IndexModel([("job_id", ASCENDING), ("post_id", ASCENDING)], unique=True),
                IndexModel([("job_id", ASCENDING), ("taken_at", DESCENDING)]),
                IndexModel([("shortcode", ASCENDING)], sparse=True),
            ]
        )
        await self.db.brand_analysis_reports.create_index(
            [("job_id", ASCENDING)], unique=True
        )
        await run_migrations(self.db)

    async def _init_qdrant(self) -> None:
        assert self.qdrant is not None
        if not await self.qdrant.collection_exists(self.settings.qdrant_trend_collection):
            await self.qdrant.create_collection(
                collection_name=self.settings.qdrant_trend_collection,
                vectors_config={
                    "text": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                    "audio_video": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                    "fused": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                },
            )
        if not await self.qdrant.collection_exists(self.settings.qdrant_user_collection):
            await self.qdrant.create_collection(
                collection_name=self.settings.qdrant_user_collection,
                vectors_config={
                    "profile": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                },
            )
        if not await self.qdrant.collection_exists(self.settings.qdrant_user_content_collection):
            await self.qdrant.create_collection(
                collection_name=self.settings.qdrant_user_content_collection,
                vectors_config={
                    "text": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                    "audio_video": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                    "fused": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                },
            )
        if not await self.qdrant.collection_exists(
            self.settings.qdrant_creator_content_collection
        ):
            await self.qdrant.create_collection(
                collection_name=self.settings.qdrant_creator_content_collection,
                vectors_config={
                    "text": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                    "audio_video": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                    "fused": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    ),
                },
            )
        if not await self.qdrant.collection_exists(self.settings.qdrant_segment_collection):
            await self.qdrant.create_collection(
                collection_name=self.settings.qdrant_segment_collection,
                vectors_config={
                    "segment": models.VectorParams(
                        size=self.settings.vector_size, distance=models.Distance.COSINE
                    )
                },
            )
        for collection in (
            self.settings.qdrant_trend_collection,
            self.settings.qdrant_user_content_collection,
            self.settings.qdrant_segment_collection,
            self.settings.qdrant_creator_content_collection,
        ):
            if not await self.qdrant.collection_exists(collection):
                continue
            collection_info = await self.qdrant.get_collection(collection)
            existing_schema = getattr(collection_info, "payload_schema", None) or {}
            for field, field_schema in (
                ("language", models.PayloadSchemaType.KEYWORD),
                ("market", models.PayloadSchemaType.KEYWORD),
                ("lifecycle", models.PayloadSchemaType.KEYWORD),
                ("schema_version", models.PayloadSchemaType.KEYWORD),
                ("content_type", models.PayloadSchemaType.KEYWORD),
                ("user_id", models.PayloadSchemaType.KEYWORD),
                ("active", models.PayloadSchemaType.BOOL),
            ):
                existing = existing_schema.get(field)
                if existing is not None and getattr(existing, "data_type", None) == field_schema:
                    continue
                try:
                    await self.qdrant.create_payload_index(
                        collection_name=collection,
                        field_name=field,
                        field_schema=field_schema,
                    )
                except Exception:
                    # Existing indexes and old Qdrant versions are harmless here.
                    pass

    async def ready(self) -> dict[str, dict[str, object]]:
        checks = {
            "mongo": probe_result(False, "unavailable"),
            "redis": probe_result(False, "unavailable"),
            "qdrant": probe_result(False, "unavailable"),
        }
        try:
            assert self.db is not None
            await self.db.command("ping")
            checks["mongo"] = probe_result(True, "ok")
        except Exception:
            pass
        try:
            assert self.redis is not None
            redis_ok = bool(await self.redis.ping())
            checks["redis"] = probe_result(
                redis_ok, "ok" if redis_ok else "unavailable"
            )
        except Exception:
            pass
        try:
            assert self.qdrant is not None
            await self.qdrant.get_collections()
            checks["qdrant"] = probe_result(True, "ok")
        except Exception:
            pass
        checks.update(await self.provider_readiness.checks(self.db))
        return checks

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()
        if self.qdrant:
            await self.qdrant.close()
        if self.mongo_client:
            await self.mongo_client.close()


def utcnow() -> datetime:
    return datetime.now(UTC)
