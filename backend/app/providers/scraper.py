import asyncio
import json
import logging
import random
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from app.core.config import Settings
from app.core.errors import TransientError
from app.core.rate_limit import GraphApiRateLimiter, build_graph_rate_limiter
from app.infrastructure.resources import utcnow
from app.providers.trends import MetaHashtagTrendSource, TrendSourceError
from app.schemas.trends import ContentMetadata, ScrapedItem

logger = logging.getLogger(__name__)

SHORTCODE_RE = re.compile(r"^/(?:reel|reels|p)/([A-Za-z0-9_-]+)/?")
INTERVENTION_PATHS = ("/challenge/", "/accounts/two_factor", "/captcha/")
_TAKEN_AT_RE = re.compile(r'"taken_at_timestamp"\s*:\s*(?:")?(\d{10})(?:")?')
_X_IG_APP_ID = "936619743392459"
_FEED_TAG_URL = "https://i.instagram.com/api/v1/feed/tag/{tag}/"
_TAG_SECTIONS_URL = "https://i.instagram.com/api/v1/tags/{tag}/sections/"
_MAX_TAG_API_PAGES = 50

_CANDIDATE_SELECTOR = (
    'main a[role="link"][href*="/p/"], '
    'main a[role="link"][href*="/reel/"], '
    'article a[role="link"][href*="/p/"], '
    'article a[role="link"][href*="/reel/"], '
    'a[href*="/p/"], '
    'a[href*="/reel/"]'
)
_MAX_EMPTY_SCROLLS = 10
_FEED_READY_TIMEOUT_S = 8.0
_PER_SCROLL_POLL_TIMEOUT_S = 3.0
_POLL_INTERVAL_S = 0.5

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _parse_timestamp(value: Any) -> int | None:
    """Convert an int, float, digit-string, or ISO 8601 string to a Unix timestamp."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            return int(cleaned)
        if cleaned:
            try:
                dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                return int(dt.timestamp())
            except ValueError:
                pass
    return None


def _first_timestamp(*values: Any) -> int | None:
    for value in values:
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def _shortcode_to_media_id(shortcode: str) -> int | None:
    """Decode an Instagram shortcode to its numeric media_id.

    Instagram shortcodes are custom base64-encoded media IDs. A failure to decode
    returns ``None`` so callers can fall back to other resolution strategies.
    """
    if not shortcode or any(char not in _SHORTCODE_ALPHABET for char in shortcode):
        return None
    try:
        media_id = 0
        for char in shortcode:
            media_id = media_id * 64 + _SHORTCODE_ALPHABET.index(char)
        return media_id
    except (ValueError, OverflowError):
        return None


def _extract_upload_date_from_jsonld(shortcode: str, data: Any) -> int | None:
    """Search schema.org/JSON-LD data for an upload/publication date belonging to the post."""
    date_keys = (
        "uploadDate",
        "datePublished",
        "dateCreated",
        "dateModified",
        "timestamp",
        "created_time",
    )

    def _scan(obj: Any) -> int | None:
        if isinstance(obj, dict):
            url_value: Any = obj.get("url") or obj.get("mainEntityOfPage") or ""
            if isinstance(url_value, dict):
                url_value = url_value.get("@id") or url_value.get("url") or ""
            if shortcode in str(url_value):
                for key in date_keys:
                    parsed = _parse_timestamp(obj.get(key))
                    if parsed is not None:
                        return parsed
            for value in obj.values():
                result = _scan(value)
                if result is not None:
                    return result
        if isinstance(obj, list):
            for item in obj:
                result = _scan(item)
                if result is not None:
                    return result
        return None

    return _scan(data)


def _extract_timestamp_from_html(shortcode: str, text: str) -> int | None:
    """Parse schema.org JSON-LD and <time> tags from raw HTML for a publish timestamp."""
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(match.group(1).strip())
        except Exception:
            continue
        parsed = _extract_upload_date_from_jsonld(shortcode, data)
        if parsed is not None:
            return parsed

    for match in re.finditer(
        r'<time[^>]*datetime=["\']([^"\']+)["\']',
        text,
        re.IGNORECASE,
    ):
        parsed = _parse_timestamp(match.group(1))
        if parsed is not None:
            return parsed

    return None

_BLOCKED_TEXT_RE = re.compile(
    r"(?i)"
    r"\b(?:suspicious|unusual)\s+(?:login\s+)?(?:activity|attempt)\b|"
    r"\bchallenge\s+required\b|"
    r"\bcaptcha\b|"
    r"\brate\s*limit\b|"
    r"\btry\s+again\s+later\b|"
    r"\btemporarily\s+locked\b|"
    r"\bhelp\s+us\s+confirm\b|"
    r"\bconfirm\s+your\s+(?:account|identity)\b|"
    r"\bverify\s+your\s+(?:account|identity)\b|"
    r"\baccount\b.{0,80}\bblocked\b|"
    r"\bblocked\b.{0,80}\baccount\b|"
    r"\bupdate\b.{0,60}\bapp\b|"
    r"\bşüpheli\s+(?:giriş|aktivite|deneme)\b|"
    r"\bdoğrulama\s+(?:gerekli|yapın|yapmanız)\b|"
    r"\bhesabınızı\s+doğrulay\b|"
    r"\bteyit\s+etmek\b|"
    r"\bengellendi\b|"
    r"\bkilitlendi\b|"
    r"\brobot\b",
    re.DOTALL,
)

# Structured log emitter: await emit(message, level=..., step=..., **data)
EmitFn = Callable[..., Awaitable[Any]]


async def noop_emit(*args: Any, **kwargs: Any) -> None:
    return None


class NeedsInterventionError(RuntimeError):
    pass


class ProfileFetchError(RuntimeError):
    """Raised when a Playwright profile fetch cannot be completed.

    ``reason`` distinguishes cases so callers can map to domain exceptions.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def parse_instagram_url(url: str) -> tuple[str, str]:
    candidate = urljoin("https://www.instagram.com", url)
    parts = urlsplit(candidate)
    if parts.hostname not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("not an Instagram URL")
    match = SHORTCODE_RE.match(parts.path)
    if not match:
        raise ValueError("not an Instagram post or reel URL")
    shortcode = match.group(1)
    canonical = urlunsplit(("https", "www.instagram.com", f"/reel/{shortcode}/", "", ""))
    return canonical, shortcode


def _extract_taken_at_timestamp(shortcode: str, data: Any) -> int | None:
    """Find the requested shortcode's own Unix publish timestamp in Instagram JSON."""

    def _scan(obj: Any) -> int | None:
        if isinstance(obj, dict):
            if obj.get("shortcode") == shortcode:
                for key in ("taken_at_timestamp", "taken_at"):
                    value = obj.get(key)
                    if isinstance(value, int):
                        return value
                    if isinstance(value, str) and value.isdigit():
                        return int(value)
                # Some older GraphQL objects keep it in a child node.
                node = obj.get("node")
                if isinstance(node, dict):
                    return _scan(node)
            for value in obj.values():
                result = _scan(value)
                if result is not None:
                    return result
        if isinstance(obj, list):
            for item in obj:
                result = _scan(item)
                if result is not None:
                    return result
        return None

    return _scan(data)


def _nested_value(data: Any, *keys: Any) -> Any:
    value: Any = data
    for key in keys:
        if isinstance(value, dict) and isinstance(key, str):
            value = value.get(key)
        elif isinstance(value, list) and isinstance(key, int):
            index = key if key >= 0 else len(value) + key
            if not (0 <= index < len(value)):
                return None
            value = value[index]
        else:
            return None
        if value is None or value == "":
            return None
    return value


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            try:
                return int(cleaned)
            except ValueError:
                continue
    return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                continue
    return None


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _find_post_node(shortcode: str, data: Any) -> dict[str, Any] | None:
    """Locate the media object that matches ``shortcode`` in Instagram JSON."""

    def _scan(obj: Any) -> dict[str, Any] | None:
        if isinstance(obj, dict):
            if obj.get("shortcode") == shortcode or str(obj.get("code")) == shortcode:
                return obj
            for value in obj.values():
                found = _scan(value)
                if found is not None:
                    return found
        if isinstance(obj, list):
            for item in obj:
                found = _scan(item)
                if found is not None:
                    return found
        return None

    return _scan(data)


