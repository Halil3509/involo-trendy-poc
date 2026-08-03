from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.tasks as compatibility
from app.core.config import Settings
from app.core.errors import TransientError
from app.workers.celery_app import celery_app
from app.workers.scheduler import scheduled_dispatch
from app.workers.tasks.creator_tracking import _service as creator_tracking_service
from app.workers.tasks.profiling import profile_all_users, profile_user
from app.workers.tasks.trends import (
    _scrape,
    embed_trend_content,
    enrich_trend_content,
    run_pipeline,
    scrape_instagram,
)


def test_compatibility_module_exports_worker_tasks() -> None:
    assert compatibility.celery_app is celery_app
    assert compatibility.scrape_instagram is scrape_instagram
    assert compatibility.enrich_trend_content is enrich_trend_content
    assert compatibility.embed_trend_content is embed_trend_content
    assert compatibility.run_pipeline is run_pipeline
    assert compatibility.profile_user is profile_user
    assert compatibility.profile_all_users is profile_all_users
    assert compatibility.scheduled_dispatch is scheduled_dispatch


def test_worker_tasks_keep_legacy_registration_names() -> None:
    expected = {
        "app.tasks.scrape_instagram": scrape_instagram,
        "app.tasks.enrich_trend_content": enrich_trend_content,
        "app.tasks.embed_trend_content": embed_trend_content,
        "app.tasks.run_pipeline": run_pipeline,
        "app.tasks.profile_user": profile_user,
        "app.tasks.profile_all_users": profile_all_users,
        "app.tasks.scheduled_dispatch": scheduled_dispatch,
    }

    assert {task.name: task for task in expected.values()} == expected
    assert {name: celery_app.tasks[name] for name in expected} == expected
    assert celery_app.conf.beat_schedule["pipeline-scheduler"] == {
        "task": "app.tasks.scheduled_dispatch",
        "schedule": 60.0,
    }


def test_scrape_instagram_retries_transient_errors() -> None:
    assert TransientError in scrape_instagram.autoretry_for


@pytest.mark.asyncio
async def test_scrape_uses_headless_from_saved_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, bool] = {}

    def fake_build_scraper(
        settings_obj: Settings, *, access_token: Any = None, **kwargs: Any
    ) -> AsyncMock:
        captured["headless"] = settings_obj.scraper_headless
        return AsyncMock()

    class FakeScraperService:
        def __init__(self, db: object, adapter: object) -> None:
            pass

        async def run(
            self,
            keywords: list[str],
            limit: int,
            emit: object,
            *,
            job_id: str | None = None,
        ) -> dict[str, int]:
            return {"discovered": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(
        "app.workers.tasks.trends.settings",
        Settings(scraper_headless=True, scraper_adapter="instagram"),
    )
    monkeypatch.setattr("app.workers.tasks.trends.build_scraper", fake_build_scraper)
    monkeypatch.setattr("app.workers.tasks.trends.ScraperService", FakeScraperService)

    resources = SimpleNamespace(
        db=SimpleNamespace(
            scraper_config=AsyncMock(
                find_one=AsyncMock(
                    return_value={
                        "key": "default",
                        "headless": False,
                        "reels_per_keyword": 7,
                    }
                )
            )
        ),
        redis=None,
    )

    result = await _scrape(resources, ["fashion"], "task-1")

    assert result == {"discovered": 0, "inserted": 0, "updated": 0}
    assert captured["headless"] is False


@pytest.mark.asyncio
async def test_creator_tracking_service_playwright_uses_no_meta_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls: list[tuple[Settings, str | None]] = []

    def fake_build_provider(
        settings_obj: Settings, *, access_token: Any = None, **kwargs: Any
    ) -> AsyncMock:
        provider_calls.append((settings_obj, access_token))
        return AsyncMock()

    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.build_creator_profile_provider",
        fake_build_provider,
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.build_meta_token_service",
        lambda *args, **kwargs: AsyncMock(
            get_valid_token=AsyncMock(
                side_effect=AssertionError("Meta token should not be requested")
            )
        ),
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.build_media_provider",
        lambda *args, **kwargs: AsyncMock(),
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.build_vision_provider",
        lambda *args, **kwargs: AsyncMock(),
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.build_embedding_provider",
        lambda *args, **kwargs: AsyncMock(),
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.build_transcription_provider",
        lambda *args, **kwargs: AsyncMock(),
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.build_profile_summary_provider",
        lambda *args, **kwargs: AsyncMock(),
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.MultimodalService",
        lambda *args, **kwargs: AsyncMock(),
    )
    monkeypatch.setattr(
        "app.workers.tasks.creator_tracking.CreatorTrackingService",
        lambda *args, **kwargs: AsyncMock(),
    )

    resources = SimpleNamespace(
        db=SimpleNamespace(
            creator_snapshots=AsyncMock(),
            creator_content=AsyncMock(),
            creator_profiles=AsyncMock(),
            tracked_creators=AsyncMock(),
            trend_content=AsyncMock(),
            user_tracked_creators=AsyncMock(),
        ),
        qdrant=AsyncMock(),
        redis=None,
        settings=Settings(creator_tracking_provider="playwright"),
    )

    await creator_tracking_service(resources, "task-1")

    assert len(provider_calls) == 1
    assert provider_calls[0][1] is None
