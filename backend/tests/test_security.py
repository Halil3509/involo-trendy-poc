import jwt

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)


def test_argon2_password_round_trip() -> None:
    encoded = hash_password("a-strong-test-password")
    assert encoded.startswith("$argon2")
    assert verify_password(encoded, "a-strong-test-password")
    assert not verify_password(encoded, "wrong-password")


def test_access_token_round_trip_and_wrong_secret() -> None:
    settings = Settings(jwt_secret="0123456789abcdef0123456789abcdef")
    token = create_access_token("507f1f77bcf86cd799439011", "user", settings)
    assert decode_access_token(token, settings)["sub"] == "507f1f77bcf86cd799439011"
    wrong = Settings(jwt_secret="abcdef0123456789abcdef0123456789")
    try:
        decode_access_token(token, wrong)
    except jwt.InvalidSignatureError:
        pass
    else:
        raise AssertionError("token signed with another key was accepted")


def test_refresh_token_is_opaque_and_hashable() -> None:
    raw, digest = new_refresh_token()
    assert raw != digest
    assert hash_refresh_token(raw) == digest
    assert len(digest) == 64
