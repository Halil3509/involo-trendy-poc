"""Shared error types and transient-failure classification helpers.

``TransientError`` marks failures that are worth retrying with backoff (network
blips, HTTP 429/5xx, cloud throttling). Non-transient failures (bad input, auth
problems, ``NeedsInterventionError``) are never retried.
"""

from __future__ import annotations

from typing import Any

import httpx

# AWS/botocore error codes that indicate throttling or transient unavailability.
THROTTLING_CODES = frozenset(
    {
        "ThrottlingException",
        "Throttling",
        "ThrottledException",
        "TooManyRequestsException",
        "RequestLimitExceeded",
        "ProvisionedThroughputExceededException",
        "RequestThrottledException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalServerException",
        "InternalServerError",
        "ModelTimeoutException",
        "ModelErrorException",
        "SlowDown",
    }
)


class TransientError(RuntimeError):
    """A retryable failure. ``retry_after`` is seconds requested by the server."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def transient_from_response(response: httpx.Response) -> TransientError | None:
    """Return a TransientError for HTTP 429/5xx responses, else None."""
    if response.status_code == 429 or response.status_code >= 500:
        return TransientError(
            f"upstream returned {response.status_code}",
            retry_after=_parse_retry_after(response),
        )
    return None


def is_expired_media_error(error: BaseException) -> bool:
    """True when the failure was caused by an HTTP 403 from a media download.

    Signed Instagram/Facebook CDN URLs expire shortly after scraping; downloads
    from such stale URLs fail with 403 and cannot succeed by retrying the same
    URL. The content must be re-scraped to obtain a fresh URL.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError) and current.response.status_code == 403:
            return True
        current = current.__cause__ or current.__context__
    return False


def is_throttling_error(error: Any) -> bool:
    """True for botocore ClientError instances whose code is a throttling code."""
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    return code in THROTTLING_CODES
