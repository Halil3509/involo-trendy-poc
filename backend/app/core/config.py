import os
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="INVOLO_",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[Any, ...]:
        if os.environ.get("PYTEST_VERSION"):
            return (init_settings,)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    environment: Literal["development", "test", "production", "staging"] = "development"
    api_prefix: str = "/api/v1"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:8020"]

    mongo_uri: str = "mongodb://localhost:8022"
    mongo_database: str = "involo"
    redis_url: str = "redis://localhost:8028/0"
    qdrant_url: str = "http://localhost:8024"
    qdrant_api_key: SecretStr | None = None
    provider_readiness_probes_enabled: bool = True
    provider_readiness_cache_ttl_seconds: float = Field(default=30.0, ge=1, le=300)
    provider_readiness_timeout_seconds: float = Field(default=3.0, ge=0.1, le=30)
    qdrant_trend_collection: str = "trend_content_v2"
    qdrant_user_collection: str = "user_profiles_v2"
    qdrant_user_content_collection: str = "user_content_v2"
    qdrant_segment_collection: str = "content_segments_v2"
    vector_size: int = Field(default=1024, ge=1, le=65_536)
    vector_schema_version: str = "nova-mm-v2"
    vector_fusion_text_weight: float = Field(default=0.45, ge=0, le=1)
    vector_fusion_media_weight: float = Field(default=0.55, ge=0, le=1)

    jwt_secret: SecretStr = SecretStr("development-only-change-me-32-chars")
    jwt_issuer: str = "involo"
    access_token_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_days: int = Field(default=30, ge=1, le=365)
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None

    scraper_adapter: Literal["instagram", "meta", "fixture"] = "fixture"
    scraper_fixture_path: str = "tests/fixtures/instagram.json"
    metadata_fixture_path: str = "tests/fixtures/metadata.json"
    scraper_storage_state_path: str = ".state/instagram.json"
    scraper_headless: bool = True
    instagram_username: str | None = None
    instagram_password: SecretStr | None = None
    scraper_min_delay_seconds: float = Field(default=1.5, ge=0)
    scraper_max_delay_seconds: float = Field(default=3.5, ge=0)
    scraper_max_keywords: int = Field(default=10, ge=1, le=100)
    scraper_items_per_keyword: int = Field(default=20, ge=1, le=100)
    scraper_max_content_age_days: int = Field(default=30, ge=0)

    # Phase 3 — metadata, scoring, transcript
    aws_region: str = "eu-central-1"
    bedrock_embedding_region: str = "us-east-1"
    bedrock_generation_region: str = "us-east-1"
    transcribe_s3_bucket: str | None = None
    transcribe_s3_endpoint_url: str | None = None
    transcribe_s3_access_key_id: str | None = None
    transcribe_s3_secret_access_key: SecretStr | None = None
    transcribe_poll_seconds: float = Field(default=5.0, ge=0.1)
    transcribe_timeout_seconds: float = Field(default=600.0, ge=1)
    transcribe_min_views: int = Field(default=0, ge=0)
    transcription_provider: Literal["aws", "fake"] = "aws"
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    media_provider: Literal["s3", "noop"] = "s3"
    media_s3_bucket: str | None = None
    media_s3_region: str = "eu-central-1"
    media_s3_bucket_owner: str | None = Field(default=None, pattern=r"^\d{12}$")
    embedding_media_s3_bucket: str | None = None
    embedding_media_s3_region: str = "us-east-1"
    embedding_media_s3_endpoint_url: str | None = None
    embedding_media_s3_bucket_owner: str | None = Field(
        default=None, pattern=r"^\d{12}$"
    )
    media_s3_prefix: str = "content-intelligence"
    media_retention_days: int = Field(default=30, ge=1, le=3650)
    media_max_download_bytes: int = Field(default=250_000_000, ge=1_000_000)
    keyframe_offsets_seconds: Annotated[list[float], NoDecode] = [0.0, 1.0, 3.0, 5.0]
    brand_analysis_keyframe_offsets_seconds: Annotated[list[float], NoDecode] = [
        0.0,
        2.5,
        5.0,
    ]
    segment_seconds: int = Field(default=15, ge=5, le=30)
    score_distribution_weight: float = Field(default=0.40, ge=0, le=1)
    score_engagement_weight: float = Field(default=0.30, ge=0, le=1)
    score_velocity_weight: float = Field(default=0.30, ge=0, le=1)
    score_comment_weight: float = Field(default=5.0, ge=0)
    score_share_weight: float = Field(default=12.0, ge=0)
    score_distribution_ratio_divisor: float = Field(default=2.0, gt=0)
    score_engagement_rate_multiplier: float = Field(default=6.0, gt=0)
    score_velocity_log_divisor: float = Field(default=7.0, gt=0)

    # Zero-score re-enrichment and metadata fallback
    metadata_fallback_provider: Literal["none", "ytdlp", "graph", "all"] = "none"
    max_zero_score_enrichment_retries: int = Field(default=3, ge=0, le=10)
    zero_score_enrichment_cooldown_minutes: int = Field(default=0, ge=0, le=10_080)

    # Phase 4 — embedding
    bedrock_embedding_model_id: str = "amazon.nova-2-multimodal-embeddings-v1:0"
    bedrock_vision_model_id: str = "us.amazon.nova-pro-v1:0"
    embedding_provider: Literal["aws", "fake"] = "aws"
    vision_provider: Literal["aws", "fake"] = "aws"
    schedule_cron: str | None = None
    schedule_pipeline: bool = False

    # Phase 5 — Instagram OAuth & user profiling
    instagram_graph_api_version: str = "v25.0"
    # Facebook App credentials for graph.facebook.com endpoints (long-lived token exchange,
    # hashtag search, business discovery). Often different from Instagram App credentials below.
    facebook_app_id: str | None = None
    facebook_app_secret: SecretStr | None = None
    # Instagram App credentials for api.instagram.com / graph.instagram.com (Instagram Login).
    instagram_app_id: str | None = None
    instagram_app_secret: SecretStr | None = None
    meta_trend_access_token: SecretStr | None = None
    meta_instagram_business_account_id: str | None = None
    instagram_oauth_redirect_uri: str = "https://involo.loca.lt/api/v1/instagram/oauth/callback"
    instagram_oauth_success_url: str = "http://localhost:8020/instagram/callback"
    instagram_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    instagram_token_encryption_key: SecretStr = SecretStr("development-only-token-encryption-key")
    instagram_webhook_verify_token: SecretStr | None = None
    bedrock_profile_model_id: str = "us.amazon.nova-pro-v1:0"
    profile_summary_max_tokens: int = Field(default=500, ge=64, le=2000)
    profiling_schedule_cron: str | None = None
    profiling_schedule_enabled: bool = False
    profiling_default_seconds_per_user: float = Field(default=60.0, gt=0)
    profiling_estimate_sample_size: int = Field(default=10, ge=1, le=100)
    profiling_max_concurrency: int = Field(default=3, ge=1, le=10)

    # Creator tracking (daily Graph API snapshots, fixed 03:00 UTC+3 = 00:00 UTC)
    creator_tracking_provider: Literal["graph_api", "fixture", "playwright"] = "fixture"
    creator_tracking_fixture_path: str = "tests/fixtures/creator_profile.json"
    creator_tracking_headless: bool = True
    creator_tracking_schedule_enabled: bool = True
    creator_tracking_schedule_cron: str = "0 0 * * *"
    creator_tracking_max_posts: int = Field(default=12, ge=1, le=50)
    creator_tracking_batch_delay_ms: int = Field(default=100, ge=0, le=5000)
    creator_ai_profile_enabled: bool = True
    qdrant_creator_content_collection: str = "creator_content_v2"

    # Brand analysis
    brand_analysis_max_posts: int = Field(default=10, ge=1, le=30)
    brand_analysis_public_fallback_enabled: bool = False
    brand_analysis_provider: Literal["aws", "fake"] = "aws"
    brand_analysis_concurrency: int = Field(default=3, ge=1, le=10)
    brand_analysis_post_timeout_seconds: float = Field(default=180.0, ge=10.0, le=1800.0)
    brand_analysis_max_report_posts: int = Field(default=30, ge=1, le=100)
    bedrock_enable_prompt_cache: bool = False
    brand_analysis_report_model_id: str = Field(default="")
    brand_analysis_caption_model_id: str = Field(default="")
    brand_analysis_report_max_tokens: int = Field(default=7000, ge=256, le=12000)
    brand_analysis_caption_max_tokens: int = Field(default=1000, ge=256, le=4000)
    bedrock_vision_max_tokens: int = Field(default=2048, ge=256, le=8000)
    brand_analysis_pdf_provider: Literal["playwright", "fake"] = "playwright"
    brand_analysis_pdf_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)

    # Phase 7 — live scraper log stream
    scraper_log_max_lines: int = Field(default=500, ge=10, le=5000)
    scraper_log_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    scraper_log_persist_lines: int = Field(default=200, ge=0, le=5000)

    # Phase 8 — hardening (retry/backoff + rate limiting)
    task_max_retries: int = Field(default=3, ge=0, le=10)
    task_retry_backoff_max: int = Field(default=300, ge=1, le=3600)
    stale_job_cleanup_hours: int = Field(default=24, ge=1, le=168)
    rate_limit_enabled: bool = True
    auth_rate_limit_max: int = Field(default=10, ge=1, le=1000)
    auth_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    recommendation_rate_limit_max: int = Field(default=20, ge=1, le=1000)
    recommendation_rate_limit_window_seconds: int = Field(default=3600, ge=1, le=86_400)

    # Graph API application-level rate limit (Meta: 200 calls per user per hour).
    graph_rate_limit_enabled: bool = True
    graph_rate_limit_user_count: int = Field(default=1, ge=1, le=10_000_000)
    graph_rate_limit_calls_per_user: int = Field(default=200, ge=1, le=1_000_000)
    graph_rate_limit_window_seconds: int = Field(default=3600, ge=60, le=86_400)

    # Phase 6 — personalized content recommendations
    bedrock_recommendation_model_id: str = "us.amazon.nova-pro-v1:0"
    recommendation_default_count: int = Field(default=3, ge=3, le=5)
    recommendation_retrieval_top_k: int = Field(default=10, ge=1, le=50)
    recommendation_retrieval_pool: int = Field(default=30, ge=1, le=100)
    recommendation_viral_weight: float = Field(default=0.2, ge=0, le=1)
    recommendation_history_limit: int = Field(default=20, ge=1, le=100)
    recommendation_dedupe_threshold: float = Field(default=0.9, ge=0, le=1)
    recommendation_max_attempts: int = Field(default=3, ge=1, le=10)
    recommendation_prompt_cache: bool = True
    recommendation_context_max_chars: int = Field(default=1200, ge=100, le=10_000)
    recommendation_max_tokens: int = Field(default=1800, ge=256, le=4096)
    recommendation_lock_ttl_seconds: int = Field(default=180, ge=30, le=900)
    recommendation_mmr_lambda: float = Field(default=0.7, ge=0, le=1)
    recommendation_min_confidence: float = Field(default=0.25, ge=0, le=1)
    snapshot_offsets_hours: Annotated[list[int], NoDecode] = [6, 24, 48, 72]
    outcome_offsets_hours: Annotated[list[int], NoDecode] = [24, 72]
    provider_cost_per_1k_input_tokens: float = Field(default=0.0, ge=0)
    provider_cost_per_1k_output_tokens: float = Field(default=0.0, ge=0)
    evaluation_min_ndcg_at_k: float = Field(default=0.5, ge=0, le=1)
    evaluation_min_precision_at_k: float = Field(default=0.2, ge=0, le=1)
    evaluation_max_brier: float = Field(default=0.25, ge=0, le=1)
    evaluation_max_p95_latency_seconds: float = Field(default=30.0, gt=0)
    evaluation_max_cost_per_prediction: float = Field(default=1.0, ge=0)
    evaluation_rollback_ndcg_drop: float = Field(default=0.1, ge=0, le=1)
    evaluation_rollback_precision_drop: float = Field(default=0.1, ge=0, le=1)
    evaluation_rollback_brier_increase: float = Field(default=0.05, ge=0, le=1)

    # Licensed/read-only topic signal connectors
    google_trends_enabled: bool = False
    google_trends_api_url: str | None = None
    google_trends_api_key: SecretStr | None = None
    youtube_signals_enabled: bool = False
    youtube_api_key: SecretStr | None = None
    reddit_signals_enabled: bool = False
    reddit_client_id: str | None = None
    reddit_client_secret: SecretStr | None = None
    reddit_user_agent: str = "involo-content-intelligence/1.0"
    topic_signals_schedule_enabled: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "keyframe_offsets_seconds",
        "snapshot_offsets_hours",
        "outcome_offsets_hours",
        "brand_analysis_keyframe_offsets_seconds",
        mode="before",
    )
    @classmethod
    def parse_numeric_lists(cls, value: object) -> object:
        if isinstance(value, str) and not value.lstrip().startswith("["):
            parts = [item.strip() for item in value.split(",") if item.strip()]
            return [float(item) for item in parts]
        return value

    @field_validator(
        "media_s3_bucket_owner",
        "embedding_media_s3_bucket_owner",
        mode="before",
    )
    @classmethod
    def empty_bucket_owner_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator(
        "aws_region",
        "bedrock_embedding_region",
        "bedrock_generation_region",
        "media_s3_region",
        "embedding_media_s3_region",
        mode="before",
    )
    @classmethod
    def region_must_not_be_empty(cls, value: object, info: ValidationInfo) -> object:
        field = info.field_name or "value"
        if value == "":
            raise ValueError(f"INVOLO_{field.upper()} is empty")
        return value

    @field_validator(
        "transcribe_s3_endpoint_url",
        "embedding_media_s3_endpoint_url",
        mode="before",
    )
    @classmethod
    def empty_endpoint_url_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("scraper_max_delay_seconds")
    @classmethod
    def delay_order(cls, value: float, info: ValidationInfo) -> float:
        if value < info.data.get("scraper_min_delay_seconds", 0):
            raise ValueError("max delay must be greater than or equal to min delay")
        return value

    @property
    def effective_facebook_app_id(self) -> str | None:
        return self.facebook_app_id or self.instagram_app_id or None

    @property
    def effective_facebook_app_secret(self) -> SecretStr | None:
        if self.facebook_app_secret and self.facebook_app_secret.get_secret_value():
            return self.facebook_app_secret
        if self.instagram_app_secret and self.instagram_app_secret.get_secret_value():
            return self.instagram_app_secret
        return None

    @model_validator(mode="after")
    def _derive_brand_analysis_model_ids(self) -> "Settings":
        if not self.brand_analysis_report_model_id:
            self.brand_analysis_report_model_id = self.bedrock_vision_model_id
        if not self.brand_analysis_caption_model_id:
            vision = self.bedrock_vision_model_id
            self.brand_analysis_caption_model_id = (
                vision.replace("nova-pro", "nova-lite") if "nova-pro" in vision else vision
            )
        return self

    @model_validator(mode="after")
    def _validate_bedrock_model_region_match(self) -> "Settings":
        expected_prefix = self.bedrock_generation_region.split("-")[0]
        generation_ids = {
            "bedrock_vision_model_id": self.bedrock_vision_model_id,
            "bedrock_profile_model_id": self.bedrock_profile_model_id,
            "bedrock_recommendation_model_id": self.bedrock_recommendation_model_id,
            "brand_analysis_report_model_id": self.brand_analysis_report_model_id,
            "brand_analysis_caption_model_id": self.brand_analysis_caption_model_id,
        }
        for field_name, model_id in generation_ids.items():
            if model_id.startswith(("eu.", "us.", "apac.", "global.")) and not model_id.startswith(
                expected_prefix + "."
            ):
                raise ValueError(
                    f"{field_name}={model_id} does not match "
                    f"bedrock_generation_region={self.bedrock_generation_region}"
                )
        return self

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        if bool(self.bootstrap_admin_email) != bool(self.bootstrap_admin_password):
            raise ValueError("both bootstrap admin email and password must be configured")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None cookies must be secure")
        if (
            self.environment in ("production", "staging")
            and self.jwt_secret.get_secret_value() == "development-only-change-me-32-chars"
        ):
            raise ValueError("production and staging require a non-default JWT secret")
        if self.environment in ("production", "staging"):
            if (
                self.instagram_token_encryption_key.get_secret_value()
                == "development-only-token-encryption-key"
            ):
                raise ValueError("production requires a non-default token encryption key")
            if not (self.instagram_app_id and self.instagram_app_secret):
                raise ValueError("Graph profiling requires Instagram app credentials")
        if self.environment in ("production", "staging"):
            if not self.provider_readiness_probes_enabled:
                raise ValueError("production requires live provider readiness probes")
            if not self.media_s3_bucket:
                raise ValueError("production requires INVOLO_MEDIA_S3_BUCKET")
            if not self.embedding_media_s3_bucket:
                raise ValueError(
                    "production requires INVOLO_EMBEDDING_MEDIA_S3_BUCKET"
                )
            if self.embedding_media_s3_endpoint_url:
                raise ValueError(
                    "production Nova embedding media bucket must use regional AWS S3"
                )
            if self.media_s3_region != self.bedrock_generation_region:
                raise ValueError(
                    "media S3 region must match the Bedrock generation region"
                )
            if self.embedding_media_s3_region != self.bedrock_embedding_region:
                raise ValueError(
                    "embedding media S3 region must match the Bedrock embedding region"
                )
            if (
                self.media_s3_bucket == self.embedding_media_s3_bucket
                and self.media_s3_region != self.embedding_media_s3_region
            ):
                raise ValueError(
                    "different Bedrock regions require separate media S3 buckets"
                )
            if self.bedrock_embedding_model_id.startswith(
                ("eu.", "us.", "apac.", "global.")
            ):
                raise ValueError(
                    "Nova multimodal embeddings require an in-region model ID"
                )
            generation_ids = (
                self.bedrock_vision_model_id,
                self.bedrock_profile_model_id,
                self.bedrock_recommendation_model_id,
            )
            if any(model_id == "amazon.nova-pro-v1:0" for model_id in generation_ids):
                raise ValueError(
                    "Nova Pro on-demand generation requires an inference profile ID"
                )
            if not self.transcribe_s3_bucket:
                raise ValueError("production requires INVOLO_TRANSCRIBE_S3_BUCKET")
            if not (
                (self.facebook_app_id and self.facebook_app_secret)
                or (self.instagram_app_id and self.instagram_app_secret)
            ):
                raise ValueError(
                    "production requires official Meta app credentials"
                )
            if self.environment == "production":
                if not (
                    self.meta_trend_access_token and self.meta_instagram_business_account_id
                ):
                    raise ValueError(
                        "production requires official Meta trend access token and account id"
                    )
        if self.recommendation_retrieval_pool < self.recommendation_retrieval_top_k:
            raise ValueError("recommendation retrieval pool must be at least top-k")
        if self.vector_fusion_text_weight + self.vector_fusion_media_weight <= 0:
            raise ValueError("at least one vector fusion weight must be positive")
        if self.google_trends_enabled and not (
            self.google_trends_api_url and self.google_trends_api_key
        ):
            raise ValueError("Google Trends connector requires its approved API URL and key")
        if self.youtube_signals_enabled and not self.youtube_api_key:
            raise ValueError("YouTube connector requires an API key")
        if self.reddit_signals_enabled and not (
            self.reddit_client_id and self.reddit_client_secret
        ):
            raise ValueError("Reddit connector requires OAuth credentials")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
