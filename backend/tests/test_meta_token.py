"""Tests for the managed Meta trend token service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.core.token_crypto import TokenCipher
from app.services.meta_token import (
    MetaTokenError,
    MetaTokenService,
    TokenBundle,
    build_meta_token_service,
)


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, filter: dict) -> dict | None:  # noqa: A002 - matches pymongo API
        _id = filter.get("_id")
        return self.docs.get(_id)

    async def update_one(self, filter: dict, update: dict, *, upsert: bool = False) -> None:  # noqa: A002
        _id = filter.get("_id")
        if _id is None:
            return
        doc = self.docs.get(_id, {})
        set_fields = update.get("$set", {})
        insert_fields = update.get("$setOnInsert", {})
        if _id not in self.docs:
            doc = {**insert_fields, **doc}
        doc.update(set_fields)
        self.docs[_id] = doc


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def _settings(token: str | None = "initial-token") -> Settings:
    return Settings(
        meta_trend_access_token=token,
        instagram_app_id="app-id",
        instagram_app_secret="app-secret",
    )


def _service(token: str | None = "initial-token") -> MetaTokenService:
    settings = _settings(token)
    db: FakeDB = FakeDB()  # type: ignore[assignment]
    cipher = TokenCipher(settings.instagram_token_encryption_key.get_secret_value())
    return MetaTokenService(settings, db, cipher)


@pytest.mark.asyncio
async def test_get_valid_token_seeds_and_exchanges_on_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()

    async def fake_exchange(access_token: str) -> TokenBundle:
        return TokenBundle(
            access_token="long-lived-token", expires_at=datetime.now(UTC) + timedelta(days=60)
        )

    monkeypatch.setattr(service, "exchange_long_lived_token", fake_exchange)

    result = await service.get_valid_token()

    assert result == "long-lived-token"
    doc = service.db["meta_access_tokens"].docs["trend"]
    assert "access_token_encrypted" in doc
    assert doc["expires_at"] > datetime.now(UTC) + timedelta(days=59)


@pytest.mark.asyncio
async def test_get_valid_token_returns_existing_token_when_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service("initial-token")
    future = datetime.now(UTC) + timedelta(days=30)
    await service._store("stored-token", expires_at=future)

    exchange_called = False

    async def fake_exchange(access_token: str) -> TokenBundle:
        nonlocal exchange_called
        exchange_called = True
        return TokenBundle(access_token="new-token", expires_at=future)

    monkeypatch.setattr(service, "exchange_long_lived_token", fake_exchange)

    result = await service.get_valid_token()

    assert result == "stored-token"
    assert not exchange_called


@pytest.mark.asyncio
async def test_get_valid_token_refreshes_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service("initial-token")
    near_expiry = datetime.now(UTC) + timedelta(days=3)
    await service._store("stored-token", expires_at=near_expiry)

    async def fake_exchange(access_token: str) -> TokenBundle:
        return TokenBundle(
            access_token="refreshed-token", expires_at=datetime.now(UTC) + timedelta(days=60)
        )

    monkeypatch.setattr(service, "exchange_long_lived_token", fake_exchange)

    result = await service.get_valid_token()

    assert result == "refreshed-token"


@pytest.mark.asyncio
async def test_get_valid_token_returns_existing_token_when_exchange_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service("initial-token")
    near_expiry = datetime.now(UTC) + timedelta(days=3)
    await service._store("stored-token", expires_at=near_expiry)

    async def fake_exchange(access_token: str) -> TokenBundle:
        raise MetaTokenError("exchange failed")

    monkeypatch.setattr(service, "exchange_long_lived_token", fake_exchange)

    result = await service.get_valid_token()

    assert result == "stored-token"


@pytest.mark.asyncio
async def test_get_valid_token_raises_when_exchange_fails_and_token_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service("initial-token")
    expired = datetime.now(UTC) - timedelta(days=1)
    await service._store("stored-token", expires_at=expired)

    async def fake_exchange(access_token: str) -> TokenBundle:
        raise MetaTokenError("exchange failed")

    monkeypatch.setattr(service, "exchange_long_lived_token", fake_exchange)

    with pytest.raises(MetaTokenError, match="exchange failed"):
        await service.get_valid_token()


@pytest.mark.asyncio
async def test_get_valid_token_raises_when_not_configured() -> None:
    service = _service(token=None)

    with pytest.raises(MetaTokenError, match="not configured"):
        await service.get_valid_token()


def test_build_meta_token_service_requires_encryption_key() -> None:
    settings = Settings(instagram_token_encryption_key="")
    with pytest.raises(MetaTokenError, match="instagram_token_encryption_key"):
        build_meta_token_service(settings, AsyncMock())


@pytest.mark.asyncio
async def test_refresh_meta_trend_token_returns_integer_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.tasks import meta_token as meta_token_module

    fake_service = AsyncMock()
    fake_service.get_valid_token = AsyncMock(return_value="EAASdc6wxyz...")
    monkeypatch.setattr(
        meta_token_module,
        "build_meta_token_service",
        lambda _settings, _db, **_kwargs: fake_service,
    )
    resources = AsyncMock()
    resources.db = AsyncMock()

    result = await meta_token_module._refresh_meta_trend_token(resources, "task-1")

    assert result == {"refreshed": 1}
    assert all(isinstance(value, int) for value in result.values())
    assert "token_preview" not in result
