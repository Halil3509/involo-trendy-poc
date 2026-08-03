"""Idempotent metric snapshots and trend lifecycle updates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infrastructure.resources import utcnow
from app.services.scoring import compute_lifecycle, compute_public_trend_score


class SnapshotService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def capture_due(
        self,
        target_offsets_hours: list[int],
        *,
        now: datetime | None = None,
        due_window_hours: float = 1.5,
    ) -> dict[str, int]:
        captured_at = now or utcnow()
        counters = {"processed": 0, "captured": 0, "not_due": 0, "existing": 0}
        async for content in self.db.trend_content.find({}):
            counters["processed"] += 1
            origin = content.get("taken_at") or content.get("first_seen_at")
            if origin is None:
                counters["not_due"] += 1
                continue
            age_hours = max((captured_at - origin).total_seconds() / 3600.0, 0.0)
            due_offsets = [
                offset
                for offset in sorted(set(target_offsets_hours))
                if offset <= age_hours < offset + due_window_hours
            ]
            if not due_offsets:
                counters["not_due"] += 1
                continue
            metrics = content.get("metrics", {})
            normalized = {
                "views": metrics.get("view_count"),
                "reach": metrics.get("reach"),
                "likes": metrics.get("like_count"),
                "comments": metrics.get("comment_count"),
                "shares": metrics.get("share_count"),
                "saves": metrics.get("save_count"),
            }
            available = [key for key, value in normalized.items() if value is not None]
            for target_offset in due_offsets:
                identity = {
                    "subject_type": "trend_content",
                    "subject_id": str(content["_id"]),
                    "offset_hours": target_offset,
                }
                if await self.db.content_metric_snapshots.find_one(identity):
                    counters["existing"] += 1
                    continue
                await self.db.content_metric_snapshots.update_one(
                    identity,
                    {
                        "$setOnInsert": {
                            "source": content.get("source", "unknown"),
                            "captured_at": captured_at,
                            "target_offset_hours": target_offset,
                            "age_hours_at_capture": age_hours,
                            "metrics": normalized,
                            "coverage": {
                                "requested": list(normalized),
                                "available": available,
                                "unavailable": [
                                    key for key in normalized if key not in available
                                ],
                            },
                            "provider_version": "snapshot-v1",
                        }
                    },
                    upsert=True,
                )
                counters["captured"] += 1
            snapshots = self.db.content_metric_snapshots.find(
                {"subject_type": "trend_content", "subject_id": str(content["_id"])}
            )
            points: list[tuple[float, float]] = []
            async for snapshot in snapshots:
                value = snapshot.get("metrics", {}).get("views")
                if value is not None:
                    elapsed = snapshot.get(
                        "age_hours_at_capture", snapshot.get("target_offset_hours")
                    )
                    if elapsed is not None:
                        points.append((float(elapsed), float(value)))
            signals = compute_lifecycle(points, now_age_hours=age_hours)
            score = compute_public_trend_score(
                normalized,
                age_hours=age_hours,
                velocity=signals.velocity,
                percentile=signals.percentile,
            )
            await self.db.trend_content.update_one(
                {"_id": content["_id"]},
                {
                    "$set": {
                        "trend_signals": {
                            **signals.__dict__,
                            "snapshot_at": captured_at,
                            "model_version": "trend-signals-v1",
                        },
                        "public_trend_score": score.__dict__,
                        "viral_score": score.score,
                        "score_confidence": score.confidence,
                    }
                },
            )
        return counters
