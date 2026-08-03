import os

import pytest

from app.core.config import Settings
from app.providers.instagram_profile import GraphInstagramProfileProvider
from app.providers.scraper import InstagramScraper
from app.providers.trends import MetaHashtagTrendSource


@pytest.mark.real_instagram
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_INSTAGRAM_SMOKE") != "1",
    reason="real Instagram smoke test is opt-in",
)
@pytest.mark.asyncio
async def test_real_instagram_smoke() -> None:
    settings = Settings(scraper_adapter="instagram")
    items = await InstagramScraper(settings).scrape(["travel"], 1)
    assert len(items) <= 1


@pytest.mark.real_instagram
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_INSTAGRAM_PROFILE_SMOKE") != "1",
    reason="real Instagram profile smoke test is opt-in",
)
@pytest.mark.asyncio
async def test_real_instagram_profile_smoke() -> None:
    token = os.environ["INVOLO_INSTAGRAM_TEST_ACCESS_TOKEN"]
    provider = GraphInstagramProfileProvider(Settings())
    account = await provider.fetch_account(token)
    assert account.id
    assert account.username


@pytest.mark.real_instagram
@pytest.mark.skipif(
    os.getenv("INVOLO_RUN_REAL_META_SMOKE") != "1",
    reason="official Meta API smoke test is opt-in",
)
@pytest.mark.asyncio
async def test_real_official_meta_hashtag_and_profile_smoke() -> None:
    settings = Settings()
    if not (
        settings.meta_trend_access_token
        and settings.meta_instagram_business_account_id
        and settings.instagram_app_id
        and settings.instagram_app_secret
    ):
        pytest.skip("official Meta credentials are not configured")
    token = settings.meta_trend_access_token.get_secret_value()
    items = await MetaHashtagTrendSource(settings).discover(
        os.getenv("INVOLO_META_SMOKE_HASHTAG", "travel"),
        access_token=token,
        instagram_business_account_id=settings.meta_instagram_business_account_id,
        limit=1,
    )
    assert len(items) <= 1
    profile_token = os.getenv("INVOLO_INSTAGRAM_TEST_ACCESS_TOKEN", token)
    account = await GraphInstagramProfileProvider(settings).fetch_account(profile_token)
    assert account.id
