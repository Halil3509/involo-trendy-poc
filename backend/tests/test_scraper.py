import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from provider_doubles import FixtureScraper

from app.core.config import Settings
from app.providers.scraper import (
    InstagramScraper,
    NeedsInterventionError,
    _extract_post_metadata,
    _extract_timestamp_from_html,
    _parse_timestamp,
    _shortcode_to_media_id,
    parse_instagram_url,
)


@pytest.mark.parametrize(
    ("url", "shortcode"),
    [
        ("https://www.instagram.com/reel/Ab_c-12/?igsh=x", "Ab_c-12"),
        ("https://instagram.com/p/Post123/", "Post123"),
        ("/reels/Reel123/", "Reel123"),
    ],
)
def test_parse_instagram_url(url: str, shortcode: str) -> None:
    canonical, parsed = parse_instagram_url(url)
    assert parsed == shortcode
    assert canonical == f"https://www.instagram.com/reel/{shortcode}/"


@pytest.mark.parametrize(
    "url",
    ["https://evil.example/reel/x/", "https://www.instagram.com/explore/", "/stories/user/1/"],
)
def test_parse_instagram_url_rejects_non_content(url: str) -> None:
    with pytest.raises(ValueError):
        parse_instagram_url(url)


@pytest.mark.asyncio
async def test_fixture_scraper_is_filtered_and_deterministic() -> None:
    path = Path(__file__).parent / "fixtures" / "instagram.json"
    scraper = FixtureScraper(path)
    first = await scraper.scrape(["travel"], 10)
    second = await scraper.scrape(["travel"], 10)
    assert first == second
    assert len(first) == 1
    assert first[0].shortcode == "Fixture_A1"
    assert first[0].discovered_keyword == "travel"


def _html_with_taken_at(shortcode: str, timestamp: int) -> str:
    payload = {
        "entry_data": {
            "PostPage": [
                {
                    "graphql": {
                        "shortcode_media": {
                            "shortcode": shortcode,
                            "taken_at_timestamp": timestamp,
                            "owner": {"username": "user"},
                        }
                    }
                }
            ]
        }
    }
    return (
        '<html><body><script type="text/javascript">window._sharedData = '
        f"{json.dumps(payload)};"
        '</script></body></html>'
    )


class _FakeResponse:
    def __init__(self, text: str, ok: bool = True) -> None:
        self._text = text
        self.ok = ok

    async def text(self) -> str:
        return self._text

    async def json(self) -> Any:
        return json.loads(self._text)


