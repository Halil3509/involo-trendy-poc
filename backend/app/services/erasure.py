"""Retry-safe erasure of data derived from an Instagram connection."""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from qdrant_client import models

from app.core.config import Settings


class InstagramErasureError(RuntimeError):
    pass


class InstagramErasureService:
    """Erase Instagram-derived data while preserving the Involo auth account."""

    _USER_COLLECTIONS = (
        "instagram_connections",
        "user_profiles",
        "user_content",
        "recommendations",
        "user_preferences",
        "audience_snapshots",
        "recommendation_events",
        "recommendation_post_links",
        "recommendation_experiments",
        "content_metric_snapshots",
        "provider_runs",
        "job_runs",
        "ranking_predictions",
    )

    def __init__(
        self, db: Any, qdrant: Any, media: Any | None, settings: Settings
    ) -> None:
        self.db = db
        self.qdrant = qdrant
        self.media = media
        self.settings = settings

    async def erase(self, user_id: ObjectId) -> dict[str, int]:
        try:
            assets = await self._media_assets(user_id)
            media = self.media
            if assets and media is None:
                raise RuntimeError("media S3 is not configured")
            if assets and media is not None:
                await media.delete_assets(assets)
            await self._delete_vectors(str(user_id))
            for collection_name in self._USER_COLLECTIONS:
                collection = getattr(self.db, collection_name)
                await collection.delete_many(
                    {"user_id": {"$in": [user_id, str(user_id)]}}
                )
        except Exception as exc:  # noqa: BLE001
            raise InstagramErasureError(
                "Instagram-derived erasure is temporarily unavailable"
            ) from exc
        return {"s3_objects": len(assets), "mongo_collections": len(self._USER_COLLECTIONS)}

    async def _media_assets(self, user_id: ObjectId) -> list[dict[str, str]]:
        assets: dict[tuple[str, str], dict[str, str]] = {}

        def add(item: Any) -> None:
            if not isinstance(item, dict):
                return
            bucket, key = item.get("bucket"), item.get("key")
            if bucket and key:
                assets[(str(bucket), str(key))] = {
                    "bucket": str(bucket),
                    "key": str(key),
                }
            add(item.get("embedding_asset"))

        async for document in self.db.user_content.find({"user_id": user_id}):
            add(document.get("media_asset"))
            for field in ("keyframes", "video_segments", "segments"):
                for item in document.get(field) or []:
                    add(item)
        return [assets[key] for key in sorted(assets)]

    async def _delete_vectors(self, user_id: str) -> None:
        selector = models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id", match=models.MatchValue(value=user_id)
                    )
                ]
            )
        )
        for collection in (
            self.settings.qdrant_user_collection,
            self.settings.qdrant_user_content_collection,
            self.settings.qdrant_segment_collection,
        ):
            await self.qdrant.delete(
                collection_name=collection,
                points_selector=selector,
                wait=True,
            )
