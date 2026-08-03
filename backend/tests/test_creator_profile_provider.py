from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import TransientError
from app.providers.creator_profile import (
    CreatorNotFoundError,
    CreatorProfileError,
    FixtureCreatorProfileProvider,
    GraphCreatorProfileProvider,
    PlaywrightCreatorProfileProvider,
    build_creator_profile_provider,
)
from app.providers.scraper import NeedsInterventionError, ProfileFetchError

FIXTURES = Path(__file__).parent / "fixtures"


def _graph_settings(**overrides: Any) -> Settings:
    return Settings(
        meta_trend_access_token="test_token",
        meta_instagram_business_account_id="178414000000000",
        creator_tracking_provider="graph_api",
        **overrides,
    )


def _graph_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=payload,
        request=httpx.Request("GET", "https://graph.facebook.com"),
    )


def test_factory_returns_graph_provider_by_default() -> None:
    provider = build_creator_profile_provider(_graph_settings())
    assert isinstance(provider, GraphCreatorProfileProvider)


def test_factory_selects_fixture_provider() -> None:
    settings = Settings(
        creator_tracking_provider="fixture",
        creator_tracking_fixture_path=str(FIXTURES / "creator_profile.json"),
    )
    provider = build_creator_profile_provider(settings)
    assert isinstance(provider, FixtureCreatorProfileProvider)


def test_factory_selects_playwright_provider() -> None:
    settings = Settings(creator_tracking_provider="playwright")
    provider = build_creator_profile_provider(settings)
    assert isinstance(provider, PlaywrightCreatorProfileProvider)


@pytest.mark.asyncio
async def test_fixture_provider_returns_profile_and_posts() -> None:
    provider = FixtureCreatorProfileProvider(FIXTURES / "creator_profile.json")

    snapshot = await provider.fetch_profile("Fixture_Creator")

    assert snapshot.username == "fixture_creator"
    assert snapshot.follower_count == 42000
    assert len(snapshot.posts) == 3
    assert snapshot.posts[0].shortcode == "CREATOR_A1"


@pytest.mark.asyncio
async def test_fixture_provider_unknown_username_raises() -> None:
    provider = FixtureCreatorProfileProvider(FIXTURES / "creator_profile.json")

    with pytest.raises(CreatorNotFoundError):
        await provider.fetch_profile("nobody")


