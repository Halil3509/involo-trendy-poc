from datetime import UTC, datetime
from pathlib import Path

import pytest
from provider_doubles import FixtureMetadataProvider

from app.providers.metadata import OfficialMetaMetadataProvider

FIXTURE = Path(__file__).parent / "fixtures" / "metadata.json"


@pytest.mark.asyncio
async def test_fixture_metadata_parses_known_shortcode() -> None:
    provider = FixtureMetadataProvider(FIXTURE)
    metadata = await provider.fetch("Fixture_A1")
    assert metadata.owner_username == "fixture_creator"
    assert metadata.like_count == 12000
    assert metadata.view_count == 500000
    assert metadata.taken_at == datetime(2026, 7, 10, tzinfo=UTC)
    assert metadata.caption_text.startswith("A deterministic travel")


@pytest.mark.asyncio
async def test_fixture_metadata_missing_shortcode_raises() -> None:
    provider = FixtureMetadataProvider(FIXTURE)
    with pytest.raises(KeyError):
        await provider.fetch("does_not_exist")


@pytest.mark.asyncio
async def test_official_meta_provider_materializes_discovered_metadata() -> None:
    provider = OfficialMetaMetadataProvider()
    metadata = await provider.fetch(
        "C4n0n1c",
        {
            "official_source": True,
            "source_id": "meta-123",
            "caption_text": "Official caption",
            "caption": "ignored",
            "like_count": 42,
            "comment_count": 7,
            "taken_at": "2026-07-20T12:00:00Z",
            "video_url": "https://example.invalid/video.mp4",
        },
    )
    assert metadata.shortcode == "C4n0n1c"
    assert metadata.media_id == "meta-123"
    assert metadata.caption_text == "Official caption"
    assert metadata.like_count == 42
    assert metadata.comment_count == 7
    assert metadata.view_count == 0
    assert metadata.owner_follower_count == 0
    assert metadata.taken_at == datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    assert metadata.video_url == "https://example.invalid/video.mp4"


@pytest.mark.asyncio
async def test_official_meta_provider_requires_discovered_metadata() -> None:
    provider = OfficialMetaMetadataProvider()
    with pytest.raises(KeyError):
        await provider.fetch("missing")