def _extract_post_metadata(shortcode: str, data: Any) -> dict[str, Any]:
    """Map an Instagram post JSON node into the metadata fields enrichment expects."""
    media = _find_post_node(shortcode, data)
    if not isinstance(media, dict):
        return {}
    result: dict[str, Any] = {}

    taken_at = _first_timestamp(
        media.get("taken_at_timestamp"),
        media.get("taken_at"),
        _nested_value(media, "taken_at", "timestamp"),
        media.get("uploadDate"),
        media.get("datePublished"),
        media.get("dateCreated"),
        media.get("created_time"),
        media.get("timestamp"),
    )
    if taken_at is not None:
        result["taken_at"] = taken_at

    owner = _nested_value(media, "owner") or _nested_value(media, "user") or {}
    if isinstance(owner, dict):
        result["owner_username"] = _first_str(
            _nested_value(owner, "username"),
            _nested_value(owner, "full_name"),
        )
        result["owner_follower_count"] = _first_int(
            _nested_value(owner, "edge_followed_by", "count"),
            _nested_value(owner, "follower_count"),
            _nested_value(owner, "followers"),
            _nested_value(owner, "edge_follow", "count"),
        )

    result["like_count"] = _first_int(
        _nested_value(media, "edge_media_preview_like", "count"),
        media.get("like_count"),
        media.get("likes_count"),
        media.get("likes"),
    )
    result["comment_count"] = _first_int(
        _nested_value(media, "edge_media_to_comment", "count"),
        media.get("comment_count"),
        media.get("comments_count"),
        media.get("comments"),
    )
    result["view_count"] = _first_int(
        media.get("video_play_count"),
        media.get("play_count"),
        media.get("video_view_count"),
        media.get("view_count"),
        media.get("views"),
    )
    result["share_count"] = _first_int(
        media.get("share_count"),
        media.get("shares"),
        media.get("reshare_count"),
        media.get("reshares"),
    )

    # Normalise media type so enrichment can decide which fields are required.
    typename = str(media.get("__typename") or "").upper()
    is_video = bool(media.get("is_video"))
    carousel_children = _nested_value(media, "edge_sidecar_to_children", "edges") or []
    carousel_count = _first_int(media.get("carousel_media_count")) or 0
    media_type_value = _first_int(media.get("media_type")) or 0
    product_type = str(media.get("product_type") or "").upper()
    if typename == "GRAPHVIDEO" or is_video or media_type_value == 2:
        result["media_type"] = (
            "REELS" if product_type in {"CLIPS", "REELS"} else "VIDEO"
        )
    elif (
        typename == "GRAPHSIDECAR"
        or carousel_children
        or carousel_count
        or media_type_value == 8
    ):
        result["media_type"] = "CAROUSEL_ALBUM"
    else:
        result["media_type"] = "IMAGE"

    duration = _first_float(
        media.get("video_duration"),
        media.get("video_length"),
        media.get("length"),
        _nested_value(media, "video_versions", 0, "duration"),
        _nested_value(media, "clips_metadata", "video_length"),
    )
    if duration is not None:
        result["video_duration"] = duration

    caption = _first_str(
        _nested_value(media, "caption", "text"),
        _nested_value(media, "edge_media_to_caption", "edges", 0, "node", "text"),
        media.get("caption"),
        media.get("accessibility_caption"),
    )
    if caption:
        result["caption_text"] = caption

    video_url = _first_str(
        _nested_value(media, "video_versions", 0, "url"),
        _nested_value(media, "video_url"),
        media.get("video_url"),
        media.get("media_url"),
    )
    if video_url:
        result["video_url"] = video_url

    thumbnail_url = _first_str(
        _nested_value(media, "display_resources", -1, "src"),
        _nested_value(media, "display_url"),
        _nested_value(media, "image_versions2", "candidates", 0, "url"),
        media.get("display_url"),
        media.get("thumbnail_src"),
    )
    if thumbnail_url:
        result["thumbnail_url"] = thumbnail_url

    return result


def _extract_post_metadata_from_text(shortcode: str, text: str) -> dict[str, Any]:
    """Fallback metadata extraction from a raw Instagram HTML/JSON response."""
    metadata: dict[str, Any] = {}

    # First try to locate a structured JSON blob containing the requested shortcode.
    for match in re.finditer(r"<script[^>]*>(.*?)</script>", text, re.DOTALL):
        script = match.group(1).strip()
        if shortcode not in script:
            continue
        data: Any = None
        try:
            if "window._sharedData" in script:
                json_match = re.search(
                    r"window\._sharedData\s*=\s*(\{.*\})\s*;?\s*$",
                    script,
                    re.DOTALL,
                )
                if json_match:
                    data = json.loads(json_match.group(1))
            else:
                data = json.loads(script)
        except Exception:
            continue
        if isinstance(data, dict):
            parsed = _extract_post_metadata(shortcode, data)
            if parsed:
                metadata = {**metadata, **parsed}
                if metadata.get("taken_at") is not None:
                    return metadata

    # Fallback: schema.org/JSON-LD and <time datetime="..."> tags.
    if metadata.get("taken_at") is None:
        taken_at = _extract_timestamp_from_html(shortcode, text)
        if taken_at is not None:
            metadata["taken_at"] = taken_at
            return metadata

    # Fallback: only trust a taken_at_timestamp that appears near the shortcode.
    if metadata.get("taken_at") is None:
        for match in _TAKEN_AT_RE.finditer(text):
            start = max(0, match.start() - 500)
            end = min(len(text), match.end() + 500)
            if shortcode in text[start:end]:
                try:
                    metadata["taken_at"] = int(match.group(1))
                    break
                except ValueError:
                    continue
    return metadata


def _media_nodes_from_response(data: Any) -> list[dict[str, Any]]:
    """Extract media nodes from Instagram internal tag API responses.

    Supports both the ``feed/tag`` shape (``items`` / ``ranked_items``) and the
    newer ``tags/{tag}/sections`` web shape (``sections[].layout_content.medias[].media``).
    """
    nodes: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return nodes

    for key in ("items", "ranked_items"):
        items = data.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    nodes.append(item)

    sections = data.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            layout = section.get("layout_content")
            if not isinstance(layout, dict):
                continue
            medias = layout.get("medias")
            if not isinstance(medias, list):
                continue
            for wrapper in medias:
                if not isinstance(wrapper, dict):
                    continue
                media = wrapper.get("media")
                if isinstance(media, dict):
                    nodes.append(media)

    return nodes


def _next_pagination_from_response(
    data: Any,
) -> tuple[str | None, list[str] | None, bool]:
    """Return the next cursor, any per-item media IDs, and ``more_available`` flag."""
    if not isinstance(data, dict):
        return None, None, False
    next_max_id = _first_str(data.get("next_max_id"))
    raw_next_media_ids = data.get("next_media_ids")
    if isinstance(raw_next_media_ids, list):
        next_media_ids = [str(media_id) for media_id in raw_next_media_ids]
    else:
        next_media_ids = None
    more_available = bool(data.get("more_available"))
    return next_max_id, next_media_ids, more_available


class ScraperAdapter(ABC):
    @abstractmethod
    async def scrape(
        self,
        keywords: list[str],
        limit: int,
        on_event: EmitFn = noop_emit,
        *,
        is_existing: Callable[[str], Awaitable[bool]] | None = None,
    ) -> list[ScrapedItem]:
        raise NotImplementedError


