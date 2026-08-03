from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    id: str
    kind: str = "scrape"
    state: str
    counters: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: list[dict[str, Any]] = Field(default_factory=list)
    target_username: str | None = None
    requested_url: str | None = None
