"""Tracked-creator profile providers backed by the Instagram Graph API.

Uses the official Meta Business Discovery edge on ``graph.facebook.com`` to
read public Instagram Business and Creator accounts. This replaces the
undocumented ``web_profile_info`` public endpoint and the yt-dlp fallback.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import TransientError, transient_from_response
from app.core.rate_limit import GraphApiRateLimiter
from app.providers.scraper import (
    InstagramScraper,
    NeedsInterventionError,
    ProfileFetchError,
    _extract_post_metadata,
    _first_int,
    _first_str,
    _nested_value,
)

_GRAPH_URL = "https://graph.facebook.com"


class CreatorProfileError(RuntimeError):
    pass


class CreatorNotFoundError(CreatorProfileError):
    pass


@dataclass(frozen=True)
class CreatorPost:
    shortcode: str
    caption: str
    media_type: str
    permalink: str | None
    taken_at: datetime
    like_count: int
    comment_count: int
    view_count: int
    media_url: str | None = None
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class CreatorProfileSnapshot:
    username: str
    display_name: str
    bio: str
    avatar_url: str | None
    follower_count: int
    following_count: int
    media_count: int
    is_private: bool
    posts: list[CreatorPost] = field(default_factory=list)


class CreatorProfileProvider(ABC):
    @abstractmethod
    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        raise NotImplementedError

    async def exists(self, username: str) -> bool:
        """Default existence check; subclasses may override with a cheaper probe."""
        try:
            await self.fetch_profile(username)
        except CreatorNotFoundError:
            return False
        return True


def _parse_iso_timestamp(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)


def _graph_media_type(row: dict[str, Any]) -> str:
    product_type = str(row.get("media_product_type") or "").upper()
    base_type = str(row.get("media_type") or "IMAGE").upper()
    if product_type == "REELS" or base_type == "VIDEO":
        return "REELS"
    if base_type == "CAROUSEL_ALBUM":
        return "CAROUSEL_ALBUM"
    return "IMAGE"


def _graph_media_urls(
    row: dict[str, Any], media_type: str
) -> tuple[str | None, str | None]:
    if media_type == "CAROUSEL_ALBUM":
        children = row.get("children") or {}
        child_rows = [c for c in children.get("data", []) if isinstance(c, dict)]
        first_child = child_rows[0] if child_rows else {}
        media_url = first_child.get("media_url") or row.get("media_url")
        thumbnail_url = first_child.get("thumbnail_url") or row.get("thumbnail_url")
        return media_url, thumbnail_url or media_url

    media_url = row.get("media_url")
    thumbnail_url = row.get("thumbnail_url")
    if media_type == "REELS" and not media_url:
        # Graph API does not always expose a downloadable Reels video URL.
        # Keep media_url None so the pipeline does not try to transcribe
        # a static thumbnail as a video.
        return None, thumbnail_url
    return media_url, thumbnail_url or media_url


def _parse_graph_post(row: dict[str, Any]) -> CreatorPost | None:
    shortcode = row.get("shortcode")
    permalink = row.get("permalink")
    if not shortcode and isinstance(permalink, str):
        parts = [part for part in permalink.split("/") if part]
        if len(parts) >= 2:
            shortcode = parts[-1]
    if not shortcode:
        return None

    media_type = _graph_media_type(row)
    media_url, thumbnail_url = _graph_media_urls(row, media_type)
    return CreatorPost(
        shortcode=str(shortcode),
        caption=str(row.get("caption") or ""),
        media_type=media_type,
        permalink=str(permalink) if permalink else f"https://www.instagram.com/p/{shortcode}/",
        taken_at=_parse_iso_timestamp(row.get("timestamp")),
        like_count=int(row.get("like_count") or 0),
        comment_count=int(row.get("comments_count") or 0),
        view_count=int(row.get("view_count") or 0),
        media_url=media_url,
        thumbnail_url=thumbnail_url,
    )


def _parse_graph_profile(
    payload: dict[str, Any], username: str
) -> CreatorProfileSnapshot:
    discovery = payload.get("business_discovery") or {}
    if not isinstance(discovery, dict) or not discovery.get("id"):
        raise CreatorNotFoundError(f"creator @{username} was not found")

    raw_media = discovery.get("media") or {}
    rows = [row for row in raw_media.get("data", []) if isinstance(row, dict)]
    posts = []
    for row in rows:
        post = _parse_graph_post(row)
        if post is not None:
            posts.append(post)

    return CreatorProfileSnapshot(
        username=str(discovery.get("username") or username),
        display_name=str(discovery.get("name") or ""),
        bio=str(discovery.get("biography") or ""),
        avatar_url=discovery.get("profile_picture_url"),
        follower_count=int(discovery.get("followers_count") or 0),
        following_count=int(discovery.get("follows_count") or 0),
        media_count=int(discovery.get("media_count") or 0),
        is_private=False,
        posts=posts,
    )


def _parse_playwright_timestamp(value: Any) -> datetime:
    parsed = _parse_iso_timestamp(value)
    if parsed.timestamp() > 0:
        return parsed
    if isinstance(value, int):
        return datetime.fromtimestamp(value, tz=UTC)
    return datetime.now(UTC)


def _parse_playwright_post(node: dict[str, Any]) -> CreatorPost | None:
    shortcode = node.get("shortcode")
    if not shortcode:
        return None

    metadata = _extract_post_metadata(shortcode, node) or {}
    if not metadata:
        return None

    typename = str(node.get("__typename") or "").upper()
    is_video = bool(node.get("is_video"))
    if typename == "GRAPHVIDEO" or is_video:
        media_type = "REELS"
    elif typename == "GRAPHSIDECAR":
        media_type = "CAROUSEL_ALBUM"
    else:
        media_type = "IMAGE"

    taken_at = _parse_playwright_timestamp(metadata.get("taken_at"))
    permalink = str(metadata.get("permalink") or f"https://www.instagram.com/p/{shortcode}/")
    return CreatorPost(
        shortcode=str(shortcode),
        caption=str(metadata.get("caption_text") or ""),
        media_type=media_type,
        permalink=permalink,
        taken_at=taken_at,
        like_count=_first_int(metadata.get("like_count")) or 0,
        comment_count=_first_int(metadata.get("comment_count")) or 0,
        view_count=_first_int(metadata.get("view_count")) or 0,
        media_url=metadata.get("video_url"),
        thumbnail_url=metadata.get("thumbnail_url"),
    )


def _parse_playwright_profile(
    user_data: dict[str, Any], username: str
) -> CreatorProfileSnapshot:
    timeline = user_data.get("edge_owner_to_timeline_media") or {}
    edges = timeline.get("edges", []) if isinstance(timeline, dict) else []
    posts: list[CreatorPost] = []
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node") or edge
            if isinstance(node, dict):
                post = _parse_playwright_post(node)
                if post is not None:
                    posts.append(post)

    return CreatorProfileSnapshot(
        username=str(user_data.get("username") or username),
        display_name=str(user_data.get("full_name") or ""),
        bio=str(user_data.get("biography") or ""),
        avatar_url=_first_str(
            user_data.get("profile_pic_url"), user_data.get("profile_pic_url_hd")
        ),
        follower_count=_first_int(
            _nested_value(user_data, "edge_followed_by", "count")
        )
        or 0,
        following_count=_first_int(
            _nested_value(user_data, "edge_follow", "count")
        )
        or 0,
        media_count=_first_int(
            _nested_value(user_data, "edge_owner_to_timeline_media", "count")
        )
        or len(posts),
        is_private=bool(user_data.get("is_private")),
        posts=posts,
    )


class GraphCreatorProfileProvider(CreatorProfileProvider):
    """Reads public Business/Creator profiles via the Instagram Graph API."""

    def __init__(
        self,
        settings: Settings,
        *,
        access_token: str | None = None,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self._access_token_value = access_token
        self.graph_limiter = graph_limiter

    @property
    def _access_token(self) -> str:
        if self._access_token_value:
            return self._access_token_value
        token = self.settings.meta_trend_access_token
        if not token:
            raise CreatorProfileError("meta_trend_access_token is not configured")
        return token.get_secret_value()

    @property
    def _business_account_id(self) -> str:
        account_id = self.settings.meta_instagram_business_account_id
        if not account_id:
            raise CreatorProfileError(
                "meta_instagram_business_account_id is not configured"
            )
        return account_id

    async def exists(self, username: str) -> bool:
        """Probe Instagram Graph API for the existence of a username."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            fields = f"business_discovery.username({username}){{id}}"
            try:
                payload = await self._get(
                    client, f"/{self._business_account_id}", params={"fields": fields}
                )
            except CreatorNotFoundError:
                return False
        discovery = payload.get("business_discovery") or {}
        return bool(discovery.get("id"))

    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = await self._get_profile(client, username)
        return _parse_graph_profile(payload, username)

    def _build_fields(self, username: str, limit: int, after: str | None) -> str:
        # shortcode is intentionally omitted from media/children expansion.
        # It is not available on IG Media objects returned by the Business Discovery
        # media edge and causes Graph API code 100 errors. _parse_graph_post falls
        # back to extracting shortcode from permalink.
        media_fields = (
            "id,caption,media_type,media_product_type,media_url,thumbnail_url,"
            "permalink,timestamp,like_count,comments_count,view_count,"
            "children{id,media_url,thumbnail_url,media_type}"
        )
        media_query = (
            f"media.after({after}).limit({limit})"
            if after
            else f"media.limit({limit})"
        )
        return (
            f"business_discovery.username({username})"
            "{"
            f"id,username,name,biography,profile_picture_url,followers_count,follows_count,"
            f"media_count,{media_query}{{{media_fields}}}"
            "}"
        )

    async def _get_profile(
        self, client: httpx.AsyncClient, username: str
    ) -> dict[str, Any]:
        max_posts = self.settings.creator_tracking_max_posts
        all_rows: list[dict[str, Any]] = []
        after: str | None = None
        remaining = max_posts
        first_payload: dict[str, Any] | None = None

        while remaining > 0:
            limit = min(remaining, 25)
            fields = self._build_fields(username, limit, after)
            payload = await self._get(
                client,
                f"/{self._business_account_id}",
                params={"fields": fields},
            )
            discovery = payload.get("business_discovery") or {}
            if first_payload is None:
                first_payload = payload

            media = discovery.get("media") or {}
            rows = [row for row in media.get("data", []) if isinstance(row, dict)]
            all_rows.extend(rows)

            after = (
                ((media.get("paging") or {}).get("cursors") or {}).get("after")
            )
            if not after or not rows or len(all_rows) >= max_posts:
                break
            remaining = max_posts - len(all_rows)

        if first_payload is None:
            # No successful request was made (should not happen because _get raises).
            raise CreatorNotFoundError(f"creator @{username} was not found")

        # Attach the full, paginated media list to the first discovery payload.
        first_discovery = dict(first_payload.get("business_discovery") or {})
        first_discovery["media"] = {"data": all_rows[:max_posts]}
        return {"business_discovery": first_discovery}

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.graph_limiter is not None:
            await self.graph_limiter.acquire()
        query = dict(params or {})
        query["access_token"] = self._access_token
        url = f"{_GRAPH_URL}/{self.settings.instagram_graph_api_version}{path}"
        try:
            response = await client.get(url, params=query)
        except httpx.ConnectError as exc:
            raise TransientError(f"Instagram Graph connection error: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise TransientError(f"Instagram Graph timeout: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"Instagram Graph network error: {exc}") from exc

        self._raise_for_graph_error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CreatorProfileError(f"invalid JSON from Instagram: {exc}") from exc
        if not isinstance(payload, dict):
            raise CreatorProfileError("Instagram returned a non-object response")
        return payload

    def _raise_for_graph_error(self, response: httpx.Response) -> None:
        if response.is_success:
            return

        message = "Instagram Graph API request failed"
        code = 0
        try:
            error = response.json().get("error", {})
            message = str(error.get("message") or message)
            code = int(error.get("code", 0) or 0)
        except (ValueError, AttributeError):
            pass

        if response.status_code in (401, 403) or code in (190, 200, 101):
            # 101 = Invalid platform app / OAuth configuration issue
            raise NeedsInterventionError(
                f"Instagram Graph API authentication failed: {message}"
            )

        transient = transient_from_response(response)
        if transient is not None:
            raise transient

        if response.status_code == 404 or code in (803,):
            raise CreatorNotFoundError(message)
        if response.status_code == 400 and code == 100:
            # "Unsupported get request" / invalid username frequently maps here.
            raise CreatorNotFoundError(message)

        raise CreatorProfileError(message)


