from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import AdminUser, resources, settings
from app.api.responses import job_response
from app.infrastructure.resources import utcnow
from app.schemas.jobs import JobResponse
from app.schemas.trends import ScraperConfig, ScraperRunRequest
from app.tasks import scrape_instagram

router = APIRouter(prefix="/admin/scraper", tags=["admin", "scraper"])



def _default_config(request: Request) -> ScraperConfig:
    return ScraperConfig()


async def _load_config(request: Request) -> ScraperConfig:
    document = await resources(request).db.scraper_config.find_one({"key": "default"})
    if not document:
        return _default_config(request)
    return ScraperConfig.model_validate(document)


@router.get("/config", response_model=ScraperConfig)
async def get_config(request: Request, _: AdminUser) -> ScraperConfig:
    return await _load_config(request)


@router.put("/config", response_model=ScraperConfig)
async def update_config(payload: ScraperConfig, request: Request, _: AdminUser) -> ScraperConfig:
    if len(payload.keywords) > settings(request).scraper_max_keywords:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"At most {settings(request).scraper_max_keywords} keywords are allowed",
        )
    await resources(request).db.scraper_config.update_one(
        {"key": "default"},
        {"$set": {**payload.model_dump(), "updated_at": utcnow()}},
        upsert=True,
    )
    return payload


@router.post("/runs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_scraper(payload: ScraperRunRequest, request: Request, _: AdminUser) -> JobResponse:
    config = await _load_config(request)
    if not config.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Scraper is disabled")
    keywords = (
        ScraperConfig(keywords=payload.keywords).keywords
        if payload.keywords is not None
        else config.keywords
    )
    if not keywords:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "At least one keyword is required"
        )
    if len(keywords) > settings(request).scraper_max_keywords:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Too many keywords")
    now = utcnow()
    task_id = uuid4().hex
    document = {
        "task_id": task_id,
        "kind": "scrape",
        "state": "queued",
        "counters": {},
        "created_at": now,
        "keywords": keywords,
    }
    await resources(request).db.job_runs.insert_one(document)
    scrape_instagram.apply_async(args=[keywords], task_id=task_id)
    return job_response(document)


@router.get("/runs/latest", response_model=JobResponse)
async def latest_job(request: Request, _: AdminUser) -> JobResponse:
    document = await resources(request).db.job_runs.find_one(
        {"kind": "scrape"}, sort=[("created_at", -1)]
    )
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No scraper jobs found")
    return job_response(document)


@router.get("/runs/{task_id}", response_model=JobResponse)
async def get_job(task_id: str, request: Request, _: AdminUser) -> JobResponse:
    document = await resources(request).db.job_runs.find_one({"task_id": task_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scraper job not found")
    return job_response(document)
