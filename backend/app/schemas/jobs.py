from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobProgress(BaseModel):
    current_keyword: str | None = None
    current_step: str = "start"
    keywords: list[dict[str, Any]] = Field(default_factory=list)
    total_discovered: int = 0
    total_target: int = 0


class JobIntervention(BaseModel):
    prompt: str
    fields: list[str]
    requested_at: str


class InterventionSubmission(BaseModel):
    code: str = Field(..., min_length=1, max_length=256)
    action: str | None = Field(default=None, pattern="^(cancel)?$")


class JobResponse(BaseModel):
    id: str
    kind: str = "scrape"
    state: str
    counters: dict[str, int] = Field(default_factory=dict)
    progress: JobProgress | None = None
    intervention: JobIntervention | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: list[dict[str, Any]] = Field(default_factory=list)
    target_username: str | None = None
    requested_url: str | None = None
