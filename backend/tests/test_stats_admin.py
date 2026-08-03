from types import SimpleNamespace

import pytest
from fakes import FakeDatabase

from app.api.routes.admin_stats import _jobs_by_state, overview, recent_jobs
from app.api.statistics import compute_pipeline_stats
from app.infrastructure.resources import utcnow


def _seed() -> FakeDatabase:
    db = FakeDatabase()
    db.users.docs.extend(
        [
            {"email": "a@x.io", "role": "admin"},
            {"email": "b@x.io", "role": "user"},
        ]
    )
    db.instagram_connections.docs.extend(
        [
            {"user_id": 1, "status": "ready"},
            {"user_id": 2, "status": "needs_reauth"},
        ]
    )
    db.trend_content.docs.extend(
        [
            {"processing_status": "discovered"},
            {"processing_status": "enriched"},
            {"processing_status": "embedded"},
            {"processing_status": "embedded"},
            {"processing_status": "failed"},
        ]
    )
    db.user_content.docs.append({"user_id": 1})
    db.user_profiles.docs.append({"user_id": 1})
    db.recommendations.docs.append({"user_id": 1, "created_at": utcnow()})
    db.job_runs.docs.extend(
        [
            {"task_id": "j1", "kind": "scrape", "state": "succeeded", "created_at": utcnow()},
            {"task_id": "j2", "kind": "enrich", "state": "failed", "created_at": utcnow()},
            {"task_id": "j3", "kind": "embed", "state": "running", "created_at": utcnow()},
        ]
    )
    return db


def _request(db: FakeDatabase) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(resources=SimpleNamespace(db=db)))
    )


@pytest.mark.asyncio
async def test_pipeline_stats_counts_by_status() -> None:
    stats = await compute_pipeline_stats(_seed())
    assert stats.discovered == 1
    assert stats.enriched == 1
    assert stats.embedded == 2
    assert stats.failed == 1


@pytest.mark.asyncio
async def test_jobs_by_state_groups_states() -> None:
    counts = await _jobs_by_state(_seed())
    assert counts == {"succeeded": 1, "failed": 1, "running": 1}


@pytest.mark.asyncio
async def test_overview_aggregates_everything() -> None:
    db = _seed()
    result = await overview(_request(db), {})
    assert result.total_users == 2
    assert result.admin_users == 1
    assert result.connected_instagram == 2
    assert result.needs_reauth == 1
    assert result.user_profiles_ready == 1
    assert result.recommendation_batches == 1
    assert result.attention_jobs == 1  # one failed job


@pytest.mark.asyncio
async def test_recent_jobs_filters_by_state() -> None:
    db = _seed()
    failed = await recent_jobs(_request(db), {}, limit=10, state="failed", kind=None)
    assert [job.id for job in failed] == ["j2"]
    everything = await recent_jobs(_request(db), {}, limit=10, state=None, kind=None)
    assert len(everything) == 3


@pytest.mark.asyncio
async def test_recent_jobs_falls_back_when_created_at_missing() -> None:
    db = FakeDatabase()
    started = utcnow()
    db.job_runs.docs.append(
        {
            "task_id": "legacy",
            "kind": "scrape",
            "state": "running",
            "started_at": started,
        }
    )
    jobs = await recent_jobs(_request(db), {}, limit=10, state=None, kind=None)
    assert len(jobs) == 1
    assert jobs[0].created_at == started


@pytest.mark.asyncio
async def test_recent_jobs_drops_non_integer_counter_values() -> None:
    db = FakeDatabase()
    db.job_runs.docs.append(
        {
            "task_id": "legacy-token-preview",
            "kind": "meta_trend_token_refresh",
            "state": "succeeded",
            "counters": {"refreshed": True, "token_preview": "EAASdc6w..."},
            "created_at": utcnow(),
        }
    )
    jobs = await recent_jobs(_request(db), {}, limit=10, state=None, kind=None)
    assert len(jobs) == 1
    assert jobs[0].counters == {"refreshed": 1}
    assert "token_preview" not in jobs[0].counters
