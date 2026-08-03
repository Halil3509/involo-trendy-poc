from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from provider_doubles import FixtureInstagramProfileProvider

from app.core.config import Settings
from app.providers.instagram_profile import (
    GraphInstagramProfileProvider,
    InstagramGraphError,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_fixture_provider_limits_media_by_age() -> None:
    provider = FixtureInstagramProfileProvider(FIXTURES / "user_media.json")

    media = await provider.fetch_recent_media(
        "fixture-access-token",
        "fixture-instagram-user",
        now=datetime(2026, 7, 16, tzinfo=UTC),
    )

    assert [item.shortcode for item in media] == ["Fixture_A1", "Fixture_B2"]
    assert all(item.insights_available for item in media)


def test_graph_authorization_url_uses_current_business_scopes() -> None:
    provider = GraphInstagramProfileProvider(
        Settings(
            instagram_app_id="123",
            instagram_app_secret="secret",
        )
    )

    parsed = urlparse(provider.authorization_url("csrf-state"))
    query = parse_qs(parsed.query)

    assert parsed.netloc == "www.instagram.com"
    assert query["state"] == ["csrf-state"]
    assert query["scope"] == ["instagram_business_basic,instagram_business_manage_insights"]
    assert "force_reauth" not in query


@pytest.mark.asyncio
async def test_audience_uses_official_breakdowns_and_separate_city_country_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GraphInstagramProfileProvider(
        Settings(instagram_app_id="123", instagram_app_secret="secret")
    )
    calls: list[dict[str, str]] = []

    async def get_stub(
        path: str,
        access_token: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        assert params is not None
        calls.append(params)
        breakdown = params["breakdowns"]
        return {
            "data": [
                {
                    "total_value": {
                        "breakdowns": [
                            {
                                "results": [
                                    {
                                        "dimension_values": [f"value-{breakdown}"],
                                        "value": 7,
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, "_get", get_stub)
    audience = await provider.fetch_audience(
        "token", "account", now=datetime(2026, 7, 17, tzinfo=UTC)
    )

    assert all("breakdowns" in call and "breakdown" not in call for call in calls)
    assert {"country", "city"} <= {call["breakdowns"] for call in calls}
    assert audience.reached_by_country == {"value-country": 7}
    assert audience.reached_by_city == {"value-city": 7}


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json = json_data

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    responses: list[tuple[str, str, _FakeResponse]] = []

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        _FakeAsyncClient.calls.append(("post", url, kwargs))
        for method, sub, response in _FakeAsyncClient.responses:
            if method == "post" and sub in url:
                return response
        raise RuntimeError(f"Unexpected POST {url}")

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        _FakeAsyncClient.calls.append(("get", url, kwargs))
        for method, sub, response in _FakeAsyncClient.responses:
            if method == "get" and sub in url:
                return response
        raise RuntimeError(f"Unexpected GET {url}")


def _patch_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.providers.instagram_profile.httpx.AsyncClient",
        _FakeAsyncClient,
    )
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.responses = []


def _provider() -> GraphInstagramProfileProvider:
    return GraphInstagramProfileProvider(
        Settings(instagram_app_id="123", instagram_app_secret="secret")
    )


@pytest.mark.asyncio
async def test_exchange_code_strips_trailing_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch)
    _FakeAsyncClient.responses = [
        (
            "post",
            "oauth/access_token",
            _FakeResponse(200, {"access_token": "short", "user_id": "123"}),
        ),
        (
            "get",
            "access_token",
            _FakeResponse(200, {"access_token": "long", "expires_in": 7200}),
        ),
    ]

    token = await _provider().exchange_code("auth-code#_")

    assert token.access_token == "long"
    assert token.instagram_user_id == "123"
    assert _FakeAsyncClient.calls[0][2]["data"]["code"] == "auth-code"


@pytest.mark.asyncio
async def test_exchange_code_parses_data_array_short_lived_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch)
    _FakeAsyncClient.responses = [
        (
            "post",
            "oauth/access_token",
            _FakeResponse(
                200,
                {"data": [{"access_token": "short2", "user_id": "456"}]},
            ),
        ),
        (
            "get",
            "access_token",
            _FakeResponse(200, {"access_token": "long2", "expires_in": 3600}),
        ),
    ]

    token = await _provider().exchange_code("auth-code")

    assert token.access_token == "long2"
    assert token.instagram_user_id == "456"


@pytest.mark.asyncio
async def test_exchange_code_raises_on_long_lived_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch)
    _FakeAsyncClient.responses = [
        (
            "post",
            "oauth/access_token",
            _FakeResponse(200, {"access_token": "short", "user_id": "789"}),
        ),
        (
            "get",
            "access_token",
            _FakeResponse(400, {"error": {"message": "Disabled"}}),
        ),
    ]

    with pytest.raises(InstagramGraphError):
        await _provider().exchange_code("auth-code")


@pytest.mark.asyncio
async def test_exchange_code_retries_long_lived_exchange_with_post_on_method_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch)
    _FakeAsyncClient.responses = [
        (
            "post",
            "oauth/access_token",
            _FakeResponse(200, {"access_token": "short", "user_id": "789"}),
        ),
        (
            "get",
            "access_token",
            _FakeResponse(
                400,
                {
                    "error": {
                        "code": 100,
                        "message": "Unsupported request - method type: get",
                    }
                },
            ),
        ),
        (
            "post",
            "access_token",
            _FakeResponse(200, {"access_token": "long", "expires_in": 5184000}),
        ),
    ]

    token = await _provider().exchange_code("auth-code")

    assert token.access_token == "long"
    assert token.instagram_user_id == "789"
    assert any(
        method == "post" and "access_token" in url
        for method, url, _ in _FakeAsyncClient.calls
    )


@pytest.mark.asyncio
async def test_exchange_code_raises_when_short_lived_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx(monkeypatch)
    _FakeAsyncClient.responses = [
        (
            "post",
            "oauth/access_token",
            _FakeResponse(200, {}),
        ),
    ]

    with pytest.raises(InstagramGraphError):
        await _provider().exchange_code("auth-code")