class _FakeRequest:
    def __init__(
        self,
        responses: dict[str, _FakeResponse] | None = None,
        default_timestamp: int | None = None,
        tag_responses: dict[str, _FakeResponse] | None = None,
        tag_post_responses: dict[str, _FakeResponse] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default_timestamp = default_timestamp or int(datetime.now(UTC).timestamp())
        self._tag_responses = tag_responses or {}
        self._tag_post_responses = tag_post_responses or {}

    def _tag_from_url(self, url: str) -> str:
        match = re.search(r"/feed/tag/([^/?]+)|/tags/([^/?]+)/sections", url)
        if not match:
            return ""
        return (match.group(1) or match.group(2) or "").lower()

    async def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        try:
            _, shortcode = parse_instagram_url(url)
        except ValueError:
            shortcode = "UNKNOWN"
        if shortcode in self._responses:
            return self._responses[shortcode]
        tag = self._tag_from_url(url)
        if tag in self._tag_responses:
            return self._tag_responses[tag]
        return _FakeResponse(_html_with_taken_at(shortcode, self._default_timestamp))

    async def post(self, url: str, **_kwargs: object) -> _FakeResponse:
        tag = self._tag_from_url(url)
        if tag in self._tag_post_responses:
            return self._tag_post_responses[tag]
        if tag in self._tag_responses:
            return self._tag_responses[tag]
        return _FakeResponse(json.dumps({"status": "fail"}), ok=False)


class _FakeLocator:
    def __init__(
        self,
        page: Any,
        text: str | None = None,
        href_batches: list[list[str]] | None = None,
    ) -> None:
        self._page = page
        self._text = text or ""
        self._href_batches = href_batches or []

    async def inner_text(self, **_kwargs: object) -> str:
        return self._text

    async def text_content(self, **_kwargs: object) -> str | None:
        return self._text

    async def count(self) -> int:
        idx = self._page._scroll_count
        if not self._href_batches:
            return 0
        if idx < len(self._href_batches):
            return len(self._href_batches[idx])
        return 0

    async def evaluate_all(self, _expression: str, **_kwargs: object) -> list[str]:
        idx = self._page._scroll_count
        if not self._href_batches:
            return []
        if idx < len(self._href_batches):
            return self._href_batches[idx]
        return []


class _FakePage:
    def __init__(
        self,
        body_text: str | None = None,
        href_batches: list[list[str]] | None = None,
        request: _FakeRequest | None = None,
    ) -> None:
        self._scroll_count = 0
        self._locator = _FakeLocator(self, body_text, href_batches)
        self.url = "https://www.instagram.com/accounts/login/"
        self.request = request or _FakeRequest()

    def locator(self, _selector: str) -> _FakeLocator:
        return self._locator

    async def screenshot(self, **_kwargs: object) -> bytes:
        return b""

    async def content(self) -> str:
        return "<html></html>"

    async def evaluate(self, expression: str, **_kwargs: object) -> Any:
        if "window.scrollTo(" in expression or "window.scrollBy(" in expression:
            self._scroll_count += 1
        return None

    async def wait_for_timeout(self, _milliseconds: float) -> None:
        return None

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.url = url


NORMAL_LOGIN_TEXT = (
    "Log into Instagram\n"
    "Mobile number, username or email\n"
    "Password\n"
    "Log in\n"
    "Forgot password?\n"
    "Log in with Facebook\n"
    "Create new account"
)


@pytest.mark.asyncio
async def test_login_intervention_text_is_detected(tmp_path: Path) -> None:
    settings = Settings(
        instagram_username="u",
        instagram_password="p",
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)
    page: Any = cast(Any, _FakePage("We noticed suspicious login activity."))
    with pytest.raises(NeedsInterventionError, match="requires verification"):
        await scraper._raise_for_intervention_text(page)


@pytest.mark.asyncio
async def test_login_intervention_text_ignored_for_normal_login_page(tmp_path: Path) -> None:
    settings = Settings(
        instagram_username="u",
        instagram_password="p",
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)
    page: Any = cast(Any, _FakePage(NORMAL_LOGIN_TEXT))
    await scraper._raise_for_intervention_text(page)


@pytest.mark.asyncio
async def test_login_intervention_text_ignores_script_tokens(tmp_path: Path) -> None:
    settings = Settings(
        instagram_username="u",
        instagram_password="p",
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)
    body_text = (
        NORMAL_LOGIN_TEXT
        + "\nXFB_HZW_CHALLENGE_COMPLETE_SUBSCRIBE sampleRate sampleRateLimit "
        "blockedAccount recaptcha robotNotHere"
    )
    page: Any = cast(Any, _FakePage(body_text))
    await scraper._raise_for_intervention_text(page)


@pytest.mark.asyncio
async def test_instagram_scraper_scrolls_until_reels_per_keyword_limit(
    tmp_path: Path,
) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    href_batches = [
        ["/reel/A/", "/reel/B/"],
        ["/reel/A/", "/reel/C/"],
        ["/reel/D/", "/reel/E/"],
    ]
    page: Any = _FakePage(href_batches=href_batches)
    result = await scraper._collect_reels(page, "fashion", 5)

    assert [item.shortcode for item in result] == ["A", "B", "C", "D", "E"]
    assert len(result) == 5
    assert page._scroll_count == 2


@pytest.mark.asyncio
async def test_instagram_scraper_skips_content_older_than_max_age(
    tmp_path: Path,
) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    old = datetime.now(UTC) - timedelta(days=60)
    recent = datetime.now(UTC) - timedelta(days=1)
    request = _FakeRequest(
        responses={
            "A": _FakeResponse(_html_with_taken_at("A", int(recent.timestamp()))),
            "B": _FakeResponse(_html_with_taken_at("B", int(old.timestamp()))),
            "C": _FakeResponse(_html_with_taken_at("C", int(recent.timestamp()))),
        }
    )
    href_batches = [["/reel/A/", "/reel/B/", "/reel/C/"]]
    page: Any = _FakePage(href_batches=href_batches, request=request)
    result = await scraper._collect_reels(page, "fashion", 2)

    assert [item.shortcode for item in result] == ["A", "C"]
    assert len(result) == 2


def test_extract_post_metadata_parses_graphql_json() -> None:
    payload = {
        "graphql": {
            "shortcode_media": {
                "shortcode": "GraphQL1",
                "taken_at_timestamp": 1_700_000_000,
                "owner": {
                    "username": "graphql_creator",
                    "edge_followed_by": {"count": 1_200},
                },
                "edge_media_preview_like": {"count": 42},
                "edge_media_to_comment": {"count": 7},
                "video_view_count": 5_000,
                "video_url": "https://example.invalid/graphql.mp4",
                "edge_media_to_caption": {
                    "edges": [{"node": {"text": "GraphQL caption"}}]
                },
                "display_url": "https://example.invalid/graphql.jpg",
            }
        }
    }
    metadata = _extract_post_metadata("GraphQL1", payload)
    assert metadata["taken_at"] == 1_700_000_000
    assert metadata["owner_username"] == "graphql_creator"
    assert metadata["owner_follower_count"] == 1_200
    assert metadata["like_count"] == 42
    assert metadata["comment_count"] == 7
    assert metadata["view_count"] == 5_000
    assert metadata["video_url"] == "https://example.invalid/graphql.mp4"
    assert metadata["caption_text"] == "GraphQL caption"
    assert metadata["thumbnail_url"] == "https://example.invalid/graphql.jpg"


def test_extract_post_metadata_parses_mobile_items_json() -> None:
    payload = {
        "items": [
            {
                "code": "Mobile1",
                "taken_at": 1_700_000_001,
                "owner": {
                    "username": "mobile_creator",
                    "follower_count": 800,
                },
                "like_count": 10,
                "comment_count": 2,
                "play_count": 300,
                "video_versions": [{"url": "https://example.invalid/mobile.mp4"}],
                "caption": {"text": "Mobile caption"},
                "image_versions2": {
                    "candidates": [{"url": "https://example.invalid/mobile.jpg"}]
                },
            }
        ]
    }
    metadata = _extract_post_metadata("Mobile1", payload)
    assert metadata["taken_at"] == 1_700_000_001
    assert metadata["owner_username"] == "mobile_creator"
    assert metadata["owner_follower_count"] == 800
    assert metadata["like_count"] == 10
    assert metadata["comment_count"] == 2
    assert metadata["view_count"] == 300
    assert metadata["video_url"] == "https://example.invalid/mobile.mp4"
    assert metadata["caption_text"] == "Mobile caption"
    assert metadata["thumbnail_url"] == "https://example.invalid/mobile.jpg"

@pytest.mark.asyncio
async def test_instagram_scraper_skips_existing_and_keeps_scrolling(
    tmp_path: Path,
) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)

    async def is_existing(canonical_url: str) -> bool:
        return canonical_url == "https://www.instagram.com/reel/A/"

    href_batches = [
        ["/reel/A/", "/reel/B/"],
        ["/reel/C/", "/reel/A/"],
    ]
    request = _FakeRequest()
    page: Any = _FakePage(href_batches=href_batches, request=request)
    result = await scraper._collect_reels(
        page, "fashion", 2, is_existing=is_existing
    )
    assert [item.shortcode for item in result] == ["B", "C"]
    assert len(result) == 2


