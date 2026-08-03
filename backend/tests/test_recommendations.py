from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId
from fakes import FakeDatabase
from provider_doubles import FakeEmbeddingProvider, FakeRecommendationProvider

from app.api.routes.recommendations import recommendation_response
from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.providers.recommendations import (
    RecommendationContext,
    RecommendationProvider,
    RecommendationProviderResult,
)
from app.schemas.recommendations import RecommendationCard, RecommendationUsage
from app.services.recommendations import (
    RecommendationGenerationError,
    RecommendationPrerequisiteError,
    RecommendationService,
)


class FakeQdrant:
    def __init__(self, average: list[float], points: list[Any]) -> None:
        self.average = average
        self.points = points
        self.retrieve_calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    async def retrieve(self, **kwargs: Any) -> list[Any]:
        self.retrieve_calls.append(kwargs)
        return [SimpleNamespace(vector={"average": self.average})]

    async def query_points(self, **kwargs: Any) -> Any:
        self.query_calls.append(kwargs)
        return SimpleNamespace(points=self.points)


def trend_point(
    mongo_id: ObjectId, *, point_id: str, score: float, viral_score: float
) -> Any:
    return SimpleNamespace(
        id=point_id,
        score=score,
        payload={
            "mongo_id": str(mongo_id),
            "viral_score": viral_score,
        },
    )


def ready_database(user_id: ObjectId) -> FakeDatabase:
    db = FakeDatabase()
    now = utcnow()
    db.instagram_connections.docs.append(
        {"user_id": user_id, "status": "ready", "instagram_username": "creator"}
    )
    db.user_profiles.docs.append(
        {
            "user_id": user_id,
            "average_vector_id": "average-id",
            "ai_profile_summary": "Sade tarifler paylaşan samimi bir yemek üreticisi.",
            "updated_at": now,
        }
    )
    return db


def service_with_trends(
    user_id: ObjectId, *, settings: Settings | None = None
) -> tuple[RecommendationService, FakeDatabase, FakeQdrant]:
    db = ready_database(user_id)
    first_id, second_id = ObjectId(), ObjectId()
    db.trend_content.docs.extend(
        [
            {
                "_id": first_id,
                "caption_text": "Hızlı akşam yemeği",
                "transcript": "Üç malzemeli pratik tarif",
                "viral_score": 80.0,
            },
            {
                "_id": second_id,
                "caption_text": "Mutfak düzeni",
                "transcript": "Küçük mutfaklar için ipuçları",
                "viral_score": 55.0,
            },
        ]
    )
    qdrant = FakeQdrant(
        [1.0] + [0.0] * 7,
        [
            trend_point(first_id, point_id="trend-1", score=0.9, viral_score=80),
            trend_point(second_id, point_id="trend-2", score=0.8, viral_score=55),
        ],
    )
    app_settings = settings or Settings(
        vector_size=8,
        recommendation_dedupe_threshold=0.999,
        recommendation_retrieval_top_k=2,
        recommendation_retrieval_pool=2,
    )
    service = RecommendationService(
        db,
        qdrant,
        app_settings,
        FakeRecommendationProvider(),
        FakeEmbeddingProvider(8),
    )
    return service, db, qdrant


@pytest.mark.asyncio
async def test_generate_retrieves_semantic_trends_and_persists_batch() -> None:
    user_id = ObjectId()
    service, db, qdrant = service_with_trends(user_id)

    document = await service.generate(user_id, 3)

    assert len(document["recommendations"]) == 3
    assert all(card["id"] for card in document["recommendations"])
    assert len(db.recommendations.docs) == 1
    assert qdrant.query_calls[0]["using"] == "fused"
    assert document["retrieval"]["trend_point_ids"] == ["trend-1", "trend-2"]
    response = recommendation_response(document)
    assert response.id
    assert len(response.recommendations) == 3
    assert not hasattr(response.recommendations[0], "dedupe_embedding")


@pytest.mark.asyncio
async def test_viral_score_can_rerank_retrieved_candidates() -> None:
    user_id = ObjectId()
    settings = Settings(
        vector_size=8,
        recommendation_retrieval_top_k=2,
        recommendation_retrieval_pool=2,
        recommendation_viral_weight=1.0,
    )
    service, db, qdrant = service_with_trends(user_id, settings=settings)
    db.trend_content.docs[0]["viral_score"] = 10.0
    db.trend_content.docs[1]["viral_score"] = 99.0

    trends = await service._retrieve_trends([1.0] + [0.0] * 7)

    assert [trend.point_id for trend in trends] == ["trend-2", "trend-1"]
    assert qdrant.query_calls[0]["limit"] == 2


