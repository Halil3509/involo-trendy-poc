from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import TransientError, is_throttling_error, transient_from_response
from app.core.rate_limit import enforce_rate_limit


def _response(status_code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code=status_code, headers=headers or {})


def test_transient_from_response_flags_429_and_5xx() -> None:
    assert isinstance(transient_from_response(_response(429)), TransientError)
    assert isinstance(transient_from_response(_response(503)), TransientError)


def test_transient_from_response_ignores_success_and_4xx() -> None:
    assert transient_from_response(_response(200)) is None
    assert transient_from_response(_response(404)) is None


def test_transient_from_response_parses_retry_after() -> None:
    error = transient_from_response(_response(429, {"Retry-After": "12"}))
    assert error is not None
    assert error.retry_after == 12.0


def test_is_throttling_error_matches_known_codes() -> None:
    throttled = SimpleNamespace(response={"Error": {"Code": "ThrottlingException"}})
    other = SimpleNamespace(response={"Error": {"Code": "AccessDenied"}})
    assert is_throttling_error(throttled) is True
    assert is_throttling_error(other) is False
    assert is_throttling_error(SimpleNamespace()) is False


class RateLimitRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def ttl(self, key: str) -> int:
        return 30


def _request(redis: RateLimitRedis, settings: Settings) -> SimpleNamespace:
    return SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="1.2.3.4"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings, resources=SimpleNamespace(redis=redis)
            )
        ),
    )


@pytest.mark.asyncio
async def test_enforce_rate_limit_blocks_after_max() -> None:
    settings = Settings()
    redis = RateLimitRedis()
    request = _request(redis, settings)

    for _ in range(2):
        await enforce_rate_limit(
            request, scope="auth", identifier="1.2.3.4", max_requests=2, window_seconds=60
        )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await enforce_rate_limit(
            request, scope="auth", identifier="1.2.3.4", max_requests=2, window_seconds=60
        )
    assert excinfo.value.status_code == 429
    assert excinfo.value.headers["Retry-After"] == "30"


@pytest.mark.asyncio
async def test_enforce_rate_limit_noop_when_disabled() -> None:
    settings = Settings(rate_limit_enabled=False)
    redis = RateLimitRedis()
    request = _request(redis, settings)
    for _ in range(10):
        await enforce_rate_limit(
            request, scope="auth", identifier="x", max_requests=1, window_seconds=60
        )
    assert redis.counts == {}
