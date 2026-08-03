from fakes import FakeDatabase

from app.infrastructure.resources import utcnow
from app.providers.topic_signals import TopicSignalProvider
from app.schemas.intelligence import TopicSignal
from app.services.topic_signals import TopicSignalService


class StubTopicProvider(TopicSignalProvider):
    source = "youtube"

    async def fetch(self, topics: list[str]) -> list[TopicSignal]:
        return [
            TopicSignal(
                topic=topic,
                source="youtube",
                license="YouTube API Services Terms of Service",
                captured_at=utcnow(),
                score=100,
                volume=10,
                source_url="https://youtube.example/search",
                provenance={"api": "YouTube Data API v3"},
            )
            for topic in topics
        ]


async def test_topic_signal_service_persists_provenance_without_instagram_metrics() -> None:
    db = FakeDatabase()
    result = await TopicSignalService(db, [StubTopicProvider()]).capture(
        [" Food ", "food", "travel"]
    )

    assert result == {"providers": 1, "signals": 2, "failed_providers": 0}
    assert len(db.topic_signal_snapshots.docs) == 2
    assert all(
        document["signal_type"] == "external_topic"
        and document["license"]
        and document["provenance"]
        for document in db.topic_signal_snapshots.docs
    )
    assert {document["topic"] for document in db.topic_signal_aggregates.docs} == {
        "food",
        "travel",
    }
    assert all(
        "instagram" not in document for document in db.topic_signal_aggregates.docs
    )
