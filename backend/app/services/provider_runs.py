"""Sanitized provider invocation telemetry."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.infrastructure.resources import utcnow


async def record_provider_call[T](
    db: Any,
    *,
    provider: str,
    model_id: str,
    stage: str,
    operation: Callable[[], Awaitable[T]],
    user_id: Any = None,
    subject_id: str | None = None,
    media_seconds: float | None = None,
    region: str | None = None,
) -> T:
    started_at = utcnow()
    started = time.perf_counter()
    state = "succeeded"
    error_type: str | None = None
    try:
        return await operation()
    except Exception as exc:
        state = "failed"
        error_type = type(exc).__name__
        raise
    finally:
        document = {
            "provider": provider,
            "model_id": model_id,
            "stage": stage,
            "state": state,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "started_at": started_at,
            "created_at": utcnow(),
            "media_seconds": media_seconds,
            "region": region,
            "subject_id": subject_id,
            "error_type": error_type,
        }
        if user_id is not None:
            document["user_id"] = user_id
        try:
            await db.provider_runs.insert_one(document)
        except Exception:  # noqa: S110 - telemetry must not mask provider results
            pass
