"""Creator Tracking Pipeline: fetch -> snapshot -> diff -> score -> AI profile.

Tracks public Instagram creators once per day (scheduled) or on demand
("Analyze Now"). Snapshots and content are stored once globally and shared by
all users; per-user access is managed through ``user_tracked_creators``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import numpy as np
from qdrant_client import AsyncQdrantClient
from sklearn.cluster import KMeans

from app.core.config import Settings
from app.core.errors import TransientError
from app.infrastructure.resources import utcnow
from app.providers.creator_profile import (
    CreatorNotFoundError,
    CreatorPost,
    CreatorProfileProvider,
    CreatorProfileSnapshot,
)
from app.providers.profile_summary import ProfileSummaryContext, ProfileSummaryProvider
from app.providers.scraper import EmitFn, NeedsInterventionError, noop_emit
from app.providers.transcription import TranscriptionProvider
from app.schemas.trends import ContentMetadata
from app.services.multimodal import MultimodalResult
from app.services.profiling import average_and_dispersion
from app.services.scoring import ScoreWeights, compute_viral_score

logger = logging.getLogger(__name__)


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
class ProcessedCreatorPost:
    post: CreatorPost
    vector: list[float]
    raw_score: float
    combined_text: str
    visual_analysis: dict[str, Any]


class CreatorTrackingService:
    def __init__(
        self,
        db: Any,
        qdrant: AsyncQdrantClient,
        settings: Settings,
        creators: CreatorProfileProvider,
        transcription: TranscriptionProvider,
        multimodal: MultimodalProcessor,
        summary: ProfileSummaryProvider,
        emit: EmitFn = noop_emit,
    ) -> None:
        self.db = db
        self.qdrant = qdrant
        self.settings = settings
        self.creators = creators
        self.transcription = transcription
        self.multimodal = multimodal
        self.summary = summary
        self.emit = emit
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

    async def run(self, creator_id: Any) -> dict[str, int]:
        creator = await self.db.tracked_creators.find_one({"_id": creator_id})
        if not creator:
            raise ValueError("tracked creator is missing")
        username = str(creator["username"])
        await self.emit(f"Fetching profile for @{username}", step="profile")
        await self.db.tracked_creators.update_one(
            {"_id": creator_id},
            {"$set": {"status": "tracking", "last_error": None}},
        )
        try:
            snapshot = await self.creators.fetch_profile(username)
            await self.emit(
                f"Profile fetched for @{username}: {snapshot.follower_count} followers, "
                f"{len(snapshot.posts)} posts",
                step="profile",
            )
            result = await self._process(creator_id, snapshot)
        except NeedsInterventionError as exc:
            await self._fail(creator_id, "needs_intervention", exc)
            raise
        except TransientError:
            # Retryable upstream failure (e.g. Instagram 429): keep the last
            # known status; Celery retries the job with backoff.
            raise
        except CreatorNotFoundError as exc:
            await self._fail(creator_id, "not_found", exc)
            raise
        except Exception as exc:
            await self._fail(creator_id, "failed", exc)
            raise
        return result

    async def _fail(self, creator_id: Any, status: str, exc: Exception) -> None:
        await self.db.tracked_creators.update_one(
            {"_id": creator_id},
            {"$set": {"status": status, "last_error": str(exc)[:500]}},
        )

    async def _process(
        self, creator_id: Any, snapshot: CreatorProfileSnapshot
    ) -> dict[str, int]:
        counters = {"snapshotted": 0, "new_posts": 0, "updated_posts": 0, "embedded": 0}
        now = utcnow()
        await self.emit(
            f"Processing {len(snapshot.posts)} posts for @{snapshot.username}",
            step="content",
            post_count=len(snapshot.posts),
        )
        await self.db.creator_snapshots.update_one(
            {"creator_id": creator_id, "day": now.date().isoformat()},
            {
                "$set": {
                    "captured_at": now,
                    "follower_count": snapshot.follower_count,
                    "following_count": snapshot.following_count,
                    "media_count": snapshot.media_count,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        counters["snapshotted"] = 1

        await self.db.creator_content.update_many(
            {"creator_id": creator_id}, {"$set": {"is_new": False}}
        )
        processed: list[ProcessedCreatorPost] = []
        for post in snapshot.posts:
            score, components = compute_viral_score(
                ContentMetadata(
                    shortcode=post.shortcode,
                    owner_username=snapshot.username,
                    owner_follower_count=snapshot.follower_count,
                    like_count=post.like_count,
                    comment_count=post.comment_count,
                    view_count=post.view_count,
                    caption_text=post.caption,
                    taken_at=post.taken_at,
                    video_url=post.media_url,
                ),
                now,
                self.weights,
            )
            score_fields = {
                "viral_score": score,
                "score_components": {
                    "distribution_score": components.distribution_score,
                    "engagement_score": components.engagement_score,
                    "velocity_score": components.velocity_score,
                    "weighted_engagement_rate": components.weighted_engagement_rate,
                    "raw_score": components.raw_score,
                },
            }
            existing = await self.db.creator_content.find_one(
                {"creator_id": creator_id, "shortcode": post.shortcode}
            )
            if existing is not None:
                await self.db.creator_content.update_one(
                    {"_id": existing["_id"]},
                    {
                        "$set": {
                            **score_fields,
                            "like_count": post.like_count,
                            "comment_count": post.comment_count,
                            "view_count": post.view_count,
                            "last_seen_at": now,
                        }
                    },
                )
                counters["updated_posts"] += 1
                continue
            try:
                result = await self._process_new_post(
                    creator_id, snapshot, post, score_fields, now=now
                )
            except Exception as exc:  # noqa: BLE001 - isolate per-post failures
                logger.exception(
                    "Creator post processing failed creator=%s shortcode=%s: %s",
                    creator_id,
                    post.shortcode,
                    exc,
                )
                await self._insert_failed_post(creator_id, post, score_fields, exc, now)
                counters["new_posts"] += 1
                continue
            counters["new_posts"] += 1
            if result is not None:
                counters["embedded"] += 1
                processed.append(result)

        trend_score = await self._trend_score(creator_id, snapshot)
        ai_fields = await self._maybe_update_ai_profile(
            creator_id, snapshot, processed, trend_score, now=now
        )
        await self.db.tracked_creators.update_one(
            {"_id": creator_id},
            {
                "$set": {
                    "display_name": snapshot.display_name,
                    "bio": snapshot.bio,
                    "avatar_url": snapshot.avatar_url,
                    "follower_count": snapshot.follower_count,
                    "following_count": snapshot.following_count,
                    "media_count": snapshot.media_count,
                    "trend_score": trend_score,
                    "status": "active",
                    "last_tracked_at": now,
                    "last_error": None,
                    **ai_fields,
                }
            },
        )
        await self.emit(
            f"Analysis complete for @{snapshot.username}: "
            f"{counters['new_posts']} new posts, "
            f"{counters['updated_posts']} updated, "
            f"{counters['embedded']} embedded, "
            f"trend_score={trend_score:.1f}",
            step="summary",
        )
        return counters

    async def _process_new_post(
        self,
        creator_id: Any,
        snapshot: CreatorProfileSnapshot,
        post: CreatorPost,
        score_fields: dict[str, Any],
        *,
        now: Any,
    ) -> ProcessedCreatorPost | None:
        transcribable_url = (
            post.media_url if post.media_type.upper() in {"REELS", "VIDEO"} else None
        )
        transcript = await self.transcription.transcribe(post.shortcode, transcribable_url)
        combined = "\n\n".join(
            part for part in (post.caption, transcript.text) if part
        ).strip()
        multimodal: MultimodalResult | None = None
        if self.settings.creator_ai_profile_enabled and post.media_url:
            multimodal = await self.multimodal.process_asset(
                source_url=post.media_url,
                content_id=f"creator:{creator_id}:{post.shortcode}",
                caption=post.caption,
                combined_text=combined or f"{post.media_type} by {snapshot.username}",
                collection=self.settings.qdrant_creator_content_collection,
                payload={
                    "creator_id": str(creator_id),
                    "username": snapshot.username,
                    "shortcode": post.shortcode,
                    "viral_score": score_fields["viral_score"],
                    "taken_at": post.taken_at.isoformat(),
                    "content_type": "creator_content",
                },
            )
        await self.db.creator_content.insert_one(
            {
                "creator_id": creator_id,
                "shortcode": post.shortcode,
                "permalink": post.permalink,
                "owner_username": snapshot.username,
                "caption_text": post.caption,
                "transcript": transcript.text,
                "language": transcript.language,
                "combined_text": combined,
                "media_type": post.media_type,
                "thumbnail_url": post.thumbnail_url,
                "taken_at": post.taken_at,
                "like_count": post.like_count,
                "comment_count": post.comment_count,
                "view_count": post.view_count,
                **score_fields,
                "media_asset": multimodal.media_asset if multimodal else None,
                "keyframes": multimodal.keyframes if multimodal else [],
                "visual_analysis": multimodal.visual_analysis if multimodal else {},
                "embedding_vector_id": multimodal.point_id if multimodal else None,
                "embedding_schema_version": self.settings.vector_schema_version,
                "processing_status": "embedded" if multimodal else "stored",
                "processing_error": None,
                "is_new": True,
                "first_seen_at": now,
                "last_seen_at": now,
                "created_at": now,
            }
        )
        if multimodal is None:
            return None
        return ProcessedCreatorPost(
            post=post,
            vector=multimodal.fused_vector,
            raw_score=float(score_fields["viral_score"]),
            combined_text=combined,
            visual_analysis=multimodal.visual_analysis,
        )

    async def _insert_failed_post(
        self,
        creator_id: Any,
        post: CreatorPost,
        score_fields: dict[str, Any],
        exc: Exception,
        now: Any,
    ) -> None:
        await self.db.creator_content.update_one(
            {"creator_id": creator_id, "shortcode": post.shortcode},
            {
                "$set": {
                    **score_fields,
                    "permalink": post.permalink,
                    "caption_text": post.caption,
                    "media_type": post.media_type,
                    "thumbnail_url": post.thumbnail_url,
                    "taken_at": post.taken_at,
                    "like_count": post.like_count,
                    "comment_count": post.comment_count,
                    "view_count": post.view_count,
                    "processing_status": "failed",
                    "processing_error": str(exc)[:500],
                    "is_new": True,
                    "last_seen_at": now,
                },
                "$setOnInsert": {"first_seen_at": now, "created_at": now},
            },
            upsert=True,
        )

    async def _trend_score(
        self, creator_id: Any, snapshot: CreatorProfileSnapshot
    ) -> float:
        posts = await self.db.creator_content.find(
            {"creator_id": creator_id}
        ).to_list(None)
        top_scores = sorted(
            (float(post.get("viral_score", 0.0)) for post in posts), reverse=True
        )[:5]
        content_score = float(np.mean(top_scores)) if top_scores else 0.0
        growth_pct = await self._weekly_growth_pct(creator_id, snapshot.follower_count)
        growth_score = min(max(growth_pct, 0.0), 10.0) * 10.0
        return round(0.7 * content_score + 0.3 * growth_score, 2)

    async def _weekly_growth_pct(self, creator_id: Any, current: int) -> float:
        snapshots = await self.db.creator_snapshots.find(
            {"creator_id": creator_id}
        ).to_list(None)
        cutoff = utcnow() - timedelta(days=7)
        past = [
            snap
            for snap in snapshots
            if snap.get("captured_at") is not None and snap["captured_at"] <= cutoff
        ]
        if not past:
            return 0.0
        baseline = max(past, key=lambda snap: snap["captured_at"])
        base = int(baseline.get("follower_count", 0))
        if base <= 0:
            return 0.0
        return (current - base) / base * 100.0

    async def _maybe_update_ai_profile(
        self,
        creator_id: Any,
        snapshot: CreatorProfileSnapshot,
        processed: list[ProcessedCreatorPost],
        trend_score: float,
        *,
        now: Any,
    ) -> dict[str, Any]:
        if not processed:
            return {}
        vectors = [item.vector for item in processed]
        average, dispersion = average_and_dispersion(vectors)
        structured = self._structured_profile(processed)
        average_score = float(np.mean([item.raw_score for item in processed]))
        summary = await self.summary.summarize(
            ProfileSummaryContext(
                username=snapshot.username,
                follower_count=snapshot.follower_count,
                content_count=len(processed),
                average_viral_score=average_score,
                vector_std_dev=dispersion,
                content_samples=[item.combined_text for item in processed],
                structured_profile=structured,
            )
        )
        await self.db.creator_profiles.update_one(
            {"creator_id": creator_id},
            {
                "$set": {
                    "username": snapshot.username,
                    "average_vector": average,
                    "vector_std_dev": dispersion,
                    "ai_summary": summary,
                    "structured_profile": structured,
                    "niches": [pillar["name"] for pillar in structured["pillars"][:3]],
                    "average_viral_score": average_score,
                    "trend_score": trend_score,
                    "content_count_analyzed": len(processed),
                    "profile_schema_version": "creator-profile-v2",
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return {"profile_updated_at": now}

    @classmethod
    def _structured_profile(
        cls, processed: list[ProcessedCreatorPost]
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
            pillars.append(
                {
                    "id": f"semantic:{label}",
                    "name": name,
                    "description": (
                        "; ".join(descriptions[:2])
                        or "Semantic theme grounded in captions and visual analysis."
                    )[:1000],
                    "content_count": len(members),
                    "average_viral_score": float(
                        np.mean([item.raw_score for item in members])
                    ),
                    "confidence": min(len(members) / 4.0, 1.0),
                }
            )
        format_scores: dict[str, list[float]] = {}
        for item in processed:
            format_scores.setdefault(item.post.media_type.lower(), []).append(
                item.raw_score
            )
        average_by_format = {
            name: float(np.mean(values)) for name, values in format_scores.items()
        }
        overall = (
            float(np.mean([item.raw_score for item in processed])) if processed else 0.0
        )
        return {
            "schema_version": "creator-profile-v2",
            "pillars": pillars,
            "winning_patterns": [
                f"{name} format"
                for name, value in average_by_format.items()
                if value >= overall
            ],
            "losing_patterns": [
                f"{name} format"
                for name, value in average_by_format.items()
                if value < overall
            ],
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
    def _pillar_terms(members: list[ProcessedCreatorPost]) -> list[str]:
        stopwords = {"the", "and", "bir", "ile", "için", "this", "that", "ve"}
        terms: list[str] = []
        for item in members:
            terms.extend(
                token.strip(".,!?;:#@").lower()
                for token in item.post.caption.split()
                if len(token.strip(".,!?;:#@")) >= 4
            )
            terms.extend(
                str(term).lower()
                for term in item.visual_analysis.get("visual_signature", [])
            )
        counts = Counter(term for term in terms if term not in stopwords)
        return [term for term, _ in counts.most_common(5)]
