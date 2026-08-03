import math

import pytest
from provider_doubles import FakeEmbeddingProvider

from app.core.config import Settings
from app.providers.embedding import BedrockEmbeddingProvider


@pytest.mark.asyncio
async def test_fake_embedding_is_deterministic() -> None:
    provider = FakeEmbeddingProvider(64)
    first = await provider.embed("hello world")
    second = await provider.embed("hello world")
    assert first == second


@pytest.mark.asyncio
async def test_fake_embedding_has_configured_size_and_is_normalized() -> None:
    provider = FakeEmbeddingProvider(128)
    vector = await provider.embed("some content text")
    assert len(vector) == 128
    magnitude = math.sqrt(sum(value * value for value in vector))
    assert magnitude == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_different_text_produces_different_vectors() -> None:
    provider = FakeEmbeddingProvider(64)
    assert await provider.embed("a") != await provider.embed("b")


def test_nova_image_embedding_request_uses_image_schema() -> None:
    provider = BedrockEmbeddingProvider(
        Settings(vector_size=8, media_s3_bucket_owner="123456789012")
    )
    request = provider._nova_request(
        None, "s3://media/keyframes/frame.webp", "GENERIC_INDEX"
    )
    params = request["singleEmbeddingParams"]

    assert "video" not in params
    assert params["image"] == {
        "format": "webp",
        "source": {
            "s3Location": {
                "uri": "s3://media/keyframes/frame.webp",
                "bucketOwner": "123456789012",
            }
        },
    }


def test_nova_video_embedding_request_uses_video_schema() -> None:
    provider = BedrockEmbeddingProvider(Settings(vector_size=8))
    request = provider._nova_request(
        None, "s3://media/segments/segment.mp4", "GENERIC_INDEX"
    )
    params = request["singleEmbeddingParams"]

    assert "image" not in params
    assert params["video"] == {
        "format": "mp4",
        "source": {
            "s3Location": {"uri": "s3://media/segments/segment.mp4"}
        },
        "embeddingMode": "AUDIO_VIDEO_COMBINED",
    }


def test_nova_embedding_media_uses_embedding_bucket_owner() -> None:
    provider = BedrockEmbeddingProvider(
        Settings(
            vector_size=8,
            embedding_media_s3_bucket="embedding-media",
            embedding_media_s3_bucket_owner="210987654321",
            media_s3_bucket_owner="123456789012",
        )
    )

    request = provider._nova_request(
        None, "s3://embedding-media/segments/a.mp4", "GENERIC_INDEX"
    )

    location = request["singleEmbeddingParams"]["video"]["source"]["s3Location"]
    assert location["bucketOwner"] == "210987654321"
