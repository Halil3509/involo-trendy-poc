"""Encrypted persistence and proactive refresh for Meta trend access tokens.

The token used for ``graph.facebook.com`` Business Discovery / Hashtag Search is
stored in MongoDB encrypted with the same Fernet key used for user Instagram
connections.  When the token is within the refresh window, or has expired, the
service exchanges it for a 60-day long-lived token through the official Meta
``oauth/access_token`` endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import Settings
from app.core.rate_limit import build_graph_rate_limiter
from app.core.token_crypto import TokenCipher

if TYPE_CHECKING:
    from app.core.rate_limit import GraphApiRateLimiter

logger = logging.getLogger(__name__)

_COLLECTION = "meta_access_tokens"
_DOCUMENT_ID = "trend"
_REFRESH_WINDOW = timedelta(days=7)
_DEFAULT_LONG_LIVED_SECONDS = 5_184_000  # 60 days


class MetaTokenError(RuntimeError):
    """A non-retryable failure while managing the Meta trend token."""


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    expires_at: datetime


class MetaTokenService:
    """Manage the single Meta trend token document.

    The document is keyed ``_id="trend"`` and contains an encrypted token and
    its expiry timestamp.  Callers should use :meth:`get_valid_token` which
    refreshes proactively before the token expires.
    """

    def __init__(
        self,
        settings: Settings,
        db: AsyncDatabase[dict[str, Any]],
        cipher: TokenCipher,
        *,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.cipher = cipher
        self.graph_limiter = graph_limiter

    async def get_valid_token(self) -> str:
        """Return a usable Meta access token, refreshing from storage if needed."""
        document = await self.db[_COLLECTION].find_one({"_id": _DOCUMENT_ID})
        if document is None:
            return await self._seed_from_settings()

        token = self._decrypt(document["access_token_encrypted"])
        expires_at = document.get("expires_at")
        if expires_at is None or expires_at < datetime.now(UTC) + _REFRESH_WINDOW:
            return await self._refresh(token, document)
        return token

    async def exchange_long_lived_token(self, access_token: str) -> TokenBundle:
        """Exchange a short-lived or existing long-lived token for a 60-day token.

        Meta's ``fb_exchange_token`` grant returns a new User Access Token good for
        approximately 60 days.  This is the supported way to keep a
        ``graph.facebook.com`` token alive without requiring the user to
        re-authenticate each time.
        """
        app_id = self.settings.effective_facebook_app_id
        app_secret = self.settings.effective_facebook_app_secret
        if not app_id or not app_secret:
            raise MetaTokenError("official Meta app credentials are not configured")

        url = (
            f"https://graph.facebook.com/{self.settings.instagram_graph_api_version}"
            "/oauth/access_token"
        )
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret.get_secret_value(),
            "fb_exchange_token": access_token,
        }
        if self.graph_limiter is not None:
            await self.graph_limiter.acquire()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)

        if not response.is_success:
            body = response.text
            try:
                body = response.json()
            except Exception:
                pass
            raise MetaTokenError(f"Meta token exchange failed: {body}")

        payload = response.json()
        if "access_token" not in payload:
            raise MetaTokenError(f"Meta token exchange returned no access_token: {payload}")

        expires_in = int(payload.get("expires_in", _DEFAULT_LONG_LIVED_SECONDS))
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        return TokenBundle(access_token=str(payload["access_token"]), expires_at=expires_at)

    async def _seed_from_settings(self) -> str:
        if not self.settings.meta_trend_access_token:
            raise MetaTokenError("meta_trend_access_token is not configured")
        token = self.settings.meta_trend_access_token.get_secret_value()
        logger.info("Seeding managed Meta trend token from settings")
        if self.settings.effective_facebook_app_id and self.settings.effective_facebook_app_secret:
            bundle = await self.exchange_long_lived_token(token)
            return await self._store(bundle.access_token, expires_at=bundle.expires_at)
        return await self._store(token)

    async def _refresh(self, token: str, document: dict[str, Any]) -> str:
        try:
            bundle = await self.exchange_long_lived_token(token)
        except MetaTokenError:
            expires_at = document.get("expires_at")
            if expires_at is not None and expires_at > datetime.now(UTC):
                logger.warning(
                    "Meta token exchange failed; returning existing token until it expires"
                )
                return token
            raise
        logger.info("Refreshed managed Meta trend token")
        return await self._store(bundle.access_token, expires_at=bundle.expires_at)

    async def _store(
        self, access_token: str, expires_at: datetime | None = None
    ) -> str:
        if expires_at is None:
            # Best-effort default when we cannot determine the actual expiry.
            expires_at = datetime.now(UTC) + timedelta(days=60)
        await self.db[_COLLECTION].update_one(
            {"_id": _DOCUMENT_ID},
            {
                "$set": {
                    "access_token_encrypted": self.cipher.encrypt(access_token),
                    "expires_at": expires_at,
                    "refreshed_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        return access_token

    def _decrypt(self, encrypted: str) -> str:
        try:
            return self.cipher.decrypt(encrypted)
        except Exception as exc:
            raise MetaTokenError("stored Meta trend token could not be decrypted") from exc


def build_meta_token_service(
    settings: Settings,
    db: AsyncDatabase[dict[str, Any]],
    *,
    redis: Any | None = None,
) -> MetaTokenService:
    """Build a MetaTokenService using the configured Instagram encryption key."""
    key = settings.instagram_token_encryption_key
    if not key:
        raise MetaTokenError("instagram_token_encryption_key is not configured")
    cipher = TokenCipher(key.get_secret_value())
    graph_limiter = build_graph_rate_limiter(redis, settings)
    return MetaTokenService(settings, db, cipher, graph_limiter=graph_limiter)
