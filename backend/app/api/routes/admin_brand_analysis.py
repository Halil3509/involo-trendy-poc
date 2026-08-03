"""Admin endpoints for Instagram brand reference analysis."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote, urlparse
from uuid import uuid4

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from starlette.websockets import WebSocketState

from app.api.dependencies import AdminUser, authorize_admin_websocket, resources
from app.api.responses import job_response
from app.core.config import Settings
from app.infrastructure.log_bus import JobLogBus
from app.infrastructure.resources import utcnow
from app.providers.brand_pdf import BrandPdfProviderError, build_brand_analysis_pdf_provider
from app.schemas.brand_analysis import BrandAnalysisRequest
from app.schemas.jobs import JobResponse
from app.tasks import analyze_brand

router = APIRouter(prefix="/admin/brand-analysis", tags=["admin", "brand-analysis"])


@router.post(
    "/runs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_brand_analysis(
    request: Request,
    payload: BrandAnalysisRequest,
    _: AdminUser,
) -> JobResponse:
    now = utcnow()
    task_id = uuid4().hex
    document: dict[str, Any] = {
        "task_id": task_id,
        "kind": "brand_analysis",
        "state": "queued",
        "counters": {
            "resolved": 0,
            "fetched": 0,
            "analyzed": 0,
            "failed": 0,
            "requested": payload.max_posts,
            "total": payload.max_posts,
        },
        "target_username": None,
        "requested_url": payload.username_or_url,
        "created_at": now,
    }
    await resources(request).db.job_runs.insert_one(document)
    analyze_brand.apply_async(
        args=[task_id, payload.username_or_url, payload.max_posts],
        task_id=task_id,
    )
    return job_response(document)


@router.get("/runs/{task_id}", response_model=JobResponse)
async def get_brand_analysis_run(
    task_id: str,
    request: Request,
    _: AdminUser,
) -> JobResponse:
    document = await resources(request).db.job_runs.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brand analysis job not found")
    return job_response(document)


@router.get("/runs/{task_id}/posts", response_model=list[dict[str, Any]])
async def get_brand_analysis_posts(
    task_id: str,
    request: Request,
    _: AdminUser,
) -> list[dict[str, Any]]:
    document = await resources(request).db.job_runs.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brand analysis job not found")
    cursor = resources(request).db.brand_analysis_posts.find({"job_id": task_id})
    posts = await cursor.sort("taken_at", -1).to_list(length=100)
    return [
        {**post, "_id": str(post["_id"])} if "_id" in post else dict(post)
        for post in posts
    ]


@router.get("/reports/{task_id}", response_model=dict[str, Any])
async def get_brand_analysis_report(
    task_id: str,
    request: Request,
    _: AdminUser,
) -> dict[str, Any]:
    document = await resources(request).db.job_runs.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brand analysis job not found")
    if document.get("state") not in {"succeeded", "analyzed"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Report is not ready yet",
        )
    report = await resources(request).db.brand_analysis_reports.find_one(
        {"job_id": task_id}
    )
    posts_cursor = resources(request).db.brand_analysis_posts.find({"job_id": task_id})
    post_documents = await posts_cursor.to_list(length=100)
    media_evidence: list[dict[str, Any]] = []
    for post in post_documents:
        items = post.get("media_items") or []
        if not items and post.get("media_url"):
            items = [
                {
                    "url": post["media_url"],
                    "media_type": post.get("media_type", "IMAGE"),
                    "label": post.get("shortcode", ""),
                }
            ]
        media_evidence.extend(item for item in items[:10] if isinstance(item, dict))
    media_evidence = media_evidence[:100]
    if report:
        return {
            "job_id": task_id,
            "schema_version": report.get("schema_version", "brand-analysis-report-v1"),
            "markdown_text": report.get("markdown_text", ""),
            "report_s3_key": report.get("report_s3_key"),
            "pdf_s3_key": report.get("pdf_s3_key"),
            "media_evidence": media_evidence,
            "strategic_brief": report.get("strategic_brief"),
        }
    if document.get("report_text") or document.get("report_s3_key"):
        return {
            "job_id": task_id,
            "schema_version": document.get("schema_version", "brand-analysis-report-v1"),
            "markdown_text": document.get("report_text", ""),
            "report_s3_key": document.get("report_s3_key"),
            "pdf_s3_key": document.get("pdf_s3_key"),
            "media_evidence": media_evidence,
            "strategic_brief": document.get("strategic_brief"),
        }
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")


def _pdf_filename(document: dict[str, Any]) -> str:
    username = document.get("target_username")
    if username:
        return f"{username}_marka_analizi.pdf"
    requested = document.get("requested_url") or ""
    parsed = urlparse(str(requested))
    path = parsed.path.strip("/").split("/")[-1] if parsed.path else ""
    if path:
        return f"{path}_marka_analizi.pdf"
    return f"{document['task_id']}_marka_analizi.pdf"


def _pdf_response(pdf_bytes: bytes, document: dict[str, Any]) -> Response:
    filename = _pdf_filename(document)
    encoded = quote(filename, safe="")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        },
    )


@router.get("/reports/{task_id}/pdf")
async def export_brand_analysis_pdf(
    task_id: str,
    request: Request,
    _: AdminUser,
) -> Response:
    document = await resources(request).db.job_runs.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brand analysis job not found")
    if document.get("state") not in {"succeeded", "analyzed"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Report is not ready yet")

    report = await resources(request).db.brand_analysis_reports.find_one({"job_id": task_id})
    markdown_text = ""
    if report:
        markdown_text = report.get("markdown_text", "")

    settings = cast(Settings, request.app.state.settings)
    pdf_provider = build_brand_analysis_pdf_provider(settings)

    if report and report.get("pdf_s3_key"):
        try:
            cached_bytes = await pdf_provider.download(report["pdf_s3_key"])
        except BrandPdfProviderError:
            pass
        else:
            return _pdf_response(cached_bytes, document)

    if not markdown_text and document.get("report_text"):
        markdown_text = document["report_text"]
    if not markdown_text:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report content not found")

    try:
        pdf = await pdf_provider.export(
            task_id,
            markdown_text,
            document.get("target_username") or document.get("requested_url") or task_id,
        )
    except BrandPdfProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if report:
        await resources(request).db.brand_analysis_reports.update_one(
            {"job_id": task_id},
            {"$set": {"pdf_s3_key": pdf.pdf_s3_key}},
        )
    else:
        await resources(request).db.job_runs.update_one(
            {"task_id": task_id},
            {"$set": {"pdf_s3_key": pdf.pdf_s3_key}},
        )

    return _pdf_response(pdf.pdf_bytes, document)


# 1008 == policy violation (RFC 6455); used when auth fails on a WebSocket.
POLICY_VIOLATION = 1008


@router.websocket("/runs/{task_id}/logs")
async def brand_analysis_logs(websocket: WebSocket, task_id: str) -> None:
    """Stream live log events for a brand analysis run to authenticated admins."""
    try:
        if not await authorize_admin_websocket(websocket):
            await websocket.close(code=POLICY_VIOLATION)
            return

        await websocket.accept()
        settings = cast(Settings, websocket.app.state.settings)
        ws_resources = websocket.app.state.resources
        bus = JobLogBus(
            ws_resources.redis,
            max_lines=settings.scraper_log_max_lines,
            ttl_seconds=settings.scraper_log_ttl_seconds,
        )

        history = await bus.history(task_id)
        for event in history:
            await websocket.send_json(event)
        if history and history[-1].get("terminal"):
            await websocket.close()
            return

        async for event in bus.subscribe(task_id):
            await websocket.send_json(event)
            if event.get("terminal"):
                break
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except RuntimeError:
                pass
