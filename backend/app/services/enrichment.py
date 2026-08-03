"""Phase 3 enrichment: metadata -> viral score -> threshold -> transcript.

Takes discovered ``trend_content`` documents (from Phase 2) and enriches them
with metadata, a normalized viral score, and (when the content passes the viral
threshold and a cost pre-filter) an AWS transcript. The raw metric components
are stored alongside the score, and the combined ``caption + transcript`` text
is prepared for the Phase 4 embedding step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.core.errors import is_expired_media_error
from app.infrastructure.resources import utcnow
from app.providers.media import MediaProvider
from app.providers.metadata import MetadataProvider, _is_incomplete
from app.providers.scraper import EmitFn, NeedsInterventionError, noop_emit
from app.providers.transcription import TranscriptionProvider
from app.schemas.trends import ContentMetadata
from app.services.provider_runs import record_provider_call
from app.services.scoring import ScoreComponents, ScoreWeights, compute_viral_score

PENDING_STATES = [None, "discovered", "failed"]


@dataclass(frozen=True)
class EnrichmentOutcome:
    viral_score: float
    above_threshold: bool
    transcribed: bool


class EnrichmentService:
    def __init__(
        self,
        db: AsyncDatabase[dict[str, Any]],
        metadata_provider: MetadataProvider,
        transcription_provider: TranscriptionProvider,
        *,
        weights: ScoreWeights,
        viral_threshold: float,
        transcribe_min_views: int,
        media_provider: MediaProvider | None = None,
        emit: EmitFn = noop_emit,
        max_zero_score_retries: int = 3,
        zero_score_cooldown_minutes: int = 0,
    ) -> None:
        self.db = db
        self.metadata_provider = metadata_provider
        self.transcription_provider = transcription_provider
        self.media_provider = media_provider
        self.weights = weights
        self.viral_threshold = viral_threshold
        self.transcribe_min_views = transcribe_min_views
        self.emit = emit
        self.max_zero_score_retries = max_zero_score_retries
        self.zero_score_cooldown_minutes = zero_score_cooldown_minutes

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

    def _pending_query(self) -> dict[str, Any]:
        base_query = {"processing_status": {"$in": PENDING_STATES}}
        if self.max_zero_score_retries <= 0:
            return base_query

        now = utcnow()
        zero_score_conditions: list[dict[str, Any]] = [
            {"processing_status": {"$nin": ["media_expired", "needs_intervention"]}},
            {"$or": [{"viral_score": 0.0}, {"viral_score": {"$exists": False}}]},
            {
                "$or": [
                    {"zero_score_retry_count": {"$exists": False}},
                    {"zero_score_retry_count": {"$lt": self.max_zero_score_retries}},
                ]
            },
        ]
        if self.zero_score_cooldown_minutes > 0:
            cutoff = now - timedelta(minutes=self.zero_score_cooldown_minutes)
            zero_score_conditions.append(
                {
                    "$or": [
                        {"enriched_at": {"$exists": False}},
                        {"enriched_at": {"$lte": cutoff}},
                    ]
                }
            )
        return {"$or": [base_query, {"$and": zero_score_conditions}]}

    async def run(self, limit: int | None = None) -> dict[str, int]:
        query = self._pending_query()
        total = await self.db.trend_content.count_documents(query)
        if limit is not None:
            total = min(total, limit)
        await self.emit(f"Starting enrichment: {total} items pending.")
        counters = {
            "processed": 0,
            "scored": 0,
            "enriched": 0,
            "transcribed": 0,
            "skipped_threshold": 0,
            "failed": 0,
            "media_expired": 0,
            "needs_intervention": 0,
        }
        cursor = self.db.trend_content.find(query)
        if limit is not None:
            cursor = cursor.limit(limit)
        index = 0
        async for document in cursor:
            index += 1
            counters["processed"] += 1
            shortcode = document["shortcode"]
            await self._maybe_emit(index, total, f"Enriching item {index}/{total}: {shortcode}")
            try:
                passed = await self.enrich_document(document, index=index, total=total)
            except NeedsInterventionError:
                counters["needs_intervention"] += 1
                await self._mark_failed(
                    document,
                    "needs_intervention",
                    "needs_intervention",
                    error_type="NeedsInterventionError",
                    provider="enrichment",
                )
                await self._maybe_emit(
                    index,
                    total,
                    f"Item {index}/{total}: {shortcode} needs intervention",
                    level="warning",
                )
                raise
            except Exception as exc:  # noqa: BLE001 - recorded per-document
                if is_expired_media_error(exc):
                    counters["media_expired"] += 1
                    await self._mark_failed(
                        document,
                        "media_expired",
                        str(exc),
                        error_type=type(exc).__name__,
                        provider="media",
                    )
                else:
                    counters["failed"] += 1
                    provider = self._provider_from_exception(exc)
                    await self._mark_failed(
                        document,
                        "failed",
                        str(exc),
                        error_type=type(exc).__name__,
                        provider=provider,
                    )
                await self._maybe_emit(
                    index,
                    total,
                    f"Item {index}/{total}: {shortcode} failed: {str(exc)}",
                    level="error",
                )
                continue
            counters["scored"] += 1
            if passed.above_threshold:
                counters["enriched"] += 1
            if passed.transcribed:
                counters["transcribed"] += 1
            if not passed.above_threshold:
                counters["skipped_threshold"] += 1
            status = "enriched" if passed.above_threshold else "stored"
            await self._maybe_emit(
                index,
                total,
                f"Item {index}/{total}: {shortcode} -> {status} (score={passed.viral_score:.2f})",
            )
        return counters

    async def enrich_document(
        self,
        document: dict[str, Any],
        *,
        index: int | None = None,
        total: int | None = None,
    ) -> EnrichmentOutcome:
        shortcode = document["shortcode"]
        now = utcnow()
        await self._maybe_emit(index, total, f"Resolving metadata for {shortcode}...")
        discovered_metadata = dict(document.get("metadata") or {})
        for key in (
            "media_id",
            "owner_username",
            "video_url",
            "thumbnail_url",
            "taken_at",
            "like_count",
            "comment_count",
            "view_count",
            "share_count",
        ):
            if document.get(key) is not None and discovered_metadata.get(key) is None:
                discovered_metadata[key] = document[key]
        if document.get("caption_text") and not discovered_metadata.get("caption_text"):
            discovered_metadata["caption_text"] = document["caption_text"]
        # Ensure source provenance is in the metadata dict for provider decisions.
        if document.get("source") and not discovered_metadata.get("source"):
            discovered_metadata["source"] = document["source"]
        if document.get("media_type") and not discovered_metadata.get("media_type"):
            discovered_metadata["media_type"] = document["media_type"]
        retry_count = int(document.get("zero_score_retry_count") or 0)
        context = {
            "zero_score_retry": retry_count,
            "source": discovered_metadata.get("source"),
        }
        metadata = await self._resolve_metadata(
            shortcode, discovered_metadata, context=context
        )
        duration_label = (
            f"{metadata.video_duration}s" if metadata.video_duration is not None else "None"
        )
        await self._maybe_emit(
            index,
            total,
            (
                f"Metadata resolved for {shortcode}: "
                f"views={metadata.view_count}, likes={metadata.like_count}, "
                f"duration={duration_label}"
            ),
        )
        canonical_fields: dict[str, Any] = {}
        if document.get("caption_text"):
            canonical_fields["caption_text"] = document["caption_text"]
        if document.get("video_url"):
            canonical_fields["video_url"] = document["video_url"]
        if canonical_fields:
            metadata = metadata.model_copy(update=canonical_fields)
        score, components = compute_viral_score(metadata, now, self.weights)
        above_threshold = score >= self.viral_threshold
        should_transcribe = above_threshold and metadata.view_count >= self.transcribe_min_views
        await self._maybe_emit(
            index,
            total,
            (
                f"{shortcode}: viral_score={score:.2f}, "
                f"threshold={self.viral_threshold}, above_threshold={above_threshold}"
            ),
        )

        media_asset: dict[str, Any] | None = None
        transcribe_url = metadata.video_url
        if above_threshold and self.media_provider is not None and metadata.video_url:
            stored_media = await self.media_provider.ingest(
                str(metadata.video_url), str(document["_id"])
            )
            media_asset = dict(stored_media.__dict__)
            transcribe_url = self.media_provider.public_url(stored_media)

        transcript_text = ""
        language: str | None = None
        if should_transcribe:
            await self._maybe_emit(index, total, f"Transcribing {shortcode}...")
            transcript = await record_provider_call(
                self.db,
                provider="aws_transcribe",
                model_id="aws-transcribe-identify-language",
                stage="transcription",
                operation=lambda: self.transcription_provider.transcribe(
                    shortcode, transcribe_url
                ),
                subject_id=str(document["_id"]),
                media_seconds=metadata.video_duration,
                region=self.transcription_provider.settings.aws_region
                if hasattr(self.transcription_provider, "settings")
                else None,
            )
            transcript_text = transcript.text
            language = transcript.language
            await self._maybe_emit(
                index,
                total,
                f"Transcription complete for {shortcode} (language={language or 'unknown'})",
            )

        combined_text = "\n\n".join(
            part for part in (metadata.caption_text, transcript_text) if part
        ).strip()
        await self._store(
            document,
            metadata,
            score=score,
            components=components,
            transcript_text=transcript_text,
            language=language,
            combined_text=combined_text,
            above_threshold=above_threshold,
            media_asset=media_asset,
            now=now,
        )
        return EnrichmentOutcome(
            viral_score=score,
            above_threshold=above_threshold,
            transcribed=bool(transcript_text),
        )

    def _metadata_from_discovered(
        self, shortcode: str, discovered_metadata: dict[str, Any]
    ) -> ContentMetadata:
        taken_at_raw = discovered_metadata.get("taken_at")
        parsed_taken_at: datetime | None = None
        if isinstance(taken_at_raw, str) and taken_at_raw:
            parsed_taken_at = datetime.fromisoformat(taken_at_raw.replace("Z", "+00:00"))
        elif isinstance(taken_at_raw, int | float):
            parsed_taken_at = datetime.fromtimestamp(taken_at_raw, tz=UTC)
        return ContentMetadata(
            shortcode=shortcode,
            media_id=discovered_metadata.get("source_id") or discovered_metadata.get("media_id"),
            owner_username=discovered_metadata.get("owner_username"),
            owner_follower_count=int(discovered_metadata.get("owner_follower_count", 0) or 0),
            like_count=int(discovered_metadata.get("like_count", 0) or 0),
            comment_count=int(discovered_metadata.get("comment_count", 0) or 0),
            view_count=int(discovered_metadata.get("view_count", 0) or 0),
            share_count=int(discovered_metadata.get("share_count", 0) or 0),
            video_duration=(
                float(discovered_metadata["video_duration"])
                if discovered_metadata.get("video_duration") is not None
                else None
            ),
            caption_text=str(
                discovered_metadata.get("caption_text", discovered_metadata.get("caption", ""))
            ),
            taken_at=parsed_taken_at,
            video_url=discovered_metadata.get("video_url"),
            media_type=discovered_metadata.get("media_type"),
        )

    async def _resolve_metadata(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        discovered = self._metadata_from_discovered(shortcode, discovered_metadata)
        retry = (context or {}).get("zero_score_retry", 0)
        if not _is_incomplete(discovered) and not retry:
            return discovered
        fetched = await self.metadata_provider.fetch(
            shortcode, discovered_metadata, context=context
        )
        values = fetched.model_dump()
        for key in (
            "media_id",
            "owner_username",
            "video_duration",
            "taken_at",
            "media_type",
        ):
            if values.get(key) is None and discovered_metadata.get(key) is not None:
                values[key] = discovered_metadata[key]
        for key in (
            "owner_follower_count",
            "like_count",
            "comment_count",
            "view_count",
            "share_count",
        ):
            if not values.get(key) and discovered_metadata.get(key) is not None:
                values[key] = discovered_metadata[key]
        return ContentMetadata.model_validate(values)

    async def _store(
        self,
        document: dict[str, Any],
        metadata: ContentMetadata,
        *,
        score: float,
        components: ScoreComponents,
        transcript_text: str,
        language: str | None,
        combined_text: str,
        above_threshold: bool,
        media_asset: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        update: dict[str, Any] = {
            "owner_username": metadata.owner_username,
            "media_id": metadata.media_id,
            "caption_text": metadata.caption_text,
            "transcript": transcript_text,
            "language": language,
            "duration_seconds": metadata.video_duration,
            "taken_at": metadata.taken_at,
            "video_url": metadata.video_url,
            "combined_text": combined_text,
            "metrics": {
                "like_count": metadata.like_count,
                "comment_count": metadata.comment_count,
                "view_count": metadata.view_count,
                "share_count": metadata.share_count,
                "follower_count": metadata.owner_follower_count,
            },
            "viral_score": score,
            "score_components": {
                "distribution_score": components.distribution_score,
                "engagement_score": components.engagement_score,
                "velocity_score": components.velocity_score,
                "weighted_engagement_rate": components.weighted_engagement_rate,
                "raw_score": components.raw_score,
            },
            "processing_status": "enriched" if above_threshold else "stored",
            "enriched_at": now,
            "enrichment_error": None,
        }
        retry_count = int(document.get("zero_score_retry_count") or 0)
        if score == 0.0:
            update["zero_score_retry_count"] = (
                retry_count + 1 if retry_count < self.max_zero_score_retries else retry_count
            )
        else:
            update["zero_score_retry_count"] = 0
        if media_asset is not None:
            update["media_asset"] = media_asset
        await self.db.trend_content.update_one({"_id": document["_id"]}, {"$set": update})

    def _provider_from_exception(self, exc: BaseException) -> str:
        name = type(exc).__name__.lower()
        if "transcribe" in name or "transcription" in name:
            return "aws_transcribe"
        return "enrichment"

    async def _mark_failed(
        self,
        document: dict[str, Any],
        status: str,
        reason: str,
        *,
        error_type: str | None = None,
        provider: str | None = None,
    ) -> None:
        await self.db.trend_content.update_one(
            {"_id": document["_id"]},
            {
                "$set": {
                    "processing_status": status,
                    "enrichment_error": reason,
                    "enrichment_error_type": error_type,
                    "enrichment_provider": provider,
                }
            },
        )
