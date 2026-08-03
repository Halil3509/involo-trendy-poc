"""Regression tests for the Meta Graph API application-level rate limiter."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import TransientError
from app.core.rate_limit import GraphApiRateLimiter, build_graph_rate_limiter


class FakeRedis:
    """In-memory Redis stand-in that supports the Lua-style eval used by GraphApiRateLimiter."""

    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.scripts: list[tuple[str, tuple[Any, ...], list[Any]]] = []

    async def get(self, key: str) -> str | None:
        return str(self.data[key]) if key in self.data else None

    async def eval(
        self,
        script: str,
        num_keys: int,
        key: str,
        *args: Any,
    ) -> list[Any]:
        self.scripts.append((script, (num_keys, key, *args), []))
        assert num_keys == 1
        max_requests = int(args[0])
        window = int(args[1])
        cost = int(args[2])
        current = self.data.get(key, 0)

        if current + cost > max_requests:
            ttl = self.ttls.get(key, window)
            return [1, ttl]

        new = current + cost
        self.data[key] = new
        if new == cost:
            self.ttls[key] = window

        if new > max_requests:
            ttl = self.ttls.get(key, window)
            return [1, ttl]
        return [0, new]


def _settings(**overrides: Any) -> Settings:
    return Settings(**overrides)


@pytest.mark.asyncio
async def test_acquire_allows_calls_under_budget() -> None:
    settings = _settings(
        graph_rate_limit_enabled=True,
        graph_rate_limit_user_count=1,
        graph_rate_limit_calls_per_user=3,
        graph_rate_limit_window_seconds=60,
    )
    redis = FakeRedis()
    limiter = GraphApiRateLimiter(redis, settings)

    for _ in range(3):
        await limiter.acquire()

    assert redis.data["involo:ratelimit:graph_api:app"] == 3


@pytest.mark.asyncio
async def test_acquire_blocks_when_budget_exhausted() -> None:
    settings = _settings(
        graph_rate_limit_enabled=True,
        graph_rate_limit_user_count=1,
        graph_rate_limit_calls_per_user=2,
        graph_rate_limit_window_seconds=90,
    )
    redis = FakeRedis()
    limiter = GraphApiRateLimiter(redis, settings)

    await limiter.acquire()
    await limiter.acquire()

    with pytest.raises(TransientError) as excinfo:
        await limiter.acquire()

    assert "rate limit reached" in str(excinfo.value).lower()
    assert excinfo.value.retry_after == 90.0


@pytest.mark.asyncio
async def test_acquire_cost_can_exceed_budget_by_one_call() -> None:
    settings = _settings(
        graph_rate_limit_enabled=True,
        graph_rate_limit_user_count=1,
        graph_rate_limit_calls_per_user=5,
        graph_rate_limit_window_seconds=60,
    )
    redis = FakeRedis()
    limiter = GraphApiRateLimiter(redis, settings)

    await limiter.acquire(cost=4)
    assert redis.data["involo:ratelimit:graph_api:app"] == 4

    await limiter.acquire(cost=1)
    assert redis.data["involo:ratelimit:graph_api:app"] == 5

    with pytest.raises(TransientError):
        await limiter.acquire(cost=1)


@pytest.mark.asyncio
async def test_acquire_noop_when_disabled() -> None:
    settings = _settings(graph_rate_limit_enabled=False)
    redis = FakeRedis()
    limiter = GraphApiRateLimiter(redis, settings)

    for _ in range(10):
        await limiter.acquire()

    assert redis.data == {}


@pytest.mark.asyncio
async def test_acquire_noop_for_zero_or_negative_cost() -> None:
    settings = _settings(
        graph_rate_limit_enabled=True,
        graph_rate_limit_user_count=1,
        graph_rate_limit_calls_per_user=1,
        graph_rate_limit_window_seconds=60,
    )
    redis = FakeRedis()
    limiter = GraphApiRateLimiter(redis, settings)

    await limiter.acquire(cost=0)
    await limiter.acquire(cost=-5)

    assert redis.data == {}


def test_build_graph_rate_limiter_returns_none_without_redis() -> None:
    settings = _settings(graph_rate_limit_enabled=True)
    assert build_graph_rate_limiter(None, settings) is None


def test_build_graph_rate_limiter_returns_none_when_disabled() -> None:
    from redis.asyncio import Redis as AsyncRedis

    settings = _settings(graph_rate_limit_enabled=False)
    redis = AsyncRedis()
    assert build_graph_rate_limiter(redis, settings) is None


def test_build_graph_rate_limiter_returns_instance_when_enabled_and_redis_available() -> None:
    from redis.asyncio import Redis as AsyncRedis

    settings = _settings(graph_rate_limit_enabled=True)
    redis = AsyncRedis()
    limiter = build_graph_rate_limiter(redis, settings)
    assert isinstance(limiter, GraphApiRateLimiter)


@pytest.mark.asyncio
async def test_rate_limit_scales_with_user_count() -> None:
    settings = _settings(
        graph_rate_limit_enabled=True,
        graph_rate_limit_user_count=3,
        graph_rate_limit_calls_per_user=2,
        graph_rate_limit_window_seconds=60,
    )
    redis = FakeRedis()
    limiter = GraphApiRateLimiter(redis, settings)

    for _ in range(6):
        await limiter.acquire()

    assert redis.data["involo:ratelimit:graph_api:app"] == 6

    with pytest.raises(TransientError) as excinfo:
        await limiter.acquire()

    assert excinfo.value.retry_after == 60.0