@pytest.mark.asyncio
async def test_instagram_scraper_ignores_initial_empty_batches(
    tmp_path: Path,
) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    href_batches = [
        [],
        [],
        ["/reel/A/", "/reel/B/", "/reel/C/"],
    ]
    page: Any = _FakePage(href_batches=href_batches)
    result = await scraper._collect_reels(page, "fashion", 2)

    assert [item.shortcode for item in result] == ["A", "B"]
    assert len(result) == 2
    assert page._scroll_count >= 2


@pytest.mark.asyncio
async def test_instagram_scraper_stops_when_feed_exhausted(
    tmp_path: Path,
) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    href_batches = [
        ["/reel/A/", "/reel/B/"],
    ]
    page: Any = _FakePage(href_batches=href_batches)
    result = await scraper._collect_reels(page, "fashion", 10)

    assert [item.shortcode for item in result] == ["A", "B"]
    assert len(result) == 2
    assert page._scroll_count >= 1


def test_shortcode_to_media_id_roundtrip() -> None:
    """Shortcodes encode media_id using Instagram's custom base64 alphabet."""
    for media_id in (0, 1, 123456789012345, 1864494021045491007):
        # Manually encode with the same alphabet to test round-trip.
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        if media_id == 0:
            shortcode = alphabet[0]
        else:
            chars = []
            remaining = media_id
            while remaining > 0:
                remaining, rem = divmod(remaining, 64)
                chars.append(alphabet[rem])
            shortcode = "".join(reversed(chars))
        assert _shortcode_to_media_id(shortcode) == media_id


