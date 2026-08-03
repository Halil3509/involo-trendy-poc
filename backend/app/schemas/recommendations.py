from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ContentFormat = Literal["reels", "carousel", "native_photo"]


class RecommendationRequest(BaseModel):
    count: int | None = Field(default=None, ge=3, le=5)


class RecommendationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=128)
    trend_id: str = Field(min_length=1, max_length=128)
    permalink: str | None = Field(default=None, max_length=2048)
    similarity: float = Field(ge=-1, le=1)
    lifecycle: str = Field(max_length=32)
    confidence: float = Field(ge=0, le=1)
    snapshot_at: datetime | None = None
    score_components: dict[str, float | int | None] = Field(default_factory=dict)


class ScriptBeat(BaseModel):
    at_seconds: float = Field(ge=0)
    direction: str = Field(min_length=1, max_length=500)
    dialogue: str | None = Field(default=None, max_length=1000)


class Shot(BaseModel):
    order: int = Field(ge=1, le=100)
    framing: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=500)
    duration_seconds: float = Field(gt=0, le=300)


class RecommendationCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    title: str = Field(min_length=1, max_length=160)
    hook: str = Field(min_length=1, max_length=500)
    cta: str = Field(min_length=1, max_length=300)
    content_format: ContentFormat
    reasoning: str = Field(min_length=1, max_length=1000)
    objective: str = Field(default="engagement", min_length=1, max_length=200)
    target_audience: str = Field(default="Existing audience", min_length=1, max_length=500)
    first_frame: str = Field(default="Open on the creator", min_length=1, max_length=500)
    hook_0_3s: str = Field(default="Deliver the hook immediately", min_length=1, max_length=500)
    script_beats: list[ScriptBeat] = Field(default_factory=list, max_length=30)
    shot_list: list[Shot] = Field(default_factory=list, max_length=50)
    overlay_text: list[str] = Field(default_factory=list, max_length=30)
    duration_seconds: int = Field(default=30, ge=5, le=600)
    location: str | None = Field(default=None, max_length=300)
    props: list[str] = Field(default_factory=list, max_length=30)
    audio_direction: str = Field(default="", max_length=500)
    caption: str = Field(default="", max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    ab_hooks: list[str] = Field(default_factory=list, max_length=2)
    publish_window: str | None = Field(default=None, max_length=200)
    why_now: str = Field(default="", max_length=1000)
    originality_guardrail: str = Field(default="", max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    evidence: list[RecommendationEvidence] = Field(default_factory=list, max_length=10)
    state: str | None = None

    @field_validator(
        "title",
        "hook",
        "cta",
        "reasoning",
        "objective",
        "target_audience",
        "first_frame",
        "hook_0_3s",
    )
    @classmethod
    def strip_recommendation_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("recommendation text cannot be blank")
        return stripped


class RecommendationUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0


class RecommendationBatchResponse(BaseModel):
    id: str
    created_at: datetime
    recommendations: list[RecommendationCard]
    usage: RecommendationUsage | None = None
