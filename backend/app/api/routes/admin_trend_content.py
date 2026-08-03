"""Admin API for inspecting individual trend-content records."""

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.dependencies import AdminUser, resources
from app.schemas.trends import TrendContentDetail, TrendContentListResponse

router = APIRouter(prefix="/admin/trend-content", tags=["admin", "trend-content"])


ALLOWED_SORT_FIELDS = {
    "_id",
    "created_at",
    "updated_at",
    "first_seen_at",
    "last_seen_at",
    "enriched_at",
    "embedded_at",
    "taken_at",
    "viral_score",
}


def _normalize(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB trend_content document for Pydantic validation."""
    result = dict(doc)
    if "_id" in result:
        result["id"] = str(result.pop("_id"))
    return result


def _build_query(
    *,
    status: str | None,
    job_id: str | None,
    action: str | None,
    keyword: str | None,
    search: str | None,
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = []

    if status is not None:
        filters.append({"processing_status": status})
    if job_id is not None:
        filters.append({"last_scrape_job_id": job_id})
    if action is not None:
        filters.append({"last_upsert_action": action})
    if keyword is not None:
        filters.append({"discovered_keywords": keyword})
    if search is not None and search.strip():
        pattern = {"$regex": search.strip(), "$options": "i"}
        filters.append(
            {
                "$or": [
                    {"caption_text": pattern},
                    {"owner_username": pattern},
                    {"shortcode": pattern},
                    {"combined_text": pattern},
                ]
            }
        )

    if not filters:
        return {}
    if len(filters) == 1:
        return filters[0]
    return {"$and": filters}


@router.get("/", response_model=TrendContentListResponse)
async def list_trend_content(
    request: Request,
    _: AdminUser,
    status: str | None = Query(default=None),
    job_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort: str = Query(default="-created_at"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TrendContentListResponse:
    db = resources(request).db

    if sort.startswith("-"):
        sort_field = sort[1:]
        sort_direction = -1
    else:
        sort_field = sort
        sort_direction = -1

    if sort_field not in ALLOWED_SORT_FIELDS:
        sort_field = "updated_at"

    query = _build_query(
        status=status, job_id=job_id, action=action, keyword=keyword, search=search
    )
    cursor = (
        db.trend_content.find(query)
        .sort(sort_field, sort_direction)
        .skip(offset)
        .limit(limit)
    )
    items = [TrendContentDetail.model_validate(_normalize(doc)) async for doc in cursor]
    total = await db.trend_content.count_documents(query)

    return TrendContentListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{content_id}", response_model=TrendContentDetail)
@router.get("/{content_id}/", response_model=TrendContentDetail)
async def get_trend_content(
    content_id: str,
    request: Request,
    _: AdminUser,
) -> TrendContentDetail:
    try:
        obj_id = ObjectId(content_id)
    except (InvalidId, ValueError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Trend content not found"
        ) from exc

    document = await resources(request).db.trend_content.find_one({"_id": obj_id})
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trend content not found")

    return TrendContentDetail.model_validate(_normalize(document))
