from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

Lifecycle = Literal["emerging", "rising", "saturated", "declining", "unknown"]
RecommendationState = Literal[
    "saved", "dismissed", "in_production", "published", "archived"
]
ExperimentState = Literal["draft", "running", "awaiting_data", "completed", "inconclusive"]


class CreatorPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_countries: list[str] = Field(default_factory=list, max_length=10)
    target_cities: list[str] = Field(default_factory=list, max_length=20)
    content_languages: list[str] = Field(default_factory=lambda: ["tr"], min_length=1, max_length=5)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    niches: list[str] = Field(default_factory=list, max_length=10)
    goals: list[str] = Field(default_factory=list, max_length=10)
    constraints: list[str] = Field(default_factory=list, max_length=20)

    @field_validator(
        "target_countries",
        "target_cities",
        "content_languages",
        "niches",
        "goals",
        "constraints",
    )
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in values if item.strip()))
        if any(len(item) > 100 for item in cleaned):
            raise ValueError("preference values must be at most 100 characters")
        return cleaned


class PreferencesResponse(CreatorPreferences):
    model_config = ConfigDict(extra="ignore")

    updated_at: datetime | None = None


class MetricCoverage(BaseModel):
    requested: list[str] = Field(default_factory=list)
    available: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)


class MetricSnapshot(BaseModel):
    source: str
    subject_type: Literal["trend_content", "user_content", "audience", "outcome"]
    subject_id: str
    captured_at: datetime
    offset_hours: int | None = None
    metrics: dict[str, float | int | None]
    coverage: MetricCoverage
    provider_version: str


class TrendSignals(BaseModel):
    velocity: float | None = None
    acceleration: float | None = None
    percentile: float | None = None
    freshness: float = Field(default=0, ge=0, le=1)
    lifecycle: Lifecycle = "unknown"
    confidence: float = Field(default=0, ge=0, le=1)
    model_version: str = "trend-signals-v1"


class VisualAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening_frame: str = Field(max_length=1000)
    hook_timing_seconds: float | None = Field(default=None, ge=0)
    ocr_text: list[str] = Field(default_factory=list, max_length=100)
    faces: list[str] = Field(default_factory=list, max_length=50)
    objects: list[str] = Field(default_factory=list, max_length=100)
    shot_changes: list[float] = Field(default_factory=list, max_length=500)
    pacing: Literal["slow", "medium", "fast", "mixed", "unknown"] = "unknown"
    overlay_style: str = Field(default="", max_length=500)
    visual_signature: list[str] = Field(default_factory=list, max_length=30)
    safety_notes: list[str] = Field(default_factory=list, max_length=30)
    originality_notes: list[str] = Field(default_factory=list, max_length=30)
    color_palette: list[str] = Field(default_factory=list, max_length=20)
    lighting_type: str = Field(default="", max_length=300)
    texture_descriptors: list[str] = Field(default_factory=list, max_length=30)
    shooting_angle: str = Field(default="", max_length=200)
    aesthetic_style: str = Field(default="", max_length=300)
    composition_style: str = Field(default="", max_length=300)
    asmr_elements: list[str] = Field(default_factory=list, max_length=30)
    contextual_placement: str = Field(default="", max_length=500)
    sensory_visual_proof: list[str] = Field(default_factory=list, max_length=20)
    aspirational_lifestyle_narrative: str = Field(default="", max_length=500)
    visual_hook: str = Field(default="", max_length=300)
    material_context: str = Field(default="", max_length=300)
    confidence: float = Field(ge=0, le=1)


class ProfilePillar(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=1000)
    content_count: int = Field(ge=1)
    average_performance_residual: float
    strengths: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class StructuredCreatorProfile(BaseModel):
    schema_version: str = "creator-profile-v2"
    pillars: list[ProfilePillar] = Field(default_factory=list)
    winning_patterns: list[str] = Field(default_factory=list)
    losing_patterns: list[str] = Field(default_factory=list)
    audience_markets: list[str] = Field(default_factory=list)
    avoid_patterns: list[str] = Field(default_factory=list)
    target_markets: list[str] = Field(default_factory=list)
    content_languages: list[str] = Field(default_factory=list)
    niches: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    data_quality: float = Field(ge=0, le=1)


class RecommendationEventRequest(BaseModel):
    state: RecommendationState
    reason: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class RecommendationEventResponse(BaseModel):
    id: str
    recommendation_id: str
    state: RecommendationState
    created_at: datetime


class PostLinkRequest(BaseModel):
    media_id: str = Field(min_length=1, max_length=200)


class PostLinkResponse(BaseModel):
    id: str
    recommendation_id: str
    media_id: str
    permalink: HttpUrl | None = None
    linked_at: datetime


class ExperimentCreate(BaseModel):
    recommendation_id: str
    name: str = Field(min_length=1, max_length=160)
    variants: list[str] = Field(min_length=2, max_length=5)


class ExperimentUpdate(BaseModel):
    state: ExperimentState
    note: str | None = Field(default=None, max_length=1000)


class ExperimentResponse(BaseModel):
    id: str
    recommendation_id: str
    name: str
    variants: list[str]
    state: ExperimentState
    created_at: datetime
    updated_at: datetime


class AdminObservabilityResponse(BaseModel):
    queue_age_seconds: float | None = None
    job_duration_p50_seconds: float | None = None
    job_duration_p95_seconds: float | None = None
    stale_trends: int = 0
    stale_profiles: int = 0
    attention_jobs: int = 0
    stale_jobs: int = 0
    snapshot_coverage: float = Field(default=0, ge=0, le=1)
    multimodal_failures: dict[str, int] = Field(default_factory=dict)
    provider_usage: dict[str, object] = Field(default_factory=dict)
    evaluation: dict[str, object] = Field(default_factory=dict)
    funnel: dict[str, int] = Field(default_factory=dict)


class EvaluationRunRequest(BaseModel):
    model_version: str = Field(min_length=1, max_length=200)
    data_cutoff: datetime
    k: int = Field(default=10, ge=1, le=100)


class EvaluationRunResponse(BaseModel):
    id: str
    model_version: str
    data_cutoff: datetime
    evaluation_version: str
    label_definition: str
    k: int
    sample_size: int
    candidate_sample_size: int
    metrics: dict[str, object]
    thresholds: dict[str, float]
    passed: bool
    rollback_recommended: bool
    created_at: datetime


class TopicSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    source: Literal["google_trends", "youtube", "reddit"]
    license: str
    captured_at: datetime
    score: float
    volume: float | None = None
    velocity: float | None = None
    source_url: str | None = None
    provenance: dict[str, str | int | float | None] = Field(default_factory=dict)
