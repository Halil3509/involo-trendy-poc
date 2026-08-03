"""Provider interface and implementations for brand reference analysis."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.core.errors import TransientError, transient_from_response
from app.core.rate_limit import GraphApiRateLimiter
from app.providers.instagram_profile import InstagramGraphError, InstagramNeedsReauth
from app.schemas.brand_analysis import BrandAnalysisPost, MediaEvidence


class BrandAnalysisError(RuntimeError):
    pass


class BrandAnalysisNeedsReauth(BrandAnalysisError):
    pass


class BrandAnalysisProvider(ABC):
    @abstractmethod
    async def resolve_username(self, username_or_url: str) -> str:
        """Normalize an Instagram URL or handle into a bare username."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_posts(
        self, username: str, max_posts: int, *, job_id: str
    ) -> list[BrandAnalysisPost]:
        """Return the most recent posts for the target username."""
        raise NotImplementedError


class GraphBrandAnalysisProvider(BrandAnalysisProvider):
    """Fetch public business account posts using the Instagram Graph API.

    Uses the official Meta Business Discovery edge on graph.facebook.com.
    Requires a configured Meta Instagram Business Account ID and a trend
    access token with permission to read public professional account metadata.
    """

    GRAPH_URL = "https://graph.facebook.com"

    def __init__(
        self,
        settings: Settings,
        access_token: str,
        *,
        business_account_id: str | None = None,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.access_token = access_token
        self.business_account_id = (
            business_account_id or settings.meta_instagram_business_account_id
        )
        self.graph_limiter = graph_limiter

    async def resolve_username(self, username_or_url: str) -> str:
        username = _extract_username(username_or_url)
        if not username:
            raise BrandAnalysisError("invalid_username_or_url")
        return username

    async def fetch_posts(
        self, username: str, max_posts: int, *, job_id: str
    ) -> list[BrandAnalysisPost]:
        target_account_id = await self._resolve_account_id(username)
        return await self._fetch_media(target_account_id, username, max_posts, job_id=job_id)

    async def _resolve_account_id(self, username: str) -> str:
        """Resolve a target username to an Instagram account ID.

        Uses the Business Discovery edge on the configured caller business
        account. The target must be a public Instagram Business/Creator account.
        """
        if not self.business_account_id:
            raise BrandAnalysisError("instagram_business_account_id_not_configured")
        if not self.access_token:
            raise BrandAnalysisError("meta_trend_access_token_not_configured")

        fields = f"business_discovery.username({username}){{id,username}}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = await self._get(
                client,
                f"/{self.business_account_id}",
                params={"fields": fields},
            )
        discovery = payload.get("business_discovery") or {}
        account_id = discovery.get("id")
        if account_id:
            return str(account_id)
        raise BrandAnalysisError("target_account_not_found_or_inaccessible")

    async def _fetch_media(
        self, account_id: str, username: str, max_posts: int, *, job_id: str
    ) -> list[BrandAnalysisPost]:
        posts: list[BrandAnalysisPost] = []
        media_fields = (
            "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,"
            "timestamp,username,like_count,comments_count,"
            "children{id,media_url,thumbnail_url,media_type}"
        )
        after: str | None = None
        remaining = max_posts

        async with httpx.AsyncClient(timeout=30.0) as client:
            while remaining > 0:
                media_query = (
                    f"media.after({after}).limit({remaining})"
                    if after
                    else f"media.limit({remaining})"
                )
                fields = (
                    f"business_discovery.username({username})"
                    f"{{{media_query}{{{media_fields}}}}}"
                )
                payload = await self._get(
                    client,
                    f"/{self.business_account_id}",
                    params={"fields": fields},
                )
                discovery = payload.get("business_discovery") or {}
                media = discovery.get("media") or {}
                rows = media.get("data", [])
                for row in rows:
                    row = await self._expand_carousel_if_needed(client, row)
                    posts.append(_post_from_row(row, job_id=job_id))
                    if len(posts) >= max_posts:
                        return posts

                after = (
                    ((media.get("paging") or {}).get("cursors") or {}).get("after")
                )
                if not after or not rows:
                    break
                remaining = max_posts - len(posts)

        return posts

    async def _expand_carousel_if_needed(
        self, client: httpx.AsyncClient, row: dict[str, Any]
    ) -> dict[str, Any]:
        raw_type = str(row.get("media_type") or row.get("media_product_type") or "")
        if raw_type != "CAROUSEL_ALBUM":
            return row

        children = row.get("children")
        if isinstance(children, dict):
            child_rows = [item for item in children.get("data", []) if isinstance(item, dict)]
            if child_rows:
                return self._with_carousel_media(row, child_rows)


        media_id = str(row.get("id", ""))
        if not media_id:
            return row

        try:
            children = await self._get(
                client,
                f"/{media_id}/children",
                params={"fields": "id,media_url,thumbnail_url,media_type"},
            )
            child_rows = [item for item in children.get("data", []) if isinstance(item, dict)]
            if not child_rows:
                return row
            return self._with_carousel_media(row, child_rows)
        except Exception:  # noqa: BLE001
            # Do not fail the whole run because one carousel could not be expanded.
            return row

    @staticmethod
    def _with_carousel_media(row: dict[str, Any], children: list[dict[str, Any]]) -> dict[str, Any]:
        media_items = []
        for index, child in enumerate(children[:10], start=1):
            child_type = str(child.get("media_type") or "IMAGE")
            child_media_url = child.get("media_url")
            child_thumbnail = child.get("thumbnail_url")
            if child_type in {"VIDEO", "REELS"} and child_thumbnail:
                display_url = child_thumbnail
                display_type = "IMAGE"
            else:
                display_url = child_media_url
                display_type = child_type
            if not display_url:
                continue
            media_items.append(
                {
                    "url": display_url,
                    "media_type": display_type,
                    "label": f"Carousel item {index}",
                    "alt_text": "",
                }
            )
        first_child = children[0]
        first_display = media_items[0] if media_items else {}
        analysis_url = first_child.get("media_url") or first_display.get("url")
        return {
            **row,
            "media_url": analysis_url,
            "media_items": media_items,
            "child_media_id": first_child.get("id"),
        }

    async def _get(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.graph_limiter is not None:
            await self.graph_limiter.acquire()
        query = dict(params or {})
        query["access_token"] = self.access_token
        try:
            response = await client.get(
                f"{self.GRAPH_URL}/{self.settings.instagram_graph_api_version}{path}",
                params=query,
            )
        except httpx.ConnectError as exc:
            raise TransientError(f"DNS/connection error: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise TransientError(f"Request timeout: {exc}") from exc
        except httpx.NetworkError as exc:
            raise TransientError(f"Network error: {exc}") from exc
        self._raise_for_graph_error(response, auth_error=True)
        payload = response.json()
        if not isinstance(payload, dict):
            raise InstagramGraphError("Instagram returned an invalid response")
        return payload

    @staticmethod
    def _raise_for_graph_error(response: httpx.Response, *, auth_error: bool = False) -> None:
        if response.is_success:
            return
        message = "Instagram Graph API request failed"
        try:
            error = response.json().get("error", {})
            message = str(error.get("message") or message)
            code = int(error.get("code", 0) or 0)
        except (ValueError, AttributeError):
            code = 0
        if auth_error and (response.status_code in (401, 403) or code in (190, 200)):
            raise InstagramNeedsReauth(message)
        transient = transient_from_response(response)
        if transient is not None:
            raise transient
        raise InstagramGraphError(message)


def _extract_username(username_or_url: str) -> str | None:
    text = username_or_url.strip()
    if not text:
        return None
    if "/" in text:
        parsed = urlparse(text)
        path = parsed.path.strip("/")
        if not path:
            return None
        first = path.split("/")[0]
        return _normalize_handle(first)
    return _normalize_handle(text.strip("@"))


def _normalize_handle(handle: str) -> str | None:
    cleaned = re.sub(r"[^a-zA-Z0-9_.]", "", handle).lower().strip(".")
    if not cleaned or len(cleaned) > 30:
        return None
    return cleaned


def _post_from_row(row: dict[str, Any], *, job_id: str) -> BrandAnalysisPost:
    media_id = str(row.get("id", ""))
    permalink = row.get("permalink")
    shortcode = media_id
    if isinstance(permalink, str):
        parts = [part for part in permalink.split("/") if part]
        if len(parts) >= 2:
            shortcode = parts[-1]
    raw_type = str(row.get("media_type") or row.get("media_product_type") or "MEDIA")
    taken_at_str = row.get("timestamp")
    taken_at: datetime | None = None
    if taken_at_str:
        taken_at = datetime.fromisoformat(str(taken_at_str).replace("Z", "+00:00"))
    raw_media_items = row.get("media_items") or []
    media_items = [
        MediaEvidence.model_validate(item)
        for item in raw_media_items[:10]
        if isinstance(item, dict)
    ]
    if not media_items:
        thumbnail_url = row.get("thumbnail_url")
        media_url = row.get("media_url")
        if raw_type in {"VIDEO", "REELS"} and thumbnail_url:
            media_items = [MediaEvidence(url=str(thumbnail_url), media_type="IMAGE")]
        elif media_url:
            media_items = [MediaEvidence(url=str(media_url), media_type=raw_type)]
    return BrandAnalysisPost(
        job_id=job_id,
        post_id=media_id,
        shortcode=shortcode,
        permalink=permalink,
        caption=str(row.get("caption", "")),
        media_type=raw_type,
        media_url=row.get("media_url"),
        media_items=media_items,
        taken_at=taken_at,
        like_count=int(row.get("like_count", 0) or 0),
        comment_count=int(row.get("comments_count", row.get("comment_count", 0)) or 0),
        view_count=int(row.get("views", row.get("view_count", 0)) or 0),
        share_count=int(row.get("shares", row.get("share_count", 0)) or 0),
        fetched_at=datetime.now(UTC),
    )


class FakeBrandAnalysisProvider(BrandAnalysisProvider):
    """Deterministic fake provider for local development and tests."""

    async def resolve_username(self, username_or_url: str) -> str:
        return _extract_username(username_or_url) or "testbrand"

    async def fetch_posts(
        self, username: str, max_posts: int, *, job_id: str
    ) -> list[BrandAnalysisPost]:
        posts: list[BrandAnalysisPost] = []
        for index in range(min(max_posts, 3)):
            posts.append(
                BrandAnalysisPost(
                    job_id=job_id,
                    post_id=f"post_{index}",
                    shortcode=f"shortcode_{index}",
                    permalink=f"https://www.instagram.com/p/shortcode_{index}/",
                    caption=f"Test post {index} for {username}",
                    media_type="IMAGE",
                    media_url=f"https://example.com/media_{index}.jpg",
                    fetched_at=datetime.now(UTC),
                )
            )
        return posts


def build_brand_analysis_provider(
    settings: Settings,
    access_token: str,
    *,
    business_account_id: str | None = None,
    redis: Any | None = None,
) -> BrandAnalysisProvider:
    from app.core.rate_limit import build_graph_rate_limiter

    if settings.brand_analysis_provider == "fake":
        return FakeBrandAnalysisProvider()
    return GraphBrandAnalysisProvider(
        settings,
        access_token,
        business_account_id=business_account_id,
        graph_limiter=build_graph_rate_limiter(redis, settings),
    )
