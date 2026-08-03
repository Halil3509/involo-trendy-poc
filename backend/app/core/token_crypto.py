"""Authenticated encryption for persisted Instagram access tokens."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class TokenEncryptionError(RuntimeError):
    pass


class TokenCipher:
    def __init__(self, secret: str) -> None:
        if len(secret) < 24:
            raise TokenEncryptionError("token encryption secret must be at least 24 characters")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise TokenEncryptionError("stored token could not be decrypted") from exc