@pytest.mark.asyncio
async def test_generate_requires_ready_profile() -> None:
    user_id = ObjectId()
    service, db, _ = service_with_trends(user_id)
    db.instagram_connections.docs[0]["status"] = "profiling"

    with pytest.raises(RecommendationPrerequisiteError):
        await service.generate(user_id, 3)

    assert db.recommendations.docs == []


class DuplicateProvider(RecommendationProvider):
    name = "duplicate"

    async def generate(self, context: RecommendationContext) -> RecommendationProviderResult:
        card = RecommendationCard(
            title="Aynı fikir",
            hook="Aynı giriş",
            cta="Aynı çağrı",
            content_format="reels",
            reasoning="Aynı gerekçe",
        )
        return RecommendationProviderResult(
            recommendations=[card for _ in range(context.count)],
            usage=RecommendationUsage(),
            model_id="duplicate",
        )


class CapturingProvider(FakeRecommendationProvider):
    def __init__(self) -> None:
        self.contexts: list[RecommendationContext] = []

    async def generate(self, context: RecommendationContext) -> RecommendationProviderResult:
        self.contexts.append(context)
        return await super().generate(context)


@pytest.mark.asyncio
async def test_duplicate_retry_failure_is_atomic() -> None:
    user_id = ObjectId()
    service, db, qdrant = service_with_trends(
        user_id,
        settings=Settings(
            vector_size=8,
            recommendation_retrieval_top_k=2,
            recommendation_retrieval_pool=2,
            recommendation_max_attempts=2,
        ),
    )
    service = RecommendationService(
        db,
        qdrant,
        service.settings,
        DuplicateProvider(),
        FakeEmbeddingProvider(8),
    )

    with pytest.raises(RecommendationGenerationError):
        await service.generate(user_id, 3)

    assert db.recommendations.docs == []


@pytest.mark.asyncio
async def test_history_is_reverse_chronological_and_user_isolated() -> None:
    user_id, other_user = ObjectId(), ObjectId()
    service, db, _ = service_with_trends(user_id)
    now = utcnow()
    db.recommendations.docs.extend(
        [
            {"_id": 1, "user_id": user_id, "created_at": now - timedelta(minutes=1)},
            {"_id": 2, "user_id": other_user, "created_at": now},
            {"_id": 3, "user_id": user_id, "created_at": now},
        ]
    )

    history = await service.list_history(user_id, 10)

    assert [document["_id"] for document in history] == [3, 1]


@pytest.mark.asyncio
async def test_preferences_shape_provider_context_and_localized_filter() -> None:
    user_id = ObjectId()
    service, db, qdrant = service_with_trends(user_id)
    provider = CapturingProvider()
    service.provider = provider
    db.user_preferences.docs.append(
        {
            "user_id": user_id,
            "target_countries": ["TR"],
            "target_cities": ["Istanbul"],
            "content_languages": ["tr"],
            "niches": ["food"],
            "goals": ["saves"],
            "constraints": ["indoor only"],
            "timezone": "Europe/Istanbul",
        }
    )

    await service.generate(user_id, 3)

    assert provider.contexts[0].preferences["constraints"] == ["indoor only"]
    query_filter = qdrant.query_calls[0]["query_filter"]
    assert [condition.key for condition in query_filter.must] == [
        "schema_version",
        "language",
        "market",
    ]


@pytest.mark.asyncio
async def test_retrieval_falls_back_only_when_localized_query_is_empty() -> None:
    user_id = ObjectId()
    service, _, qdrant = service_with_trends(user_id)
    original_points = qdrant.points
    calls = 0

    async def query_points(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        qdrant.query_calls.append(kwargs)
        return SimpleNamespace(points=[] if calls == 1 else original_points)

    qdrant.query_points = query_points  # type: ignore[method-assign]
    trends = await service._retrieve_trends(
        [1.0] + [0.0] * 7,
        {"content_languages": ["tr"], "target_countries": ["TR"]},
    )

    assert trends
    assert calls == 2
    assert service.last_retrieval_localization_fallback is True
    assert len(qdrant.query_calls[1]["query_filter"].must) == 1


@pytest.mark.asyncio
async def test_history_hydrates_latest_recommendation_state() -> None:
    user_id = ObjectId()
    service, db, _ = service_with_trends(user_id)
    now = utcnow()
    db.recommendations.docs.append(
        {
            "_id": 5,
            "user_id": user_id,
            "created_at": now,
            "recommendations": [{"id": "idea-1"}],
        }
    )
    db.recommendation_events.docs.extend(
        [
            {
                "user_id": user_id,
                "recommendation_id": "idea-1",
                "state": "saved",
                "created_at": now - timedelta(minutes=1),
            },
            {
                "user_id": user_id,
                "recommendation_id": "idea-1",
                "state": "in_production",
                "created_at": now,
            },
        ]
    )

    history = await service.list_history(user_id, 10)

    assert history[0]["recommendations"][0]["state"] == "in_production"
