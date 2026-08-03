"""Creator preferences, feedback/outcomes, experiments, and observability services."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, cast

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.token_crypto import TokenCipher
from app.infrastructure.resources import utcnow
from app.providers.instagram_profile import InstagramProfileProvider
from app.schemas.intelligence import (
    CreatorPreferences,
    ExperimentCreate,
    ExperimentUpdate,
    RecommendationEventRequest,
)


class IntelligenceNotFoundError(RuntimeError):
    pass


class IntelligenceConflictError(RuntimeError):
    pass


class PreferencesService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def get(self, user_id: ObjectId) -> dict[str, Any]:
        stored = await self.db.user_preferences.find_one({"user_id": user_id})
        if stored:
            return cast(dict[str, Any], stored)
        return {"user_id": user_id, **CreatorPreferences().model_dump(), "updated_at": None}

    async def put(
        self, user_id: ObjectId, preferences: CreatorPreferences
    ) -> dict[str, Any]:
        now = utcnow()
        await self.db.user_preferences.update_one(
            {"user_id": user_id},
            {
                "$set": {**preferences.model_dump(), "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return await self.get(user_id)


class RecommendationLearningService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def append_event(
        self, user_id: ObjectId, recommendation_id: str, request: RecommendationEventRequest
    ) -> dict[str, Any]:
        await self._owned_recommendation(user_id, recommendation_id)
        document = {
            "user_id": user_id,
            "recommendation_id": recommendation_id,
            **request.model_dump(),
            "created_at": utcnow(),
        }
        try:
            result = await self.db.recommendation_events.insert_one(document)
            document["_id"] = result.inserted_id
        except DuplicateKeyError:
            existing = await self.db.recommendation_events.find_one(
                {"user_id": user_id, "idempotency_key": request.idempotency_key}
            )
            if not existing or existing.get("recommendation_id") != recommendation_id:
                raise IntelligenceConflictError("idempotency key was already used") from None
            document = existing
        return document

    async def link_post(
        self, user_id: ObjectId, recommendation_id: str, media_id: str
    ) -> dict[str, Any]:
        await self._owned_recommendation(user_id, recommendation_id)
        media = await self.db.user_content.find_one({"user_id": user_id, "media_id": media_id})
        if not media:
            raise IntelligenceNotFoundError("media does not belong to the connected account")
        now = utcnow()
        document = {
            "user_id": user_id,
            "recommendation_id": recommendation_id,
            "media_id": media_id,
            "permalink": media.get("permalink"),
            "linked_at": now,
            "baseline_metrics": media.get("metrics", {}),
            "outcome_status": "scheduled",
            "outcome_offsets_pending": [24, 72],
        }
        try:
            result = await self.db.recommendation_post_links.insert_one(document)
            document["_id"] = result.inserted_id
        except DuplicateKeyError as exc:
            raise IntelligenceConflictError("recommendation or media is already linked") from exc
        return document

    async def create_experiment(
        self, user_id: ObjectId, request: ExperimentCreate
    ) -> dict[str, Any]:
        await self._owned_recommendation(user_id, request.recommendation_id)
        now = utcnow()
        document = {
            "user_id": user_id,
            **request.model_dump(),
            "state": "draft",
            "results": [],
            "created_at": now,
            "updated_at": now,
        }
        result = await self.db.recommendation_experiments.insert_one(document)
        document["_id"] = result.inserted_id
        return document

    async def update_experiment(
        self, user_id: ObjectId, experiment_id: ObjectId, request: ExperimentUpdate
    ) -> dict[str, Any]:
        existing = await self.db.recommendation_experiments.find_one(
            {"_id": experiment_id, "user_id": user_id}
        )
        if not existing:
            raise IntelligenceNotFoundError("experiment not found")
        allowed: dict[str, set[str]] = {
            "draft": {"running"},
            "running": {"awaiting_data", "inconclusive"},
            "awaiting_data": {"completed", "inconclusive"},
            "completed": set(),
            "inconclusive": set(),
        }
        if request.state != existing["state"] and request.state not in allowed[existing["state"]]:
            raise IntelligenceConflictError("invalid experiment state transition")
        await self.db.recommendation_experiments.update_one(
            {"_id": experiment_id, "user_id": user_id},
            {
                "$set": {
                    "state": request.state,
                    "note": request.note,
                    "updated_at": utcnow(),
                }
            },
        )
        updated = await self.db.recommendation_experiments.find_one({"_id": experiment_id})
        return cast(dict[str, Any], updated)

    async def _owned_recommendation(
        self, user_id: ObjectId, recommendation_id: str
    ) -> dict[str, Any]:
        batch = await self.db.recommendations.find_one(
            {"user_id": user_id, "recommendations.id": recommendation_id}
        )
        if not batch:
            raise IntelligenceNotFoundError("recommendation not found")
        return cast(dict[str, Any], batch)


class OutcomeService:
    def __init__(
        self,
        db: Any,
        instagram: InstagramProfileProvider | None = None,
        cipher: TokenCipher | None = None,
    ) -> None:
        self.db = db
        self.instagram = instagram
        self.cipher = cipher

    async def capture_due(self, *, offset_hours: int) -> dict[str, int]:
        counters = {"processed": 0, "captured": 0, "missing": 0}
        cutoff = utcnow() - timedelta(hours=offset_hours)
        cursor = self.db.recommendation_post_links.find(
            {
                "linked_at": {"$lte": cutoff},
                "outcome_offsets_pending": offset_hours,
            }
        )
        async for link in cursor:
            counters["processed"] += 1
            media = await self.db.user_content.find_one(
                {"user_id": link["user_id"], "media_id": link["media_id"]}
            )
            if not media:
                counters["missing"] += 1
                continue
            current = media.get("metrics", {})
            if self.instagram is not None and self.cipher is not None:
                connection = await self.db.instagram_connections.find_one(
                    {"user_id": link["user_id"]}
                )
                if connection:
                    token = self.cipher.decrypt(connection["access_token_encrypted"])
                    refreshed = await self.instagram.fetch_recent_media(
                        token,
                        str(connection["instagram_user_id"]),
                        now=utcnow(),
                    )
                    matched = next(
                        (item for item in refreshed if item.id == link["media_id"]),
                        None,
                    )
                    if matched is not None:
                        current = {
                            "view_count": matched.view_count,
                            "like_count": matched.like_count,
                            "comment_count": matched.comment_count,
                            "share_count": matched.share_count,
                            **(matched.metrics or {}),
                        }
            baseline = link.get("baseline_metrics", {})
            uplift = {
                key: _safe_uplift(current.get(key), baseline.get(key))
                for key in (
                    "view_count",
                    "share_count",
                    "saved",
                    "follows",
                    "ig_reels_avg_watch_time",
                    "ig_reels_video_view_total_time",
                )
                if current.get(key) is not None and baseline.get(key) is not None
            }
            now = utcnow()
            await self.db.content_metric_snapshots.update_one(
                {
                    "subject_type": "outcome",
                    "subject_id": str(link["_id"]),
                    "offset_hours": offset_hours,
                },
                {
                    "$set": {
                        "source": "meta_owned_media_insights",
                        "user_id": link["user_id"],
                        "recommendation_id": link["recommendation_id"],
                        "media_id": link["media_id"],
                        "captured_at": now,
                        "metrics": current,
                        "uplift": uplift,
                        "coverage": sorted(current),
                        "provider_version": "meta-insights-v1",
                    }
                },
                upsert=True,
            )
            await self.db.recommendation_post_links.update_one(
                {"_id": link["_id"]},
                {
                    "$pull": {"outcome_offsets_pending": offset_hours},
                    "$set": {"outcome_status": "captured", "updated_at": now},
                },
            )
            counters["captured"] += 1
        return counters


class ObservabilityService:
    def __init__(self, db: Any, settings: Any) -> None:
        self.db = db
        self.settings = settings

    async def summary(self) -> dict[str, Any]:
        now = utcnow()
        oldest = await self.db.job_runs.find_one({"state": "queued"}, sort=[("created_at", 1)])
        queue_age = (now - oldest["created_at"]).total_seconds() if oldest else None
        stale_threshold = now - timedelta(hours=self.settings.stale_job_cleanup_hours)
        attention_jobs = await self.db.job_runs.count_documents(
            {
                "$or": [
                    {"state": "needs_intervention"},
                    {
                        "state": "failed",
                        "created_at": {"$gte": now - timedelta(days=7)},
                    },
                ]
            }
        )
        stale_jobs = await self.db.job_runs.count_documents(
            {
                "state": {"$in": ["queued", "running"]},
                "created_at": {"$lt": stale_threshold},
            }
        )
        durations: list[float] = []
        async for job in self.db.job_runs.find({"state": "succeeded"}):
            if job.get("started_at") and job.get("finished_at"):
                durations.append((job["finished_at"] - job["started_at"]).total_seconds())
        durations.sort()
        trend_total = await self.db.trend_content.count_documents({})
        snapshot_subjects = await self.db.content_metric_snapshots.distinct(
            "subject_id", {"subject_type": "trend_content"}
        )
        events = {
            state: await self.db.recommendation_events.count_documents({"state": state})
            for state in ("saved", "dismissed", "in_production", "published", "archived")
        }
        provider_usage = await self._provider_usage()
        latest_evaluation = await self.db.evaluation_runs.find_one(
            {}, sort=[("created_at", -1)]
        )
        if latest_evaluation:
            latest_evaluation = {
                **latest_evaluation,
                "_id": str(latest_evaluation["_id"]),
            }
        return {
            "queue_age_seconds": queue_age,
            "job_duration_p50_seconds": _percentile(durations, 0.5),
            "job_duration_p95_seconds": _percentile(durations, 0.95),
            "attention_jobs": attention_jobs,
            "stale_jobs": stale_jobs,
            "stale_trends": await self.db.trend_content.count_documents(
                {"last_seen_at": {"$lt": now - timedelta(days=7)}}
            ),
            "stale_profiles": await self.db.user_profiles.count_documents(
                {"updated_at": {"$lt": now - timedelta(days=7)}}
            ),
            "snapshot_coverage": len(snapshot_subjects) / max(trend_total, 1),
            "multimodal_failures": {
                "vision": await self.db.user_content.count_documents(
                    {"processing_status": "failed", "processing_error_stage": "vision"}
                ),
                "embedding": await self.db.user_content.count_documents(
                    {"processing_status": "failed", "processing_error_stage": "embedding"}
                ),
            },
            "provider_usage": provider_usage,
            "evaluation": {
                "latest": latest_evaluation,
                "thresholds": {
                    "min_ndcg_at_k": self.settings.evaluation_min_ndcg_at_k,
                    "min_precision_at_k": self.settings.evaluation_min_precision_at_k,
                    "max_brier": self.settings.evaluation_max_brier,
                    "max_p95_latency_seconds": (
                        self.settings.evaluation_max_p95_latency_seconds
                    ),
                    "max_cost_per_prediction": (
                        self.settings.evaluation_max_cost_per_prediction
                    ),
                    "rollback_ndcg_drop": self.settings.evaluation_rollback_ndcg_drop,
                    "rollback_precision_drop": (
                        self.settings.evaluation_rollback_precision_drop
                    ),
                    "rollback_brier_increase": (
                        self.settings.evaluation_rollback_brier_increase
                    ),
                },
            },
            "funnel": {
                "generated": await self.db.recommendations.count_documents({}),
                **events,
            },
        }

    async def _provider_usage(self) -> dict[str, Any]:
        input_tokens = 0
        output_tokens = 0
        async for batch in self.db.recommendations.find({}):
            usage = batch.get("usage", {})
            input_tokens += int(usage.get("input_tokens", 0))
            output_tokens += int(usage.get("output_tokens", 0))
        cost = (
            input_tokens / 1000 * self.settings.provider_cost_per_1k_input_tokens
            + output_tokens / 1000 * self.settings.provider_cost_per_1k_output_tokens
        )
        groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        async for run in self.db.provider_runs.find({}):
            key = (
                str(run.get("provider", "unknown")),
                str(run.get("model_id", "unknown")),
                str(run.get("stage", "unknown")),
                str(run.get("region", "unknown")),
            )
            group = groups.setdefault(
                key,
                {
                    "provider": key[0],
                    "model_id": key[1],
                    "stage": key[2],
                    "region": key[3],
                    "runs": 0,
                    "failures": 0,
                    "duration_ms": 0.0,
                    "media_seconds": 0.0,
                },
            )
            group["runs"] += 1
            group["failures"] += int(run.get("state") == "failed")
            group["duration_ms"] += float(run.get("duration_ms", 0))
            group["media_seconds"] += float(run.get("media_seconds") or 0)
        for group in groups.values():
            group["average_duration_ms"] = group.pop("duration_ms") / max(
                group["runs"], 1
            )
        return {
            "totals": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost": round(cost, 4),
            },
            "groups": list(groups.values()),
        }


def _safe_uplift(current: Any, baseline: Any) -> float | None:
    if current is None or baseline is None:
        return None
    return (float(current) - float(baseline)) / max(abs(float(baseline)), 1.0)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = min(math.ceil((len(values) - 1) * quantile), len(values) - 1)
    return values[index]
