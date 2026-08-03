import json

import pytest

from app.infrastructure.log_bus import ScraperLogBus, channel_key, stream_key


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.published: list[tuple[str, str]] = []
        self.expirations: dict[str, int] = {}

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> None:
        data = self.lists.get(key, [])
        stop = None if end == -1 else end + 1
        self.lists[key] = data[start:stop]

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl

    async def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, data))
        return 1

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        data = self.lists.get(key, [])
        stop = None if end == -1 else end + 1
        return data[start:stop]


@pytest.mark.asyncio
async def test_publish_appends_and_history_is_chronological() -> None:
    redis = FakeRedis()
    bus = ScraperLogBus(redis, max_lines=500, ttl_seconds=60)

    await bus.publish("task-1", "first", step="start")
    await bus.publish("task-1", "second", level="success", step="done", terminal=True, count=3)

    history = await bus.history("task-1")
    assert [event["message"] for event in history] == ["first", "second"]
    assert history[0]["step"] == "start"
    assert history[1]["terminal"] is True
    assert history[1]["level"] == "success"
    assert history[1]["data"] == {"count": 3}
    assert redis.expirations[stream_key("task-1")] == 60


@pytest.mark.asyncio
async def test_publish_broadcasts_to_channel() -> None:
    redis = FakeRedis()
    bus = ScraperLogBus(redis)
    await bus.publish("abc", "hello")

    assert len(redis.published) == 1
    channel, payload = redis.published[0]
    assert channel == channel_key("abc")
    assert json.loads(payload)["message"] == "hello"


@pytest.mark.asyncio
async def test_history_caps_at_max_lines() -> None:
    redis = FakeRedis()
    bus = ScraperLogBus(redis, max_lines=3, ttl_seconds=60)
    for index in range(10):
        await bus.publish("t", f"line-{index}")

    history = await bus.history("t")
    assert [event["message"] for event in history] == ["line-7", "line-8", "line-9"]