def test_parse_timestamp_handles_int_float_iso() -> None:
    ts = 1_700_000_000
    assert _parse_timestamp(ts) == ts
    assert _parse_timestamp(float(ts)) == ts
    assert _parse_timestamp(str(ts)) == ts
    iso = datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")
    assert _parse_timestamp(iso) == ts
    assert _parse_timestamp(None) is None
    assert _parse_timestamp("") is None


def test_extract_timestamp_from_html_jsonld() -> None:
    timestamp = 1_700_000_000
    iso = datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")
    html = (
        '<html><head>'
        '<script type="application/ld+json">'
        '{"@type":"VideoObject","url":"https://www.instagram.com/p/ABC123/",'
        '"uploadDate":"' + iso + '"}'
        '</script></head></html>'
    )
    assert _extract_timestamp_from_html("ABC123", html) == timestamp


def test_extract_timestamp_from_html_time_tag() -> None:
    timestamp = 1_700_000_000
    iso = datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")
    html = f'<html><body><time datetime="{iso}">Oct 14, 2023</time></body></html>'
    assert _extract_timestamp_from_html("ABC123", html) == timestamp


def test_extract_post_metadata_parses_iso_upload_date() -> None:
    iso = "2024-11-14T12:00:00Z"
    expected = int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    payload = {
        "graphql": {
            "shortcode_media": {
                "shortcode": "GraphQL2",
                "uploadDate": iso,
                "owner": {"username": "graphql_creator"},
            }
        }
    }
    metadata = _extract_post_metadata("GraphQL2", payload)
    assert metadata["taken_at"] == expected
    assert metadata["owner_username"] == "graphql_creator"


class _FakeInternalResponse:
    def __init__(self, text: str, ok: bool = True) -> None:
        self._text = text
        self.ok = ok

    async def text(self) -> str:
        return self._text

    async def json(self) -> Any:
        return json.loads(self._text)


class _FakeInternalRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def get(self, url: str, **_kwargs: object) -> _FakeInternalResponse:
        if "/media/" in url and "/info/" in url:
            return _FakeInternalResponse(json.dumps(self._payload))
        return _FakeInternalResponse("<html></html>")


