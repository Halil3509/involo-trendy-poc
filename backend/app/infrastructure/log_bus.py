"""Live job log fan-out over Redis pub/sub with a capped replay buffer.

The Celery worker and the FastAPI process run separately, so background job
events are published to Redis. Late-joining WebSocket clients replay the recent
history list first, then live-tail the pub/sub channel.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from app.infrastructure.resources import utcnow

LogEvent = dict[str, Any]


def channel_key(task_id: str) -> str:
    return f"involo:job:logs:{task_id}"


def stream_key(task_id: str) -> str:
    return f"involo:job:logstream:{task_id}"


class JobLogBus:
    def __init__(
        self,
        redis: Redis,
        *,
        max_lines: int = 500,
        ttl_seconds: int = 3600,
    ) -> None:
        self.redis = redis
        self.max_lines = max_lines
        self.ttl_seconds = ttl_seconds

    async def publish(
        self,
        task_id: str,
        message: str,
        *,
        level: str = "info",
        step: str | None = None,
        terminal: bool = False,
        **data: Any,
    ) -> LogEvent:
        event: LogEvent = {
            "ts": utcnow().isoformat(),
            "level": level,
            "message": message,
        }
        if step is not None:
            event["step"] = step
        if terminal:
            event["terminal"] = True
        if data:
            event["data"] = data
        payload = json.dumps(event)
        list_key = stream_key(task_id)
        await self.redis.rpush(list_key, payload)  # type: ignore[misc]
        await self.redis.ltrim(list_key, -self.max_lines, -1)  # type: ignore[misc]
        await self.redis.expire(list_key, self.ttl_seconds)
        await self.redis.publish(channel_key(task_id), payload)
        return event

    async def history(self, task_id: str) -> list[LogEvent]:
        raw = await self.redis.lrange(stream_key(task_id), 0, -1)  # type: ignore[misc]
        events: list[LogEvent] = []
        for item in raw:
            try:
                events.append(json.loads(item))
            except (ValueError, TypeError):
                continue
        return events

    async def subscribe(self, task_id: str) -> AsyncIterator[LogEvent]:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel_key(task_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    yield json.loads(message["data"])
                except (ValueError, TypeError, KeyError):
                    continue
        finally:
            await pubsub.unsubscribe(channel_key(task_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]


# Backward-compatible alias used by older code and tests.
ScraperLogBus = JobLogBus
