"""Official Meta trend discovery with explicit provenance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import Settings
from app.core.errors import TransientError, transient_from_response

if TYPE_CHECKING:
    from app.core.rate_limit import GraphApiRateLimiter

logger = logging.getLogger(__name__)


class TrendSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrendSourceItem:
    source_id: str
    source: str
    license: str
    permalink: str
    caption: str
    media_type: str
    media_url: str | None
    like_count: int
    comment_count: int
    taken_at: datetime
    provenance: dict[str, str]


class MetaHashtagTrendSource:
    """Instagram Graph Hashtag Search/Public Content Access adapter.

    It intentionally has no scraping fallback. Required Meta permissions are
    validated by the provider response and surfaced as a stable provider error.
    """

    GRAPH_URL = "https://graph.facebook.com"
    MEDIA_FIELDS = (
        "id,caption,media_type,media_url,permalink,"
        "timestamp,comments_count,like_count,children{media_url}"
    )
    DEFAULT_PAGE_SIZE = 10

    def __init__(
        self,
        settings: Settings,
        *,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        app_id = settings.effective_facebook_app_id
        app_secret = settings.effective_facebook_app_secret
        if not app_id or not app_secret:
            raise TrendSourceError("official Meta credentials are not configured")
        self.settings = settings
        self.graph_limiter = graph_limiter

    async def discover(
        self, hashtag: str, *, access_token: str, instagram_business_account_id: str, limit: int
    ) -> list[TrendSourceItem]:
        normalized = hashtag.strip().lstrip("#")
        search = await self._get(
            "/ig_hashtag_search",
            access_token,
            {"user_id": instagram_business_account_id, "q": normalized},
        )
        data = search.get("data") or []
        if not data:
            return []
        hashtag_id = str(data[0]["id"])
        return await self._fetch_top_media(
            hashtag_id,
            access_token,
            instagram_business_account_id,
            normalized,
            limit,
        )

    async def _fetch_top_media(
        self,
        hashtag_id: str,
        access_token: str,
        instagram_business_account_id: str,
        hashtag: str,
        total_limit: int,
    ) -> list[TrendSourceItem]:
        """Fetch top media in small pages with size fallback on data errors.

        Meta returns HTTP 500 / "Please reduce the amount of data you're asking
        for" when a single ``top_media`` response grows too large. We request
        small pages (10 by default) and halve the page size on that specific
        error. Any other transient failure or persistent data error stops the
        pagination and returns whatever was collected so the scraper does not
        fail the whole job.
        """
        result: list[TrendSourceItem] = []
        after: str | None = None
        remaining = total_limit
        page_size = self.DEFAULT_PAGE_SIZE

        while remaining > 0:
            params: dict[str, Any] = {
                "user_id": instagram_business_account_id,
                "fields": self.MEDIA_FIELDS,
                "limit": min(remaining, page_size),
            }
            if after is not None:
                params["after"] = after

            try:
                media = await self._get(
                    f"/{hashtag_id}/top_media",
                    access_token,
                    params,
                )
            except TransientError as exc:
                if "Please reduce the amount of data" in str(exc) and page_size > 1:
                    page_size = max(1, page_size // 2)
                    continue
                logger.warning(
                    "Meta top_media transient error for #%s: %s",
                    hashtag,
                    exc,
                )
                break

            rows = media.get("data", [])
            for row in rows[:remaining]:
                item = self._media_row_to_item(row, hashtag, hashtag_id)
                if item is not None:
                    result.append(item)

            paging = media.get("paging") or {}
            cursors = paging.get("cursors") or {}
            after = cursors.get("after")
            if not after or not rows:
                break
            remaining -= len(rows)

        return result[:total_limit]

    def _media_row_to_item(
        self,
        row: dict[str, Any],
        hashtag: str,
        hashtag_id: str,
    ) -> TrendSourceItem | None:
        timestamp = row.get("timestamp")
        permalink = row.get("permalink")
        if not timestamp or not permalink:
            return None

        media_url = row.get("media_url")
        media_type = str(row.get("media_type") or "MEDIA")
        if not media_url and media_type in ("CAROUSEL_ALBUM", "CAROUSEL"):
            children = row.get("children") or {}
            for child in children.get("data") or []:
                child_url = child.get("media_url")
                if child_url:
                    media_url = child_url
                    break

        return TrendSourceItem(
            source_id=str(row["id"]),
            source="meta_instagram_hashtag",
            license="meta_platform_terms",
            permalink=str(permalink),
            caption=str(row.get("caption") or ""),
            media_type=media_type,
            media_url=media_url,
            like_count=int(row.get("like_count") or 0),
            comment_count=int(row.get("comments_count") or 0),
            taken_at=datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")),
            provenance={
                "hashtag": hashtag,
                "hashtag_id": hashtag_id,
                "media_edge": "top_media",
                "api_version": self.settings.instagram_graph_api_version,
            },
        )

    async def _get(
        self, path: str, access_token: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if self.graph_limiter is not None:
            await self.graph_limiter.acquire()
        query = {**params, "access_token": access_token}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.GRAPH_URL}/{self.settings.instagram_graph_api_version}{path}",
                params=query,
            )
        transient = transient_from_response(response)
        if transient is not None:
            raise self._error_from_response(transient, response, params)
        if not response.is_success:
            raise self._error_from_response(
                TrendSourceError("official Meta trend request failed"),
                response,
                params,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise TrendSourceError("official Meta trend response was invalid")
        return payload

    def _error_from_response(
        self,
        cause: BaseException,
        response: httpx.Response,
        params: dict[str, Any],
    ) -> BaseException:
        redacted = {**params, "access_token": "***"}
        request_hint = f"{response.request.method} {response.request.url.path} with {redacted}"
        try:
            body = response.json()
        except Exception:
            body = response.text or "empty response"
        meta_message = ""
        if isinstance(body, dict):
            error_payload = body.get("error", {})
            if isinstance(error_payload, dict):
                meta_message = error_payload.get("message", "")
                if meta_message:
                    meta_message = f" - {meta_message}"
        summary = str(body)[:500]
        message = (
            f"{cause} ({response.status_code} for {request_hint}"
            f"{meta_message}; body: {summary})"
        )
        if isinstance(cause, TransientError):
            return TransientError(message, retry_after=cause.retry_after)
        return type(cause)(message)