@pytest.mark.asyncio
async def test_internal_api_metadata_parses_items_response(tmp_path: Path) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    timestamp = 1_700_000_001
    payload = {
        "items": [
            {
                "code": "Mobile1",
                "taken_at": timestamp,
                "owner": {"username": "mobile_creator", "follower_count": 800},
                "like_count": 10,
                "comment_count": 2,
                "play_count": 300,
                "video_versions": [{"url": "https://example.invalid/mobile.mp4"}],
                "caption": {"text": "Mobile caption"},
            }
        ]
    }
    page: Any = _FakePage(request=_FakeInternalRequest(payload))
    result = await scraper._internal_api_metadata(page, "Mobile1")
    assert result is not None
    assert result["taken_at"] == timestamp
    assert result["owner_username"] == "mobile_creator"


@pytest.mark.asyncio
async def test_post_metadata_falls_back_to_internal_api(tmp_path: Path) -> None:
    """When the public page returns no date, _post_metadata resolves it via the internal API."""
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    timestamp = 1_700_000_002
    payload = {
        "items": [
            {
                "code": "Fallback1",
                "taken_at": timestamp,
                "owner": {"username": "fallback_user"},
                "like_count": 5,
                "comment_count": 1,
                "play_count": 100,
            }
        ]
    }

    class _Request:
        async def get(self, url: str, **_kwargs: object) -> _FakeInternalResponse:
            if "/media/" in url and "/info/" in url:
                return _FakeInternalResponse(json.dumps(payload))
            return _FakeInternalResponse("<html></html>")

    page: Any = _FakePage(request=_Request())
    result = await scraper._post_metadata(page, "Fallback1")
    assert result is not None
    assert result["taken_at"] == timestamp
    assert result["owner_username"] == "fallback_user"


def _make_api_node(
    shortcode: str,
    timestamp: int,
    media_type: int = 2,
    owner_username: str = "creator",
) -> dict[str, Any]:
    return {
        "code": shortcode,
        "taken_at": timestamp,
        "media_type": media_type,
        "like_count": 10,
        "comment_count": 2,
        "play_count": 300,
        "caption": {"text": f"Caption for {shortcode}"},
        "user": {"username": owner_username, "follower_count": 800},
        "video_versions": [{"url": f"https://example.invalid/{shortcode}.mp4"}],
        "image_versions2": {
            "candidates": [{"url": f"https://example.invalid/{shortcode}.jpg"}]
        },
    }


def _feed_tag_payload(
    nodes: list[dict[str, Any]],
    *,
    more_available: bool = False,
    next_max_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "items": nodes,
        "more_available": more_available,
        "next_max_id": next_max_id,
    }


def _tag_sections_payload(
    nodes: list[dict[str, Any]],
    *,
    more_available: bool = False,
    next_max_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "sections": [
            {
                "layout_content": {
                    "medias": [{"media": node} for node in nodes]
                }
            }
        ],
        "more_available": more_available,
        "next_max_id": next_max_id,
    }


@pytest.mark.asyncio
async def test_instagram_scraper_collects_from_feed_tag_api(tmp_path: Path) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    recent_ts = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    payload = _feed_tag_payload(
        [
            _make_api_node("ApiA", recent_ts),
            _make_api_node("ApiB", recent_ts),
        ]
    )
    request = _FakeRequest(tag_responses={"travel": _FakeResponse(json.dumps(payload))})
    page: Any = _FakePage(href_batches=[["/reel/C/"]], request=request)
    result = await scraper._collect_reels(page, "travel", 2)

    assert [item.shortcode for item in result] == ["ApiA", "ApiB"]
    assert len(result) == 2
    assert all(item.author == "creator" for item in result)
    assert page._scroll_count == 0


@pytest.mark.asyncio
async def test_instagram_scraper_falls_back_to_tag_sections_api(tmp_path: Path) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    recent_ts = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    feed_response = _FakeResponse(json.dumps({"status": "fail"}), ok=False)
    sections_payload = _tag_sections_payload(
        [
            _make_api_node("SecA", recent_ts),
            _make_api_node("SecB", recent_ts),
        ]
    )
    sections_response = _FakeResponse(json.dumps(sections_payload))
    request = _FakeRequest(
        tag_responses={"travel": feed_response},
        tag_post_responses={"travel": sections_response},
    )
    page: Any = _FakePage(href_batches=[["/reel/C/"]], request=request)
    result = await scraper._collect_reels(page, "travel", 2)

    assert [item.shortcode for item in result] == ["SecA", "SecB"]
    assert len(result) == 2
    assert page._scroll_count == 0


