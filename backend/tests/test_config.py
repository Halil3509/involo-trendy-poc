import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_parse_comma_separated_origins() -> None:
    settings = Settings(cors_origins="https://one.test, https://two.test")  # type: ignore[arg-type]
    assert settings.cors_origins == ["https://one.test", "https://two.test"]


def test_settings_reject_invalid_delay_order() -> None:
    with pytest.raises(ValidationError, match="max delay"):
        Settings(scraper_min_delay_seconds=2, scraper_max_delay_seconds=1)


def test_vector_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(vector_size=0)


def test_recommendation_pool_must_cover_top_k() -> None:
    with pytest.raises(ValidationError, match="retrieval pool"):
        Settings(recommendation_retrieval_top_k=20, recommendation_retrieval_pool=10)


def test_bedrock_defaults_use_stage_appropriate_regions_and_profiles() -> None:
    settings = Settings()

    assert settings.bedrock_embedding_region == "us-east-1"
    assert settings.bedrock_generation_region == "us-east-1"
    assert settings.bedrock_embedding_model_id == (
        "amazon.nova-2-multimodal-embeddings-v1:0"
    )
    assert settings.bedrock_vision_model_id == "us.amazon.nova-pro-v1:0"
    assert settings.bedrock_profile_model_id == "us.amazon.nova-pro-v1:0"
    assert settings.bedrock_recommendation_model_id == "us.amazon.nova-pro-v1:0"
    assert settings.brand_analysis_caption_model_id == "us.amazon.nova-lite-v1:0"
    assert settings.brand_analysis_report_model_id == "us.amazon.nova-pro-v1:0"
    assert settings.bedrock_vision_max_tokens == 2048
    assert settings.brand_analysis_concurrency == 3
    assert settings.brand_analysis_max_report_posts == 30


def test_brand_analysis_model_ids_inherit_from_bedrock_vision() -> None:
    settings = Settings(
        bedrock_vision_model_id="us.amazon.nova-pro-v1:0",
        brand_analysis_caption_model_id="",
        brand_analysis_report_model_id="",
        bedrock_generation_region="us-east-1",
    )
    assert settings.brand_analysis_caption_model_id == "us.amazon.nova-lite-v1:0"
    assert settings.brand_analysis_report_model_id == "us.amazon.nova-pro-v1:0"


def test_brand_analysis_caption_model_matches_generation_region() -> None:
    """Regression: caption model must inherit the vision model's region/profile prefix.

    Hard-coding an eu-prefixed model while the rest of the stack uses us-east-1 caused
    Bedrock ValidationException: The provided model identifier is invalid.
    """
    settings = Settings(
        bedrock_generation_region="us-east-1",
        bedrock_vision_model_id="us.amazon.nova-pro-v1:0",
    )
    assert settings.brand_analysis_caption_model_id == "us.amazon.nova-lite-v1:0"


def test_brand_analysis_model_ids_can_be_overridden() -> None:
    settings = Settings(
        bedrock_vision_model_id="us.amazon.nova-pro-v1:0",
        brand_analysis_caption_model_id="us.amazon.nova-lite-v1:0",
    )
    assert settings.brand_analysis_caption_model_id == "us.amazon.nova-lite-v1:0"
    assert settings.brand_analysis_report_model_id == "us.amazon.nova-pro-v1:0"


def test_brand_analysis_speed_settings_defaults() -> None:
    settings = Settings()
    assert settings.brand_analysis_concurrency == 3
    assert settings.brand_analysis_max_report_posts == 30
    assert settings.brand_analysis_keyframe_offsets_seconds == [0.0, 2.5, 5.0]
    assert settings.brand_analysis_report_max_tokens == 7000
    assert settings.brand_analysis_caption_max_tokens == 1000
    assert settings.bedrock_vision_max_tokens == 2048
    assert not settings.bedrock_enable_prompt_cache
    assert settings.profiling_max_concurrency == 3


def test_production_rejects_embedding_bucket_region_mismatch() -> None:
    with pytest.raises(ValidationError, match="embedding media S3 region"):
        Settings(
            environment="production",
            jwt_secret="x" * 32,
            instagram_token_encryption_key="y" * 32,
            instagram_app_id="app",
            instagram_app_secret="secret",
            meta_trend_access_token="token",
            meta_instagram_business_account_id="account",
            media_s3_bucket="media-eu",
            media_s3_region="us-east-1",
            bedrock_generation_region="us-east-1",
            embedding_media_s3_bucket="media-us",
            embedding_media_s3_region="eu-central-1",
            transcribe_s3_bucket="transcribe",
        )


def test_empty_s3_endpoint_urls_are_normalized_to_none() -> None:
    settings = Settings(
        transcribe_s3_endpoint_url="",
        embedding_media_s3_endpoint_url="",
    )
    assert settings.transcribe_s3_endpoint_url is None
    assert settings.embedding_media_s3_endpoint_url is None
