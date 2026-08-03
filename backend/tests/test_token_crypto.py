import pytest

from app.core.token_crypto import TokenCipher, TokenEncryptionError


def test_token_cipher_round_trip_and_random_nonce() -> None:
    cipher = TokenCipher("a sufficiently long test encryption secret")
    first = cipher.encrypt("instagram-token")
    second = cipher.encrypt("instagram-token")

    assert first != second
    assert cipher.decrypt(first) == "instagram-token"
    assert cipher.decrypt(second) == "instagram-token"


def test_token_cipher_rejects_wrong_key_and_short_secrets() -> None:
    encrypted = TokenCipher("a sufficiently long test encryption secret").encrypt("token")

    with pytest.raises(TokenEncryptionError):
        TokenCipher("another sufficiently long encryption secret").decrypt(encrypted)
    with pytest.raises(TokenEncryptionError):
        TokenCipher("too-short")