@pytest.mark.asyncio
async def test_instagram_scraper_api_skips_old_posts_and_stops(tmp_path: Path) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    recent_ts = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    old_ts = int((datetime.now(UTC) - timedelta(days=60)).timestamp())
    payload = _feed_tag_payload(
        [
            _make_api_node("ApiA", recent_ts),
            _make_api_node("ApiOld", old_ts),
        ]
    )
    request = _FakeRequest(tag_responses={"travel": _FakeResponse(json.dumps(payload))})
    page: Any = _FakePage(request=request)
    result = await scraper._collect_reels(page, "travel", 5)

    assert [item.shortcode for item in result] == ["ApiA"]


@pytest.mark.asyncio
async def test_ensure_authenticated_noop_when_not_on_login_page(tmp_path: Path) -> None:
    settings = Settings(
        instagram_username="u",
        instagram_password="p",
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)
    page: Any = _FakePage()
    page.url = "https://www.instagram.com/explore/"

    login_mock = AsyncMock()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(scraper, "_login", login_mock)
    try:
        await scraper._ensure_authenticated(page, target_url="https://www.instagram.com/explore/")
    finally:
        monkeypatch.undo()

    login_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_authenticated_logs_in_and_returns_to_target(tmp_path: Path) -> None:
    settings = Settings(
        instagram_username="u",
        instagram_password="p",
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)
    page: Any = _FakePage()
    page.url = "https://www.instagram.com/accounts/login/"

    async def fake_login(p: Any, on_event: Any = None) -> None:
        p.url = "https://www.instagram.com/"

    login_mock = AsyncMock(side_effect=fake_login)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(scraper, "_login", login_mock)
    try:
        await scraper._ensure_authenticated(
            page, target_url="https://www.instagram.com/testuser/"
        )
    finally:
        monkeypatch.undo()

    login_mock.assert_awaited_once()
    assert page.url == "https://www.instagram.com/testuser/"


@pytest.mark.asyncio
async def test_fetch_creator_profile_ensures_authentication_on_login_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        instagram_username="u",
        instagram_password="p",
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)

    class _Page:
        def __init__(self) -> None:
            self.url = "https://www.instagram.com/accounts/login/"

        async def goto(self, url: str, **_kwargs: object) -> None:
            self.url = url

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def wait_for_timeout(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _Response:
        def __init__(self, text: str, status: int = 200) -> None:
            self._text = text
            self.status = status
            self.ok = 200 <= status < 300
            self.headers: dict[str, str] = {}

        async def text(self) -> str:
            return self._text

    class _Request:
        async def get(self, url: str, **_kwargs: object) -> _Response:
            assert "web_profile_info" in url
            return _Response(
                json.dumps(
                    {
                        "data": {
                            "user": {
                                "username": "testuser",
                                "is_private": False,
                            }
                        }
                    }
                )
            )

    page = _Page()
    page.request = _Request()

    @asynccontextmanager
    async def _fake_session(*_args: object, **_kwargs: object) -> AsyncGenerator[Any, None]:
        yield page

    ensure_spy = AsyncMock(
        side_effect=lambda p, target_url, on_event: setattr(p, "url", target_url)
    )
    monkeypatch.setattr(scraper, "_session_context", _fake_session)
    monkeypatch.setattr(scraper, "_ensure_authenticated", ensure_spy)
    monkeypatch.setattr(scraper, "_api_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(scraper, "_dismiss_cookie_banner", AsyncMock(return_value=None))
    monkeypatch.setattr(scraper, "_dismiss_login_prompt", AsyncMock(return_value=True))

    result = await scraper.fetch_creator_profile("testuser", 5)

    assert result["username"] == "testuser"
    assert result["is_private"] is False
    ensure_spy.assert_awaited_once()
    assert ensure_spy.call_args is not None
    _page_arg, kwargs = ensure_spy.call_args.args, ensure_spy.call_args.kwargs
    assert kwargs["target_url"] == "https://www.instagram.com/testuser/"


@pytest.mark.asyncio
async def test_load_storage_state_adds_saved_cookies(tmp_path: Path) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "instagram.json"))
    scraper = InstagramScraper(settings)
    cookies = [{"name": "sessionid", "value": "abc", "domain": ".instagram.com"}]
    state = {"cookies": cookies, "origins": []}
    (tmp_path / "instagram.json").write_text(json.dumps(state))

    scraper.context = AsyncMock()
    await scraper._load_storage_state()

    scraper.context.add_cookies.assert_awaited_once_with(cookies)


