"""Thin APIs for preferences, recommendation learning, and observability."""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import AdminUser, CurrentUser, resources, settings
from app.schemas.intelligence import (
    AdminObservabilityResponse,
    CreatorPreferences,
    EvaluationRunRequest,
    EvaluationRunResponse,
    ExperimentCreate,
    ExperimentResponse,
    ExperimentUpdate,
    PostLinkRequest,
    PostLinkResponse,
    PreferencesResponse,
    RecommendationEventRequest,
    RecommendationEventResponse,
    StructuredCreatorProfile,
)
from app.services.evaluation import OfflineEvaluationService
from app.services.intelligence import (
    IntelligenceConflictError,
    IntelligenceNotFoundError,
    ObservabilityService,
    PreferencesService,
    RecommendationLearningService,
)

router = APIRouter(tags=["content-intelligence"])


def _db(request: Request) -> Any:
    database = resources(request).db
    assert database is not None
    return database


def _learning(request: Request) -> RecommendationLearningService:
    return RecommendationLearningService(_db(request))


@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(request: Request, user: CurrentUser) -> PreferencesResponse:
    document = await PreferencesService(_db(request)).get(user["_id"])
    return PreferencesResponse.model_validate(document)


@router.put("/preferences", response_model=PreferencesResponse)
async def put_preferences(
    payload: CreatorPreferences, request: Request, user: CurrentUser
) -> PreferencesResponse:
    document = await PreferencesService(_db(request)).put(user["_id"], payload)
    return PreferencesResponse.model_validate(document)


@router.get("/profile/analytics", response_model=StructuredCreatorProfile)
async def profile_analytics(request: Request, user: CurrentUser) -> StructuredCreatorProfile:
    profile = await _db(request).user_profiles.find_one({"user_id": user["_id"]})
    if not profile:
        raise HTTPException(status.HTTP_409_CONFLICT, "User profile analysis is not ready")
    return StructuredCreatorProfile.model_validate(
        profile.get("structured_profile")
        or {
            "pillars": [],
            "winning_patterns": [],
            "losing_patterns": [],
            "audience_markets": [],
            "avoid_patterns": [],
            "data_quality": 0,
        }
    )


@router.post(
    "/recommendations/{recommendation_id}/events",
    response_model=RecommendationEventResponse,
)
async def recommendation_event(
    recommendation_id: str,
    payload: RecommendationEventRequest,
    request: Request,
    user: CurrentUser,
) -> RecommendationEventResponse:
    try:
        document = await _learning(request).append_event(user["_id"], recommendation_id, payload)
    except IntelligenceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except IntelligenceConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RecommendationEventResponse(
        id=str(document["_id"]),
        recommendation_id=document["recommendation_id"],
        state=document["state"],
        created_at=document["created_at"],
    )


@router.post(
    "/recommendations/{recommendation_id}/post-link",
    response_model=PostLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_recommendation_post(
    recommendation_id: str,
    payload: PostLinkRequest,
    request: Request,
    user: CurrentUser,
) -> PostLinkResponse:
    try:
        document = await _learning(request).link_post(
            user["_id"], recommendation_id, payload.media_id
        )
    except IntelligenceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except IntelligenceConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PostLinkResponse(
        id=str(document["_id"]),
        recommendation_id=document["recommendation_id"],
        media_id=document["media_id"],
        permalink=document.get("permalink"),
        linked_at=document["linked_at"],
    )


@router.post(
    "/recommendation-experiments",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment(
    payload: ExperimentCreate, request: Request, user: CurrentUser
) -> ExperimentResponse:
    try:
        document = await _learning(request).create_experiment(user["_id"], payload)
    except IntelligenceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _experiment_response(document)


@router.patch("/recommendation-experiments/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(
    experiment_id: str,
    payload: ExperimentUpdate,
    request: Request,
    user: CurrentUser,
) -> ExperimentResponse:
    if not ObjectId.is_valid(experiment_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "experiment not found")
    try:
        document = await _learning(request).update_experiment(
            user["_id"], ObjectId(experiment_id), payload
        )
    except IntelligenceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except IntelligenceConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _experiment_response(document)


@router.get("/admin/observability", response_model=AdminObservabilityResponse)
async def admin_observability(
    request: Request, _admin: AdminUser
) -> AdminObservabilityResponse:
    document = await ObservabilityService(_db(request), settings(request)).summary()
    return AdminObservabilityResponse.model_validate(document)


@router.post("/admin/evaluations/run", response_model=EvaluationRunResponse)
async def run_offline_evaluation(
    payload: EvaluationRunRequest, request: Request, _admin: AdminUser
) -> EvaluationRunResponse:
    try:
        document = await OfflineEvaluationService(_db(request), settings(request)).run(
            model_version=payload.model_version,
            data_cutoff=payload.data_cutoff,
            k=payload.k,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return EvaluationRunResponse(id=str(document["_id"]), **document)


def _experiment_response(document: dict[str, Any]) -> ExperimentResponse:
    return ExperimentResponse(
        id=str(document["_id"]),
        recommendation_id=document["recommendation_id"],
        name=document["name"],
        variants=document["variants"],
        state=document["state"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )
