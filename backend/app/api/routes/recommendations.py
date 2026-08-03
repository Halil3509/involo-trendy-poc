"""Authenticated recommendation generation and history endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from redis.exceptions import LockError

from app.api.dependencies import CurrentUser, resources, settings
from app.core.rate_limit import enforce_rate_limit
from app.providers.embedding import EmbeddingError, build_embedding_provider
from app.providers.recommendations import (
    RecommendationProviderError,
    build_recommendation_provider,
)
from app.schemas.recommendations import (
    RecommendationBatchResponse,
    RecommendationCard,
    RecommendationRequest,
    RecommendationUsage,
)
from app.services.recommendations import (
    RecommendationGenerationError,
    RecommendationInfrastructureError,
    RecommendationPrerequisiteError,
    RecommendationService,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _service(request: Request) -> RecommendationService:
    app_resources = resources(request)
    assert app_resources.db is not None
    assert app_resources.qdrant is not None
    app_settings = settings(request)
    return RecommendationService(
        app_resources.db,
        app_resources.qdrant,
        app_settings,
        build_recommendation_provider(app_settings),
        build_embedding_provider(app_settings),
    )


def recommendation_response(document: dict[str, Any]) -> RecommendationBatchResponse:
    cards = [
        RecommendationCard.model_validate(
            {
                key: value
                for key, value in card.items()
                if key in RecommendationCard.model_fields
            }
        )
        for card in document.get("recommendations", [])
    ]
    usage = document.get("usage")
    return RecommendationBatchResponse(
        id=str(document["_id"]),
        created_at=document["created_at"],
        recommendations=cards,
        usage=RecommendationUsage.model_validate(usage) if usage else None,
    )


@router.post("", response_model=RecommendationBatchResponse)
async def create_recommendations(
    payload: RecommendationRequest, request: Request, user: CurrentUser
) -> RecommendationBatchResponse:
    app_settings = settings(request)
    count = payload.count or app_settings.recommendation_default_count
    await enforce_rate_limit(
        request,
        scope="recommendations",
        identifier=str(user["_id"]),
        max_requests=app_settings.recommendation_rate_limit_max,
        window_seconds=app_settings.recommendation_rate_limit_window_seconds,
    )
    redis = resources(request).redis
    assert redis is not None
    lock = redis.lock(
        f"involo:recommendations:user:{user['_id']}",
        timeout=app_settings.recommendation_lock_ttl_seconds,
        blocking_timeout=0,
    )
    acquired = await lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Recommendation generation is already in progress",
        )
    try:
        document = await _service(request).generate(user["_id"], count)
        return recommendation_response(document)
    except RecommendationPrerequisiteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RecommendationGenerationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except RecommendationProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except (RecommendationInfrastructureError, EmbeddingError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Recommendation service is temporarily unavailable",
        ) from exc
    finally:
        try:
            await lock.release()
        except LockError:
            # The TTL can elapse during a slow external model call; do not mask
            # the already determined API result with a lock ownership error.
            pass


@router.get("", response_model=list[RecommendationBatchResponse])
async def list_recommendations(
    request: Request,
    user: CurrentUser,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[RecommendationBatchResponse]:
    documents = await _service(request).list_history(user["_id"], limit)
    return [recommendation_response(document) for document in documents]