@pytest.mark.asyncio
async def test_graph_fetch_profile_parses_business_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "business_discovery": {
            "id": "178414111111111",
            "username": "thecreator",
            "name": "The Creator",
            "biography": "Hello",
            "profile_picture_url": "https://cdn.invalid/pic.jpg",
            "followers_count": 12345,
            "follows_count": 100,
            "media_count": 87,
            "media": {
                "data": [
                    {
                        "id": "post_1",
                        "shortcode": "ABC123",
                        "caption": "hello world",
                        "media_type": "IMAGE",
                        "media_product_type": "FEED",
                        "media_url": "https://cdn.invalid/photo.jpg",
                        "thumbnail_url": "https://cdn.invalid/photo_thumb.jpg",
                        "permalink": "https://www.instagram.com/p/ABC123/",
                        "timestamp": "2025-01-01T12:00:00+0000",
                        "like_count": 500,
                        "comments_count": 25,
                        "view_count": 0,
                    }
                ]
            },
        }
    }

    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return _graph_response(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    snapshot = await provider.fetch_profile("thecreator")

    assert snapshot.username == "thecreator"
    assert snapshot.display_name == "The Creator"
    assert snapshot.follower_count == 12345
    assert snapshot.following_count == 100
    assert snapshot.media_count == 87
    assert snapshot.avatar_url == "https://cdn.invalid/pic.jpg"
    assert snapshot.is_private is False
    assert len(snapshot.posts) == 1
    post = snapshot.posts[0]
    assert post.shortcode == "ABC123"
    assert post.media_type == "IMAGE"
    assert post.caption == "hello world"
    assert post.like_count == 500
    assert post.comment_count == 25
    assert post.media_url == "https://cdn.invalid/photo.jpg"
    assert post.thumbnail_url == "https://cdn.invalid/photo_thumb.jpg"
    assert post.permalink == "https://www.instagram.com/p/ABC123/"


@pytest.mark.asyncio
async def test_graph_fetch_profile_carousel_expands_first_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "business_discovery": {
            "id": "178414111111111",
            "username": "carousel_user",
            "name": "Carousel User",
            "biography": "",
            "profile_picture_url": None,
            "followers_count": 1000,
            "follows_count": 0,
            "media_count": 1,
            "media": {
                "data": [
                    {
                        "id": "post_2",
                        "shortcode": "CAR456",
                        "caption": "swipe",
                        "media_type": "CAROUSEL_ALBUM",
                        "media_product_type": "FEED",
                        "permalink": "https://www.instagram.com/p/CAR456/",
                        "timestamp": "2025-02-01T12:00:00+0000",
                        "like_count": 100,
                        "comments_count": 5,
                        "view_count": 0,
                        "children": {
                            "data": [
                                {
                                    "id": "child_1",
                                    "media_url": "https://cdn.invalid/child.jpg",
                                    "thumbnail_url": "https://cdn.invalid/child_thumb.jpg",
                                    "media_type": "IMAGE",
                                }
                            ]
                        },
                    }
                ]
            },
        }
    }

    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return _graph_response(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    snapshot = await provider.fetch_profile("carousel_user")

    post = snapshot.posts[0]
    assert post.shortcode == "CAR456"
    assert post.media_type == "CAROUSEL_ALBUM"
    assert post.media_url == "https://cdn.invalid/child.jpg"
    assert post.thumbnail_url == "https://cdn.invalid/child_thumb.jpg"


@pytest.mark.asyncio
async def test_graph_fetch_profile_paginates_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def make_row(shortcode: str) -> dict[str, Any]:
        return {
            "id": f"post_{shortcode}",
            "shortcode": shortcode,
            "caption": "post",
            "media_type": "IMAGE",
            "media_product_type": "FEED",
            "media_url": f"https://cdn.invalid/{shortcode}.jpg",
            "permalink": f"https://www.instagram.com/p/{shortcode}/",
            "timestamp": "2025-03-01T12:00:00+0000",
            "like_count": 1,
            "comments_count": 0,
            "view_count": 0,
        }

    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        calls.append((args, kwargs))
        if len(calls) == 1:
            return _graph_response(
                {
                    "business_discovery": {
                        "id": "178414111111111",
                        "username": "paged_user",
                        "followers_count": 100,
                        "media_count": 3,
                        "media": {
                            "data": [make_row("A1"), make_row("A2")],
                            "paging": {"cursors": {"after": "cursor_1"}},
                        },
                    }
                }
            )
        return _graph_response(
            {
                "business_discovery": {
                    "id": "178414111111111",
                    "media": {
                        "data": [make_row("A3")],
                        "paging": {"cursors": {}},
                    },
                }
            }
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings(creator_tracking_max_posts=3))

    snapshot = await provider.fetch_profile("paged_user")

    assert len(snapshot.posts) == 3
    assert [post.shortcode for post in snapshot.posts] == ["A1", "A2", "A3"]
    assert len(calls) == 2
    second_fields = calls[1][1].get("params", {}).get("fields", "")
    assert "media.after(cursor_1)" in second_fields


@pytest.mark.asyncio
async def test_graph_fetch_profile_missing_business_discovery_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return _graph_response({})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    with pytest.raises(CreatorNotFoundError):
        await provider.fetch_profile("ghost")


@pytest.mark.asyncio
async def test_graph_fetch_profile_http_404_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return _graph_response(
            {"error": {"message": "not found", "code": 803}}, status_code=404
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    with pytest.raises(CreatorNotFoundError):
        await provider.fetch_profile("ghost")


@pytest.mark.asyncio
async def test_graph_fetch_profile_http_400_code_100_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return _graph_response(
            {"error": {"message": "Unsupported get request.", "code": 100}},
            status_code=400,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    with pytest.raises(CreatorNotFoundError):
        await provider.fetch_profile("ghost")


@pytest.mark.asyncio
async def test_graph_fetch_profile_http_401_raises_needs_intervention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return _graph_response(
            {"error": {"message": "Invalid token", "code": 190}}, status_code=401
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    with pytest.raises(NeedsInterventionError):
        await provider.fetch_profile("thecreator")


@pytest.mark.asyncio
async def test_graph_fetch_profile_http_429_raises_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            status_code=429,
            json={"error": {"message": "rate limit", "code": 4}},
            headers={"Retry-After": "120"},
            request=httpx.Request("GET", "https://graph.facebook.com"),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    with pytest.raises(TransientError) as exc_info:
        await provider.fetch_profile("thecreator")
    assert exc_info.value.retry_after == 120


@pytest.mark.asyncio
async def test_graph_reels_without_media_url_uses_thumbnail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "business_discovery": {
            "id": "178414111111111",
            "username": "reels_user",
            "followers_count": 1000,
            "media_count": 1,
            "media": {
                "data": [
                    {
                        "id": "reel_1",
                        "shortcode": "REEL1",
                        "caption": "reel",
                        "media_type": "VIDEO",
                        "media_product_type": "REELS",
                        "thumbnail_url": "https://cdn.invalid/reel_thumb.jpg",
                        "permalink": "https://www.instagram.com/p/REEL1/",
                        "timestamp": "2025-04-01T12:00:00+0000",
                        "like_count": 1000,
                        "comments_count": 50,
                        "view_count": 50000,
                    }
                ]
            },
        }
    }

    async def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return _graph_response(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = GraphCreatorProfileProvider(_graph_settings())

    snapshot = await provider.fetch_profile("reels_user")

    post = snapshot.posts[0]
    assert post.media_type == "REELS"
    assert post.media_url is None
    assert post.thumbnail_url == "https://cdn.invalid/reel_thumb.jpg"


def _web_profile_user() -> dict[str, Any]:
    return {
        "username": "playwright_user",
        "full_name": "Playwright User",
        "biography": "travel and food",
        "profile_pic_url": "https://cdn.invalid/avatar.jpg",
        "edge_followed_by": {"count": 12345},
        "edge_follow": {"count": 100},
        "edge_owner_to_timeline_media": {
            "count": 2,
            "edges": [
                {
                    "node": {
                        "__typename": "GraphVideo",
                        "shortcode": "REEL1",
                        "is_video": True,
                        "taken_at_timestamp": 1704067200,
                        "edge_media_to_caption": {
                            "edges": [{"node": {"text": "reel caption"}}]
                        },
                        "edge_media_preview_like": {"count": 500},
                        "edge_media_to_comment": {"count": 25},
                        "video_view_count": 10000,
                        "video_url": "https://cdn.invalid/reel.mp4",
                        "display_url": "https://cdn.invalid/reel_display.jpg",
                    }
                },
                {
                    "node": {
                        "__typename": "GraphImage",
                        "shortcode": "IMG2",
                        "is_video": False,
                        "taken_at_timestamp": 1704153600,
                        "edge_media_to_caption": {
                            "edges": [{"node": {"text": "photo caption"}}]
                        },
                        "edge_media_preview_like": {"count": 200},
                        "edge_media_to_comment": {"count": 10},
                        "display_url": "https://cdn.invalid/photo.jpg",
                    }
                },
            ],
        },
        "is_private": False,
    }


@pytest.mark.asyncio
async def test_playwright_fetch_profile_parses_web_profile_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _web_profile_user()

    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        assert username == "playwright_user"
        assert limit == 12
        return user

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )
    settings = Settings(creator_tracking_provider="playwright")
    provider = PlaywrightCreatorProfileProvider(settings)

    snapshot = await provider.fetch_profile("playwright_user")

    assert snapshot.username == "playwright_user"
    assert snapshot.display_name == "Playwright User"
    assert snapshot.bio == "travel and food"
    assert snapshot.avatar_url == "https://cdn.invalid/avatar.jpg"
    assert snapshot.follower_count == 12345
    assert snapshot.following_count == 100
    assert snapshot.media_count == 2
    assert snapshot.is_private is False
    assert len(snapshot.posts) == 2
    first, second = snapshot.posts
    assert first.shortcode == "REEL1"
    assert first.media_type == "REELS"
    assert first.caption == "reel caption"
    assert first.like_count == 500
    assert first.comment_count == 25
    assert first.view_count == 10000
    assert first.media_url == "https://cdn.invalid/reel.mp4"
    assert first.thumbnail_url == "https://cdn.invalid/reel_display.jpg"
    assert second.shortcode == "IMG2"
    assert second.media_type == "IMAGE"
    assert second.caption == "photo caption"
    assert second.view_count == 0


@pytest.mark.asyncio
async def test_playwright_fetch_profile_limits_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _web_profile_user()
    user["edge_owner_to_timeline_media"]["edges"].append(
        {
            "node": {
                "__typename": "GraphImage",
                "shortcode": "IMG3",
                "is_video": False,
                "taken_at_timestamp": 1704240000,
                "edge_media_to_caption": {"edges": []},
                "edge_media_preview_like": {"count": 0},
                "edge_media_to_comment": {"count": 0},
                "display_url": "https://cdn.invalid/photo3.jpg",
            }
        }
    )

    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        assert limit == 2
        trimmed = dict(user)
        timeline = dict(trimmed["edge_owner_to_timeline_media"])
        timeline["edges"] = timeline["edges"][:limit]
        trimmed["edge_owner_to_timeline_media"] = timeline
        return trimmed

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )
    settings = Settings(
        creator_tracking_provider="playwright",
        creator_tracking_max_posts=2,
    )
    provider = PlaywrightCreatorProfileProvider(settings)

    snapshot = await provider.fetch_profile("playwright_user")

    assert len(snapshot.posts) == 2


@pytest.mark.asyncio
async def test_playwright_not_found_maps_to_creator_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        raise ProfileFetchError("not_found", "creator not found")

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )
    provider = PlaywrightCreatorProfileProvider(Settings())

    with pytest.raises(CreatorNotFoundError):
        await provider.fetch_profile("ghost")


@pytest.mark.asyncio
async def test_playwright_private_maps_to_creator_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        raise ProfileFetchError("private", "creator is private")

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )
    provider = PlaywrightCreatorProfileProvider(Settings())

    with pytest.raises(CreatorNotFoundError):
        await provider.fetch_profile("private_user")