@pytest.mark.asyncio
async def test_load_storage_state_is_noop_when_file_missing(tmp_path: Path) -> None:
    settings = Settings(scraper_storage_state_path=str(tmp_path / "missing.json"))
    scraper = InstagramScraper(settings)
    scraper.context = AsyncMock()

    await scraper._load_storage_state()

    scraper.context.add_cookies.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismiss_login_prompt_returns_true_when_close_clicked() -> None:
    settings = Settings(scraper_storage_state_path="/tmp/instagram.json")
    scraper = InstagramScraper(settings)
    page = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.get_by_role = MagicMock(
        return_value=MagicMock(first=MagicMock(click=AsyncMock()))
    )
    page.locator = MagicMock(
        return_value=MagicMock(first=MagicMock(click=AsyncMock()))
    )

    result = await scraper._dismiss_login_prompt(page)

    assert result is True
    page.keyboard.press.assert_awaited_once_with("Escape")
    page.get_by_role.assert_called_once()
    page.get_by_role.return_value.first.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_creator_profile_falls_back_to_login_on_unauthorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        instagram_username="u",
        instagram_password="p",
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)

    class _Response:
        def __init__(self, status: int) -> None:
            self.status = status
            self.ok = 200 <= status < 300
            self.headers: dict[str, str] = {}

        async def text(self) -> str:
            return json.dumps(
                {
                    "data": {
                        "user": {
                            "username": "testuser",
                            "is_private": False,
                        }
                    }
                }
            )

    responses = [_Response(401), _Response(200)]

    class _Request:
        async def get(self, url: str, **_kwargs: object) -> _Response:
            return responses.pop(0)

    class _Page:
        url = "https://www.instagram.com/testuser/"

        async def goto(self, url: str, **_kwargs: object) -> None:
            self.url = url

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def wait_for_timeout(self, *_args: object, **_kwargs: object) -> None:
            pass

    page = _Page()
    page.request = _Request()

    @asynccontextmanager
    async def _fake_session(*_args: object, **_kwargs: object) -> AsyncGenerator[Any, None]:
        yield page

    monkeypatch.setattr(scraper, "_session_context", _fake_session)
    monkeypatch.setattr(scraper, "_ensure_authenticated", AsyncMock())
    monkeypatch.setattr(scraper, "_api_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(scraper, "_dismiss_cookie_banner", AsyncMock())
    monkeypatch.setattr(scraper, "_dismiss_login_prompt", AsyncMock(return_value=True))
    monkeypatch.setattr(scraper, "_prepare_creator_profile_page", AsyncMock())
    login_mock = AsyncMock()
    monkeypatch.setattr(scraper, "_login", login_mock)

    result = await scraper.fetch_creator_profile("testuser", 5)

    assert result["username"] == "testuser"
    login_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_creator_profile_raises_when_unauthorized_and_no_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        scraper_storage_state_path=str(tmp_path / "instagram.json"),
    )
    scraper = InstagramScraper(settings)

    class _Response:
        status = 401
        ok = False
        headers: dict[str, str] = {}

        async def text(self) -> str:
            return ""

    class _Request:
        async def get(self, url: str, **_kwargs: object) -> _Response:
            assert "web_profile_info" in url
            return _Response()

    class _Page:
        url = "https://www.instagram.com/testuser/"

        async def goto(self, url: str, **_kwargs: object) -> None:
            self.url = url

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def wait_for_timeout(self, *_args: object, **_kwargs: object) -> None:
            pass

    page = _Page()
    page.request = _Request()

    @asynccontextmanager
    async def _fake_session(*_args: object, **_kwargs: object) -> AsyncGenerator[Any, None]:
        yield page

    monkeypatch.setattr(scraper, "_session_context", _fake_session)
    monkeypatch.setattr(scraper, "_ensure_authenticated", AsyncMock())
    monkeypatch.setattr(scraper, "_api_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(scraper, "_dismiss_cookie_banner", AsyncMock())
    monkeypatch.setattr(scraper, "_dismiss_login_prompt", AsyncMock(return_value=True))
    monkeypatch.setattr(scraper, "_prepare_creator_profile_page", AsyncMock())

    with pytest.raises(NeedsInterventionError):
        await scraper.fetch_creator_profile("testuser", 5)


@pytest.mark.asyncio
async def test_instagram_session_metadata_provider_fills_views_and_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from app.providers.scraper import InstagramSessionMetadataProvider

    state = {
        "cookies": [
            {"name": "sessionid", "value": "session"},
            {"name": "csrftoken", "value": "csrf"},
        ]
    }
    storage = tmp_path / "instagram.json"
    storage.write_text(json.dumps(state))
    settings = Settings(scraper_storage_state_path=str(storage))
    provider = InstagramSessionMetadataProvider(settings)

    payload = {
        "items": [
            {
                "code": "abc",
                "taken_at": 1_700_000_000,
                "user": {"username": "creator"},
                "like_count": 10,
                "comment_count": 2,
                "play_count": 5000,
                "media_type": 2,
                "product_type": "clips",
                "video_duration": 12.5,
                "video_versions": [{"url": "https://cdn.example/video.mp4"}],
            }
        ]
    }

    class _Response:
        status_code = 200
        text = json.dumps(payload)

    async def fake_get(self: Any, url: str, **kwargs: Any) -> Any:
        return _Response()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await provider.fetch(
        "abc",
        {"taken_at": 1_700_000_000, "owner_username": "creator", "source": "instagram"},
        context={"source": "instagram"},
    )

    assert result.view_count == 5000
    assert result.video_duration == 12.5
    assert result.video_url == "https://cdn.example/video.mp4"
    assert result.media_type == "REELS"
    assert result.like_count == 10
    assert result.comment_count == 2


@pytest.mark.asyncio
async def test_instagram_session_metadata_provider_requires_session_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from app.providers.scraper import InstagramSessionMetadataProvider

    storage = tmp_path / "instagram.json"
    storage.write_text(json.dumps({"cookies": []}))
    settings = Settings(scraper_storage_state_path=str(storage))
    provider = InstagramSessionMetadataProvider(settings)

    async def fail_get(self: Any, url: str, **kwargs: Any) -> Any:
        raise AssertionError("network must not be called without a session cookie")

    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)

    result = await provider.fetch(
        "abc", {"taken_at": 1_700_000_000, "owner_username": "creator"}
    )
    assert result.view_count == 0
    assert result.video_duration is None


@pytest.mark.asyncio
async def test_instagram_session_metadata_provider_raises_on_blocked_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from app.providers.scraper import InstagramSessionMetadataProvider

    storage = tmp_path / "instagram.json"
    storage.write_text(json.dumps({"cookies": [{"name": "sessionid", "value": "s"}]}))
    settings = Settings(scraper_storage_state_path=str(storage))
    provider = InstagramSessionMetadataProvider(settings)

    class _Response:
        status_code = 403
        text = "Please wait a few minutes before you try again."

    async def fake_get(self: Any, url: str, **kwargs: Any) -> Any:
        return _Response()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(NeedsInterventionError):
        await provider.fetch("abc", {"taken_at": 1_700_000_000})
