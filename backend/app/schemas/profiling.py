from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

from app.schemas.intelligence import StructuredCreatorProfile

ProfilingState = Literal[
    "disconnected", "connected", "profiling", "ready", "failed", "needs_reauth"
]


class OAuthStartResponse(BaseModel):
    authorization_url: str


class InstagramStatusResponse(BaseModel):
    status: ProfilingState
    instagram_username: str | None = None
    connected_at: datetime | None = None
    last_synced_at: datetime | None = None
    content_count_analyzed: int = 0
    ai_profile_summary: str | None = None
    vector_std_dev: float | None = None
    error: str | None = None
    structured_profile: StructuredCreatorProfile | None = None


class ProfilingConfig(BaseModel):
    enabled: bool = False
    schedule_cron: str | None = None

    @field_validator("schedule_cron")
    @classmethod
    def validate_profiling_cron(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        from croniter import croniter

        candidate = value.strip()
        if not croniter.is_valid(candidate):
            raise ValueError("schedule_cron must be a valid cron expression")
        return candidate


class ProfilingEstimate(BaseModel):
    connected_users: int
    average_seconds_per_user: float
    estimated_duration_seconds: float
    estimated_start_at: datetime | None = None
    estimated_finish_at: datetime | None = None
