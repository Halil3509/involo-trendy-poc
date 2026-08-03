from __future__ import annotations

from typing import Any

from app.infrastructure.resources import utcnow
from app.providers.topic_signals import TopicSignalProvider


class TopicSignalService:
    def __init__(self, db: Any, providers: list[TopicSignalProvider]) -> None:
        self.db = db
        self.providers = providers

    async def capture(self, topics: list[str]) -> dict[str, int]:
        normalized_topics = list(
            dict.fromkeys(topic.strip().lower() for topic in topics if topic.strip())
        )
        counters = {"providers": 0, "signals": 0, "failed_providers": 0}
        now = utcnow()
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for provider in self.providers:
            try:
                signals = await provider.fetch(normalized_topics)
            except Exception as exc:  # noqa: BLE001 - one connector must not block others
                counters["failed_providers"] += 1
                await self.db.provider_runs.insert_one(
                    {
                        "provider": provider.source,
                        "kind": "topic_signals",
                        "state": "failed",
                        "error": str(exc)[:500],
                        "created_at": now,
                    }
                )
                continue
            counters["providers"] += 1
            for signal in signals:
                document = signal.model_dump()
                document["signal_type"] = "external_topic"
                await self.db.topic_signal_snapshots.insert_one(document)
                by_topic.setdefault(signal.topic, []).append(document)
                counters["signals"] += 1
            await self.db.provider_runs.insert_one(
                {
                    "provider": provider.source,
                    "kind": "topic_signals",
                    "state": "succeeded",
                    "signal_count": len(signals),
                    "created_at": now,
                }
            )
        for topic, source_signals in by_topic.items():
            # Normalize only across source-level topic signals. This is explicitly
            # not an Instagram engagement or performance metric.
            source_scores = {
                str(item["source"]): float(item["score"]) for item in source_signals
            }
            maximum = max(source_scores.values(), default=0.0)
            normalized = {
                source: (score / maximum if maximum > 0 else 0.0)
                for source, score in source_scores.items()
            }
            await self.db.topic_signal_aggregates.update_one(
                {"topic": topic},
                {
                    "$set": {
                        "topic": topic,
                        "signal_type": "external_topic",
                        "normalized_source_scores": normalized,
                        "sources": sorted(source_scores),
                        "captured_at": now,
                    }
                },
                upsert=True,
            )
        return counters
