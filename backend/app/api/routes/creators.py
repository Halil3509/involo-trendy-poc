"""User-facing creator tracking endpoints.

Creator data (snapshots, content, AI profiles) is stored once globally and
shared; ``user_tracked_creators`` links control which creators each user sees.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pymongo.errors import DuplicateKeyError

from app.api.dependencies import CurrentUser, resources
from app.api.responses import job_response
from app.infrastructure.resources import utcnow
from app.providers.creator_profile import (
    CreatorNotFoundError,
    CreatorProfileError,
    build_creator_profile_provider,
)
from app.schemas.creators import (
    CreatorContentItem,
    CreatorContentResponse,
    CreatorDetailResponse,
    CreatorListResponse,
    CreatorSummary,
    FollowerHistoryResponse,
    FollowerPoint,
    TrackCreatorRequest,
)
from app.schemas.jobs import JobResponse
from app.tasks import track_creator

router = APIRouter(tags=["creators"])

_USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_INVALID_USERNAME_EDGES = re.compile(r"^[._]|\.{2}|_{2}|[._]$")
_RANGE_DAYS = {"week": 7, "month": 30, "year": 365}


def _summary(creator: dict[str, Any], added_at: Any = None) -> CreatorSummary:
    return CreatorSummary(
        id=str(creator["_id"]),
        username=creator["username"],
        display_name=creator.get("display_name", ""),
        avatar_url=creator.get("avatar_url"),
        follower_count=int(creator.get("follower_count", 0)),
        media_count=int(creator.get("media_count", 0)),
        trend_score=float(creator.get("trend_score", 0.0)),
        status=creator.get("status", "active"),
        last_tracked_at=creator.get("last_tracked_at"),
        last_error=creator.get("last_error"),
        added_at=added_at,
    )


async def _linked_creator(
    request: Request, user: dict[str, Any], creator_id: str
) -> dict[str, Any]:
    try:
        object_id = ObjectId(creator_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "creator not found") from None
    db = resources(request).db
    link = await db.user_tracked_creators.find_one(
        {"user_id": user["_id"], "creator_id": object_id}
    )
    if not link:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "creator not found")
    creator = await db.tracked_creators.find_one({"_id": object_id})
    if not creator:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "creator not found")
    return cast(dict[str, Any], creator)


def _normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not _USERNAME_RE.match(normalized) or _INVALID_USERNAME_EDGES.search(normalized):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid username")
    return normalized


@router.post("/creators", response_model=CreatorSummary, status_code=status.HTTP_201_CREATED)
async def add_creator(
    request: Request, payload: TrackCreatorRequest, user: CurrentUser
) -> CreatorSummary:
    username = _normalize_username(payload.username)
    db = resources(request).db
    settings = resources(request).settings
    now = utcnow()

    provider = build_creator_profile_provider(
        settings, redis=resources(request).redis
    )
    try:
        exists = await provider.exists(username)
    except CreatorNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Instagram user not found"
        ) from exc
    except CreatorProfileError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"cannot validate creator at this time: {exc}",
        ) from exc
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instagram user not found")
    try:
        await db.tracked_creators.update_one(
            {"username": username},
            {
                "$setOnInsert": {
                    "username": username,
                    "display_name": "",
                    "bio": "",
                    "avatar_url": None,
                    "follower_count": 0,
                    "following_count": 0,
                    "media_count": 0,
                    "trend_score": 0.0,
                    "status": "active",
                    "last_tracked_at": None,
                    "last_error": None,
                    "created_at": now,
                }
            },
            upsert=True,
        )
    except DuplicateKeyError:
        pass  # concurrent add of the same username; the creator exists
    creator = await db.tracked_creators.find_one({"username": username})
    assert creator is not None
    link = await db.user_tracked_creators.update_one(
        {"user_id": user["_id"], "creator_id": creator["_id"]},
        {"$setOnInsert": {"added_at": now}},
        upsert=True,
    )
    added_at: datetime | None
    if link.upserted_id is not None:
        await _queue_track_job(db, str(creator["_id"]))
        added_at = now
    else:
        existing_link = await db.user_tracked_creators.find_one(
            {"user_id": user["_id"], "creator_id": creator["_id"]}
        )
        added_at = (existing_link or {}).get("added_at")
    return _summary(creator, added_at)


async def _queue_track_job(db: Any, creator_id: str) -> None:
    task_id = uuid4().hex
    await db.job_runs.insert_one(
        {
            "task_id": task_id,
            "kind": "creator_track",
            "state": "queued",
            "counters": {},
            "created_at": utcnow(),
            "target_username": creator_id,
        }
    )
    track_creator.apply_async(args=[creator_id], task_id=task_id)


@router.get("/creators", response_model=CreatorListResponse)
async def list_creators(request: Request, user: CurrentUser) -> CreatorListResponse:
    db = resources(request).db
    links = await db.user_tracked_creators.find({"user_id": user["_id"]}).to_list(None)
    links_by_id = {link["creator_id"]: link for link in links}
    creators: list[CreatorSummary] = []
    for creator_id, link in links_by_id.items():
        creator = await db.tracked_creators.find_one({"_id": creator_id})
        if creator:
            creators.append(_summary(creator, link.get("added_at")))
    creators.sort(key=lambda item: item.trend_score, reverse=True)
    return CreatorListResponse(creators=creators)


@router.get("/creators/{creator_id}", response_model=CreatorDetailResponse)
async def creator_detail(
    request: Request, creator_id: str, user: CurrentUser
) -> CreatorDetailResponse:
    db = resources(request).db
    creator = await _linked_creator(request, user, creator_id)
    profile = await db.creator_profiles.find_one({"creator_id": creator["_id"]}) or {}
    summary = _summary(creator)
    return CreatorDetailResponse(
        **summary.model_dump(),
        bio=creator.get("bio", ""),
        following_count=int(creator.get("following_count", 0)),
        ai_summary=profile.get("ai_summary"),
        structured_profile=profile.get("structured_profile"),
        average_viral_score=profile.get("average_viral_score"),
        profile_updated_at=profile.get("updated_at"),
    )


@router.get("/creators/{creator_id}/followers", response_model=FollowerHistoryResponse)
async def creator_followers(
    request: Request,
    creator_id: str,
    user: CurrentUser,
    range: str = Query(default="month", pattern="^(week|month|year)$"),
) -> FollowerHistoryResponse:
    db = resources(request).db
    creator = await _linked_creator(request, user, creator_id)
    cutoff = utcnow() - timedelta(days=_RANGE_DAYS[range])
    snapshots = await db.creator_snapshots.find({"creator_id": creator["_id"]}).to_list(None)
    points = [
        FollowerPoint(
            captured_at=snap["captured_at"],
            follower_count=int(snap.get("follower_count", 0)),
        )
        for snap in sorted(snapshots, key=lambda item: item["captured_at"])
        if snap.get("captured_at") is not None and snap["captured_at"] >= cutoff
    ]
    delta = (
        points[-1].follower_count - points[0].follower_count if len(points) >= 2 else 0
    )
    return FollowerHistoryResponse(range=range, points=points, delta=delta)


@router.get("/creators/{creator_id}/content", response_model=CreatorContentResponse)
async def creator_content(
    request: Request,
    creator_id: str,
    user: CurrentUser,
    sort: str = Query(default="recent", pattern="^(recent|viral)$"),
    limit: int = Query(default=30, ge=1, le=100),
) -> CreatorContentResponse:
    db = resources(request).db
    creator = await _linked_creator(request, user, creator_id)
    docs = await db.creator_content.find({"creator_id": creator["_id"]}).to_list(None)
    key = "viral_score" if sort == "viral" else "taken_at"
    docs.sort(key=lambda doc: (doc.get(key) is not None, doc.get(key)), reverse=True)
    items = [
        CreatorContentItem(
            shortcode=doc["shortcode"],
            permalink=doc.get("permalink"),
            caption_text=doc.get("caption_text", ""),
            media_type=doc.get("media_type", "IMAGE"),
            thumbnail_url=doc.get("thumbnail_url"),
            taken_at=doc.get("taken_at"),
            like_count=int(doc.get("like_count", 0)),
            comment_count=int(doc.get("comment_count", 0)),
            view_count=int(doc.get("view_count", 0)),
            viral_score=float(doc.get("viral_score", 0.0)),
            is_new=bool(doc.get("is_new", False)),
            processing_status=doc.get("processing_status", "discovered"),
        )
        for doc in docs[:limit]
    ]
    return CreatorContentResponse(
        items=items, new_count=sum(1 for doc in docs if doc.get("is_new"))
    )


@router.post("/creators/{creator_id}/analyze", response_model=JobResponse)
async def analyze_creator(
    request: Request, creator_id: str, user: CurrentUser
) -> JobResponse:
    db = resources(request).db
    creator = await _linked_creator(request, user, creator_id)
    task_id = uuid4().hex
    document: dict[str, Any] = {
        "task_id": task_id,
        "kind": "creator_track",
        "state": "queued",
        "counters": {},
        "created_at": utcnow(),
        "target_username": creator.get("username"),
    }
    await db.job_runs.insert_one(document)
    track_creator.apply_async(args=[str(creator["_id"])], task_id=task_id)
    return job_response(document)


@router.delete("/creators/{creator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_creator(
    request: Request, creator_id: str, user: CurrentUser
) -> Response:
    db = resources(request).db
    creator = await _linked_creator(request, user, creator_id)
    await db.user_tracked_creators.delete_one(
        {"user_id": user["_id"], "creator_id": creator["_id"]}
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