@pytest.mark.asyncio
async def test_playwright_request_failed_maps_to_profile_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        raise ProfileFetchError("request_failed", "upstream error")

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )
    provider = PlaywrightCreatorProfileProvider(Settings())

    with pytest.raises(CreatorProfileError):
        await provider.fetch_profile("broken")


@pytest.mark.asyncio
async def test_playwright_needs_intervention_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        raise NeedsInterventionError("challenge")

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )
    provider = PlaywrightCreatorProfileProvider(Settings())

    with pytest.raises(NeedsInterventionError):
        await provider.fetch_profile("challenge_user")


@pytest.mark.asyncio
async def test_playwright_rate_limit_raises_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        self: Any, username: str, limit: int, on_event: Any = None
    ) -> dict[str, Any]:
        raise TransientError(
            "Instagram profile API rate limited for @majasrecipes:",
            retry_after=120.0,
        )

    monkeypatch.setattr(
        "app.providers.scraper.InstagramScraper.fetch_creator_profile",
        fake_fetch,
    )
    provider = PlaywrightCreatorProfileProvider(Settings())

    with pytest.raises(TransientError) as exc_info:
        await provider.fetch_profile("majasrecipes")
    assert exc_info.value.retry_after == 120.0


def test_graph_build_fields_omits_shortcode_in_media() -> None:
    provider = GraphCreatorProfileProvider(_graph_settings())
    fields = provider._build_fields("testuser", 10, None)

    assert "shortcode" not in fields
    assert "permalink" in fields
