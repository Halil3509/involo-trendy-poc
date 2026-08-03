"""Threshold-free user content profiling pipeline."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import numpy as np
from qdrant_client import AsyncQdrantClient, models
from sklearn.cluster import KMeans

from app.core.config import Settings
from app.core.token_crypto import TokenCipher
from app.infrastructure.resources import utcnow
from app.providers.instagram_profile import (
    InstagramAccount,
    InstagramMedia,
    InstagramNeedsReauth,
    InstagramProfileProvider,
)
from app.providers.profile_summary import ProfileSummaryContext, ProfileSummaryProvider
from app.providers.transcription import TranscriptionProvider
from app.schemas.trends import ContentMetadata
from app.services.multimodal import MultimodalResult
from app.services.scoring import (
    ScoreWeights,
    compute_performance_residual,
    compute_viral_score,
)

logger = logging.getLogger(__name__)

_USER_AVERAGE_NAMESPACE = uuid.UUID("5787b647-83f9-4cd2-b748-58a80d6f6374")


class MultimodalProcessor(Protocol):
    async def process_asset(
        self,
        *,
        source_url: str,
        content_id: str,
        caption: str,
        combined_text: str,
        collection: str,
        payload: dict[str, Any],
    ) -> MultimodalResult: ...


@dataclass
class ProcessedMedia:
    item: InstagramMedia
    vector: list[float]
    raw_score: float
    combined_text: str
    point_id: str
    visual_analysis: dict[str, Any]
    residual: float = 50.0


def user_average_point_id(user_id: str) -> str:
    return str(uuid.uuid5(_USER_AVERAGE_NAMESPACE, user_id))


def average_and_dispersion(vectors: list[list[float]]) -> tuple[list[float], float]:
    if not vectors:
        raise ValueError("at least one vector is required")
    matrix = np.asarray(vectors, dtype=float)
    average = matrix.mean(axis=0)
    distances_squared = np.sum((matrix - average) ** 2, axis=1)
    dispersion = float(np.sqrt(np.mean(distances_squared)))
    return [float(value) for value in average], dispersion


class ProfilingService:
    def __init__(
        self,
        db: Any,
        qdrant: AsyncQdrantClient,
        settings: Settings,
        instagram: InstagramProfileProvider,
        transcription: TranscriptionProvider,
        multimodal: MultimodalProcessor,
        summary: ProfileSummaryProvider,
        cipher: TokenCipher,
    ) -> None:
        self.db = db
        self.qdrant = qdrant
        self.settings = settings
        self.instagram = instagram
        self.transcription = transcription
        self.multimodal = multimodal
        self.summary = summary
        self.cipher = cipher
        self.weights = ScoreWeights(
            distribution_weight=settings.score_distribution_weight,
            engagement_weight=settings.score_engagement_weight,
            velocity_weight=settings.score_velocity_weight,
            comment_weight=settings.score_comment_weight,
            share_weight=settings.score_share_weight,
            distribution_ratio_divisor=settings.score_distribution_ratio_divisor,
            engagement_rate_multiplier=settings.score_engagement_rate_multiplier,
            velocity_log_divisor=settings.score_velocity_log_divisor,
        )

    async def run(self, user_id: Any) -> dict[str, int]:
        connection = await self.db.instagram_connections.find_one({"user_id": user_id})
        if not connection:
            raise ValueError("Instagram account is not connected")
        await self.db.instagram_connections.update_one(
            {"user_id": user_id},
            {"$set": {"status": "profiling", "error": None, "profiling_started_at": utcnow()}},
        )
        try:
            token = await self._valid_token(connection)
            account = await self.instagram.fetch_account(token)
            media = await self.instagram.fetch_recent_media(token, account.id, now=utcnow())
            try:
                audience = await self.instagram.fetch_audience(token, account.id, now=utcnow())
                await self.db.audience_snapshots.insert_one(
                    {
                        "user_id": user_id,
                        "instagram_account_id": account.id,
                        **audience.__dict__,
                        "provider_version": self.settings.instagram_graph_api_version,
                    }
                )
            except Exception:  # noqa: BLE001 - media profiling can proceed without demographics
                pass
            result = await self._process(user_id, account, media)
            if not media:
                await self.db.user_profiles.delete_one({"user_id": user_id})
                await self.qdrant.delete(
                    collection_name=self.settings.qdrant_user_collection,
                    points_selector=models.PointIdsList(
                        points=[user_average_point_id(str(user_id))]
                    ),
                    wait=True,
                )
            await self.db.instagram_connections.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "status": "ready" if media else "connected",
                        "instagram_user_id": account.id,
                        "instagram_username": account.username,
                        "follower_count": account.follower_count,
                        "last_synced_at": utcnow(),
                        "error": None if media else "Son 90 günde işlenebilir içerik bulunamadı.",
                    }
                },
            )
            return result
        except InstagramNeedsReauth as exc:
            await self.db.instagram_connections.update_one(
                {"user_id": user_id},
                {"$set": {"status": "needs_reauth", "error": str(exc)[:500]}},
            )
            raise
        except Exception as exc:
            await self.db.instagram_connections.update_one(
                {"user_id": user_id},
                {"$set": {"status": "failed", "error": str(exc)[:500]}},
            )
            raise

    async def _valid_token(self, connection: dict[str, Any]) -> str:
        token = self.cipher.decrypt(connection["access_token_encrypted"])
        expires_at = connection["token_expires_at"]
        if expires_at > utcnow() + timedelta(days=1):
            return token
        refreshed = await self.instagram.refresh_token(token)
        await self.db.instagram_connections.update_one(
            {"_id": connection["_id"]},
            {
                "$set": {
                    "access_token_encrypted": self.cipher.encrypt(refreshed.access_token),
                    "token_expires_at": refreshed.expires_at,
                    "token_refreshed_at": utcnow(),
                }
            },
        )
        return refreshed.access_token

    async def _process(
        self, user_id: Any, account: InstagramAccount, media: list[InstagramMedia]
    ) -> dict[str, int]:
        counters = {"processed": 0, "transcribed": 0, "embedded": 0, "failed": 0}
        processed: list[ProcessedMedia] = []
        now = utcnow()
        sem = asyncio.Semaphore(getattr(self.settings, "profiling_max_concurrency", 3))
        tasks = [
            asyncio.create_task(self._process_one(user_id, account, item, now, sem))
            for item in media
        ]
        for result in await asyncio.gather(*tasks):
            counters["processed"] += 1
            if isinstance(result, Exception):
                counters["failed"] += 1
                logger.error(
                    "Profiling failed for user_id=%s: %s",
                    user_id,
                    result,
                    exc_info=result,
                )
                continue
            processed.append(result)
            counters["embedded"] += 1
            if result.combined_text != result.item.caption:
                counters["transcribed"] += 1
        if not processed:
            if media:
                raise RuntimeError("Hiçbir kullanıcı içeriği başarıyla profillenmedi")
            return counters

        await self._apply_performance_residuals(user_id, processed)
        vectors = [item.vector for item in processed]
        scores = [item.raw_score for item in processed]
        average, dispersion = average_and_dispersion(vectors)
        user_id_str = str(user_id)
        average_id = user_average_point_id(user_id_str)
        await self.qdrant.upsert(
            collection_name=self.settings.qdrant_user_collection,
            points=[
                models.PointStruct(
                    id=average_id,
                    vector={"profile": average},
                    payload={
                        "user_id": user_id_str,
                        "instagram_username": account.username,
                        "content_count": len(vectors),
                        "updated_at": now.isoformat(),
                    },
                )
            ],
        )
        average_score = float(np.mean(scores))
        latest_audience = await self.db.audience_snapshots.find_one(
            {"user_id": user_id}, sort=[("captured_at", -1)]
        )
        preferences = await self.db.user_preferences.find_one({"user_id": user_id}) or {}
        structured_profile = self._structured_profile(
            processed, latest_audience, preferences
        )
        summary = await self.summary.summarize(
            ProfileSummaryContext(
                username=account.username,
                follower_count=account.follower_count,
                content_count=len(processed),
                average_viral_score=average_score,
                vector_std_dev=dispersion,
                content_samples=[item.combined_text for item in processed],
                preferences=self._preference_context(preferences),
                structured_profile=structured_profile,
            )
        )
        await self.db.user_profiles.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "instagram_username": account.username,
                    "last_synced_at": now,
                    "average_vector_id": average_id,
                    "vector_std_dev": dispersion,
                    "ai_profile_summary": summary,
                    "content_count_analyzed": len(processed),
                    "average_viral_score": average_score,
                    "structured_profile": structured_profile,
                    "profile_schema_version": "creator-profile-v2",
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return counters

    async def _process_one(
        self,
        user_id: Any,
        account: InstagramAccount,
        item: InstagramMedia,
        now: Any,
        sem: asyncio.Semaphore,
    ) -> ProcessedMedia | Exception:
        async with sem:
            try:
                cached = await self._load_cached_media(user_id, account, item, now)
                if cached:
                    return cached
                return await self._process_media(user_id, account, item, now=now)
            except Exception as exc:  # noqa: BLE001 - isolate individual media failures
                logger.exception(
                    "Profiling failed for user_id=%s media_id=%s media_type=%s: %s",
                    user_id,
                    item.id,
                    item.media_type,
                    exc,
                )
                await self.db.user_content.update_one(
                    {"user_id": user_id, "media_id": item.id},
                    {
                        "$set": {
                            "processing_status": "failed",
                            "processing_error": str(exc)[:500],
                            "updated_at": utcnow(),
                        },
                        "$setOnInsert": {"created_at": utcnow()},
                    },
                    upsert=True,
                )
                return exc

    async def _load_cached_media(
        self,
        user_id: Any,
        account: InstagramAccount,
        item: InstagramMedia,
        now: Any,
    ) -> ProcessedMedia | None:
        """Reuse an already-embedded media item to avoid re-downloading/transcribing."""
        doc = await self.db.user_content.find_one(
            {
                "user_id": user_id,
                "media_id": item.id,
                "processing_status": "embedded",
                "fused_vector": {"$exists": True},
            }
        )
        if not doc:
            return None
        fused_vector = doc.get("fused_vector")
        point_id = doc.get("embedding_vector_id")
        if not fused_vector or not point_id:
            return None
        metadata = ContentMetadata(
            shortcode=item.shortcode,
            media_id=item.id,
            owner_username=account.username,
            owner_follower_count=account.follower_count,
            like_count=item.like_count,
            comment_count=item.comment_count,
            view_count=item.view_count,
            share_count=item.share_count,
            caption_text=item.caption,
            taken_at=item.taken_at,
            video_url=item.media_url,
        )
        score, components = compute_viral_score(metadata, now, self.weights)
        await self.db.user_content.update_one(
            {"user_id": user_id, "media_id": item.id},
            {
                "$set": {
                    "owner_username": account.username,
                    "caption_text": item.caption,
                    "metrics": {
                        "like_count": item.like_count,
                        "comment_count": item.comment_count,
                        "view_count": item.view_count,
                        "share_count": item.share_count,
                        "follower_count": account.follower_count,
                        "insights_available": item.insights_available,
                        **(item.metrics or {}),
                    },
                    "viral_score": score,
                    "score_components": {
                        "distribution_score": components.distribution_score,
                        "engagement_score": components.engagement_score,
                        "velocity_score": components.velocity_score,
                        "weighted_engagement_rate": components.weighted_engagement_rate,
                        "raw_score": components.raw_score,
                    },
                    "updated_at": utcnow(),
                }
            },
        )
        return ProcessedMedia(
            item=item,
            vector=list(fused_vector),
            raw_score=score,
            combined_text=doc.get("combined_text", item.caption),
            point_id=str(point_id),
            visual_analysis=doc.get("visual_analysis") or {},
        )

    async def _process_media(
        self,
        user_id: Any,
        account: InstagramAccount,
        item: InstagramMedia,
        *,
        now: Any,
    ) -> ProcessedMedia:
        metadata = ContentMetadata(
            shortcode=item.shortcode,
            media_id=item.id,
            owner_username=account.username,
            owner_follower_count=account.follower_count,
            like_count=item.like_count,
            comment_count=item.comment_count,
            view_count=item.view_count,
            share_count=item.share_count,
            caption_text=item.caption,
            taken_at=item.taken_at,
            video_url=item.media_url,
        )
        score, components = compute_viral_score(metadata, now, self.weights)
        transcribable_url = (
            item.media_url if item.media_type.upper() in {"REELS", "VIDEO"} else None
        )
        transcript = await self.transcription.transcribe(item.shortcode, transcribable_url)
        combined = "\n\n".join(part for part in (item.caption, transcript.text) if part).strip()
        if not item.media_url:
            raise ValueError("owned media has no downloadable media URL")
        preferences = await self.db.user_preferences.find_one({"user_id": user_id}) or {}
        languages = list(preferences.get("content_languages", []))
        markets = list(preferences.get("target_countries", []))
        multimodal = await self.multimodal.process_asset(
            source_url=item.media_url,
            content_id=f"user:{user_id}:{item.id}",
            caption=item.caption,
            combined_text=combined or f"{item.media_type} by {account.username}",
            collection=self.settings.qdrant_user_content_collection,
            payload={
                "user_id": str(user_id),
                "media_id": item.id,
                "shortcode": item.shortcode,
                "viral_score": score,
                "language": languages[0] if languages else transcript.language,
                "market": markets[0] if markets else None,
                "taken_at": item.taken_at.isoformat(),
                "content_type": "user_content",
            },
        )
        timestamp = utcnow()
        await self.db.user_content.update_one(
            {"user_id": user_id, "media_id": item.id},
            {
                "$set": {
                    "source": "user_profiling",
                    "shortcode": item.shortcode,
                    "permalink": item.permalink,
                    "owner_username": account.username,
                    "caption_text": item.caption,
                    "transcript": transcript.text,
                    "language": transcript.language,
                    "combined_text": combined,
                    "taken_at": item.taken_at,
                    "metrics": {
                        "like_count": item.like_count,
                        "comment_count": item.comment_count,
                        "view_count": item.view_count,
                        "share_count": item.share_count,
                        "follower_count": account.follower_count,
                        "insights_available": item.insights_available,
                        **(item.metrics or {}),
                    },
                    "viral_score": score,
                    "score_components": {
                        "distribution_score": components.distribution_score,
                        "engagement_score": components.engagement_score,
                        "velocity_score": components.velocity_score,
                        "weighted_engagement_rate": components.weighted_engagement_rate,
                        "raw_score": components.raw_score,
                    },
                    "media_asset": multimodal.media_asset,
                    "keyframes": multimodal.keyframes,
                    "visual_analysis": multimodal.visual_analysis,
                    "video_segments": multimodal.video_segments,
                    "processing_regions": multimodal.processing_regions,
                    "embedding_vector_id": multimodal.point_id,
                    "fused_vector": multimodal.fused_vector,
                    "embedding_schema_version": self.settings.vector_schema_version,
                    "processing_status": "embedded",
                    "processing_error": None,
                    "updated_at": timestamp,
                },
                "$setOnInsert": {"created_at": timestamp},
            },
            upsert=True,
        )
        return ProcessedMedia(
            item=item,
            vector=multimodal.fused_vector,
            raw_score=score,
            combined_text=combined,
            point_id=multimodal.point_id,
            visual_analysis=multimodal.visual_analysis,
        )

    async def _apply_performance_residuals(
        self, user_id: Any, processed: list[ProcessedMedia]
    ) -> None:
        cohorts: dict[str, list[float]] = {}
        for entry in processed:
            cohorts.setdefault(entry.item.media_type.lower(), []).append(entry.raw_score)
        for entry in processed:
            cohort = cohorts[entry.item.media_type.lower()]
            residual = compute_performance_residual(
                entry.raw_score,
                cohort,
                available_metrics=len(entry.item.metrics or {}),
                expected_metrics=7,
            )
            entry.residual = residual.components["z_residual"]
            await self.db.user_content.update_one(
                {"user_id": user_id, "media_id": entry.item.id},
                {
                    "$set": {
                        "performance_score": {
                            "score": residual.score,
                            "confidence": residual.confidence,
                            "model_version": residual.model_version,
                            "components": residual.components,
                            "cohort": {
                                "creator": str(user_id),
                                "media_format": entry.item.media_type.lower(),
                                "size": len(cohort),
                            },
                        }
                    }
                },
            )

    @classmethod
    def _structured_profile(
        cls,
        processed: list[ProcessedMedia],
        audience: dict[str, Any] | None,
        preferences: dict[str, Any],
    ) -> dict[str, Any]:
        labels = cls._semantic_labels([item.vector for item in processed])
        pillars: list[dict[str, Any]] = []
        for label in sorted(set(labels)):
            members = [
                item
                for item, item_label in zip(processed, labels, strict=True)
                if item_label == label
            ]
            terms = cls._pillar_terms(members)
            name = " · ".join(terms[:3]) or f"Content theme {label + 1}"
            descriptions = [
                str(item.visual_analysis.get("opening_frame", "")).strip()
                for item in members
                if item.visual_analysis.get("opening_frame")
            ]
            residual = float(np.mean([item.residual for item in members]))
            pillars.append(
                {
                    "id": f"semantic:{label}",
                    "name": name,
                    "description": (
                        "; ".join(descriptions[:2])
                        or "Semantic theme grounded in captions and visual analysis."
                    )[:1000],
                    "content_count": len(members),
                    "average_performance_residual": residual,
                    "strengths": ["above comparable format cohort"] if residual > 0 else [],
                    "opportunities": ["test a stronger hook"] if residual <= 0 else [],
                    "confidence": min(len(members) / 4.0, 1.0),
                }
            )
        format_residuals: dict[str, list[float]] = {}
        for entry in processed:
            format_residuals.setdefault(entry.item.media_type.lower(), []).append(entry.residual)
        format_patterns = {
            name: float(np.mean(values)) for name, values in format_residuals.items()
        }
        audience_markets = list((audience or {}).get("reached_by_country", {}).keys())[:10]
        target_markets = [
            *preferences.get("target_countries", []),
            *preferences.get("target_cities", []),
        ]
        return {
            "schema_version": "creator-profile-v2",
            "pillars": pillars,
            "winning_patterns": [
                f"{name} format" for name, residual in format_patterns.items() if residual >= 0
            ],
            "losing_patterns": [
                f"{name} format" for name, residual in format_patterns.items() if residual < 0
            ],
            "audience_markets": audience_markets,
            "avoid_patterns": list(preferences.get("constraints", [])),
            "target_markets": target_markets,
            "content_languages": list(preferences.get("content_languages", [])),
            "niches": list(preferences.get("niches", [])),
            "goals": list(preferences.get("goals", [])),
            "constraints": list(preferences.get("constraints", [])),
            "data_quality": min(len(processed) / 10.0, 1.0),
        }

    @staticmethod
    def _semantic_labels(vectors: list[list[float]]) -> list[int]:
        if len(vectors) < 4:
            return [0] * len(vectors)
        cluster_count = min(4, max(2, round(np.sqrt(len(vectors)))))
        labels = KMeans(n_clusters=cluster_count, random_state=42, n_init=10).fit_predict(
            np.asarray(vectors, dtype=float)
        )
        return [int(label) for label in labels]

    @staticmethod
    def _pillar_terms(members: list[ProcessedMedia]) -> list[str]:
        stopwords = {"the", "and", "bir", "ile", "için", "this", "that", "ve"}
        terms: list[str] = []
        for item in members:
            terms.extend(
                token.strip(".,!?;:#@").lower()
                for token in item.item.caption.split()
                if len(token.strip(".,!?;:#@")) >= 4
            )
            terms.extend(
                str(term).lower()
                for term in item.visual_analysis.get("visual_signature", [])
            )
        counts = Counter(term for term in terms if term not in stopwords)
        return [term for term, _ in counts.most_common(5)]

    @staticmethod
    def _preference_context(preferences: dict[str, Any]) -> dict[str, list[str] | str]:
        return {
            "target_countries": list(preferences.get("target_countries", [])),
            "target_cities": list(preferences.get("target_cities", [])),
            "content_languages": list(preferences.get("content_languages", [])),
            "timezone": str(preferences.get("timezone", "UTC")),
            "niches": list(preferences.get("niches", [])),
            "goals": list(preferences.get("goals", [])),
            "constraints": list(preferences.get("constraints", [])),
        }
