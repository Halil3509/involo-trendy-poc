"""Redis fixed-window rate limiting for sensitive endpoints."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any, cast

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.errors import TransientError

logger = logging.getLogger(__name__)


def client_identifier(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    identifier: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    cfg = cast(Settings, request.app.state.settings)
    if not cfg.rate_limit_enabled:
        return
    redis = request.app.state.resources.redis
    if redis is None:
        return
    key = f"involo:ratelimit:{scope}:{identifier}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > max_requests:
        ttl = await redis.ttl(key)
        retry_after = ttl if ttl and ttl > 0 else window_seconds
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests; please slow down.",
            headers={"Retry-After": str(retry_after)},
        )


class RateLimiter:
    """FastAPI dependency: fixed-window limiter keyed by client IP."""

    def __init__(self, scope: str, max_attr: str, window_attr: str) -> None:
        self.scope = scope
        self.max_attr = max_attr
        self.window_attr = window_attr

    async def __call__(self, request: Request) -> None:
        cfg = cast(Settings, request.app.state.settings)
        await enforce_rate_limit(
            request,
            scope=self.scope,
            identifier=client_identifier(request),
            max_requests=int(getattr(cfg, self.max_attr)),
            window_seconds=int(getattr(cfg, self.window_attr)),
        )


auth_rate_limit = RateLimiter(
    "auth", "auth_rate_limit_max", "auth_rate_limit_window_seconds"
)


class GraphApiRateLimiter:
    """Application-level rate limiter for Meta Graph API calls.

    Meta's application-level limit is 200 calls per user per hour across all
    calls made with any access token other than Page access tokens. This class
    keeps a single Redis-backed fixed-window counter for the whole app and
    raises TransientError when the budget is exhausted, so callers can retry
    with backoff instead of hitting Meta's 429.
    """

    _LUA_CHECK_INCR = """
    local key = KEYS[1]
    local max_requests = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local cost = tonumber(ARGV[3])
    local current = redis.call('get', key) or '0'
    if tonumber(current) + cost > max_requests then
        local ttl = redis.call('ttl', key)
        return {1, ttl}
    end
    local new = redis.call('incrby', key, cost)
    if new == cost then
        redis.call('expire', key, window)
    end
    if new > max_requests then
        local ttl = redis.call('ttl', key)
        return {1, ttl}
    end
    return {0, new}
    """

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.settings = settings

    def _key(self) -> str:
        return "involo:ratelimit:graph_api:app"

    def _max_requests(self) -> int:
        return (
            self.settings.graph_rate_limit_user_count
            * self.settings.graph_rate_limit_calls_per_user
        )

    async def acquire(self, *, cost: int = 1) -> None:
        """Reserve a Graph API call. Raise TransientError if the budget is exhausted."""
        if not self.settings.graph_rate_limit_enabled:
            return
        if cost <= 0:
            return
        max_requests = self._max_requests()
        if max_requests <= 0:
            return
        window = self.settings.graph_rate_limit_window_seconds
        result = await cast(
            Awaitable[Any],
            self.redis.eval(
                self._LUA_CHECK_INCR,
                1,
                self._key(),
                str(max_requests),
                str(window),
                str(cost),
            ),
        )
        if not isinstance(result, (list, tuple)) or len(result) < 2:
            logger.warning("Unexpected rate limiter script response: %s", result)
            return
        blocked = result[0]
        ttl = result[1]
        if blocked:
            retry_after = ttl if ttl and ttl > 0 else window
            raise TransientError(
                "Meta Graph API application rate limit reached",
                retry_after=float(retry_after),
            )


def build_graph_rate_limiter(redis: Redis | None, settings: Settings) -> GraphApiRateLimiter | None:
    """Build a Graph API rate limiter when Redis is available and rate limiting is enabled."""
    if redis is None or not settings.graph_rate_limit_enabled:
        return None
    return GraphApiRateLimiter(redis, settings)