class MetaTrendAdapter(ScraperAdapter):
    def __init__(
        self,
        settings: Settings,
        *,
        access_token: str | None = None,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        if not access_token and not settings.meta_trend_access_token:
            raise TrendSourceError("Meta trend access token is not configured")
        if not settings.meta_instagram_business_account_id:
            raise TrendSourceError("Meta Instagram business account id is not configured")
        self.settings = settings
        self.source = MetaHashtagTrendSource(settings, graph_limiter=graph_limiter)
        self._access_token = access_token or (
            settings.meta_trend_access_token.get_secret_value()
            if settings.meta_trend_access_token
            else None
        )
        assert self._access_token is not None

    async def scrape(
        self,
        keywords: list[str],
        limit: int,
        on_event: EmitFn = noop_emit,
        *,
        is_existing: Callable[[str], Awaitable[bool]] | None = None,
    ) -> list[ScrapedItem]:
        token = self._access_token
        assert token is not None
        account_id = self.settings.meta_instagram_business_account_id
        assert account_id is not None
        result: list[ScrapedItem] = []
        failed_keywords = 0
        max_age_days = self.settings.scraper_max_content_age_days
        cutoff = utcnow() - timedelta(days=max_age_days) if max_age_days else None
        for keyword in keywords:
            await on_event(
                f"Querying official Meta hashtag top media for '{keyword}'.",
                step="keyword",
                keyword=keyword,
            )
            try:
                items = await self.source.discover(
                    keyword,
                    access_token=token,
                    instagram_business_account_id=account_id,
                    limit=limit,
                )
            except (TrendSourceError, TransientError) as exc:
                failed_keywords += 1
                await on_event(
                    f"Hashtag '{keyword}' failed: {exc}",
                    level="error",
                    step="keyword",
                    keyword=keyword,
                    error=str(exc),
                )
                continue
            for item in items:
                try:
                    canonical, shortcode = parse_instagram_url(item.permalink)
                except ValueError:
                    shortcode = item.permalink
                    canonical = item.permalink
                if cutoff is not None and item.taken_at < cutoff:
                    await on_event(
                        f"Skipping post older than {max_age_days} day(s): {shortcode}",
                        step="filtered",
                        keyword=keyword,
                        shortcode=shortcode,
                        taken_at=item.taken_at.isoformat(),
                    )
                    continue
                if is_existing is not None and await is_existing(canonical):
                    await on_event(
                        f"Skipping already stored post: {shortcode}",
                        step="filtered",
                        keyword=keyword,
                        shortcode=shortcode,
                        reason="existing",
                    )
                    continue
                result.append(
                    ScrapedItem(
                        canonical_url=canonical,
                        shortcode=shortcode,
                        caption=item.caption,
                        discovered_keyword=keyword,
                        metadata={
                            "official_source": True,
                            "source_id": item.source_id,
                            "source": item.source,
                            "license": item.license,
                            "permalink": item.permalink,
                            "caption_text": item.caption,
                            "media_type": item.media_type,
                            "video_url": (
                                item.media_url
                                if item.media_type.upper() in {"VIDEO", "REELS"}
                                else None
                            ),
                            "taken_at": item.taken_at.isoformat(),
                            "like_count": item.like_count,
                            "comment_count": item.comment_count,
                            "provenance": item.provenance,
                        },
                    )
                )
        summary_level = (
            "error"
            if failed_keywords == len(keywords) and keywords
            else "success"
            if result
            else "info"
        )
        await on_event(
            (
                f"Scraped {len(result)} items from {len(keywords)} keyword(s) "
                f"({failed_keywords} failed)."
            ),
            level=summary_level,
            step="done",
            discovered=len(result),
            keywords_count=len(keywords),
            failed_keywords=failed_keywords,
        )
        return result


class InstagramScraper(ScraperAdapter):
    def __init__(
        self,
        settings: Settings,
        *,
        access_token: str | None = None,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self._access_token = access_token
        self.graph_limiter = graph_limiter
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    def _raise_for_intervention_response(self, response: Any, text: str) -> None:
        """Raise NeedsInterventionError when Instagram blocks an in-session API request."""
        status = getattr(response, "status", None) or getattr(response, "status_code", None)
        if status in (401, 403) or (
            status == 400
            and any(
                word in text.lower()
                for word in ("checkpoint", "challenge", "suspicious", "restrict", "unusual")
            )
        ):
            raise NeedsInterventionError(
                f"Instagram request blocked or requires verification (status {status}). "
                "Resolve any checkpoint, captcha, or two-factor prompt in a browser "
                "and refresh the saved session."
            )

    async def _intervention_guard(self, page: Page) -> None:
        if any(path in page.url for path in INTERVENTION_PATHS):
            raise NeedsInterventionError(
                "Instagram requires challenge, captcha, or two-factor authentication"
            )

    # Labels that are too generic (e.g. "Allow", "Accept", "Tamam") can match login/
    # notification buttons and break the flow, so keep this list specific to cookies.
    _COOKIE_BANNER_RE = re.compile(
        r"^\s*(Allow all cookies|Accept all cookies|"
        r"Tüm çerezlere izin ver|Tüm tanımlama bilgilerine izin ver|"
        r"Only allow necessary cookies|Sadece gerekli çerezlere izin ver|"
        r"Manage cookies|Çerezleri yönet)\s*$",
        re.IGNORECASE,
    )

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        # Cookie-consent banners can overlay the login form. Try common labels and locales.
        labels = [
            re.compile(r"^Allow all cookies$", re.IGNORECASE),
            re.compile(r"^Tüm çerezlere izin ver$", re.IGNORECASE),
            re.compile(r"^Accept all cookies$", re.IGNORECASE),
            re.compile(r"^Tüm tanımlama bilgilerine izin ver$", re.IGNORECASE),
            re.compile(r"^Only allow necessary cookies$", re.IGNORECASE),
            re.compile(r"^Sadece gerekli çerezlere izin ver$", re.IGNORECASE),
        ]
        for label in labels:
            try:
                await page.get_by_role("button", name=label).click(timeout=3_000)
            except PlaywrightError:
                continue
            else:
                await page.wait_for_timeout(300)
                return

    async def _dismiss_login_prompt(self, page: Page) -> bool:
        """Close Instagram 'Log in or sign up' modals by clicking the X button.

        Instagram shows a login/signup overlay to logged-out visitors. Closing
        it lets public creator profiles load without entering credentials.
        """
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except PlaywrightError:
            pass
        close_labels = [
            re.compile(r"^\s*Close\s*$", re.IGNORECASE),
            re.compile(r"^\s*Kapat\s*$", re.IGNORECASE),
        ]
        for label in close_labels:
            try:
                await page.get_by_role("button", name=label).first.click(timeout=2_000)
                await page.wait_for_timeout(300)
                return True
            except PlaywrightError:
                continue
        close_selectors = [
            '[aria-label="Close"]',
            'div[role="button"][aria-label="Close"]',
            'svg[aria-label="Close"]',
            '[role="dialog"] [role="button"]:first-of-type',
            '[role="dialog"] button:first-of-type',
        ]
        for selector in close_selectors:
            try:
                await page.locator(selector).first.click(timeout=2_000)
                await page.wait_for_timeout(300)
                return True
            except PlaywrightError:
                continue
        return False

    async def _capture_login_debug(self, page: Page) -> Path | None:
        """Capture a screenshot and HTML dump of the current page for diagnosis."""
        try:
            debug_dir = Path(self.settings.scraper_storage_state_path).parent / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = utcnow().strftime("%Y%m%dT%H%M%S.%fZ")
            prefix = f"login-{timestamp}"
            screenshot_path = debug_dir / f"{prefix}.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            html = await page.content()
            (debug_dir / f"{prefix}.html").write_text(html, encoding="utf-8")
            return screenshot_path
        except (PlaywrightError, OSError):
            return None

    async def _raise_for_intervention_text(self, page: Page) -> None:
        """Raise NeedsInterventionError if visible page text indicates a block/challenge."""
        try:
            body_text = await page.locator("body").inner_text(timeout=3_000)
        except PlaywrightError:
            return
        if body_text and _BLOCKED_TEXT_RE.search(body_text):
            debug_path = await self._capture_login_debug(page)
            detail = (
                f" Debug artifacts saved to {debug_path.parent}."
                if debug_path
                else ""
            )
            raise NeedsInterventionError(
                "Instagram appears to require verification or has blocked this login "
                "(suspicious activity, challenge, captcha, or rate limit)."
                f"{detail}"
            )

    async def _login(self, page: Page) -> None:
        username = (self.settings.instagram_username or "").strip()
        raw_password = self.settings.instagram_password
        password = raw_password.get_secret_value().strip() if raw_password else ""
        if not username or not password:
            raise NeedsInterventionError(
                "Instagram session expired and credentials are not configured"
            )

        try:
            await page.goto(
                "https://www.instagram.com/accounts/login/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except PlaywrightError as goto_exc:
            raise NeedsInterventionError(
                f"Could not open Instagram login page: {goto_exc}"
            ) from goto_exc

        await self._intervention_guard(page)

        # The form is rendered by JS; wait for the page to settle and dismiss any banner.
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightError:
            pass
        await page.wait_for_timeout(1_500)
        await self._dismiss_cookie_banner(page)
        await self._raise_for_intervention_text(page)

        # Build robust, multi-lingual locators scoped to the login form if possible.
        form_scope = page.locator("#loginForm, #login_form, form").last
        username_input = (
            form_scope.locator('input[name="username"]')
            .or_(form_scope.locator('input[name="email"]'))
            .or_(form_scope.locator('input[autocomplete="username"]'))
            .or_(form_scope.locator('input[autocomplete~="username"]'))
            .or_(form_scope.locator('input[autocapitalize="off"][type="text"]'))
            .or_(
                page.get_by_label(
                    re.compile(r"(?:phone|mobile) number.*username.*email", re.IGNORECASE)
                )
            )
            .or_(
                page.get_by_label(
                    re.compile(
                        r"telefon numarası.*kullanıcı adı.*e-posta", re.IGNORECASE
                    )
                )
            )
            .or_(
                page.get_by_placeholder(
                    re.compile(r"(?:phone|mobile) number.*username.*email", re.IGNORECASE)
                )
            )
            .or_(
                page.get_by_placeholder(
                    re.compile(
                        r"telefon numarası.*kullanıcı adı.*e-posta", re.IGNORECASE
                    )
                )
            )
            .or_(
                page.get_by_role(
                    "textbox",
                    name=re.compile(
                        r"(?:phone|mobile) number.*username.*email", re.IGNORECASE
                    ),
                )
            )
            .or_(
                page.get_by_role(
                    "textbox",
                    name=re.compile(
                        r"telefon.*kullanıcı.*e-posta", re.IGNORECASE
                    ),
                )
            )
            .or_(page.locator('input[name="username"]'))
            .or_(page.locator('input[name="email"]'))
            .or_(page.locator('input[autocomplete="username"]'))
            .or_(page.locator('input[autocomplete~="username"]'))
            .or_(page.locator('input[autocapitalize="off"][type="text"]'))
            .or_(page.locator('input[type="text"][autocapitalize="off"]'))
        )
        password_input = (
            form_scope.locator('input[name="password"]')
            .or_(form_scope.locator('input[name="pass"]'))
            .or_(form_scope.locator('input[autocomplete="current-password"]'))
            .or_(
                page.get_by_label(re.compile(r"password|şifre", re.IGNORECASE))
            )
            .or_(
                page.get_by_placeholder(re.compile(r"password|şifre", re.IGNORECASE))
            )
            .or_(
                page.get_by_role(
                    "textbox",
                    name=re.compile(r"password|şifre", re.IGNORECASE),
                )
            )
            .or_(page.locator('input[type="password"]'))
        )
        submit_button = (
            form_scope.locator('button[type="submit"]')
            .or_(
                page.get_by_role(
                    "button",
                    name=re.compile(
                        r"^\s*log\s*in\s*$|^\s*giriş\s*yap\s*$", re.IGNORECASE
                    ),
                )
            )
            .or_(page.locator('button:text-matches("^\\s*log\\s*in\\s*$", "i")'))
            .or_(
                page.locator('button:text-matches("^\\s*giriş\\s*yap\\s*$", "i")')
            )
            .or_(
                page.locator(
                    'div[role="button"]:text-matches("^\\s*log\\s*in\\s*$", "i")'
                )
            )
            .or_(
                page.locator(
                    'div[role="button"]:text-matches("^\\s*giriş\\s*yap\\s*$", "i")'
                )
            )
        )

        try:
            await username_input.wait_for(state="attached", timeout=10_000)
            await username_input.wait_for(state="visible", timeout=30_000)
            await username_input.click()
            await username_input.fill("")
            await username_input.fill(username)

            await page.wait_for_timeout(500)

            await password_input.wait_for(state="visible", timeout=15_000)
            await password_input.click()
            await password_input.fill("")
            await password_input.fill(password)

            await page.wait_for_timeout(500)

            await submit_button.wait_for(state="visible", timeout=15_000)
            await submit_button.click()
        except PlaywrightError as fill_exc:
            # Distinguish a closed page/context from a missing element so the user gets
            # a useful message instead of a raw Playwright error.
            error_name = type(fill_exc).__name__
            if "closed" in error_name.lower() or "closed" in str(fill_exc).lower():
                raise NeedsInterventionError(
                    "Instagram login was interrupted because the browser page or "
                    "context was closed; this can happen if the browser window was "
                    "closed or the worker was restarted while logging in."
                ) from fill_exc
            debug_path = await self._capture_login_debug(page)
            detail = (
                f"; debug artifacts saved to {debug_path.parent}"
                if debug_path
                else ""
            )
            raise NeedsInterventionError(
                "Instagram login form could not be filled; check the login page "
                f"state or credentials. Playwright error: {fill_exc}{detail}"
            ) from fill_exc

        # Give the login request time to navigate. If the click did not trigger the
        # form submission, press Enter on the password field as a fallback.
        await self._wait_for_login_navigation(page)

        state_path = Path(self.settings.scraper_storage_state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        assert self.context is not None
        await self.context.storage_state(path=state_path)

    async def _wait_for_login_navigation(self, page: Page) -> None:
        """Wait for Instagram login to complete, falling back to Enter if needed."""
        for attempt in range(1, 3):
            if attempt == 2:
                try:
                    await page.locator('input[type="password"]').first.press(
                        "Enter", timeout=5_000
                    )
                except PlaywrightError:
                    pass
                await page.wait_for_timeout(500)

            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightError:
                pass
            await page.wait_for_timeout(1_500)
            await self._intervention_guard(page)
            await self._raise_for_intervention_text(page)

            try:
                current_url = page.url
            except PlaywrightError as url_exc:
                raise NeedsInterventionError(
                    "Could not read Instagram page URL after login; page may be closed."
                ) from url_exc

            if "/accounts/login" not in current_url:
                return

        # Still on the login page after both attempts; capture diagnostics.
        debug_path = await self._capture_login_debug(page)
        detail = f" Debug artifacts saved to {debug_path.parent}." if debug_path else ""

        error_text = await self._extract_login_error_text(page)
        if error_text:
            detail = f"; page message: {error_text.strip()}{detail}"

        raise NeedsInterventionError(f"Instagram login was not accepted{detail}")

    async def _extract_login_error_text(self, page: Page) -> str | None:
        """Try to read a human-readable error message from the login page."""
        selectors = [
            '[data-testid="login-error-message"]',
            '#slfErrorAlert',
            '[role="alert"]',
        ]
        for selector in selectors:
            try:
                text = await page.locator(selector).first.text_content(timeout=2_000)
                if text and text.strip():
                    return text.strip()
            except PlaywrightError:
                continue

        # Look for common inline error strings anywhere on the page.
        try:
            body_text = await page.locator("body").text_content(timeout=3_000)
        except PlaywrightError:
            return None
        if not body_text:
            return None
        for phrase in (
            "the login information you entered is incorrect",
            "password was incorrect",
            "username you entered",
            "couldn\'t log in",
            "could not log in",
            "try again later",
            "please wait a few minutes",
            "your account has been disabled",
            "suspicious login attempt",
            "checkpoint",
        ):
            if phrase.lower() in body_text.lower():
                return phrase
        return None

    async def _ensure_session(self, page: Page) -> None:
        await page.goto(
            "https://www.instagram.com/accounts/edit/",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await self._intervention_guard(page)
        # Allow any client-side/session redirect to settle before deciding whether
        # the stored session is still valid and can be reused.
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightError:
            pass
        await self._intervention_guard(page)
        if "/accounts/login" in page.url:
            await self._login(page)

    async def _ensure_authenticated(
        self,
        page: Page,
        target_url: str | None = None,
        on_event: EmitFn = noop_emit,
    ) -> None:
        """Log in if Instagram redirected to the login page, then return to target_url.

        This keeps Creator Tracking and Analyzer flows moving when Instagram
        requires authentication mid-session.
        """
        if "/accounts/login" not in page.url:
            return
        await on_event(
            "Instagram session expired; logging in.",
            level="warning",
            step="session",
        )
        await self._login(page)
        if not target_url:
            return
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightError as goto_exc:
            raise NeedsInterventionError(
                f"Could not return to {target_url} after login: {goto_exc}"
            ) from goto_exc
        await self._intervention_guard(page)

    async def _load_storage_state(self) -> None:
        """Seed the browser context with saved cookies from a previous run.

        This lets the Playwright scraper and creator tracking share the same
        instagram.json storage-state cache without re-entering credentials.
        """
        state_path = Path(self.settings.scraper_storage_state_path)
        if not await asyncio.to_thread(state_path.exists):
            return
        try:
            raw_state = await asyncio.to_thread(state_path.read_text)
            state = json.loads(raw_state)
        except (OSError, json.JSONDecodeError):
            return
        cookies = state.get("cookies")
        if not isinstance(cookies, list) or not cookies:
            return
        if self.context is None:
            return
        try:
            await self.context.add_cookies(cookies)
        except PlaywrightError:
            pass

    @asynccontextmanager
    async def _session_context(
        self,
        on_event: EmitFn = noop_emit,
        *,
        headless: bool | None = None,
        require_auth: bool = True,
    ) -> AsyncGenerator[Page, None]:
        """Launch a persistent Chromium session and yield a page.

        Validates the Instagram session before yielding when ``require_auth`` is
        True. Storage state is loaded at startup and saved on success so the
        scraper and creator tracking share the same instagram.json cache.
        """
        state_path = Path(self.settings.scraper_storage_state_path)
        profile_dir = state_path.parent / "profile"
        use_headless = self.settings.scraper_headless if headless is None else headless
        await on_event(
            f"Launching browser (headless={use_headless}).", step="session"
        )
        await asyncio.to_thread(profile_dir.mkdir, parents=True, exist_ok=True)
        context_options: dict[str, Any] = {
            "headless": use_headless,
            "args": [
                "--window-size=900,700",
                "--window-position=100,100",
                "--disable-blink-features=AutomationControlled",
            ],
            "user_agent": _USER_AGENT,
            "viewport": {"width": 900, "height": 700},
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        async with async_playwright() as playwright:
            self.context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                **context_options,
            )
            self.browser = self.context.browser
            await self.context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            await self._load_storage_state()
            page = await self.context.new_page()
            success = False
            try:
                if require_auth:
                    await on_event("Validating Instagram session.", step="session")
                    await self._ensure_session(page)
                else:
                    await on_event("Using shared Playwright storage state.", step="session")
                await on_event("Session ready.", level="success", step="session")
                yield page
                success = True
            finally:
                if self.context is not None:
                    if success:
                        try:
                            state_path.parent.mkdir(parents=True, exist_ok=True)
                            await self.context.storage_state(path=state_path)
                        except PlaywrightError:
                            pass
                    await self.context.close()
                if self.browser is not None:
                    try:
                        await self.browser.close()
                    except PlaywrightError:
                        pass
                self.context = None
                self.browser = None

    async def _pace(self) -> None:
        await asyncio.sleep(
            random.uniform(
                self.settings.scraper_min_delay_seconds,
                self.settings.scraper_max_delay_seconds,
            )
        )

    async def _post_metadata(self, page: Page, shortcode: str) -> dict[str, Any] | None:
        """Fetch a post page and parse the metadata enrichment expects.

        Tries Instagram's internal JSON endpoint, the public HTML page, the
        authenticated internal media API, and (when configured) the Instagram
        Graph oEmbed/media endpoints so the scraper does not discard posts as
        ``unknown_date`` when a publish timestamp can still be resolved.
        """
        base_url = f"https://www.instagram.com/p/{shortcode}/"
        headers = {
            "accept": "application/json",
            "x-ig-app-id": _X_IG_APP_ID,
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://www.instagram.com/",
        }
        best_metadata: dict[str, Any] = {}

        def _complete(metadata: dict[str, Any]) -> bool:
            if metadata.get("taken_at") is None:
                return False
            if not metadata.get("owner_username"):
                return False
            if metadata.get("media_type") in {"REELS", "VIDEO"}:
                if metadata.get("video_url") is not None and metadata.get("video_duration") is None:
                    return False
            return True

        for url in (f"{base_url}?__a=1&__d=dis", base_url):
            try:
                response = await page.request.get(url, headers=headers)
                if not response.ok:
                    text = await response.text()
                    self._raise_for_intervention_response(response, text)
                    continue
                text = await response.text()
                # Instagram occasionally wraps JSON in an XSSI guard.
                if text.startswith("for(;;);"):
                    text = text[len("for(;;);"):]
                try:
                    data = json.loads(text)
                except Exception:
                    data = None
                if isinstance(data, dict):
                    parsed = _extract_post_metadata(shortcode, data)
                    if parsed:
                        best_metadata = {**best_metadata, **parsed}
                        if _complete(best_metadata):
                            best_metadata["source"] = "instagram"
                            return best_metadata
                parsed = _extract_post_metadata_from_text(shortcode, text)
                if parsed:
                    best_metadata = {**best_metadata, **parsed}
                    if _complete(best_metadata):
                        best_metadata["source"] = "instagram"
                        return best_metadata
            except NeedsInterventionError:
                raise
            except Exception:
                continue

        if not _complete(best_metadata):
            fallback = await self._internal_api_metadata(page, shortcode)
            if fallback is not None:
                best_metadata = {**best_metadata, **fallback}

        use_fallbacks = self.settings.metadata_fallback_provider != "none"
        if use_fallbacks and not _complete(best_metadata):
            if best_metadata.get("taken_at") is None or best_metadata.get("video_url") is None:
                fallback = await self._yt_dlp_metadata(shortcode)
                if fallback is not None:
                    best_metadata = {**best_metadata, **fallback}

        if use_fallbacks and best_metadata.get("taken_at") is None:
            fallback = await self._graph_api_metadata(shortcode)
            if fallback is not None:
                best_metadata = {**best_metadata, **fallback}

        if best_metadata:
            best_metadata["source"] = "instagram"
        return best_metadata or None

    async def _fetch_owner_follower_count(
        self, page: Page, user_id: int
    ) -> int | None:
        """Read a user's follower count from Instagram's mobile user info endpoint."""
        url = f"https://i.instagram.com/api/v1/users/{user_id}/info/"
        headers = {
            "accept": "application/json",
            "x-ig-app-id": _X_IG_APP_ID,
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://www.instagram.com/",
        }
        try:
            response = await page.request.get(url, headers=headers)
            if not response.ok:
                return None
            text = await response.text()
            if text.startswith("for(;;);"):
                text = text[len("for(;;);"):]
            data = json.loads(text)
            user = _nested_value(data, "user") if isinstance(data, dict) else None
            if not isinstance(user, dict):
                return None
            return _first_int(
                user.get("follower_count"),
                user.get("followers"),
                _nested_value(user, "edge_followed_by", "count"),
            )
        except Exception:
            return None

    async def _internal_api_metadata(
        self, page: Page, shortcode: str
    ) -> dict[str, Any] | None:
        """Resolve a shortcode to a numeric media_id and call the mobile media info API.

        The ``i.instagram.com`` endpoint requires an authenticated session. Using the
        Playwright request context carries the logged-in cookies, and the response has
        the same ``items[0]`` shape that ``_extract_post_metadata`` already supports.
        """
        media_id = _shortcode_to_media_id(shortcode)
        if media_id is None:
            return None
        url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
        headers = {
            "accept": "application/json",
            "x-ig-app-id": _X_IG_APP_ID,
            "x-requested-with": "XMLHttpRequest",
            "referer": f"https://www.instagram.com/p/{shortcode}/",
        }
        try:
            response = await page.request.get(url, headers=headers)
            if not response.ok:
                text = await response.text()
                self._raise_for_intervention_response(response, text)
                return None
            text = await response.text()
            if text.startswith("for(;;);"):
                text = text[len("for(;;);"):]
            try:
                data = json.loads(text)
            except Exception:
                return None
            if not isinstance(data, dict):
                return None
            metadata = _extract_post_metadata(shortcode, data) or {}
            if not metadata.get("owner_follower_count"):
                owner = (
                    _nested_value(data, "items", 0, "user")
                    or _nested_value(data, "items", 0, "owner")
                    or {}
                )
                owner_pk = _first_int(owner.get("pk"), owner.get("id"))
                if owner_pk is not None:
                    follower_count = await self._fetch_owner_follower_count(page, owner_pk)
                    if follower_count:
                        metadata["owner_follower_count"] = follower_count
            return metadata or None
        except NeedsInterventionError:
            raise
        except Exception:
            return None
        return None

    async def _graph_api_metadata(self, shortcode: str) -> dict[str, Any] | None:
        """Best-effort Graph API fallback to resolve a timestamp for a public post.

        This requires ``meta_trend_access_token`` (or equivalent) and the necessary
        Instagram oEmbed / media permissions. Errors are swallowed so that missing
        permissions do not break the scraper.
        """
        access_token = self._access_token
        if access_token is None:
            return None
        version = self.settings.instagram_graph_api_version
        permalink = f"https://www.instagram.com/p/{shortcode}/"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if self.graph_limiter is not None:
                    await self.graph_limiter.acquire()
                oembed_resp = await client.get(
                    f"https://graph.facebook.com/{version}/instagram_oembed",
                    params={
                        "url": permalink,
                        "access_token": access_token,
                        "fields": "media_id,shortcode,author_name",
                    },
                )
                if not oembed_resp.is_success:
                    return None
                oembed = oembed_resp.json()
                media_id = oembed.get("media_id")
                if not media_id:
                    return None
                if self.graph_limiter is not None:
                    await self.graph_limiter.acquire()
                media_resp = await client.get(
                    f"https://graph.facebook.com/{version}/{media_id}",
                    params={
                        "access_token": access_token,
                        "fields": (
                            "timestamp,like_count,comments_count,caption,"
                            "media_url,permalink,media_type,username"
                        ),
                    },
                )
                if not media_resp.is_success:
                    return None
                media = media_resp.json()
                taken_at = _parse_timestamp(media.get("timestamp"))
                if taken_at is None:
                    return None
                result: dict[str, Any] = {"taken_at": taken_at}
                if media.get("like_count"):
                    result["like_count"] = int(media["like_count"])
                if media.get("comments_count"):
                    result["comment_count"] = int(media["comments_count"])
                if media.get("caption"):
                    result["caption_text"] = str(media["caption"])
                if media.get("media_url"):
                    result["video_url"] = str(media["media_url"])
                if media.get("permalink"):
                    result["permalink"] = str(media["permalink"])
                if media.get("username"):
                    result["owner_username"] = str(media["username"])
                return result
        except Exception:
            return None

    async def _yt_dlp_metadata(self, shortcode: str) -> dict[str, Any] | None:
        """Fallback metadata extraction using yt-dlp for public Instagram reels."""
        from app.providers.metadata import _fetch_ytdlp_post

        return await _fetch_ytdlp_post(shortcode)

    async def _api_headers(self, page: Page, referer: str | None = None) -> dict[str, str]:
        """Build headers for Instagram internal API calls using the Playwright session."""
        headers: dict[str, str] = {
            "accept": "application/json",
            "x-ig-app-id": _X_IG_APP_ID,
            "x-requested-with": "XMLHttpRequest",
        }
        if referer:
            headers["referer"] = referer
        csrf = await self._csrf_token(page)
        if csrf:
            headers["x-csrftoken"] = csrf
        return headers

    async def _csrf_token(self, page: Page) -> str | None:
        """Extract the csrftoken from the Playwright context or page shared data."""
        try:
            cookies = await page.context.cookies()
        except Exception:
            cookies = []
        for cookie in cookies:
            if isinstance(cookie, dict) and cookie.get("name") == "csrftoken":
                return cookie.get("value")
        try:
            return cast(
                str | None,
                await page.evaluate(
                    "() => { try { return window._sharedData.config.csrf_token; } "
                    "catch(e) { return null; } }"
                ),
            )
        except Exception:
            return None

    async def _process_media_node(
        self,
        node: dict[str, Any],
        keyword: str,
        seen_canonical: set[str],
        is_existing: Callable[[str], Awaitable[bool]] | None,
        cutoff: datetime | None,
        skip_stats: dict[str, int],
        on_event: EmitFn,
    ) -> tuple[ScrapedItem | None, bool]:
        """Validate and convert a single internal API media node into a ScrapedItem."""
        shortcode = _first_str(node.get("code"), node.get("shortcode"))
        if not shortcode:
            return None, False

        canonical = f"https://www.instagram.com/reel/{shortcode}/"
        if canonical in seen_canonical:
            return None, False
        seen_canonical.add(canonical)

        if is_existing is not None and await is_existing(canonical):
            skip_stats["existing"] += 1
            await on_event(
                f"Skipping already stored post: {shortcode}",
                step="filtered",
                keyword=keyword,
                shortcode=shortcode,
                reason="existing",
            )
            return None, False

        metadata = _extract_post_metadata(shortcode, node) or {}
        metadata["source"] = "instagram"

        taken_at_raw = metadata.get("taken_at")
        taken_at: datetime | None = None
        if isinstance(taken_at_raw, int):
            taken_at = datetime.fromtimestamp(taken_at_raw, tz=UTC)
        elif isinstance(taken_at_raw, str) and taken_at_raw:
            try:
                taken_at = datetime.fromisoformat(taken_at_raw.replace("Z", "+00:00"))
            except ValueError:
                pass

        if cutoff is not None and taken_at is None:
            skip_stats["unknown_date"] += 1
            await on_event(
                f"Skipping post with unknown publish date: {shortcode}",
                step="filtered",
                keyword=keyword,
                shortcode=shortcode,
                reason="unknown_date",
            )
            return None, False

        if cutoff is not None and taken_at is not None and taken_at < cutoff:
            skip_stats["too_old"] += 1
            await on_event(
                f"Skipping post older than "
                f"{self.settings.scraper_max_content_age_days} day(s): {shortcode}",
                step="filtered",
                keyword=keyword,
                shortcode=shortcode,
                taken_at=taken_at.isoformat() if taken_at else None,
                reason="too_old",
            )
            return None, True

        return ScrapedItem(
            canonical_url=canonical,
            shortcode=shortcode,
            caption=metadata.get("caption_text", ""),
            author=metadata.get("owner_username"),
            thumbnail_url=metadata.get("thumbnail_url"),
            discovered_keyword=keyword,
            source="instagram",
            metadata=metadata,
        ), False

    async def _try_feed_tag(
        self,
        page: Page,
        keyword: str,
        limit: int,
        on_event: EmitFn,
        is_existing: Callable[[str], Awaitable[bool]] | None,
        cutoff: datetime | None,
    ) -> list[ScrapedItem] | None:
        """Collect recent posts from the legacy ``feed/tag`` internal API."""
        tag = keyword.lstrip("#").replace(" ", "")
        seen_canonical: set[str] = set()
        result: list[ScrapedItem] = []
        skip_stats: dict[str, int] = {"existing": 0, "too_old": 0, "unknown_date": 0}
        next_max_id: str | None = None

        for _ in range(_MAX_TAG_API_PAGES):
            if len(result) >= limit:
                break
            params: dict[str, str] = {"__a": "1", "rank_token": str(uuid.uuid4())}
            if next_max_id:
                params["max_id"] = next_max_id
            url = f"{_FEED_TAG_URL.format(tag=tag)}?{urlencode(params)}"
            headers = await self._api_headers(
                page, referer=f"https://www.instagram.com/explore/tags/{tag}/"
            )
            try:
                response = await page.request.get(url, headers=headers)
                if not response.ok:
                    return None
                text = await response.text()
                if text.startswith("for(;;);"):
                    text = text[len("for(;;);") :]
                data = json.loads(text)
            except Exception:
                return None

            if not isinstance(data, dict) or data.get("status") == "fail":
                return None

            nodes = _media_nodes_from_response(data)
            if not nodes:
                return None

            next_max_id, _, more = _next_pagination_from_response(data)
            all_too_old = True
            for node in nodes:
                item, is_too_old = await self._process_media_node(
                    node,
                    keyword,
                    seen_canonical,
                    is_existing,
                    cutoff,
                    skip_stats,
                    on_event,
                )
                if item:
                    result.append(item)
                    all_too_old = False
                elif not is_too_old:
                    all_too_old = False
                if len(result) >= limit:
                    break

            if len(result) >= limit:
                break
            if all_too_old:
                break
            if not more or not next_max_id:
                break
            await self._pace()

        await on_event(
            f"Collected {len(result)} item(s) for '{keyword}' via feed/tag API.",
            step="collected",
            keyword=keyword,
            count=len(result),
            skip_stats=skip_stats,
            source="feed_tag_api",
        )
        return result

    async def _try_tag_sections(
        self,
        page: Page,
        keyword: str,
        limit: int,
        on_event: EmitFn,
        is_existing: Callable[[str], Awaitable[bool]] | None,
        cutoff: datetime | None,
    ) -> list[ScrapedItem] | None:
        """Collect posts from the web ``tags/{tag}/sections`` internal API.

        Queries the ``clips`` tab first so real Reels (which carry ``play_count``)
        are preferred, then fills the remaining quota from the ``recent`` tab.
        """
        tag = keyword.lstrip("#").replace(" ", "")
        seen_canonical: set[str] = set()
        result: list[ScrapedItem] = []
        skip_stats: dict[str, int] = {"existing": 0, "too_old": 0, "unknown_date": 0}

        any_tab_ok = False
        for tab in ("clips", "recent"):
            tab_ok = await self._collect_tag_sections_tab(
                page,
                tag,
                keyword,
                limit,
                tab,
                on_event,
                is_existing,
                cutoff,
                result,
                seen_canonical,
                skip_stats,
            )
            any_tab_ok = any_tab_ok or tab_ok
            if len(result) >= limit:
                break

        if not result and not any_tab_ok:
            return None

        await on_event(
            f"Collected {len(result)} item(s) for '{keyword}' via tag sections API.",
            step="collected",
            keyword=keyword,
            count=len(result),
            skip_stats=skip_stats,
            source="tag_sections_api",
        )
        return result

    async def _collect_tag_sections_tab(
        self,
        page: Page,
        tag: str,
        keyword: str,
        limit: int,
        tab: str,
        on_event: EmitFn,
        is_existing: Callable[[str], Awaitable[bool]] | None,
        cutoff: datetime | None,
        result: list[ScrapedItem],
        seen_canonical: set[str],
        skip_stats: dict[str, int],
    ) -> bool:
        """Append up to ``limit`` posts from one ``tags/{tag}/sections`` tab.

        Returns True when at least one page of the tab responded with usable
        data, so the caller can decide whether the sections API works at all.
        """
        state: dict[str, Any] = {"next_max_id": None, "next_media_ids": None, "page": 1}
        tab_ok = False

        for _ in range(_MAX_TAG_API_PAGES):
            if len(result) >= limit:
                break
            url = f"{_TAG_SECTIONS_URL.format(tag=tag)}?__a=1"
            form: dict[str, Any] = {
                "include_persistent": "0",
                "page": str(state["page"]),
                "surface": "grid",
                "tab": tab,
            }
            if state["next_max_id"]:
                form["max_id"] = state["next_max_id"]
            if state["next_media_ids"]:
                form["next_media_ids[]"] = state["next_media_ids"]
            headers = await self._api_headers(
                page, referer=f"https://www.instagram.com/explore/tags/{tag}/"
            )
            headers["content-type"] = "application/x-www-form-urlencoded"
            try:
                response = await page.request.post(url, form=form, headers=headers)
                if not response.ok:
                    return tab_ok
                text = await response.text()
                if text.startswith("for(;;);"):
                    text = text[len("for(;;);") :]
                data = json.loads(text)
            except Exception:
                return tab_ok

            if not isinstance(data, dict) or data.get("status") == "fail":
                return tab_ok

            nodes = _media_nodes_from_response(data)
            if not nodes:
                return tab_ok

            tab_ok = True

            next_max_id, next_media_ids, more = _next_pagination_from_response(data)
            state["next_max_id"] = next_max_id
            state["next_media_ids"] = next_media_ids
            state["page"] += 1

            all_too_old = True
            for node in nodes:
                item, is_too_old = await self._process_media_node(
                    node,
                    keyword,
                    seen_canonical,
                    is_existing,
                    cutoff,
                    skip_stats,
                    on_event,
                )
                if item:
                    result.append(item)
                    all_too_old = False
                elif not is_too_old:
                    all_too_old = False
                if len(result) >= limit:
                    break

            if len(result) >= limit:
                break
            if all_too_old:
                break
            if not more:
                break
            await self._pace()

        return tab_ok

    async def _collect_tag_api(
        self,
        page: Page,
        keyword: str,
        limit: int,
        on_event: EmitFn,
        is_existing: Callable[[str], Awaitable[bool]] | None,
    ) -> list[ScrapedItem] | None:
        """Try internal tag APIs first and return items if any work."""
        max_age_days = self.settings.scraper_max_content_age_days
        cutoff = utcnow() - timedelta(days=max_age_days) if max_age_days else None

        await on_event(
            f"Collecting '{keyword}' via internal tag API.",
            step="collect",
            keyword=keyword,
            limit=limit,
        )

        items = await self._try_feed_tag(
            page, keyword, limit, on_event, is_existing, cutoff
        )
        if items is not None:
            return items

        items = await self._try_tag_sections(
            page, keyword, limit, on_event, is_existing, cutoff
        )
        if items is not None:
            return items

        return None

    async def _collect_reels(
        self,
        page: Page,
        keyword: str,
        limit: int,
        on_event: EmitFn = noop_emit,
        is_existing: Callable[[str], Awaitable[bool]] | None = None,
    ) -> list[ScrapedItem]:
        """Scroll the keyword page and collect up to ``limit`` recent unique reels/posts.

        Posts already stored in the database are skipped and do not count toward
        ``limit``. Scrolling keeps going until the target is reached, the feed stops
        producing new candidates, or a maximum number of scroll attempts is hit.
        """
        seen_canonical: set[str] = set()
        result: list[ScrapedItem] = []
        skip_stats: dict[str, int] = {
            "existing": 0,
            "too_old": 0,
            "unknown_date": 0,
        }
        max_age_days = self.settings.scraper_max_content_age_days
        cutoff = utcnow() - timedelta(days=max_age_days) if max_age_days else None
        max_scroll_attempts = min(200, max(50, limit * 2))
        empty_scrolls = 0
        scroll_attempts = 0

        await on_event(
            f"Collecting up to {limit} recent reel(s) for '{keyword}'.",
            step="collect",
            keyword=keyword,
            limit=limit,
        )

        api_items = await self._collect_tag_api(
            page, keyword, limit, on_event, is_existing
        )
        if api_items is not None:
            return api_items

        hrefs = await self._get_current_hrefs(page)
        while (
            len(result) < limit
            and empty_scrolls < _MAX_EMPTY_SCROLLS
            and scroll_attempts < max_scroll_attempts
        ):
            new_this_scroll = 0
            for href in hrefs:
                try:
                    canonical, shortcode = parse_instagram_url(href)
                except ValueError:
                    continue
                if canonical in seen_canonical:
                    continue
                seen_canonical.add(canonical)
                new_this_scroll += 1

                # Skip already stored content; it must not count toward the keyword target.
                if is_existing is not None and await is_existing(canonical):
                    skip_stats["existing"] += 1
                    await on_event(
                        f"Skipping already stored post: {shortcode}",
                        step="filtered",
                        keyword=keyword,
                        shortcode=shortcode,
                        reason="existing",
                    )
                    continue

                metadata = await self._post_metadata(page, shortcode) or {}
                if not metadata:
                    metadata = {"source": "instagram"}

                taken_at_raw = metadata.get("taken_at")
                taken_at: datetime | None = None
                if isinstance(taken_at_raw, int):
                    taken_at = datetime.fromtimestamp(taken_at_raw, tz=UTC)
                elif isinstance(taken_at_raw, str) and taken_at_raw:
                    try:
                        taken_at = datetime.fromisoformat(
                            taken_at_raw.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                if cutoff is not None and taken_at is None:
                    skip_stats["unknown_date"] += 1
                    await on_event(
                        f"Skipping post with unknown publish date: {shortcode}",
                        step="filtered",
                        keyword=keyword,
                        shortcode=shortcode,
                        reason="unknown_date",
                    )
                    continue

                if cutoff is not None and taken_at is not None and taken_at < cutoff:
                    skip_stats["too_old"] += 1
                    await on_event(
                        f"Skipping post older than {max_age_days} day(s): {shortcode}",
                        step="filtered",
                        keyword=keyword,
                        shortcode=shortcode,
                        taken_at=taken_at.isoformat(),
                        reason="too_old",
                    )
                    continue

                result.append(
                    ScrapedItem(
                        canonical_url=canonical,
                        shortcode=shortcode,
                        caption=metadata.get("caption_text", ""),
                        author=metadata.get("owner_username"),
                        thumbnail_url=metadata.get("thumbnail_url"),
                        discovered_keyword=keyword,
                        metadata=metadata,
                    )
                )
                if len(result) >= limit:
                    break

            if len(result) >= limit:
                break

            if new_this_scroll == 0:
                empty_scrolls += 1
            else:
                empty_scrolls = 0

            if empty_scrolls >= _MAX_EMPTY_SCROLLS:
                break

            scroll_height = await self._scroll_height(page)
            await on_event(
                (
                    f"Scrolled '{keyword}' (attempt {scroll_attempts + 1}): "
                    f"seen={len(seen_canonical)}, collected={len(result)}, "
                    f"empty_scrolls={empty_scrolls}, scroll_height={scroll_height}."
                ),
                step="scroll",
                keyword=keyword,
                seen=len(seen_canonical),
                collected=len(result),
                empty_scrolls=empty_scrolls,
                scroll_height=scroll_height,
                scroll_attempt=scroll_attempts + 1,
            )

            await self._scroll_feed(page)
            hrefs = await self._wait_for_new_hrefs(
                page, seen_canonical, _PER_SCROLL_POLL_TIMEOUT_S
            )
            scroll_attempts += 1

        await on_event(
            f"Collected {len(result)} recent reel(s) for '{keyword}'.",
            step="collected",
            keyword=keyword,
            count=len(result),
            seen=len(seen_canonical),
            skip_stats=skip_stats,
            scroll_attempts=scroll_attempts,
            empty_scrolls=empty_scrolls,
        )
        return result

    async def _get_current_hrefs(self, page: Page) -> list[str]:
        """Return the set of candidate post/reel URLs currently in the DOM."""
        try:
            hrefs = await page.locator(_CANDIDATE_SELECTOR).evaluate_all(
                "(els) => els.map((e) => e.href)"
                ".filter((value, index, arr) => arr.indexOf(value) === index)"
            )
        except PlaywrightError:
            return []
        if not isinstance(hrefs, list):
            return []
        return cast(list[str], hrefs)

    def _has_unseen(self, hrefs: list[str], seen: set[str]) -> bool:
        for href in hrefs:
            try:
                canonical, _ = parse_instagram_url(href)
            except ValueError:
                continue
            if canonical not in seen:
                return True
        return False

    async def _wait_for_new_hrefs(
        self,
        page: Page,
        seen: set[str],
        timeout_s: float,
    ) -> list[str]:
        """Scroll/poll until new unseen candidate links appear or timeout elapses."""
        poll_ms = int(_POLL_INTERVAL_S * 1000)
        max_polls = max(1, int(timeout_s / _POLL_INTERVAL_S))
        for _ in range(max_polls):
            hrefs = await self._get_current_hrefs(page)
            if self._has_unseen(hrefs, seen):
                return hrefs
            await page.wait_for_timeout(poll_ms)
        return await self._get_current_hrefs(page)

    async def _scroll_feed(self, page: Page) -> None:
        script = """
            (function() {
                const main = document.querySelector('main');
                if (main && main.scrollHeight > main.clientHeight) {
                    main.scrollTo(0, main.scrollHeight);
                } else {
                    window.scrollTo(0, document.body.scrollHeight);
                    window.scrollTo(0, document.documentElement.scrollHeight);
                }
            })()
        """
        try:
            await page.evaluate(script)
        except PlaywrightError:
            pass

    async def _scroll_height(self, page: Page) -> int:
        try:
            height = await page.evaluate(
                "Math.max(document.body ? document.body.scrollHeight : 0, "
                "document.documentElement ? document.documentElement.scrollHeight : 0)"
            )
        except PlaywrightError:
            return 0
        if isinstance(height, (int, float)):
            return int(height)
        if isinstance(height, str) and height.isdigit():
            return int(height)
        return 0

    def _search_url(self, keyword: str) -> str:
        query = keyword.lstrip("#").strip()
        return f"https://www.instagram.com/explore/search/keyword/?q={quote(query, safe='')}"

    def _tag_url(self, keyword: str) -> str:
        slug = keyword.lstrip("#").replace(" ", "")
        return f"https://www.instagram.com/explore/tags/{slug}/"

    async def _feed_ready(self, page: Page, timeout_s: float) -> bool:
        """Return True once at least one candidate link exists in the DOM."""
        poll_ms = int(_POLL_INTERVAL_S * 1000)
        max_polls = max(1, int(timeout_s / _POLL_INTERVAL_S))
        for _ in range(max_polls):
            try:
                count = await page.locator(_CANDIDATE_SELECTOR).count()
            except PlaywrightError:
                count = 0
            if count:
                return True
            await page.wait_for_timeout(poll_ms)
        return False

    async def _open_keyword_feed(
        self,
        page: Page,
        keyword: str,
        on_event: EmitFn = noop_emit,
    ) -> bool:
        """Open the search/keyword feed and wait for it to render.

        Falls back to the legacy tag page if the searchable keyword feed does not
        load candidates within the timeout.
        """
        search_url = self._search_url(keyword)
        tag_url = self._tag_url(keyword)
        urls = [(search_url, "search"), (tag_url, "tag")]
        for url, label in urls:
            await on_event(
                f"Opening explore page for '{keyword}' ({label}).",
                step="keyword",
                keyword=keyword,
                url=url,
            )
            await page.goto(url, wait_until="domcontentloaded")
            await self._intervention_guard(page)
            await self._ensure_authenticated(page, target_url=url, on_event=on_event)
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except PlaywrightError:
                pass
            await self._dismiss_cookie_banner(page)
            if await self._feed_ready(page, _FEED_READY_TIMEOUT_S):
                await on_event(
                    f"Feed ready for '{keyword}' ({label}).",
                    step="keyword",
                    keyword=keyword,
                    url=url,
                )
                return True
            await on_event(
                f"Feed not ready on {label} URL for '{keyword}', trying fallback.",
                step="keyword",
                keyword=keyword,
                url=url,
            )
        return False

    async def _prepare_creator_profile_page(
        self,
        page: Page,
        username: str,
        on_event: EmitFn,
    ) -> None:
        """Open a creator profile and dismiss blocking cookie/login prompts."""
        profile_url = f"https://www.instagram.com/{username}/"
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
        await self._intervention_guard(page)
        await self._ensure_authenticated(
            page, target_url=profile_url, on_event=on_event
        )
        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except PlaywrightError:
            pass
        await self._dismiss_cookie_banner(page)
        await self._dismiss_login_prompt(page)

    async def fetch_creator_profile(
        self,
        username: str,
        limit: int,
        on_event: EmitFn = noop_emit,
    ) -> dict[str, Any]:
        """Fetch Instagram profile data via the internal web_profile_info API.

        Tries to view the profile without authenticating, closing any login/
        signup modal so public creators can be tracked. Falls back to the saved
        storage-state cache or a full login only when Instagram requires it.
        """
        async with self._session_context(
            on_event=on_event,
            headless=self.settings.creator_tracking_headless,
            require_auth=False,
        ) as page:
            await on_event(
                f"Opening profile page for @{username}.",
                step="profile",
                username=username,
            )
            await self._prepare_creator_profile_page(page, username, on_event)

            profile_url = f"https://www.instagram.com/{username}/"
            api_url = (
                "https://i.instagram.com/api/v1/users/web_profile_info/"
                f"?username={username}"
            )
            headers = await self._api_headers(page, referer=profile_url)
            await on_event(
                f"Fetching profile API for @{username}.",
                step="profile",
                username=username,
            )
            try:
                response = await page.request.get(api_url, headers=headers)
            except PlaywrightError as exc:
                raise ProfileFetchError(
                    "request_failed",
                    f"Instagram profile request failed for @{username}: {exc}",
                ) from exc

            if response.status in (401, 403):
                credentials_configured = bool(
                    (self.settings.instagram_username or "").strip()
                    and self.settings.instagram_password
                    and self.settings.instagram_password.get_secret_value().strip()
                )
                if credentials_configured:
                    await on_event(
                        "Instagram profile API requires authentication; logging in.",
                        level="warning",
                        step="session",
                    )
                    await self._login(page)
                    await self._prepare_creator_profile_page(
                        page, username, on_event
                    )
                    headers = await self._api_headers(page, referer=profile_url)
                    try:
                        response = await page.request.get(api_url, headers=headers)
                    except PlaywrightError as exc:
                        raise ProfileFetchError(
                            "request_failed",
                            f"Instagram profile request failed for @{username}: {exc}",
                        ) from exc
                else:
                    raise NeedsInterventionError(
                        "Instagram requires authentication to view this profile. "
                        "Configure INVOLO_INSTAGRAM_USERNAME and INVOLO_INSTAGRAM_PASSWORD "
                        "or provide a valid saved session."
                    )

            if response.status == 404:
                raise ProfileFetchError(
                    "not_found", f"creator @{username} was not found"
                )
            if response.status == 429:
                text = await response.text()
                retry_after = response.headers.get("retry-after")
                retry_after_s = float(retry_after) if retry_after else 300.0
                raise TransientError(
                    f"Instagram profile API rate limited for @{username}: {text[:200]}",
                    retry_after=retry_after_s,
                )
            if not response.ok:
                text = await response.text()
                raise ProfileFetchError(
                    "request_failed",
                    (
                        f"Instagram profile API returned {response.status} "
                        f"for @{username}: {text[:200]}"
                    ),
                )

            text = await response.text()
            if text.startswith("for(;;);"):
                text = text[len("for(;;);"):]
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise ProfileFetchError(
                    "invalid_response",
                    f"invalid JSON from Instagram for @{username}: {exc}",
                ) from exc
            if not isinstance(data, dict):
                raise ProfileFetchError(
                    "invalid_response",
                    f"non-object response from Instagram for @{username}",
                )

            user_data = (data.get("data") or {}).get("user")
            if not isinstance(user_data, dict):
                raise ProfileFetchError(
                    "not_found", f"creator @{username} was not found"
                )
            if user_data.get("is_private"):
                raise ProfileFetchError(
                    "private", f"creator @{username} is private"
                )

            timeline = user_data.get("edge_owner_to_timeline_media") or {}
            edges = timeline.get("edges", [])
            if isinstance(edges, list) and len(edges) > limit:
                timeline["edges"] = edges[:limit]
                user_data["edge_owner_to_timeline_media"] = timeline
            return user_data

    async def scrape(
        self,
        keywords: list[str],
        limit: int,
        on_event: EmitFn = noop_emit,
        *,
        is_existing: Callable[[str], Awaitable[bool]] | None = None,
    ) -> list[ScrapedItem]:
        keywords = keywords[: self.settings.scraper_max_keywords]
        async with self._session_context(
            on_event=on_event, headless=self.settings.scraper_headless
        ) as page:
            items: list[ScrapedItem] = []
            for keyword in keywords:
                feed_opened = False
                attempts = 0
                while not feed_opened and attempts < 2:
                    attempts += 1
                    feed_opened = await self._open_keyword_feed(page, keyword, on_event)
                    if not feed_opened:
                        if "/accounts/login" in page.url and attempts == 1:
                            await on_event(
                                "Instagram session expired; logging in.",
                                level="warning",
                                step="session",
                            )
                            await self._login(page)
                        else:
                            await on_event(
                                f"Could not open explore page for '{keyword}'.",
                                level="error",
                                step="keyword",
                                keyword=keyword,
                            )
                            break
                if not feed_opened:
                    continue
                items.extend(
                    await self._collect_reels(page, keyword, limit, on_event, is_existing)
                )
                await self._pace()
            return items


class FixtureTrendAdapter(ScraperAdapter):
    """JSON-fixture scraper for local development and tests (no network)."""

    def __init__(self, settings: Settings, *, access_token: str | None = None) -> None:
        self.settings = settings
        self._access_token = access_token
        self.path = Path(settings.scraper_fixture_path)

    async def scrape(
        self,
        keywords: list[str],
        limit: int,
        on_event: EmitFn = noop_emit,
        *,
        is_existing: Callable[[str], Awaitable[bool]] | None = None,
    ) -> list[ScrapedItem]:
        _ = is_existing
        rows = json.loads(self.path.read_text()) if self.path.exists() else []
        result: list[ScrapedItem] = []
        for keyword in keywords:
            await on_event(f"Searching fixture '{keyword}'.", step="keyword", keyword=keyword)
            matches = [
                row for row in rows if row.get("keyword", "").lower() == keyword.lower()
            ]
            for row in matches[:limit]:
                canonical, shortcode = parse_instagram_url(row["url"])
                result.append(
                    ScrapedItem(
                        canonical_url=canonical,
                        shortcode=shortcode,
                        caption=row.get("caption", ""),
                        author=row.get("author"),
                        thumbnail_url=row.get("thumbnail_url"),
                        discovered_keyword=keyword,
                        metadata=row.get("metadata", {}),
                    )
                )
        return result


class InstagramSessionMetadataProvider:
    """Fetch per-post metadata from Instagram's authenticated internal media API.

    Tag/feed discovery nodes do not include play/view counts, video URLs or
    durations. This provider reuses the Playwright session cookies (storage
    state) to call ``i.instagram.com/api/v1/media/{media_id}/info/`` directly
    from the enrichment worker, without launching a browser. It is the primary
    completion source for public posts; Graph API and yt-dlp fallbacks stay
    disabled for this provenance.
    """

    skip_for_instagram_source = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _load_cookies(self) -> dict[str, str]:
        try:
            state = json.loads(
                Path(self.settings.scraper_storage_state_path).read_text()
            )
        except (OSError, json.JSONDecodeError):
            return {}
        cookies: dict[str, str] = {}
        for cookie in state.get("cookies", []):
            name = cookie.get("name")
            value = cookie.get("value")
            if isinstance(name, str) and isinstance(value, str):
                cookies[name] = value
        return cookies

    async def fetch(
        self,
        shortcode: str,
        discovered_metadata: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> ContentMetadata:
        from app.providers.metadata import _merge_content_metadata, metadata_from_dict

        base = metadata_from_dict(shortcode, discovered_metadata or {})
        cookies = self._load_cookies()
        if not cookies.get("sessionid"):
            logger.warning(
                "Instagram storage state %s has no session cookie; "
                "cannot resolve per-post metadata for %r",
                self.settings.scraper_storage_state_path,
                shortcode,
            )
            return base
        media_id = _shortcode_to_media_id(shortcode)
        if media_id is None:
            return base
        url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
        headers = {
            "accept": "application/json",
            "x-ig-app-id": _X_IG_APP_ID,
            "x-requested-with": "XMLHttpRequest",
            "referer": f"https://www.instagram.com/reel/{shortcode}/",
        }
        if cookies.get("csrftoken"):
            headers["x-csrftoken"] = cookies["csrftoken"]
        try:
            async with httpx.AsyncClient(
                timeout=20.0, cookies=cookies, headers=headers
            ) as client:
                response = await client.get(url)
            text = response.text
        except Exception as exc:  # noqa: BLE001 - network is best-effort
            logger.warning(
                "Instagram media info request failed for %r: %s", shortcode, exc
            )
            return base
        if text.startswith("for(;;);"):
            text = text[len("for(;;);") :]
        status = response.status_code
        lowered = text.lower()
        if status in (401, 403) or (
            status == 400
            and any(
                word in lowered
                for word in ("checkpoint", "challenge", "suspicious", "restrict", "unusual")
            )
        ):
            raise NeedsInterventionError(
                "Instagram media info API requires verification or has blocked the session"
            )
        if status < 200 or status >= 300:
            logger.warning(
                "Instagram media info %s returned %s for %r",
                media_id,
                status,
                shortcode,
            )
            return base
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return base
        row = _extract_post_metadata(shortcode, data)
        if not row:
            return base
        row["source"] = "instagram"
        fetched = metadata_from_dict(shortcode, row)
        return _merge_content_metadata(base, fetched)


def build_scraper(
    settings: Settings,
    *,
    access_token: str | None = None,
    redis: Any | None = None,
) -> ScraperAdapter:
    graph_limiter = build_graph_rate_limiter(redis, settings)
    if settings.scraper_adapter == "meta":
        return MetaTrendAdapter(settings, access_token=access_token, graph_limiter=graph_limiter)
    if settings.scraper_adapter == "instagram":
        return InstagramScraper(settings, access_token=access_token, graph_limiter=graph_limiter)
    if settings.scraper_adapter == "fixture":
        return FixtureTrendAdapter(settings, access_token=access_token)
    raise RuntimeError(f"unsupported scraper adapter: {settings.scraper_adapter}")
