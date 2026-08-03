from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScraperConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    keywords: list[str] = Field(
        default_factory=lambda: ["recipe", "food"], max_length=100
    )
    reels_per_keyword: int = Field(default=50, ge=1, le=500)
    enabled: bool = True
    headless: bool = True
    viral_threshold: float = Field(default=40.0, ge=0, le=100)
    transcribe_min_views: int = Field(default=0, ge=0)
    schedule_cron: str | None = None
    schedule_pipeline: bool = False

    @model_validator(mode="before")
    @classmethod
    def _map_legacy_items_per_keyword(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "items_per_keyword" in data and "reels_per_keyword" not in data:
                data["reels_per_keyword"] = data.pop("items_per_keyword")
        return data

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        if any(len(value) > 80 for value in normalized):
            raise ValueError("keywords must be at most 80 characters")
        return normalized

    @field_validator("schedule_cron")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        from croniter import croniter

        candidate = value.strip()
        if not croniter.is_valid(candidate):
            raise ValueError("schedule_cron must be a valid cron expression")
        return candidate


class ScraperRunRequest(BaseModel):
    keywords: list[str] | None = None


class PipelineStats(BaseModel):
    discovered: int = 0
    enriched: int = 0
    stored: int = 0
    embedded: int = 0
    needs_intervention: int = 0
    failed: int = 0


class AdminOverview(BaseModel):
    total_users: int = 0
    admin_users: int = 0
    connected_instagram: int = 0
    needs_reauth: int = 0
    trend_content_total: int = 0
    pipeline: PipelineStats = Field(default_factory=PipelineStats)
    user_content_total: int = 0
    user_profiles_ready: int = 0
    recommendation_batches: int = 0
    jobs_by_state: dict[str, int] = Field(default_factory=dict)
    attention_jobs: int = 0


class TrendContentListItem(BaseModel):
    id: str
    shortcode: str | None = None
    owner_username: str | None = None
    caption_text: str = ""
    canonical_url: str | None = None
    discovered_keywords: list[str] = Field(default_factory=list)
    processing_status: str | None = None
    last_upsert_action: str | None = None
    last_scrape_job_id: str | None = None
    viral_score: float = 0.0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TrendContentListResponse(BaseModel):
    items: list[TrendContentListItem]
    total: int
    limit: int
    offset: int


class TrendContentDetail(TrendContentListItem):
    canonical_url: str | None = None
    media_id: str | None = None
    video_url: str | None = None
    thumbnail_url: str | None = None
    author: str | None = None
    source: str | None = None
    metrics: dict[str, Any] | None = None
    score_components: dict[str, Any] | None = None
    transcript: str | None = None
    language: str | None = None
    combined_text: str | None = None
    duration_seconds: float | None = None
    taken_at: datetime | None = None
    media_asset: Any | None = None
    keyframes: list[Any] | None = None
    visual_analysis: Any | None = None
    video_segments: list[Any] | None = None
    processing_regions: dict[str, str] | None = None
    embedding_vector_id: str | None = None
    embedding_schema_version: str | None = None
    enrichment_error: str | None = None
    enrichment_error_type: str | None = None
    enrichment_provider: str | None = None
    embedded_at: datetime | None = None
    enriched_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class ScrapedItem(BaseModel):
    canonical_url: str
    shortcode: str
    caption: str = ""
    author: str | None = None
    thumbnail_url: str | None = None
    discovered_keyword: str
    source: Literal["instagram"] = "instagram"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentMetadata(BaseModel):
    shortcode: str
    media_id: str | None = None
    owner_username: str | None = None
    owner_follower_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    view_count: int = 0
    share_count: int = 0
    video_duration: float | None = None
    caption_text: str = ""
    taken_at: datetime | None = None
    video_url: str | None = None
    media_type: str | None = None


class TranscriptResult(BaseModel):
    text: str = ""
    language: str | None = None
