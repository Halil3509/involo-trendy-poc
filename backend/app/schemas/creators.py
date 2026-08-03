"""Public contracts for the creator tracking feature."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

USERNAME_RE = r"^[A-Za-z0-9._]{1,30}$"


class TrackCreatorRequest(BaseModel):
    username: str = Field(min_length=1, max_length=30)

    @field_validator("username", mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().lstrip("@").lower()


class CreatorSummary(BaseModel):
    id: str
    username: str
    display_name: str = ""
    avatar_url: str | None = None
    follower_count: int = 0
    media_count: int = 0
    trend_score: float = 0.0
    status: str = "active"
    last_tracked_at: datetime | None = None
    last_error: str | None = None
    added_at: datetime | None = None


class CreatorListResponse(BaseModel):
    creators: list[CreatorSummary]


class CreatorDetailResponse(CreatorSummary):
    bio: str = ""
    following_count: int = 0
    ai_summary: str | None = None
    structured_profile: dict[str, Any] | None = None
    average_viral_score: float | None = None
    profile_updated_at: datetime | None = None


class FollowerPoint(BaseModel):
    captured_at: datetime
    follower_count: int


class FollowerHistoryResponse(BaseModel):
    range: Literal["week", "month", "year"]
    points: list[FollowerPoint]
    delta: int = 0


class CreatorContentItem(BaseModel):
    shortcode: str
    permalink: str | None = None
    caption_text: str = ""
    media_type: str = "IMAGE"
    thumbnail_url: str | None = None
    taken_at: datetime | None = None
    like_count: int = 0
    comment_count: int = 0
    view_count: int = 0
    viral_score: float = 0.0
    is_new: bool = False
    processing_status: str = "discovered"


class CreatorContentResponse(BaseModel):
    items: list[CreatorContentItem]
    new_count: int = 0
