from datetime import UTC, datetime

import pytest
from fakes import FakeDatabase

from app.core.config import Settings
from app.services.evaluation import OfflineEvaluationService, evaluate_rankings

RANKINGS = [
    [
        {"rank": 1, "probability": 0.8, "later_outperformer": True},
        {"rank": 2, "probability": 0.2, "later_outperformer": False},
    ],
    [
        {"rank": 1, "probability": 0.7, "later_outperformer": False},
        {"rank": 2, "probability": 0.6, "later_outperformer": True},
    ],
]


def test_offline_ranking_metrics_are_deterministic() -> None:
    metrics = evaluate_rankings(RANKINGS, k=1)

    assert metrics["ndcg_at_k"] == pytest.approx(0.5)
    assert metrics["precision_at_k"] == pytest.approx(0.5)
    assert metrics["brier"] == pytest.approx(0.1825)
    assert sum(bucket["count"] for bucket in metrics["reliability_buckets"]) == 4


@pytest.mark.asyncio
async def test_evaluation_run_persists_threshold_decision() -> None:
    db = FakeDatabase()
    cutoff = datetime(2026, 7, 17, tzinfo=UTC)
    for index, candidates in enumerate(RANKINGS):
        db.ranking_predictions.docs.append(
            {
                "model_version": "retrieval-v2",
                "predicted_at": datetime(2026, 7, 1, tzinfo=UTC),
                "candidates": candidates,
                "latency_seconds": 0.2 + index,
                "estimated_cost": 0.01,
            }
        )
    settings = Settings(
        evaluation_min_ndcg_at_k=0.4,
        evaluation_min_precision_at_k=0.4,
        evaluation_max_brier=0.2,
    )

    run = await OfflineEvaluationService(db, settings).run(
        model_version="retrieval-v2", data_cutoff=cutoff, k=1
    )

    assert run["sample_size"] == 2
    assert run["passed"] is True
    assert run["rollback_recommended"] is False
    assert db.evaluation_runs.docs[0]["evaluation_version"] == "offline-ranking-v1"


@pytest.mark.asyncio
async def test_later_snapshot_views_create_outperformer_labels() -> None:
    db = FakeDatabase()
    predicted_at = datetime(2026, 7, 1, tzinfo=UTC)
    cutoff = datetime(2026, 7, 17, tzinfo=UTC)
    db.trend_content.docs.extend([{"_id": "a"}, {"_id": "b"}])
    db.ranking_predictions.docs.append(
        {
            "model_version": "retrieval-v2",
            "predicted_at": predicted_at,
            "candidates": [
                {"trend_id": "a", "rank": 1, "probability": 0.9},
                {"trend_id": "b", "rank": 2, "probability": 0.1},
            ],
        }
    )
    db.content_metric_snapshots.docs.extend(
        [
            {
                "subject_type": "trend_content",
                "subject_id": "a",
                "captured_at": cutoff,
                "metrics": {"views": 100},
            },
            {
                "subject_type": "trend_content",
                "subject_id": "b",
                "captured_at": cutoff,
                "metrics": {"views": 10},
            },
        ]
    )

    run = await OfflineEvaluationService(db, Settings()).run(
        model_version="retrieval-v2", data_cutoff=cutoff, k=1
    )

    assert run["metrics"]["ndcg_at_k"] == 1
    assert run["metrics"]["precision_at_k"] == 1
