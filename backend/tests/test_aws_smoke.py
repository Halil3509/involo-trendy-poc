import asyncio
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import boto3
import pytest

from app.core.config import Settings
from app.providers.embedding import BedrockEmbeddingProvider
from app.providers.media import Keyframe, StoredMedia
from app.providers.profile_summary import BedrockProfileSummaryProvider, ProfileSummaryContext
from app.providers.recommendations import (
    BedrockRecommendationProvider,
    RecommendationContext,
    TrendContext,
)
from app.providers.vision import NovaVisionProvider


def _s3(settings: Settings) -> Any:
    options: dict[str, Any] = {
        "region_name": settings.media_s3_region,
        "endpoint_url": settings.transcribe_s3_endpoint_url,
    }
    if settings.transcribe_s3_access_key_id:
        options["aws_access_key_id"] = settings.transcribe_s3_access_key_id
    if settings.transcribe_s3_secret_access_key:
        options["aws_secret_access_key"] = (
            settings.transcribe_s3_secret_access_key.get_secret_value()
        )
    return boto3.client("s3", **options)


def _embedding_s3(settings: Settings) -> Any:
    options: dict[str, Any] = {
        "region_name": settings.embedding_media_s3_region,
        "endpoint_url": settings.embedding_media_s3_endpoint_url,
    }
    if settings.embedding_media_s3_endpoint_url and settings.transcribe_s3_access_key_id:
        options["aws_access_key_id"] = settings.transcribe_s3_access_key_id
    if (
        settings.embedding_media_s3_endpoint_url
        and settings.transcribe_s3_secret_access_key
    ):
        options["aws_secret_access_key"] = (
            settings.transcribe_s3_secret_access_key.get_secret_value()
        )
    return boto3.client("s3", **options)


def _generate_media_assets(settings: Settings, image: Path, video: Path) -> None:
    subprocess.run(
        [
            settings.ffmpeg_binary,
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1",
            "-frames:v",
            "1",
            "-y",
            str(image),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [
            settings.ffmpeg_binary,
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.real_aws
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_AWS_SMOKE") != "1",
    reason="real AWS smoke test is opt-in",
)
@pytest.mark.asyncio
async def test_real_bedrock_embedding_smoke() -> None:
    settings = Settings()
    provider = BedrockEmbeddingProvider(settings)
    vector = await provider.embed("involo smoke test content")
    assert len(vector) == settings.vector_size


@pytest.mark.real_aws
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_AWS_SMOKE") != "1",
    reason="real AWS smoke test is opt-in",
)
@pytest.mark.asyncio
async def test_real_bedrock_profile_summary_smoke() -> None:
    settings = Settings()
    provider = BedrockProfileSummaryProvider(settings)
    summary = await provider.summarize(
        ProfileSummaryContext(
            username="involo_smoke",
            follower_count=1000,
            content_count=2,
            average_viral_score=42.0,
            vector_std_dev=0.2,
            content_samples=["Sürdürülebilir moda üzerine kısa video"],
        )
    )
    assert summary.strip()


@pytest.mark.real_aws
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_AWS_SMOKE") != "1",
    reason="real AWS smoke test is opt-in",
)
@pytest.mark.asyncio
async def test_real_bedrock_recommendation_smoke() -> None:
    settings = Settings()
    provider = BedrockRecommendationProvider(settings)
    result = await provider.generate(
        RecommendationContext(
            profile_summary="Sürdürülebilir yaşam üzerine samimi kısa videolar üretiyor.",
            trends=[
                TrendContext(
                    title="Evde dönüşüm",
                    text="Eski eşyaları yeniden kullanmak için üç pratik adım.",
                    viral_score=82.0,
                )
            ],
            past_ideas=[],
            count=3,
        )
    )
    assert len(result.recommendations) == 3


@pytest.mark.real_aws
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_S3_SMOKE") != "1",
    reason="real S3 read/write smoke test is opt-in",
)
def test_real_media_s3_round_trip_smoke() -> None:
    settings = Settings()
    if not settings.media_s3_bucket:
        pytest.skip("media S3 bucket is not configured")
    client = _s3(settings)
    key = f"{settings.media_s3_prefix}/smoke/{uuid.uuid4().hex}.txt"
    try:
        client.put_object(Bucket=settings.media_s3_bucket, Key=key, Body=b"ok")
        result = client.get_object(Bucket=settings.media_s3_bucket, Key=key)
        assert result["Body"].read() == b"ok"
    finally:
        client.delete_object(Bucket=settings.media_s3_bucket, Key=key)