class FixtureCreatorProfileProvider(CreatorProfileProvider):
    """JSON-fixture provider for local development and tests (no network)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def exists(self, username: str) -> bool:
        payload = json.loads(self.path.read_text())
        rows = payload.get("creators", payload if isinstance(payload, list) else [])
        return any(
            str(item.get("username", "")).lower() == username.lower() for item in rows
        )

    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        payload = json.loads(self.path.read_text())
        rows = payload.get("creators", payload if isinstance(payload, list) else [])
        row = next(
            (item for item in rows if str(item.get("username", "")).lower() == username.lower()),
            None,
        )
        if row is None:
            raise CreatorNotFoundError(f"no fixture creator for {username!r}")
        posts = [
            CreatorPost(
                shortcode=str(post["shortcode"]),
                caption=str(post.get("caption", "")),
                media_type=str(post.get("media_type", "REELS")),
                permalink=post.get("permalink")
                or f"https://www.instagram.com/p/{post['shortcode']}/",
                taken_at=datetime.fromisoformat(
                    str(post["taken_at"]).replace("Z", "+00:00")
                ),
                like_count=int(post.get("like_count", 0)),
                comment_count=int(post.get("comment_count", 0)),
                view_count=int(post.get("view_count", 0)),
                media_url=post.get("media_url"),
                thumbnail_url=post.get("thumbnail_url"),
            )
            for post in row.get("posts", [])
        ]
        return CreatorProfileSnapshot(
            username=str(row["username"]),
            display_name=str(row.get("display_name", "")),
            bio=str(row.get("bio", "")),
            avatar_url=row.get("avatar_url"),
            follower_count=int(row.get("follower_count", 0)),
            following_count=int(row.get("following_count", 0)),
            media_count=int(row.get("media_count", len(posts))),
            is_private=bool(row.get("is_private", False)),
            posts=posts,
        )


class PlaywrightCreatorProfileProvider(CreatorProfileProvider):
    """Reads Instagram creator profiles via a headless Playwright browser."""

    def __init__(self, settings: Settings, *, access_token: str | None = None) -> None:
        self.scraper = InstagramScraper(settings, access_token=access_token)

    async def fetch_profile(self, username: str) -> CreatorProfileSnapshot:
        try:
            user_data = await self.scraper.fetch_creator_profile(
                username,
                limit=self.scraper.settings.creator_tracking_max_posts,
            )
        except ProfileFetchError as exc:
            if exc.reason in {"not_found", "private"}:
                raise CreatorNotFoundError(str(exc)) from exc
            raise CreatorProfileError(str(exc)) from exc
        except NeedsInterventionError:
            raise
        return _parse_playwright_profile(user_data, username)


def build_creator_profile_provider(
    settings: Settings,
    *,
    access_token: str | None = None,
    redis: Any | None = None,
) -> CreatorProfileProvider:
    from app.core.rate_limit import build_graph_rate_limiter

    if settings.creator_tracking_provider == "fixture":
        return FixtureCreatorProfileProvider(settings.creator_tracking_fixture_path)
    if settings.creator_tracking_provider == "playwright":
        return PlaywrightCreatorProfileProvider(settings, access_token=access_token)
    return GraphCreatorProfileProvider(
        settings,
        access_token=access_token,
        graph_limiter=build_graph_rate_limiter(redis, settings),
    )
