"""Instagram Business Login and Graph API providers for user profiling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.errors import transient_from_response
from app.core.rate_limit import GraphApiRateLimiter
from app.infrastructure.resources import utcnow


class InstagramGraphError(RuntimeError):
    pass


class InstagramNeedsReauth(InstagramGraphError):
    pass


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    expires_at: datetime
    instagram_user_id: str | None = None


@dataclass(frozen=True)
class InstagramAccount:
    id: str
    username: str
    follower_count: int


@dataclass(frozen=True)
class InstagramMedia:
    id: str
    shortcode: str
    caption: str
    media_type: str
    media_url: str | None
    permalink: str | None
    taken_at: datetime
    like_count: int
    comment_count: int
    view_count: int
    share_count: int
    insights_available: bool
    metrics: dict[str, int | float | None] | None = None
    unavailable_metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstagramAudience:
    captured_at: datetime
    reached_by_country: dict[str, int]
    reached_by_city: dict[str, int]
    engaged_by_country: dict[str, int]
    follower_age_gender: dict[str, int]
    online_followers: dict[str, int]
    available_metrics: tuple[str, ...]
    unavailable_metrics: tuple[str, ...]


class InstagramProfileProvider(ABC):
    @abstractmethod
    def authorization_url(self, state: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def exchange_code(self, code: str) -> TokenBundle:
        raise NotImplementedError

    @abstractmethod
    async def refresh_token(self, access_token: str) -> TokenBundle:
        raise NotImplementedError

    @abstractmethod
    async def fetch_account(self, access_token: str) -> InstagramAccount:
        raise NotImplementedError

    @abstractmethod
    async def fetch_recent_media(
        self, access_token: str, account_id: str, *, now: datetime
    ) -> list[InstagramMedia]:
        raise NotImplementedError

    async def fetch_audience(
        self, access_token: str, account_id: str, *, now: datetime
    ) -> InstagramAudience:
        raise InstagramGraphError("audience insights are unavailable")


class GraphInstagramProfileProvider(InstagramProfileProvider):
    AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
    TOKEN_URL = "https://api.instagram.com/oauth/access_token"
    GRAPH_URL = "https://graph.instagram.com"

    def __init__(
        self,
        settings: Settings,
        *,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.graph_limiter = graph_limiter
        if not settings.instagram_app_id or not settings.instagram_app_secret:
            raise InstagramGraphError("Instagram app credentials are not configured")

    @property
    def _secret(self) -> str:
        assert self.settings.instagram_app_secret is not None
        return self.settings.instagram_app_secret.get_secret_value()

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.settings.instagram_app_id,
                "redirect_uri": self.settings.instagram_oauth_redirect_uri,
                "response_type": "code",
                "scope": "instagram_business_basic,instagram_business_manage_insights",
                "state": state,
            }
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    async def exchange_code(self, code: str) -> TokenBundle:
        # Instagram sometimes appends a fragment marker to the code.
        if code.endswith("#_"):
            code = code[:-2]

        async with httpx.AsyncClient(timeout=30.0) as client:
            short_response = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.settings.instagram_app_id,
                    "client_secret": self._secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.settings.instagram_oauth_redirect_uri,
                    "code": code,
                },
            )
            self._raise_for_graph_error(short_response)
            short = short_response.json()
            short_token, ig_user_id = self._extract_short_token(short)
            long_token, expires_in = await self._exchange_long_lived_token(
                client, short_token
            )
        return TokenBundle(
            access_token=long_token,
            expires_at=utcnow() + timedelta(seconds=expires_in),
            instagram_user_id=ig_user_id,
        )

    @staticmethod
    def _extract_short_token(payload: dict[str, Any]) -> tuple[str, str | None]:
        data = payload.get("data")
        if isinstance(data, list) and len(data) > 0:
            token_info = data[0]
            short_token = token_info.get("access_token")
            ig_user_id = (
                str(token_info.get("user_id")) if token_info.get("user_id") else None
            )
        else:
            short_token = payload.get("access_token")
            ig_user_id = str(payload.get("user_id")) if payload.get("user_id") else None
        if not short_token:
            raise InstagramGraphError(
                "Instagram token response did not contain access_token"
            )
        return str(short_token), ig_user_id

    async def _exchange_long_lived_token(
        self, client: httpx.AsyncClient, short_token: str
    ) -> tuple[str, int]:
        params = {
            "grant_type": "ig_exchange_token",
            "client_secret": self._secret,
            "access_token": short_token,
        }
        long_response = await client.get(
            f"{self.GRAPH_URL}/access_token",
            params=params,
        )
        if self._should_retry_with_post(long_response):
            long_response = await client.post(
                f"{self.GRAPH_URL}/access_token",
                data=params,
            )
        self._raise_for_graph_error(long_response)
        long = long_response.json()
        return str(long["access_token"]), int(long.get("expires_in", 5184000))

    @staticmethod
    def _should_retry_with_post(response: httpx.Response) -> bool:
        """Some Instagram apps reject the GET method on token endpoints."""
        if response.status_code == 405:
            return True
        if response.status_code != 400:
            return False
        try:
            error = response.json().get("error", {})
            message = str(error.get("message", "")).lower()
            code = int(error.get("code", 0) or 0)
        except (ValueError, AttributeError):
            return False
        return code == 100 and ("method" in message or "unsupported" in message)

    async def refresh_token(self, access_token: str) -> TokenBundle:
        params = {"grant_type": "ig_refresh_token", "access_token": access_token}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.GRAPH_URL}/refresh_access_token",
                params=params,
            )
            if self._should_retry_with_post(response):
                response = await client.post(
                    f"{self.GRAPH_URL}/refresh_access_token",
                    data=params,
                )
            self._raise_for_graph_error(response, auth_error=True)
            payload = response.json()
        return TokenBundle(
            access_token=str(payload["access_token"]),
            expires_at=utcnow()
            + timedelta(seconds=int(payload.get("expires_in", 5184000))),
        )

    async def fetch_account(self, access_token: str) -> InstagramAccount:
        payload = await self._get(
            "/me",
            access_token,
            params={
                "fields": "id,user_id,username,followers_count",
            },
        )
        account_id = payload.get("user_id") or payload.get("id")
        if not account_id:
            raise InstagramGraphError("Instagram account response did not contain an id")
        return InstagramAccount(
            id=str(account_id),
            username=str(payload.get("username", "")),
            follower_count=int(payload.get("followers_count", 0) or 0),
        )

    async def fetch_recent_media(
        self, access_token: str, account_id: str, *, now: datetime
    ) -> list[InstagramMedia]:
        cutoff = now - timedelta(days=90)
        result: list[InstagramMedia] = []
        path: str | None = f"/{account_id}/media"
        params: dict[str, Any] | None = {
            "fields": (
                "id,caption,media_type,media_product_type,media_url,permalink,"
                "timestamp,username,like_count,comments_count"
            ),
            "limit": 15,
        }
        while path and len(result) < 15:
            page = await self._get(path, access_token, params=params)
            for row in page.get("data", []):
                timestamp = row.get("timestamp")
                if not timestamp:
                    continue
                taken_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if taken_at < cutoff:
                    return result
                insights = await self._fetch_insights(
                    str(row["id"]),
                    access_token,
                    media_type=str(row.get("media_product_type") or row.get("media_type") or ""),
                )
                merged = {**row, **insights}
                if not merged.get("media_url"):
                    media_type = str(row.get("media_type") or "").upper()
                    if media_type == "CAROUSEL_ALBUM":
                        try:
                            children_payload = await self._get(
                                f'/{row["id"]}/children',
                                access_token,
                                params={"fields": "id,media_url,media_type"},
                            )
                            for child in children_payload.get("data", []):
                                child_url = child.get("media_url")
                                if child_url:
                                    merged["media_url"] = child_url
                                    break
                        except InstagramNeedsReauth:
                            raise
                        except InstagramGraphError:
                            pass
                result.append(
                    _media_from_mapping(merged, fallback_id=str(row["id"]), taken_at=taken_at)
                )
                if len(result) >= 15:
                    break
            after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
            path = f"/{account_id}/media" if after else None
            params = {
                "fields": (
                    "id,caption,media_type,media_product_type,media_url,permalink,"
                    "timestamp,username,like_count,comments_count"
                ),
                "limit": 15,
                "after": after,
            }
        return result

    async def _fetch_insights(
        self, media_id: str, access_token: str, *, media_type: str
    ) -> dict[str, Any]:
        requested = [
            "views",
            "reach",
            "shares",
            "saved",
            "total_interactions",
        ]
        if media_type.upper() in {"REELS", "VIDEO"}:
            requested.extend(
                [
                    "ig_reels_video_view_total_time",
                    "ig_reels_avg_watch_time",
                ]
            )
        values: dict[str, Any] = {"insights_available": False, "unavailable_metrics": []}
        for metric_name in requested:
            try:
                payload = await self._get(
                    f"/{media_id}/insights",
                    access_token,
                    params={"metric": metric_name},
                )
            except InstagramNeedsReauth:
                raise
            except InstagramGraphError:
                values["unavailable_metrics"].append(metric_name)
                continue
            metric_data: dict[str, Any] = next(iter(payload.get("data", [])), {})
            raw = metric_data.get("total_value", {}).get("value")
            if raw is None:
                raw = (metric_data.get("values") or [{}])[0].get("value")
            values[metric_name] = raw
            values["insights_available"] = True
        return values

    async def fetch_audience(
        self, access_token: str, account_id: str, *, now: datetime
    ) -> InstagramAudience:
        requests = (
            ("reached_country", "reached_audience_demographics", "country", "lifetime"),
            ("reached_city", "reached_audience_demographics", "city", "lifetime"),
            ("engaged_country", "engaged_audience_demographics", "country", "lifetime"),
            ("follower_age_gender", "follower_demographics", "age,gender", "lifetime"),
            ("online_followers", "online_followers", "hour", "day"),
        )
        available: list[str] = []
        unavailable: list[str] = []
        raw_values: dict[str, Any] = {}
        for key, metric, breakdowns, period in requests:
            try:
                payload = await self._get(
                    f"/{account_id}/insights",
                    access_token,
                    params={
                        "metric": metric,
                        "period": period,
                        "metric_type": "total_value",
                        "breakdowns": breakdowns,
                    },
                )
                raw_values[key] = _flatten_breakdown(payload)
                available.append(f"{metric}:{breakdowns}")
            except InstagramNeedsReauth:
                raise
            except InstagramGraphError:
                unavailable.append(f"{metric}:{breakdowns}")
        return InstagramAudience(
            captured_at=now,
            reached_by_country=raw_values.get("reached_country", {}),
            reached_by_city=raw_values.get("reached_city", {}),
            engaged_by_country=raw_values.get("engaged_country", {}),
            follower_age_gender=raw_values.get("follower_age_gender", {}),
            online_followers=raw_values.get("online_followers", {}),
            available_metrics=tuple(available),
            unavailable_metrics=tuple(unavailable),
        )

    async def _get(
        self, path: str, access_token: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        query = dict(params or {})
        query["access_token"] = access_token
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.GRAPH_URL}/{self.settings.instagram_graph_api_version}{path}",
                params=query,
            )
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

    async def _acquire_graph_limit(self) -> None:
        if self.graph_limiter is not None:
            await self.graph_limiter.acquire()


def _media_from_mapping(
    row: dict[str, Any], *, fallback_id: str, taken_at: datetime
) -> InstagramMedia:
    media_id = str(row.get("id") or fallback_id)
    permalink = row.get("permalink")
    shortcode = media_id
    if isinstance(permalink, str):
        parts = [part for part in permalink.split("/") if part]
        if len(parts) >= 2:
            shortcode = parts[-1]
    views = int(row.get("views") or row.get("reach") or 0)
    likes = int(row.get("like_count", 0) or 0)
    comments = int(row.get("comments_count", row.get("comment_count", 0)) or 0)
    raw_media_type = str(row.get("media_type") or "")
    media_type = (
        raw_media_type
        if raw_media_type.upper() == "VIDEO"
        else str(row.get("media_product_type") or raw_media_type or "MEDIA")
    )
    return InstagramMedia(
        id=media_id,
        shortcode=shortcode,
        caption=str(row.get("caption", "")),
        media_type=media_type,
        media_url=row.get("media_url"),
        permalink=permalink,
        taken_at=taken_at,
        like_count=likes,
        comment_count=comments,
        view_count=views,
        share_count=int(row.get("shares", row.get("share_count", 0)) or 0),
        insights_available=bool(row.get("insights_available", True)),
        metrics={
            key: row.get(key)
            for key in (
                "views",
                "reach",
                "shares",
                "saved",
                "total_interactions",
                "ig_reels_video_view_total_time",
                "ig_reels_avg_watch_time",
            )
            if key in row
        },
        unavailable_metrics=tuple(row.get("unavailable_metrics", [])),
    )


def build_instagram_profile_provider(
    settings: Settings,
    *,
    redis: Any | None = None,
) -> InstagramProfileProvider:
    from app.core.rate_limit import build_graph_rate_limiter

    return GraphInstagramProfileProvider(
        settings,
        graph_limiter=build_graph_rate_limiter(redis, settings),
    )


def _flatten_breakdown(payload: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for metric in payload.get("data", []):
        breakdowns = metric.get("total_value", {}).get("breakdowns", [])
        for breakdown in breakdowns:
            for row in breakdown.get("results", []):
                dimension = "|".join(str(value) for value in row.get("dimension_values", []))
                if dimension:
                    result[dimension] = int(row.get("value", 0) or 0)
    return result
