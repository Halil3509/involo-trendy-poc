"""Pydantic schemas for the Instagram brand reference analysis module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ConfidenceLevel = Literal["low", "medium", "high"]
MetricBasis = Literal[
    "raw_total",
    "per_view",
    "per_follower",
    "median_raw",
    "average_raw",
    "interactions_total_proxy",
    "interactions_per_view",
    "unknown",
]


class BrandAnalysisRequest(BaseModel):
    username_or_url: str = Field(min_length=1, max_length=200)
    max_posts: int = Field(default=10, ge=1, le=30)


class CaptionAnalysis(BaseModel):
    tone: str = ""
    structure: str = ""
    hashtag_strategy: str = ""
    emoji_usage: str = ""
    cta_type: str = ""
    keywords: list[str] = Field(default_factory=list)
    target_audience_hint: str = ""
    message_clarity_score: int = Field(default=5, ge=1, le=10)
    semantic_tags: list[str] = Field(default_factory=list)
    content_job: str = ""
    anomaly_candidate: str | None = None
    premium_signals: list[str] = Field(default_factory=list)
    hook_type: str = ""
    narrative_arc: list[str] = Field(default_factory=list)
    persona_triggers: list[str] = Field(default_factory=list)
    aspiration_level: str = ""
    slide_count_estimate: int = Field(default=0, ge=0)


class MediaEvidence(BaseModel):
    url: str | None = None
    media_type: str = "IMAGE"
    label: str = ""
    offset_seconds: float | None = None
    alt_text: str = ""


class BrandAnalysisPost(BaseModel):
    job_id: str
    post_id: str
    shortcode: str
    permalink: str | None = None
    caption: str
    media_type: str
    media_url: str | None = None
    media_items: list[MediaEvidence] = Field(default_factory=list)
    taken_at: datetime | None = None
    like_count: int = 0
    comment_count: int = 0
    view_count: int = 0
    share_count: int = 0
    comments_available: bool = False
    comment_samples: list[str] = Field(default_factory=list)
    fetched_at: datetime
    media_s3_key: str | None = None
    visual_analysis: dict[str, Any] | None = None
    caption_analysis: CaptionAnalysis | None = None
    analyzed_at: datetime | None = None


class PostSummary(BaseModel):
    shortcode: str
    media_type: str
    permalink: str | None = None
    caption: str = ""
    media_items: list[MediaEvidence] = Field(default_factory=list)
    like_count: int = 0
    comment_count: int = 0
    view_count: int = 0
    share_count: int = 0
    taken_at: datetime | None = None
    caption_summary: str = ""
    visual_summary: str = ""
    engagement_rate: float | None = None
    engagement_basis: MetricBasis = "interactions_total_proxy"
    engagement_comparable: bool = False
    semantic_tags: list[str] = Field(default_factory=list)
    content_job: str = ""
    anomaly_candidate: str | None = None
    premium_signals: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "low"


class MetricObservation(BaseModel):
    label: str
    value: float
    basis: MetricBasis
    comparable: bool
    confidence: ConfidenceLevel = "low"
    note: str = ""


class PerformanceSummary(BaseModel):
    organic_metrics: list[MetricObservation] = Field(default_factory=list)
    anomaly_metrics: list[MetricObservation] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
    valid_rate_comparisons: list[str] = Field(default_factory=list)
    invalid_rate_comparisons: list[str] = Field(default_factory=list)


class EvidenceReference(BaseModel):
    shortcode: str
    permalink: str | None = None
    field: str
    excerpt: str = ""
    why_supports: str = ""
    confidence: ConfidenceLevel = "low"


class EvidenceChain(BaseModel):
    chain_id: str = ""
    observation: str
    semantic_meaning: str
    preference_hypothesis: str
    adaptable_principle: str
    strategic_decision: str
    evidence: list[EvidenceReference] = Field(default_factory=list)
    alternative_explanation: str = ""
    confidence: ConfidenceLevel = "low"


class BrandWorldSynthesis(BaseModel):
    emotional_effect: str = ""
    brand_promise: str = ""
    persona: str = ""
    visual_codes: list[str] = Field(default_factory=list)
    verbal_codes: list[str] = Field(default_factory=list)
    lifestyle_context: str = ""
    premium_mechanism: str = ""
    avoided_elements: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "low"


class SuccessDNATriad(BaseModel):
    desire: str = Field(default="", description="Arzu — what the audience is invited to want")
    proof: str = Field(default="", description="Kanıt — sensory/visible evidence for the promise")
    lifestyle: str = Field(
        default="", description="Yaşam Tarzı — aspirational world the product lives in"
    )


class ContentSeriesMechanic(BaseModel):
    mechanic_name: str = ""
    base_category_type: str = ""
    observed_frequency: int = 0
    percentage_of_sample: float = 0.0
    psychological_function: str = ""
    execution_formula: str = ""
    content_jobs: list[str] = Field(default_factory=list)
    sample_shortcodes: list[str] = Field(default_factory=list)
    evidence_excerpt: str = ""
    confidence: ConfidenceLevel = "low"


class ContentRecipeFormatRole(BaseModel):
    format: str
    count: int = 1
    percentage: float = 0.0
    role_in_brand_world: str = ""
    content_jobs: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "low"


class ContentRecipe(BaseModel):
    observed_window_days: int | None = None
    coverage_label: str = ""
    cadence_estimate: str = ""
    posts_per_week_estimate: float | None = None
    cadence_confidence: ConfidenceLevel = "low"
    formats: list[ContentRecipeFormatRole] = Field(default_factory=list)
    content_jobs: list[list[str]] = Field(default_factory=list)
    anomaly_count: int = 0
    anomaly_note: str = ""
    confidence: ConfidenceLevel = "low"


class StrategicDecision(BaseModel):
    decision: str = ""
    rationale: str = ""
    evidence_chain_ids: list[str] = Field(default_factory=list)
    guardrail: str = ""
    first_action: str = ""
    success_signal: str = ""
    confidence: ConfidenceLevel = "low"


class VisualDNA(BaseModel):
    color_palette: list[str] = Field(default_factory=list)
    lighting_recipe: str = ""
    texture_signatures: list[str] = Field(default_factory=list)
    shooting_angles: list[str] = Field(default_factory=list)
    aesthetic_style: str = ""
    avoided_visual_elements: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "low"


class PersonaProfile(BaseModel):
    age_range: str = ""
    lifestyle_descriptor: str = ""
    aspiration: str = ""
    psychological_trigger: str = ""
    trigger_phrases: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "low"


class CarouselSlide(BaseModel):
    slide_number: int = 1
    role: str = ""
    content_pattern: str = ""


class CarouselAnatomy(BaseModel):
    hook_pattern: str = ""
    slide_roles: list[CarouselSlide] = Field(default_factory=list)
    cta_pattern: str = ""
    avg_slide_count: float = 0.0
    confidence: ConfidenceLevel = "low"


class ContentCalendarEntry(BaseModel):
    day: str = ""
    content_cluster: str = ""
    format: str = ""
    hook_template: str = ""
    slide_count: int | None = None
    cta_template: str = ""


class WeeklyContentCalendar(BaseModel):
    entries: list[ContentCalendarEntry] = Field(default_factory=list)
    weekly_cadence_note: str = ""
    confidence: ConfidenceLevel = "low"


class BrandAnalysisStrategicBrief(BaseModel):
    schema_version: str = "brand-analysis-strategic-brief-v3"
    executive_answer: str = ""
    success_dna: SuccessDNATriad = Field(default_factory=SuccessDNATriad)
    brand_world: BrandWorldSynthesis = Field(default_factory=BrandWorldSynthesis)
    visual_dna: VisualDNA | None = None
    persona_profile: PersonaProfile | None = None
    carousel_anatomy: CarouselAnatomy | None = None
    weekly_content_calendar: WeeklyContentCalendar | None = None
    production_brief: list[str] = Field(default_factory=list)
    preference_hypotheses: list[EvidenceChain] = Field(default_factory=list)
    evidence_chains: list[EvidenceChain] = Field(default_factory=list)
    content_recipe: ContentRecipe = Field(default_factory=ContentRecipe)
    content_series: list[ContentSeriesMechanic] = Field(default_factory=list)
    performance_summary: PerformanceSummary = Field(default_factory=PerformanceSummary)
    limitations: list[str] = Field(default_factory=list)
    decisions: list[StrategicDecision] = Field(default_factory=list)


class BrandAnalysisReportContext(BaseModel):
    job_id: str
    target_username: str
    post_count: int
    total_likes: int = 0
    total_comments: int = 0
    total_views: int = 0
    total_shares: int = 0
    avg_likes: float = 0.0
    avg_comments: float = 0.0
    avg_views: float = 0.0
    avg_shares: float = 0.0
    avg_engagement_rate: float | None = None
    engagement_basis: MetricBasis = "interactions_total_proxy"
    data_quality_notes: list[str] = Field(default_factory=list)
    format_breakdown: dict[str, int] = Field(default_factory=dict)
    caption_patterns: dict[str, Any] = Field(default_factory=dict)
    visual_patterns: dict[str, Any] = Field(default_factory=dict)
    top_post_shortcode: str | None = None
    bottom_post_shortcode: str | None = None
    posts: list[PostSummary] = Field(default_factory=list)
    posting_rhythm_summary: str = ""
    format_performance: dict[str, Any] = Field(default_factory=dict)
    engagement_distribution: dict[str, float] = Field(default_factory=dict)
    tone_frequency: dict[str, int] = Field(default_factory=dict)
    cta_frequency: dict[str, int] = Field(default_factory=dict)
    hashtag_frequency: dict[str, int] = Field(default_factory=dict)
    emoji_frequency: dict[str, int] = Field(default_factory=dict)
    visual_signature_frequency: dict[str, int] = Field(default_factory=dict)
    semantic_observations: list[EvidenceChain] = Field(default_factory=list)
    content_recipe: ContentRecipe | None = None
    performance_summary: PerformanceSummary | None = None
    brand_world: BrandWorldSynthesis | None = None
    image_narratives: list[str] = Field(default_factory=list)
    lifestyle_narratives: list[str] = Field(default_factory=list)
    sensory_proof_frequency: dict[str, int] = Field(default_factory=dict)
    contextual_placement_frequency: dict[str, int] = Field(default_factory=dict)
    anomaly_post_shortcodes: list[str] = Field(default_factory=list)


class BrandAnalysisReport(BaseModel):
    schema_version: str = "brand-analysis-report-v1"
    job_id: str
    markdown_text: str
    report_s3_key: str | None = None
    pdf_s3_key: str | None = None
    media_evidence: list[MediaEvidence] = Field(default_factory=list)
    strategic_brief: BrandAnalysisStrategicBrief | None = None


class BrandAnalysisPdf(BaseModel):
    job_id: str
    pdf_bytes: bytes
    pdf_s3_key: str


class BrandAnalysisJobResponse(BaseModel):
    id: str
    kind: str = "brand_analysis"
    state: str
    counters: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    target_username: str | None = None
    requested_url: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    logs: list[dict[str, object]] = Field(default_factory=list)
