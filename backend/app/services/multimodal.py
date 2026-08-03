"""Idempotent S3 → keyframes → Nova vision/embedding processing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import numpy as np
from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings
from app.core.errors import is_expired_media_error
from app.infrastructure.resources import utcnow
from app.providers.embedding import EmbeddingProvider
from app.providers.media import MediaProvider, StoredMedia
from app.providers.scraper import EmitFn, noop_emit
from app.providers.vision import VisionProvider
from app.services.provider_runs import record_provider_call

_SEGMENT_NAMESPACE = uuid.UUID("82c922c2-cc11-4eed-94f0-94cf5bb6f983")


@dataclass(frozen=True)
class MultimodalResult:
    point_id: str
    text_vector: list[float]
    media_vector: list[float]
    fused_vector: list[float]
    media_asset: dict[str, Any]
    keyframes: list[dict[str, Any]]
    visual_analysis: dict[str, Any]
    video_segments: list[dict[str, Any]] = field(default_factory=list)
    processing_regions: dict[str, str] = field(default_factory=dict)


def fuse_vectors(
    text: list[float] | None,
    media: list[float] | None,
    *,
    text_weight: float,
    media_weight: float,
) -> list[float]:
    candidates = [(text, text_weight), (media, media_weight)]
    available = [
        (vector, weight)
        for vector, weight in candidates
        if vector is not None and weight > 0
    ]
    if not available:
        raise ValueError("at least one modality vector is required")
    total = sum(weight for _, weight in available)
    first_vector, first_weight = available[0]
    fused = np.asarray(first_vector, dtype=float) * (first_weight / total)
    for vector, weight in available[1:]:
        fused += np.asarray(vector, dtype=float) * (weight / total)
    norm = float(np.linalg.norm(fused))
    return [float(value) for value in (fused / norm if norm else fused)]


def pool_normalized_vectors(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("at least one segment vector is required")
    normalized: list[Any] = []
    for values in vectors:
        vector = np.asarray(values, dtype=float)
        norm = float(np.linalg.norm(vector))
        normalized.append(vector / norm if norm else vector)
    pooled = np.mean(np.stack(normalized), axis=0)
    pooled_norm = float(np.linalg.norm(pooled))
    return [float(value) for value in (pooled / pooled_norm if pooled_norm else pooled)]


def deterministic_media_point_id(
    *,
    collection: str,
    content_type: str,
    content_id: str,
    owner_id: str,
    asset_type: str,
    position: str,
    schema_version: str,
) -> str:
    identity = "|".join(
        (
            collection,
            content_type,
            owner_id,
            content_id,
            asset_type,
            position,
            schema_version,
        )
    )
    return str(uuid.uuid5(_SEGMENT_NAMESPACE, identity))


class MultimodalService:
    def __init__(
        self,
        db: Any,
        qdrant: AsyncQdrantClient,
        settings: Settings,
        media: MediaProvider,
        vision: VisionProvider,
        embedding: EmbeddingProvider,
        emit: EmitFn = noop_emit,
    ) -> None:
        self.db = db
        self.qdrant = qdrant
        self.settings = settings
        self.media = media
        self.vision = vision
        self.embedding = embedding
        self.emit = emit

    def _should_log(self, index: int | None, total: int | None) -> bool:
        if index is None or total is None:
            return True
        if total <= 50:
            return True
        return index == 1 or index % 10 == 0 or index == total

    async def _maybe_emit(
        self,
        index: int | None,
        total: int | None,
        message: str,
        *,
        level: str = "info",
        **kwargs: Any,
    ) -> None:
        if self._should_log(index, total):
            await self.emit(message, level=level, **kwargs)

    async def process_trend(
        self,
        document: dict[str, Any],
        *,
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        source_url = document.get("video_url")
        if not source_url:
            raise ValueError("content has no media URL")
        content_id = str(document["_id"])
        shortcode = document.get("shortcode")
        identifier = shortcode or content_id
        existing_media = self._stored_media_from_asset(document.get("media_asset"))
        await self._maybe_emit(index, total, f"Processing media for {identifier}...")
        result = await self.process_asset(
            source_url=str(source_url),
            content_id=content_id,
            caption=str(document.get("caption_text", "")),
            combined_text=str(document.get("combined_text", "")),
            collection=self.settings.qdrant_trend_collection,
            payload={
                "mongo_id": content_id,
                "shortcode": shortcode,
                "source": document.get("source"),
                "language": document.get("language"),
                "market": document.get("market"),
                "lifecycle": document.get("trend_signals", {}).get(
                    "lifecycle", "unknown"
                ),
                "viral_score": document.get("viral_score", 0),
                "content_type": "trend_content",
                "active": True,
            },
            shortcode=shortcode,
            existing_media=existing_media,
            index=index,
            total=total,
        )
        await self._maybe_emit(index, total, f"Media processing complete for {identifier}")
        await self.db.trend_content.update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "media_asset": result.media_asset,
                    "keyframes": result.keyframes,
                    "visual_analysis": result.visual_analysis,
                    "video_segments": result.video_segments,
                    "processing_regions": result.processing_regions,
                    "embedding_vector_id": result.point_id,
                    "embedding_schema_version": self.settings.vector_schema_version,
                    "processing_status": "embedded",
                    "embedded_at": utcnow(),
                    "processing_error": None,
                    "processing_error_stage": None,
                }
            },
        )

    @staticmethod
    def _stored_media_from_asset(asset: Any) -> StoredMedia | None:
        """Rehydrate a previously ingested S3 original from a Mongo document."""
        if not isinstance(asset, dict):
            return None
        try:
            return StoredMedia(
                bucket=str(asset["bucket"]),
                key=str(asset["key"]),
                uri=str(asset.get("uri") or f"s3://{asset['bucket']}/{asset['key']}"),
                content_type=str(asset.get("content_type") or "video/mp4"),
                sha256=str(asset.get("sha256") or ""),
                size_bytes=int(asset.get("size_bytes") or 0),
                region=str(asset.get("region") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def process_asset(
        self,
        *,
        source_url: str,
        content_id: str,
        caption: str,
        combined_text: str,
        collection: str,
        payload: dict[str, Any],
        shortcode: str | None = None,
        existing_media: StoredMedia | None = None,
        index: int | None = None,
        total: int | None = None,
    ) -> MultimodalResult:
        """
        Process a media asset for embedding and storage.

        Args:
            source_url: The URL of the media asset to process.
            content_id: The ID of the content to process.
            caption: The caption of the content.
            combined_text: The combined text of the content.
            collection: The Qdrant collection to use.
            payload: Additional payload to store with the content.
            shortcode: Optional shortcode for logging.
            index: Optional item index for progress logging.
            total: Optional total item count for progress logging.

        Returns:
            MultimodalResult: The result of the processing.
        """

        identifier = shortcode or payload.get("shortcode") or content_id[:8]
        if existing_media is not None:
            stored = existing_media
            await self._maybe_emit(
                index,
                total,
                f"Reusing stored media for {identifier} ({stored.key})",
            )
        else:
            await self._maybe_emit(index, total, f"Downloading media for {identifier}...")
            stored = await self.media.ingest(source_url, content_id)
            size_mb = stored.size_bytes / (1024 * 1024)
            await self._maybe_emit(
                index,
                total,
                f"Media downloaded and stored for {identifier} ({size_mb:.2f} MB)",
                data={"size_bytes": stored.size_bytes, "content_type": stored.content_type},
            )
        frames = await self.media.extract_keyframes(
            stored, content_id, self.settings.keyframe_offsets_seconds
        )
        video_segments = await self.media.segment_video(
            stored, content_id, self.settings.segment_seconds
        )
        message = (
            f"Extracted {len(frames)} keyframes and "
            f"{len(video_segments)} segments for {identifier}"
        )
        await self._maybe_emit(index, total, message)
        embedding_segments = [
            await self.media.prepare_embedding_media(segment.media)
            for segment in video_segments
        ]
        embedding_frames = [
            await self.media.prepare_embedding_media(frame.media) for frame in frames
        ]
        user_id = payload.get("user_id")
        analysis = await record_provider_call(
            self.db,
            provider="amazon_bedrock",
            model_id=self.settings.bedrock_vision_model_id,
            stage="vision",
            operation=partial(self.vision.analyze, stored, frames, caption=caption),
            user_id=user_id,
            subject_id=content_id,
            region=self.settings.bedrock_generation_region,
        )
        await self._maybe_emit(index, total, f"Vision analysis complete for {identifier}")
        text_input = "\n".join(
            (
                combined_text,
                analysis.opening_frame,
                " ".join(analysis.ocr_text),
                " ".join(analysis.visual_signature),
            )
        ).strip()
        text_vector = await record_provider_call(
            self.db,
            provider="amazon_bedrock",
            model_id=self.settings.bedrock_embedding_model_id,
            stage="embedding_text",
            operation=partial(self.embedding.embed, text_input),
            user_id=user_id,
            subject_id=content_id,
            region=self.settings.bedrock_embedding_region,
        )
        await self._maybe_emit(index, total, f"Text embedding complete for {identifier}")
        segment_vectors = []
        for segment, embedding_media in zip(
            video_segments, embedding_segments, strict=True
        ):
            segment_vectors.append(
                await record_provider_call(
                    self.db,
                    provider="amazon_bedrock",
                    model_id=self.settings.bedrock_embedding_model_id,
                    stage="embedding_video",
                    operation=partial(self.embedding.embed_media, embedding_media.uri),
                    user_id=user_id,
                    subject_id=content_id,
                    media_seconds=segment.end_seconds - segment.start_seconds,
                    region=self.settings.bedrock_embedding_region,
                )
            )
        await self._maybe_emit(
            index, total, f"Video segment embeddings complete for {identifier}"
        )
        media_vector = pool_normalized_vectors(segment_vectors)
        fused = fuse_vectors(
            text_vector,
            media_vector,
            text_weight=self.settings.vector_fusion_text_weight,
            media_weight=self.settings.vector_fusion_media_weight,
        )
        payload = {
            **payload,
            "schema_version": self.settings.vector_schema_version,
            "embedding_region": self.settings.bedrock_embedding_region,
            "generation_region": self.settings.bedrock_generation_region,
        }
        content_type = str(payload.get("content_type", "unknown"))
        owner_id = str(payload.get("user_id") or payload.get("source") or "global")
        point_id = deterministic_media_point_id(
            collection=collection,
            content_type=content_type,
            content_id=content_id,
            owner_id=owner_id,
            asset_type="content",
            position="0",
            schema_version=self.settings.vector_schema_version,
        )
        await self.qdrant.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={
                        "text": text_vector,
                        "audio_video": media_vector,
                        "fused": fused,
                    },
                    payload=payload,
                )
            ],
        )
        await self._maybe_emit(index, total, f"Qdrant upsert complete for {identifier}")
        for index, (segment, embedding_media, vector) in enumerate(
            zip(video_segments, embedding_segments, segment_vectors, strict=True)
        ):
            segment_id = deterministic_media_point_id(
                collection=collection,
                content_type=content_type,
                content_id=content_id,
                owner_id=owner_id,
                asset_type="video_segment",
                position=str(index),
                schema_version=self.settings.vector_schema_version,
            )
            await self.qdrant.upsert(
                collection_name=self.settings.qdrant_segment_collection,
                points=[
                    models.PointStruct(
                        id=segment_id,
                        vector={"segment": vector},
                        payload={
                            **payload,
                            "content_point_id": point_id,
                            "type": "video_segment",
                            "segment_index": index,
                            "start_seconds": segment.start_seconds,
                            "end_seconds": segment.end_seconds,
                            "timestamp": segment.start_seconds,
                            "timestamp_seconds": segment.start_seconds,
                            "s3_uri": embedding_media.uri,
                        },
                    )
                ],
            )
        for index, (frame, embedding_media) in enumerate(
            zip(frames, embedding_frames, strict=True)
        ):
            vector = await record_provider_call(
                self.db,
                provider="amazon_bedrock",
                model_id=self.settings.bedrock_embedding_model_id,
                stage="embedding_image",
                operation=partial(self.embedding.embed_media, embedding_media.uri),
                user_id=user_id,
                subject_id=content_id,
                region=self.settings.bedrock_embedding_region,
            )
            segment_id = deterministic_media_point_id(
                collection=collection,
                content_type=content_type,
                content_id=content_id,
                owner_id=owner_id,
                asset_type="keyframe",
                position=f"{index}:{frame.offset_seconds}",
                schema_version=self.settings.vector_schema_version,
            )
            await self.qdrant.upsert(
                collection_name=self.settings.qdrant_segment_collection,
                points=[
                    models.PointStruct(
                        id=segment_id,
                        vector={"segment": vector},
                        payload={
                            **payload,
                            "content_point_id": point_id,
                            "type": "keyframe",
                            "frame_index": index,
                            "offset_seconds": frame.offset_seconds,
                            "timestamp": frame.offset_seconds,
                            "timestamp_seconds": frame.offset_seconds,
                            "s3_uri": embedding_media.uri,
                        },
                    )
                ],
            )
        await self._maybe_emit(
            index, total, f"Keyframe embeddings complete for {identifier}"
        )
        return MultimodalResult(
            point_id=point_id,
            text_vector=text_vector,
            media_vector=media_vector,
            fused_vector=fused,
            media_asset=stored.__dict__,
            keyframes=[
                {
                    "offset_seconds": frame.offset_seconds,
                    **frame.media.__dict__,
                    "embedding_asset": embedding_media.__dict__,
                }
                for frame, embedding_media in zip(
                    frames, embedding_frames, strict=True
                )
            ],
            visual_analysis=analysis.model_dump(),
            video_segments=[
                {
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                    **segment.media.__dict__,
                    "embedding_asset": embedding_media.__dict__,
                }
                for segment, embedding_media in zip(
                    video_segments, embedding_segments, strict=True
                )
            ],
            processing_regions={
                "vision": self.settings.bedrock_generation_region,
                "text_embedding": self.settings.bedrock_embedding_region,
                "media_embedding": self.settings.bedrock_embedding_region,
            },
        )

    async def backfill(self, limit: int | None = None) -> dict[str, int]:
        return await self._run_trends(
            {
                "video_url": {"$ne": None},
                "$or": [
                    {"embedding_schema_version": {"$ne": self.settings.vector_schema_version}},
                    {"visual_analysis": {"$exists": False}},
                    {"video_segments": {"$exists": False}},
                ],
            },
            limit,
        )

    async def run_eligible(self, limit: int | None = None) -> dict[str, int]:
        return await self._run_trends(
            {
                "processing_status": "enriched",
                "video_url": {"$ne": None},
            },
            limit,
        )

    async def _run_trends(
        self, query: dict[str, Any], limit: int | None
    ) -> dict[str, int]:
        counters = {"processed": 0, "embedded": 0, "failed": 0}
        total = await self.db.trend_content.count_documents(query)
        if limit is not None:
            total = min(total, limit)
        await self.emit(f"Starting embedding: {total} eligible items.")
        cursor = self.db.trend_content.find(query)
        if limit is not None:
            cursor = cursor.limit(limit)
        index = 0
        async for document in cursor:
            index += 1
            counters["processed"] += 1
            shortcode = document.get("shortcode") or str(document["_id"])
            await self._maybe_emit(
                index, total, f"Embedding item {index}/{total}: {shortcode}"
            )
            try:
                await self.process_trend(document, index=index, total=total)
                counters["embedded"] += 1
            except Exception as exc:  # noqa: BLE001
                counters["failed"] += 1
                await self._maybe_emit(
                    index,
                    total,
                    f"Item {index}/{total}: {shortcode} embedding failed: {str(exc)}",
                    level="error",
                )
                # A 403 from the signed CDN URL cannot recover by retrying; the
                # content must be re-scraped for a fresh URL. Mark it terminal so
                # it leaves the retry loop until the scraper refreshes it.
                failed_status = (
                    "media_expired" if is_expired_media_error(exc) else "enriched"
                )
                await self.db.trend_content.update_one(
                    {"_id": document["_id"]},
                    {
                        "$set": {
                            "processing_status": failed_status,
                            "processing_error_stage": "multimodal",
                            "processing_error": str(exc),
                            "embedded_at": None,
                            "embedding_vector_id": None,
                            "updated_at": utcnow(),
                        }
                    },
                )
        await self.emit(
            f"Embedding stage complete: {counters['embedded']} embedded, "
            f"{counters['failed']} failed."
        )
        return counters