@pytest.mark.real_aws
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_AWS_SMOKE") != "1",
    reason="real AWS smoke test is opt-in",
)
def test_real_transcribe_api_access_smoke() -> None:
    settings = Settings()
    result = boto3.client(
        "transcribe", region_name=settings.aws_region
    ).list_transcription_jobs(MaxResults=1)
    assert "TranscriptionJobSummaries" in result


@pytest.mark.real_aws
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_MEDIA_SMOKE") != "1",
    reason="real Bedrock media smoke test is opt-in",
)
@pytest.mark.asyncio
async def test_real_nova_image_video_embedding_and_vision_smoke() -> None:
    settings = Settings()
    if not (settings.media_s3_bucket and settings.embedding_media_s3_bucket):
        pytest.skip("generation and embedding media S3 buckets are not configured")
    client = _s3(settings)
    embedding_client = _embedding_s3(settings)
    prefix = f"{settings.media_s3_prefix}/smoke/{uuid.uuid4().hex}"
    image_key = f"{prefix}.jpg"
    video_key = f"{prefix}.mp4"
    with tempfile.TemporaryDirectory() as directory:
        image = Path(directory) / "smoke.jpg"
        video = Path(directory) / "smoke.mp4"
        await asyncio.to_thread(_generate_media_assets, settings, image, video)
        try:
            client.upload_file(
                str(image),
                settings.media_s3_bucket,
                image_key,
                ExtraArgs={"ContentType": "image/jpeg"},
            )
            client.upload_file(
                str(video),
                settings.media_s3_bucket,
                video_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            embedding_client.upload_file(
                str(image),
                settings.embedding_media_s3_bucket,
                image_key,
                ExtraArgs={"ContentType": "image/jpeg"},
            )
            embedding_client.upload_file(
                str(video),
                settings.embedding_media_s3_bucket,
                video_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            image_media = StoredMedia(
                settings.media_s3_bucket,
                image_key,
                f"s3://{settings.media_s3_bucket}/{image_key}",
                "image/jpeg",
                "smoke",
                image.stat().st_size,
            )
            video_media = StoredMedia(
                settings.media_s3_bucket,
                video_key,
                f"s3://{settings.media_s3_bucket}/{video_key}",
                "video/mp4",
                "smoke",
                video.stat().st_size,
            )
            embedding_image = StoredMedia(
                settings.embedding_media_s3_bucket,
                image_key,
                f"s3://{settings.embedding_media_s3_bucket}/{image_key}",
                "image/jpeg",
                "smoke",
                image.stat().st_size,
                settings.embedding_media_s3_region,
            )
            embedding_video = StoredMedia(
                settings.embedding_media_s3_bucket,
                video_key,
                f"s3://{settings.embedding_media_s3_bucket}/{video_key}",
                "video/mp4",
                "smoke",
                video.stat().st_size,
                settings.embedding_media_s3_region,
            )
            embedding = BedrockEmbeddingProvider(settings)
            assert len(await embedding.embed("provider smoke test")) == settings.vector_size
            assert len(await embedding.embed_media(embedding_image.uri)) == settings.vector_size
            assert len(await embedding.embed_media(embedding_video.uri)) == settings.vector_size
            analysis = await NovaVisionProvider(settings).analyze(
                video_media,
                [Keyframe(0, image_media)],
                caption="provider smoke test",
            )
            assert 0 <= analysis.confidence <= 1
        finally:
            client.delete_objects(
                Bucket=settings.media_s3_bucket,
                Delete={
                    "Objects": [{"Key": image_key}, {"Key": video_key}],
                    "Quiet": True,
                },
            )
            embedding_client.delete_objects(
                Bucket=settings.embedding_media_s3_bucket,
                Delete={
                    "Objects": [{"Key": image_key}, {"Key": video_key}],
                    "Quiet": True,
                },
            )
