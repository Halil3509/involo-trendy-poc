"""Offline ranking and probability calibration evaluation."""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any

from bson import ObjectId

from app.infrastructure.resources import utcnow


def evaluate_rankings(
    rankings: list[list[dict[str, Any]]], *, k: int
) -> dict[str, Any]:
    if not rankings:
        raise ValueError("no labeled historical rankings are available")
    ndcg_values: list[float] = []
    precision_values: list[float] = []
    probability_labels: list[tuple[float, int]] = []
    for ranking in rankings:
        ordered = sorted(ranking, key=lambda item: int(item.get("rank", 0)))
        top = ordered[:k]
        gains = [int(bool(item["later_outperformer"])) for item in top]
        dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
        ideal = sorted(
            (int(bool(item["later_outperformer"])) for item in ordered), reverse=True
        )[:k]
        idcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(ideal))
        ndcg_values.append(dcg / idcg if idcg else 0.0)
        precision_values.append(sum(gains) / k)
        probability_labels.extend(
            (
                min(max(float(item["probability"]), 0.0), 1.0),
                int(bool(item["later_outperformer"])),
            )
            for item in ordered
        )
    brier = sum((probability - label) ** 2 for probability, label in probability_labels)
    brier /= len(probability_labels)
    return {
        "ndcg_at_k": sum(ndcg_values) / len(ndcg_values),
        "precision_at_k": sum(precision_values) / len(precision_values),
        "brier": brier,
        "reliability_buckets": _reliability_buckets(probability_labels),
    }


def _reliability_buckets(values: list[tuple[float, int]]) -> list[dict[str, float | int]]:
    buckets: list[dict[str, float | int]] = []
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        members = [
            (probability, label)
            for probability, label in values
            if lower <= probability <= upper
            and (probability < upper or index == 9)
        ]
        if not members:
            continue
        buckets.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(members),
                "mean_probability": sum(item[0] for item in members) / len(members),
                "observed_rate": sum(item[1] for item in members) / len(members),
            }
        )
    return buckets


class OfflineEvaluationService:
    def __init__(self, db: Any, settings: Any) -> None:
        self.db = db
        self.settings = settings

    async def run(
        self, *, model_version: str, data_cutoff: datetime, k: int
    ) -> dict[str, Any]:
        rankings: list[list[dict[str, Any]]] = []
        latencies: list[float] = []
        costs: list[float] = []
        cursor = self.db.ranking_predictions.find(
            {"model_version": model_version, "predicted_at": {"$lte": data_cutoff}}
        )
        async for prediction in cursor:
            await self._hydrate_labels(prediction, data_cutoff)
            candidates = [
                item
                for item in prediction.get("candidates", [])
                if item.get("later_outperformer") is not None
            ]
            if candidates:
                rankings.append(candidates)
            if prediction.get("latency_seconds") is not None:
                latencies.append(float(prediction["latency_seconds"]))
            if prediction.get("estimated_cost") is not None:
                costs.append(float(prediction["estimated_cost"]))
        metrics = evaluate_rankings(rankings, k=k)
        metrics["p95_latency_seconds"] = _percentile(latencies, 0.95)
        metrics["cost_per_prediction"] = sum(costs) / len(costs) if costs else 0.0
        thresholds = {
            "min_ndcg_at_k": self.settings.evaluation_min_ndcg_at_k,
            "min_precision_at_k": self.settings.evaluation_min_precision_at_k,
            "max_brier": self.settings.evaluation_max_brier,
            "max_p95_latency_seconds": self.settings.evaluation_max_p95_latency_seconds,
            "max_cost_per_prediction": self.settings.evaluation_max_cost_per_prediction,
        }
        passed = (
            metrics["ndcg_at_k"] >= thresholds["min_ndcg_at_k"]
            and metrics["precision_at_k"] >= thresholds["min_precision_at_k"]
            and metrics["brier"] <= thresholds["max_brier"]
            and (metrics["p95_latency_seconds"] or 0)
            <= thresholds["max_p95_latency_seconds"]
            and metrics["cost_per_prediction"] <= thresholds["max_cost_per_prediction"]
        )
        previous = await self.db.evaluation_runs.find_one(
            {"model_version": model_version}, sort=[("created_at", -1)]
        )
        rollback = self._rollback(metrics, previous)
        document = {
            "model_version": model_version,
            "data_cutoff": data_cutoff,
            "evaluation_version": "offline-ranking-v1",
            "label_definition": (
                "explicit later outperformer label, otherwise later snapshot views "
                "above the ranked-query median"
            ),
            "k": k,
            "sample_size": len(rankings),
            "candidate_sample_size": sum(len(ranking) for ranking in rankings),
            "metrics": metrics,
            "thresholds": thresholds,
            "passed": passed,
            "rollback_recommended": rollback,
            "created_at": utcnow(),
        }
        result = await self.db.evaluation_runs.insert_one(document)
        document["_id"] = result.inserted_id
        return document

    async def _hydrate_labels(
        self, prediction: dict[str, Any], data_cutoff: datetime
    ) -> None:
        snapshot_scores: list[tuple[dict[str, Any], float]] = []
        for candidate in prediction.get("candidates", []):
            if candidate.get("later_outperformer") is not None:
                continue
            trend_id = candidate.get("trend_id")
            identifier: Any = (
                ObjectId(trend_id)
                if isinstance(trend_id, str) and ObjectId.is_valid(trend_id)
                else trend_id
            )
            trend = await self.db.trend_content.find_one({"_id": identifier})
            label = (trend or {}).get("evaluation_label") or {}
            labeled_at = label.get("labeled_at")
            if labeled_at and labeled_at <= data_cutoff:
                candidate["later_outperformer"] = bool(label.get("outperformer"))
                continue
            snapshots = self.db.content_metric_snapshots.find(
                {
                    "subject_type": "trend_content",
                    "subject_id": str(trend_id),
                }
            )
            eligible: list[dict[str, Any]] = []
            async for snapshot in snapshots:
                captured_at = snapshot.get("captured_at")
                if (
                    captured_at
                    and prediction["predicted_at"] < captured_at <= data_cutoff
                ):
                    eligible.append(snapshot)
            if eligible:
                latest = max(eligible, key=lambda item: item["captured_at"])
                metrics = latest.get("metrics", {})
                value = metrics.get("views", metrics.get("view_count"))
                if value is not None:
                    snapshot_scores.append((candidate, float(value)))
        if len(snapshot_scores) >= 2:
            midpoint = statistics.median(value for _, value in snapshot_scores)
            for candidate, value in snapshot_scores:
                candidate["later_outperformer"] = value > midpoint

    def _rollback(self, metrics: dict[str, Any], previous: dict[str, Any] | None) -> bool:
        if not previous:
            return False
        baseline = previous.get("metrics", {})
        return bool(
            float(baseline.get("ndcg_at_k", 0)) - float(metrics["ndcg_at_k"])
            >= self.settings.evaluation_rollback_ndcg_drop
            or float(baseline.get("precision_at_k", 0))
            - float(metrics["precision_at_k"])
            >= self.settings.evaluation_rollback_precision_drop
            or float(metrics["brier"]) - float(baseline.get("brier", 1))
            >= self.settings.evaluation_rollback_brier_increase
        )


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(math.ceil((len(ordered) - 1) * quantile), len(ordered) - 1)
    return ordered[index]
