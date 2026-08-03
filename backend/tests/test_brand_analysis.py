"""Tests for the Instagram brand reference analysis module."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fakes import FakeDatabase
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from provider_doubles import (
    FakeBrandAnalysisPdfProvider,
    FakeBrandAnalysisProvider,
    FakeBrandAnalysisReportProvider,
    FakeBrandCaptionAnalyzer,
    FakeMediaProvider,
    FakeVisionProvider,
)
from pydantic import ValidationError

from app.api.dependencies import require_admin, require_user
from app.api.routes import admin_brand_analysis, admin_stats
from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.providers import brand_pdf
from app.providers.brand_analysis import (
    BrandAnalysisError,
    GraphBrandAnalysisProvider,
    _extract_username,
    _post_from_row,
)
from app.providers.brand_pdf import PlaywrightBrandAnalysisPdfProvider
from app.schemas.brand_analysis import (
    BrandAnalysisReportContext,
    BrandAnalysisRequest,
    MediaEvidence,
    PostSummary,
)
from app.services.brand_analysis import BrandAnalysisService
from app.workers.runtime import cancel_key


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.lists: dict[str, list[str]] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.data else 0

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                count += 1
        return count

    async def set(self, key: str, value: Any, *, ex: int | None = None) -> None:
        self.data[key] = value

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> None:
        stop = None if end == -1 else end + 1
        self.lists[key] = self.lists.get(key, [])[start:stop]

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def publish(self, channel: str, data: str) -> int:
        return 1

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        stop = None if end == -1 else end + 1
        return self.lists.get(key, [])[start:stop]

    async def aclose(self) -> None:
        pass

    async def ping(self) -> bool:
        return True


class FakeResources:
    def __init__(self) -> None:
        self.settings = Settings()
        self.db = FakeDatabase()
        self.redis = FakeRedis()

    async def ready(self) -> dict[str, Any]:
        return {}

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _request() -> MagicMock:
    request = MagicMock()
    request.app.state.resources = FakeResources()
    request.app.state.settings = Settings()
    return request


def _build_service(db: FakeDatabase, provider: FakeBrandAnalysisProvider) -> BrandAnalysisService:
    settings = Settings()
    return BrandAnalysisService(
        db,
        settings,
        provider,
        FakeMediaProvider(),
        FakeVisionProvider(),
        FakeBrandCaptionAnalyzer(),
        FakeBrandAnalysisReportProvider(),
    )


def test_extract_username_variants() -> None:
    assert _extract_username("https://www.instagram.com/markaadi/") == "markaadi"
    assert _extract_username("https://instagram.com/markaadi") == "markaadi"
    assert _extract_username("@markaadi") == "markaadi"
    assert _extract_username("markaadi") == "markaadi"
    assert _extract_username("") is None
    assert _extract_username("https://www.instagram.com/") is None


@pytest.mark.anyio
async def test_service_resolves_and_persists_posts() -> None:
    db = FakeDatabase()
    provider = FakeBrandAnalysisProvider()
    service = _build_service(db, provider)
    logs: list[str] = []

    async def emit(message: str, **kwargs: Any) -> None:
        logs.append(message)

    counters = await service.run("job_1", "https://instagram.com/markaadi/", 2, emit=emit)

    assert counters["resolved"] == 1
    assert counters["fetched"] == 2
    assert counters["failed"] == 0
    assert any("@markaadi" in log for log in logs)
    assert any("2 gönderi bulundu" in log for log in logs)
    assert len(db.brand_analysis_posts.docs) == 2
    assert db.brand_analysis_posts.docs[0]["job_id"] == "job_1"


@pytest.mark.anyio
async def test_service_is_idempotent_across_runs() -> None:
    db = FakeDatabase()
    provider = FakeBrandAnalysisProvider()
    service = _build_service(db, provider)

    await service.run("job_1", "markaadi", 2)
    first_count = len(db.brand_analysis_posts.docs)
    await service.run("job_1", "markaadi", 2)
    assert len(db.brand_analysis_posts.docs) == first_count


@pytest.mark.anyio
async def test_service_cancels_during_post_analysis() -> None:
    db = FakeDatabase()
    provider = FakeBrandAnalysisProvider()
    cancel_calls = 0

    async def is_cancelled() -> bool:
        nonlocal cancel_calls
        cancel_calls += 1
        return cancel_calls >= 3

    report_provider = FakeBrandAnalysisReportProvider()
    generate_called = False
    original_generate = report_provider.generate

    async def tracked_generate(context: Any) -> Any:
        nonlocal generate_called
        generate_called = True
        return await original_generate(context)

    report_provider.generate = tracked_generate  # type: ignore[method-assign]

    service = BrandAnalysisService(
        db,
        Settings(),
        provider,
        FakeMediaProvider(),
        FakeVisionProvider(),
        FakeBrandCaptionAnalyzer(),
        report_provider,
        is_cancelled=is_cancelled,
    )
    logs: list[str] = []

    async def emit(message: str, **kwargs: Any) -> None:
        logs.append(message)

    with pytest.raises(asyncio.CancelledError):
        await service.run("job_1", "markaadi", 3, emit=emit)

    job = db.job_runs.docs[0]
    assert job["task_id"] == "job_1"
    assert job["state"] == "cancelled"
    assert any("durduruldu" in log.lower() for log in logs)
    assert not generate_called


@pytest.mark.anyio
async def test_graph_provider_resolve_username() -> None:
    provider = GraphBrandAnalysisProvider(Settings(), "test_token")
    assert await provider.resolve_username("https://www.instagram.com/markaadi/") == "markaadi"
    with pytest.raises(BrandAnalysisError):
        await provider.resolve_username("")


@pytest.mark.anyio
async def test_graph_provider_resolve_account_id_uses_business_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GraphBrandAnalysisProvider(
        Settings(),
        "test_token",
        business_account_id="178414000000000",
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_get(client: Any, path: str, *, params: Any = None) -> dict[str, Any]:
        del client
        calls.append((path, params or {}))
        return {
            "business_discovery": {
                "id": "178414111111111",
                "username": "caudalie",
            }
        }

    provider._get = fake_get  # type: ignore[method-assign]

    account_id = await provider._resolve_account_id("caudalie")

    assert account_id == "178414111111111"
    assert len(calls) == 1
    assert calls[0][0] == "/178414000000000"
    assert "business_discovery.username(caudalie)" in calls[0][1].get("fields", "")


@pytest.mark.anyio
async def test_graph_provider_fetch_posts_uses_business_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GraphBrandAnalysisProvider(
        Settings(),
        "test_token",
        business_account_id="178414000000000",
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    media_row = {
        "id": "post_1",
        "caption": "Caudalie post",
        "media_type": "IMAGE",
        "media_url": "https://example.com/post.jpg",
        "permalink": "https://www.instagram.com/p/ABC123/",
        "timestamp": "2024-01-01T12:00:00+0000",
        "username": "caudalie",
        "like_count": 42,
        "comments_count": 5,
    }

    async def fake_get(client: Any, path: str, *, params: Any = None) -> dict[str, Any]:
        del client
        calls.append((path, params or {}))
        # First request returns two rows and a cursor; second returns one row.
        if len(calls) == 1:
            return {
                "business_discovery": {
                    "media": {
                        "data": [media_row, media_row],
                        "paging": {"cursors": {"after": "cursor_1"}},
                    }
                }
            }
        return {
            "business_discovery": {
                "media": {
                    "data": [media_row],
                    "paging": {"cursors": {}},
                }
            }
        }

    provider._get = fake_get  # type: ignore[method-assign]

    posts = await provider._fetch_media("unused", "caudalie", 3, job_id="job_1")

    assert len(posts) == 3
    assert all(post.job_id == "job_1" for post in posts)
    assert calls[0][0] == "/178414000000000"
    first_fields = calls[0][1].get("fields", "")
    assert "business_discovery.username(caudalie)" in first_fields
    assert "media.limit(3)" in first_fields
    second_fields = calls[1][1].get("fields", "")
    assert "media.after(cursor_1).limit(1)" in second_fields


def _extract_children_subquery(fields: str) -> str:
    """Return the contents of the first children{...} block, or empty string."""
    start = fields.find("children{")
    if start == -1:
        return ""
    inner_start = start + len("children{")
    depth = 1
    for index, char in enumerate(fields[inner_start:], start=inner_start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return fields[inner_start:index]
    return ""


@pytest.mark.anyio
async def test_graph_provider_carousel_children_request_omits_invalid_fields() -> None:
    provider = GraphBrandAnalysisProvider(
        Settings(),
        "test_token",
        business_account_id="178414000000000",
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_get(client: Any, path: str, *, params: Any = None) -> dict[str, Any]:
        del client
        calls.append((path, params or {}))
        if path == "/178414000000000":
            return {
                "business_discovery": {
                    "media": {
                        "data": [
                            {
                                "id": "carousel_1",
                                "caption": "Carousel post",
                                "media_type": "CAROUSEL_ALBUM",
                                "permalink": "https://www.instagram.com/p/CAR123/",
                                "timestamp": "2024-01-01T12:00:00+0000",
                                "username": "caudalie",
                                "like_count": 10,
                                "comments_count": 2,
                            }
                        ],
                        "paging": {"cursors": {}},
                    }
                }
            }
        if path == "/carousel_1/children":
            return {
                "data": [
                    {
                        "id": "child_1",
                        "media_type": "IMAGE",
                        "media_url": "https://example.com/child1.jpg",
                    }
                ]
            }
        return {}

    provider._get = fake_get  # type: ignore[method-assign]

    posts = await provider._fetch_media("unused", "caudalie", 1, job_id="job_1")

    assert len(posts) == 1
    assert posts[0].media_url == "https://example.com/child1.jpg"

    first_fields = calls[0][1].get("fields", "")
    children_subquery = _extract_children_subquery(first_fields)
    assert children_subquery
    assert "permalink" not in children_subquery
    assert "media_product_type" not in children_subquery
    assert set(children_subquery.split(",")) == {"id", "media_url", "thumbnail_url", "media_type"}

    children_call = next(c for c in calls if c[0] == "/carousel_1/children")
    fallback_fields = children_call[1].get("fields", "")
    assert "permalink" not in fallback_fields
    assert "media_product_type" not in fallback_fields
    assert set(fallback_fields.split(",")) == {"id", "media_url", "thumbnail_url", "media_type"}


@pytest.mark.anyio
async def test_post_from_row_handles_permalink_shortcode() -> None:
    row = {
        "id": "123456789",
        "caption": "Test caption",
        "media_type": "IMAGE",
        "media_url": "https://example.com/image.jpg",
        "permalink": "https://www.instagram.com/p/ABC123/",
        "timestamp": "2024-01-01T12:00:00+0000",
        "like_count": 10,
        "comments_count": 3,
    }
    post = _post_from_row(row, job_id="job_1")
    assert post.shortcode == "ABC123"
    assert post.post_id == "123456789"
    assert post.caption == "Test caption"
    assert post.media_items
    assert post.media_items[0].url == "https://example.com/image.jpg"
    assert post.media_items[0].media_type == "IMAGE"


@pytest.mark.anyio
async def test_service_updates_job_target_username() -> None:
    db = FakeDatabase()
    provider = FakeBrandAnalysisProvider()
    service = _build_service(db, provider)

    await service.run("job_1", "markaadi", 2)

    job = db.job_runs.docs[0]
    assert job["target_username"] == "markaadi"


def test_brand_analysis_request_validation() -> None:
    req = BrandAnalysisRequest(username_or_url="markaadi", max_posts=10)
    assert req.username_or_url == "markaadi"
    assert req.max_posts == 10

    with pytest.raises(ValueError):
        BrandAnalysisRequest(username_or_url="markaadi", max_posts=50)


class FakeAsyncResult:
    id = "task_123"


@pytest.mark.anyio
async def test_brand_analysis_route_returns_202(monkeypatch: Any) -> None:
    request = _request()
    monkeypatch.setattr(
        admin_brand_analysis.analyze_brand,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )
    payload = BrandAnalysisRequest(username_or_url="https://www.instagram.com/markaadi/")
    response = await admin_brand_analysis.start_brand_analysis(request, payload, {})
    assert response.kind == "brand_analysis"
    assert response.state == "queued"
    assert response.id


@pytest.mark.anyio
async def test_brand_analysis_run_get_not_found() -> None:
    request = _request()
    with pytest.raises(HTTPException) as exc_info:
        await admin_brand_analysis.get_brand_analysis_run("not_a_job", request, {})
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_process_posts_fake_vision_and_caption() -> None:
    db = FakeDatabase()
    provider = FakeBrandAnalysisProvider()
    service = _build_service(db, provider)

    await service.run("job_3", "markaadi", 2)

    posts = db.brand_analysis_posts.docs
    assert len(posts) == 2
    for post in posts:
        assert post.get("media_s3_key")
        assert post.get("visual_analysis")
        assert post.get("caption_analysis")
        assert post.get("analyzed_at")
        items = post.get("media_items") or []
        assert items
        assert any(str(item.get("url", "")).startswith("https://") for item in items)


@pytest.mark.anyio
async def test_generate_report_fake_bedrock() -> None:
    db = FakeDatabase()
    provider = FakeBrandAnalysisProvider()
    service = _build_service(db, provider)

    await service.run("job_4", "markaadi", 2)

    job = db.job_runs.docs[0]
    assert job["state"] == "analyzed"
    assert job.get("report_s3_key")
    assert job.get("report_text")
    report = db.brand_analysis_reports.docs[0]
    assert report["job_id"] == "job_4"
    assert report["markdown_text"]
    for heading in [
        "Yönetici Özeti",
        "Marka Dünyası",
        "Hizmet Verilen Marka Neden Beğenebilir?",
        "Kanıt Zincirleri",
        "İçerik Reçetesi",
        "Performans Kanıtları",
        "Stratejik Kararlar",
        "Ek — Referans Gönderi Galerisi",
    ]:
        assert heading in report["markdown_text"]
    assert "![" in report["markdown_text"]
    assert "https://" in report["markdown_text"]
    assert report.get("strategic_brief")
    brief = report["strategic_brief"]
    assert brief["executive_answer"]
    assert brief["success_dna"]
    assert brief["success_dna"]["desire"]
    assert brief["success_dna"]["proof"]
    assert brief["success_dna"]["lifestyle"]
    assert brief["brand_world"]
    assert brief["content_recipe"]
    assert brief["content_series"]
    assert brief["performance_summary"]
    assert brief["decisions"]
    assert 3 <= len(brief["decisions"]) <= 5
    assert "Marka Başarısı DNA'sı" in report["markdown_text"]
    assert "İçerik Serisi Mekanikleri" in report["markdown_text"]
    assert "Arzu" in report["markdown_text"]
    assert "Kanıt" in report["markdown_text"]
    assert "Yaşam Tarzı" in report["markdown_text"]

    context = service._build_report_context("job_4", "markaadi", db.brand_analysis_posts.docs)
    assert context.posting_rhythm_summary
    assert context.format_performance
    assert context.engagement_distribution
    assert context.tone_frequency
    assert context.cta_frequency
    assert context.hashtag_frequency
    assert context.emoji_frequency
    assert context.visual_signature_frequency
    assert context.brand_world
    assert context.content_recipe
    assert context.performance_summary
    assert context.semantic_observations
    # Reports are built from posts sorted by engagement proxy descending.
    assert (context.posts[0].engagement_rate or 0.0) >= (context.posts[-1].engagement_rate or 0.0)


def test_build_image_appendix_selects_top_three_by_engagement() -> None:
    from app.providers.brand_report import _build_image_appendix

    posts = [
        PostSummary(
            shortcode=f"sc{i}",
            media_type="IMAGE",
            permalink=f"https://example.com/p/sc{i}",
            media_items=[
                MediaEvidence(url=f"https://example.com/img{i}.jpg", media_type="IMAGE")
            ],
            like_count=i,
            comment_count=0,
            engagement_rate=float(i),
            confidence="medium",
        )
        for i in range(5)
    ]
    context = BrandAnalysisReportContext(
        job_id="job_test",
        target_username="testbrand",
        post_count=5,
        posts=posts,
    )
    appendix = _build_image_appendix(context)
    assert appendix.count("https://example.com/img") == 3
    assert "sc4" in appendix
    assert "sc3" in appendix
    assert "sc2" in appendix
    assert "sc0" not in appendix


@pytest.mark.anyio
async def test_get_report_returns_markdown() -> None:
    request = _request()
    job_id = "job_report_ready"
    await request.app.state.resources.db.job_runs.insert_one({
        "task_id": job_id,
        "kind": "brand_analysis",
        "state": "succeeded",
        "counters": {},
        "report_text": "# Rapor\n\nÖzet",
        "report_s3_key": "reports/brand/job_report_ready/report.md",
        "created_at": "2024-01-01T00:00:00Z",
    })
    response = await admin_brand_analysis.get_brand_analysis_report(job_id, request, {})
    assert response["job_id"] == job_id
    assert "# Rapor" in response["markdown_text"]
    assert response["report_s3_key"]


@pytest.mark.anyio
async def test_carousel_first_child_fetch() -> None:
    provider = GraphBrandAnalysisProvider(Settings(), "test_token")

    async def fake_get(client: Any, path: str, *, params: Any = None) -> dict[str, Any]:
        del client
        if path == "/carousel_1/children":
            return {
                "data": [
                    {
                        "id": "child_1",
                        "media_type": "IMAGE",
                        "media_url": "https://example.com/child.jpg",
                        "permalink": "https://www.instagram.com/p/child_shortcode/",
                    }
                ]
            }
        return {}

    provider._get = fake_get  # type: ignore[method-assign]
    row = {
        "id": "carousel_1",
        "caption": "Carousel caption",
        "media_type": "CAROUSEL_ALBUM",
        "permalink": "https://www.instagram.com/p/carousel_shortcode/",
    }
    result = await provider._expand_carousel_if_needed("client", row)  # type: ignore[arg-type]
    assert result["media_url"] == "https://example.com/child.jpg"
    assert result["media_type"] == "CAROUSEL_ALBUM"
    assert result["media_items"][0]["url"] == "https://example.com/child.jpg"
    assert result["permalink"] == "https://www.instagram.com/p/carousel_shortcode/"
    assert result["child_media_id"] == "child_1"


@pytest.mark.anyio
async def test_brand_analysis_report_not_ready() -> None:
    request = _request()
    job_id = "job_report_test"
    await request.app.state.resources.db.job_runs.insert_one({
        "task_id": job_id,
        "kind": "brand_analysis",
        "state": "running",
        "counters": {},
        "created_at": "2024-01-01T00:00:00Z",
    })
    with pytest.raises(HTTPException) as exc_info:
        await admin_brand_analysis.get_brand_analysis_report(job_id, request, {})
    assert exc_info.value.status_code == 409


@pytest.mark.anyio
async def test_brand_analysis_posts_route() -> None:
    request = _request()
    job_id = "job_posts_test"
    await request.app.state.resources.db.job_runs.insert_one({
        "task_id": job_id,
        "kind": "brand_analysis",
        "state": "succeeded",
        "counters": {},
        "created_at": "2024-01-01T00:00:00Z",
    })
    await request.app.state.resources.db.brand_analysis_posts.insert_one({
        "job_id": job_id,
        "post_id": "p1",
        "shortcode": "s1",
        "caption": "caption",
        "media_type": "IMAGE",
        "fetched_at": "2024-01-01T00:00:00Z",
    })
    posts = await admin_brand_analysis.get_brand_analysis_posts(job_id, request, {})
    assert len(posts) == 1
    assert posts[0]["post_id"] == "p1"


def test_brand_analysis_posts_route_serializes_object_id() -> None:
    client = _brand_client(admin_role="admin")
    db = client.app.state.resources.db
    job_id = "job_oid_test"
    db.job_runs.docs.append({
        "task_id": job_id,
        "kind": "brand_analysis",
        "state": "succeeded",
        "counters": {},
        "created_at": "2024-01-01T00:00:00Z",
    })
    post_oid = ObjectId()
    db.brand_analysis_posts.docs.append({
        "_id": post_oid,
        "job_id": job_id,
        "post_id": "p1",
        "shortcode": "s1",
        "caption": "caption",
        "media_type": "IMAGE",
        "fetched_at": "2024-01-01T00:00:00Z",
    })
    response = client.get(f"/api/v1/admin/brand-analysis/runs/{job_id}/posts")
    assert response.status_code == 200
    posts = response.json()
    assert len(posts) == 1
    assert posts[0]["post_id"] == "p1"
    assert posts[0]["_id"] == str(post_oid)


@pytest.mark.anyio
async def test_fake_provider_returns_posts_with_job_id() -> None:
    provider = FakeBrandAnalysisProvider()
    posts = await provider.fetch_posts("markaadi", 2, job_id="job_2")
    assert len(posts) == 2
    assert all(post.job_id == "job_2" for post in posts)


def test_brand_analysis_request_invalid_input() -> None:
    with pytest.raises(ValidationError):
        BrandAnalysisRequest(username_or_url="", max_posts=10)
    with pytest.raises(ValidationError):
        BrandAnalysisRequest(username_or_url="markaadi", max_posts=50)
    with pytest.raises(ValidationError):
        BrandAnalysisRequest(username_or_url="markaadi", max_posts=0)


def _brand_client(*, admin_role: str | None = None) -> TestClient:
    app = FastAPI()
    settings = Settings()
    settings.brand_analysis_pdf_provider = "fake"
    app.state.settings = settings
    app.state.resources = FakeResources()

    async def _require_user() -> dict[str, Any]:
        return {"role": admin_role or "user"}

    async def _require_admin() -> dict[str, Any]:
        if (admin_role or "user") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
        return {"role": admin_role or "user"}

    app.dependency_overrides[require_user] = _require_user
    app.dependency_overrides[require_admin] = _require_admin
    app.include_router(admin_brand_analysis.router, prefix="/api/v1")
    return TestClient(app)


def test_start_job_requires_admin(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        admin_brand_analysis.analyze_brand,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )
    client = _brand_client(admin_role="user")
    response = client.post(
        "/api/v1/admin/brand-analysis/runs",
        json={"username_or_url": "markaadi", "max_posts": 10},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_start_job_invalid_input(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        admin_brand_analysis.analyze_brand,
        "apply_async",
        lambda *args, **kwargs: FakeAsyncResult(),
    )
    client = _brand_client(admin_role="admin")
    response = client.post(
        "/api/v1/admin/brand-analysis/runs",
        json={"username_or_url": "markaadi", "max_posts": 50},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_export_pdf_requires_admin() -> None:
    client = _brand_client(admin_role="user")
    response = client.get("/api/v1/admin/brand-analysis/reports/job_pdf_test/pdf")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_export_pdf_not_found() -> None:
    client = _brand_client(admin_role="admin")
    response = client.get("/api/v1/admin/brand-analysis/reports/missing_job/pdf")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_export_pdf_not_ready() -> None:
    client = _brand_client(admin_role="admin")
    client.app.state.resources.db.job_runs.docs.append({
        "task_id": "job_pdf_not_ready",
        "kind": "brand_analysis",
        "state": "running",
        "counters": {},
        "created_at": "2024-01-01T00:00:00Z",
    })
    response = client.get("/api/v1/admin/brand-analysis/reports/job_pdf_not_ready/pdf")
    assert response.status_code == status.HTTP_409_CONFLICT


def test_export_pdf_report_content_not_found() -> None:
    client = _brand_client(admin_role="admin")
    client.app.state.resources.db.job_runs.docs.append({
        "task_id": "job_pdf_no_content",
        "kind": "brand_analysis",
        "state": "succeeded",
        "target_username": "markaadi",
        "counters": {},
        "created_at": "2024-01-01T00:00:00Z",
    })
    response = client.get("/api/v1/admin/brand-analysis/reports/job_pdf_no_content/pdf")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_export_pdf_returns_generated_pdf(monkeypatch: Any) -> None:
    client = _brand_client(admin_role="admin")
    fake_pdf = FakeBrandAnalysisPdfProvider(
        export_bytes=b"%PDF-1.4 generated",
        download_bytes=b"%PDF-1.4 cached",
    )
    monkeypatch.setattr(
        admin_brand_analysis,
        "build_brand_analysis_pdf_provider",
        lambda settings: fake_pdf,
    )

    job_id = "job_pdf_generate"
    client.app.state.resources.db.job_runs.docs.append({
        "task_id": job_id,
        "kind": "brand_analysis",
        "state": "succeeded",
        "target_username": "markaadi",
        "counters": {},
        "created_at": "2024-01-01T00:00:00Z",
    })
    client.app.state.resources.db.brand_analysis_reports.docs.append({
        "job_id": job_id,
        "markdown_text": "# Rapor\n\nİçerik",
        "report_s3_key": f"reports/brand/{job_id}/report.md",
    })

    response = client.get(f"/api/v1/admin/brand-analysis/reports/{job_id}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert "markaadi_marka_analizi.pdf" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.4 generated"
    assert len(fake_pdf.export_calls) == 1
    assert fake_pdf.export_calls[0][0] == job_id

    reports = client.app.state.resources.db.brand_analysis_reports.docs
    report = next(
        (d for d in reports if d["job_id"] == job_id),
        None,
    )
    assert report is not None
    assert report["pdf_s3_key"] == f"reports/brand/{job_id}/report.pdf"


def test_export_pdf_uses_cached_pdf_when_s3_key_exists(monkeypatch: Any) -> None:
    client = _brand_client(admin_role="admin")
    fake_pdf = FakeBrandAnalysisPdfProvider(
        export_bytes=b"%PDF-1.4 generated",
        download_bytes=b"%PDF-1.4 cached",
    )
    monkeypatch.setattr(
        admin_brand_analysis,
        "build_brand_analysis_pdf_provider",
        lambda settings: fake_pdf,
    )

    job_id = "job_pdf_cached"
    client.app.state.resources.db.job_runs.docs.append({
        "task_id": job_id,
        "kind": "brand_analysis",
        "state": "succeeded",
        "target_username": "markaadi",
        "counters": {},
        "created_at": "2024-01-01T00:00:00Z",
    })
    client.app.state.resources.db.brand_analysis_reports.docs.append({
        "job_id": job_id,
        "markdown_text": "# Rapor\n\nİçerik",
        "report_s3_key": f"reports/brand/{job_id}/report.md",
        "pdf_s3_key": f"reports/brand/{job_id}/report.pdf",
    })

    response = client.get(f"/api/v1/admin/brand-analysis/reports/{job_id}/pdf")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 cached"
    assert len(fake_pdf.download_calls) == 1
    assert fake_pdf.download_calls[0] == f"reports/brand/{job_id}/report.pdf"
    assert len(fake_pdf.export_calls) == 0


@pytest.mark.anyio
async def test_fetch_posts_fake_provider() -> None:
    provider = FakeBrandAnalysisProvider()
    posts = await provider.fetch_posts("markaadi", 3, job_id="job_fetch_test")
    assert len(posts) == 3
    assert all(post.job_id == "job_fetch_test" for post in posts)
    assert all(post.media_url for post in posts)


@pytest.mark.anyio
async def test_stop_job_cancels_brand_analysis(monkeypatch: Any) -> None:
    db = FakeDatabase()
    db.job_runs.docs.append(
        {
            "task_id": "brand_job_1",
            "kind": "brand_analysis",
            "state": "running",
            "created_at": utcnow(),
        }
    )
    redis = FakeRedis()
    revoke_mock = MagicMock()
    monkeypatch.setattr(
        "app.api.routes.admin_stats.celery_app.control.revoke",
        revoke_mock,
    )
    request = MagicMock()
    request.app.state.resources = MagicMock()
    request.app.state.resources.db = db
    request.app.state.resources.redis = redis

    result = await admin_stats.stop_job("brand_job_1", request, {})

    assert result.state == "cancelled"
    document = await db.job_runs.find_one({"task_id": "brand_job_1"})
    assert document is not None
    assert document["state"] == "cancelled"
    assert document["error"] == "Stopped by user"
    assert redis.data.get(cancel_key("brand_job_1")) == "1"
    revoke_mock.assert_called_once_with("brand_job_1", terminate=False)


def test_playwright_pdf_provider_falls_back_to_system_chrome(monkeypatch: Any) -> None:
    """When the bundled Chromium is missing, the provider uses a system browser."""
    settings = Settings()
    settings.brand_analysis_pdf_provider = "playwright"
    provider = PlaywrightBrandAnalysisPdfProvider(settings)

    fake_page = MagicMock()
    fake_page.set_content = AsyncMock()
    fake_page.pdf = AsyncMock(return_value=b"%PDF fake")
    fake_browser = MagicMock()
    fake_browser.new_page = AsyncMock(return_value=fake_page)
    fake_browser.close = AsyncMock()
    fake_chromium = MagicMock()
    fake_chromium.executable_path = "/nonexistent/playwright/chromium"
    fake_chromium.launch = AsyncMock(return_value=fake_browser)
    fake_playwright = MagicMock()
    fake_playwright.chromium = fake_chromium

    class FakeAsyncPlaywright:
        async def __aenter__(self) -> Any:
            return fake_playwright

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: FakeAsyncPlaywright())
    monkeypatch.setattr(brand_pdf.os.path, "exists", lambda path: False)
    def _fake_which(name: str) -> str | None:
        return "/usr/bin/google-chrome" if name == "google-chrome" else None

    monkeypatch.setattr(brand_pdf.shutil, "which", _fake_which)

    pdf_bytes = asyncio.run(provider._render_pdf("<html></html>"))

    assert pdf_bytes == b"%PDF fake"
    assert fake_chromium.launch.call_count == 1
    launch_kwargs = fake_chromium.launch.call_args.kwargs
    assert launch_kwargs.get("executable_path") == "/usr/bin/google-chrome"
