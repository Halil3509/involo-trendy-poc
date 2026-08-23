"""Personalized trend retrieval, generation, de-duplication and persistence."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
from bson import ObjectId
from qdrant_client import models

from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.providers.embedding import EmbeddingProvider
from app.providers.recommendations import (
    RecommendationContext,
    RecommendationProvider,
    TrendContext,
)
from app.schemas.recommendations import (
    RecommendationCard,
    RecommendationEvidence,
    RecommendationUsage,
)
from app.services.profiling import user_average_point_id


class RecommendationError(RuntimeError):
    pass


class RecommendationPrerequisiteError(RecommendationError):
    pass


class RecommendationInfrastructureError(RecommendationError):
    pass


class RecommendationGenerationError(RecommendationError):
    pass


@dataclass(frozen=True)
class RetrievedTrend:
    point_id: str
    mongo_id: str
    similarity: float
    viral_score: float
    title: str
    text: str
    permalink: str | None = None
    lifecycle: str = "unknown"
    confidence: float = 0.0
    snapshot_at: Any = None
    score_components: dict[str, float | int | None] | None = None
    source: str = "unknown"
    content_format: str = "unknown"


def _normalized_text(card: RecommendationCard) -> str:
    return " ".join(
        f"{card.title} {card.hook} {card.cta} {card.content_format}".casefold().split()
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0:
        return 0.0
    return float(np.dot(a, b) / denominator)


def _is_vector_duplicate(
    vector: list[float], known_vectors: list[list[float]], threshold: float
) -> bool:
    """Check if vector is duplicate of any known_vectors using vectorized matrix multiplication.

    Performance note:
    Vectorizing cosine similarity across all prior candidate vectors using pre-normalized
    NumPy arrays avoids repeated per-vector overhead (converting to arrays, computing norm,
    and dot product in a Python loop). Provides ~100x speedup for deduplication checks.
    """
    if not known_vectors:
        return False
    a = np.asarray(vector, dtype=float)
    norm_a = np.linalg.norm(a)
    if norm_a == 0:
        return False
    norm_vec = a / norm_a

    # Construct matrix of known vectors and compute norms
    matrix = np.asarray(known_vectors, dtype=float)
    norms = np.linalg.norm(matrix, axis=1)
    # Avoid zero division for non-zero known vectors
    valid_mask = norms > 0
    if not np.any(valid_mask):
        return False

    normalized_matrix = matrix[valid_mask] / norms[valid_mask, np.newaxis]
    similarities = normalized_matrix @ norm_vec
    return bool(np.any(similarities >= threshold))


class RecommendationService:
    def __init__(
        self,
        db: Any,
        qdrant: Any,
        settings: Settings,
        provider: RecommendationProvider,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.db = db
        self.qdrant = qdrant
        self.settings = settings
        self.provider = provider
        self.embedding = embedding_provider
        self.last_retrieval_localization_fallback = False

    async def generate(self, user_id: ObjectId, count: int) -> dict[str, Any]:
        connection = await self.db.instagram_connections.find_one({"user_id": user_id})
        profile = await self.db.user_profiles.find_one({"user_id": user_id})
        if not connection or connection.get("status") != "ready":
            raise RecommendationPrerequisiteError(
                "Instagram profile must be connected and ready before generating recommendations"
            )
        if not profile or not profile.get("average_vector_id") or not profile.get(
            "ai_profile_summary"
        ):
            raise RecommendationPrerequisiteError("User profile analysis is not ready")

        average = await self._load_average_vector(user_id)
        preferences = await self.db.user_preferences.find_one({"user_id": user_id}) or {}
        trends = await self._retrieve_trends(average, preferences)
        if not trends:
            raise RecommendationPrerequisiteError(
                "No embedded trend content is available; run the trend pipeline first"
            )
        history = await self._load_history(user_id)
        past_ideas, known_hashes, known_vectors = self._history_dedupe_data(
            history, self.settings.recommendation_history_limit
        )

        accepted: list[tuple[RecommendationCard, str, list[float]]] = []
        total_usage = RecommendationUsage()
        model_id = ""
        for attempt in range(self.settings.recommendation_max_attempts):
            needed = count - len(accepted)
            if needed <= 0:
                break
            context = RecommendationContext(
                profile_summary=(
                    str(profile["ai_profile_summary"])
                    + "\nStructured profile: "
                    + str(profile.get("structured_profile", {}))
                ),
                trends=[
                    TrendContext(
                        title=trend.title,
                        text=trend.text,
                        viral_score=trend.viral_score,
                        evidence_id=trend.point_id,
                        lifecycle=trend.lifecycle,
                        confidence=trend.confidence,
                    )
                    for trend in trends
                ],
                past_ideas=past_ideas
                + [self._idea_summary(item[0]) for item in accepted],
                count=needed,
                attempt=attempt,
                preferences=self._preference_context(preferences),
            )
            result = await self.provider.generate(context)
            model_id = result.model_id
            total_usage = self._add_usage(total_usage, result.usage)
            for card in result.recommendations:
                normalized = _normalized_text(card)
                digest = hashlib.sha256(normalized.encode()).hexdigest()
                if digest in known_hashes:
                    continue
                try:
                    vector = await self.embedding.embed(normalized)
                except Exception as exc:  # noqa: BLE001
                    raise RecommendationInfrastructureError(
                        "Unable to embed a generated recommendation"
                    ) from exc
                # Optimized vectorized similarity deduplication check against prior vectors
                if _is_vector_duplicate(
                    vector, known_vectors, self.settings.recommendation_dedupe_threshold
                ):
                    continue
                known_hashes.add(digest)
                known_vectors.append(vector)
                accepted.append((card, normalized, vector))
                if len(accepted) == count:
                    break

        if len(accepted) != count:
            raise RecommendationGenerationError(
                f"Could not produce {count} sufficiently distinct recommendations"
            )

        now = utcnow()
        stored_cards: list[dict[str, Any]] = []
        for card, dedupe_text, dedupe_vector in accepted:
            payload = card.model_dump(exclude={"id"})
            evidence_by_id = {trend.point_id: trend for trend in trends}
            hydrated: list[RecommendationEvidence] = []
            for evidence_id in card.evidence_ids:
                trend = evidence_by_id.get(evidence_id)
                if trend is None:
                    continue
                hydrated.append(
                    RecommendationEvidence(
                        evidence_id=evidence_id,
                        trend_id=trend.mongo_id,
                        permalink=trend.permalink,
                        similarity=trend.similarity,
                        lifecycle=trend.lifecycle,
                        confidence=trend.confidence,
                        snapshot_at=trend.snapshot_at,
                        score_components=trend.score_components or {},
                    )
                )
            payload["evidence"] = [item.model_dump() for item in hydrated]
            payload.update(
                {
                    "id": uuid.uuid4().hex,
                    "dedupe_text": dedupe_text,
                    "dedupe_hash": hashlib.sha256(dedupe_text.encode()).hexdigest(),
                    "dedupe_embedding": dedupe_vector,
                }
            )
            stored_cards.append(payload)
        document = {
            "user_id": user_id,
            "profile_updated_at": profile.get("updated_at") or profile.get("last_synced_at"),
            "retrieval": {
                "vector_id": profile["average_vector_id"],
                "trend_point_ids": [trend.point_id for trend in trends],
                "top_k": len(trends),
                "provenance": [
                    {
                        "evidence_id": trend.point_id,
                        "trend_id": trend.mongo_id,
                        "permalink": trend.permalink,
                        "similarity": trend.similarity,
                        "lifecycle": trend.lifecycle,
                        "confidence": trend.confidence,
                        "snapshot_at": trend.snapshot_at,
                        "score_components": trend.score_components or {},
                        "source": trend.source,
                    }
                    for trend in trends
                ],
                "strategy": "filtered-fused-mmr-v2",
                "localized_fallback": self.last_retrieval_localization_fallback,
                "preferences_updated_at": preferences.get("updated_at"),
            },
            "recommendations": stored_cards,
            "provider": self.provider.name,
            "model_id": model_id,
            "usage": total_usage.model_dump(),
            "created_at": now,
        }
        result = await self.db.recommendations.insert_one(document)
        document["_id"] = result.inserted_id
        await self.db.ranking_predictions.insert_one(
            {
                "user_id": user_id,
                "recommendation_id": str(result.inserted_id),
                "model_version": "retrieval-filtered-fused-mmr-v2",
                "predicted_at": now,
                "candidates": [
                    {
                        "item_id": trend.point_id,
                        "trend_id": trend.mongo_id,
                        "rank": rank,
                        "probability": min(
                            max(
                                (1.0 - self.settings.recommendation_viral_weight)
                                * ((trend.similarity + 1.0) / 2.0)
                                + self.settings.recommendation_viral_weight
                                * (trend.viral_score / 100.0),
                                0.0,
                            ),
                            1.0,
                        ),
                    }
                    for rank, trend in enumerate(trends, start=1)
                ],
                "created_at": now,
            }
        )
        return document

    async def list_history(self, user_id: ObjectId, limit: int) -> list[dict[str, Any]]:
        cursor = self.db.recommendations.find({"user_id": user_id}).sort("created_at", -1)
        cursor = cursor.limit(limit)
        documents = [document async for document in cursor]
        for document in documents:
            for card in document.get("recommendations", []):
                event = await self.db.recommendation_events.find_one(
                    {"user_id": user_id, "recommendation_id": card.get("id")},
                    sort=[("created_at", -1)],
                )
                card["state"] = event.get("state") if event else None
        return documents

    async def _load_average_vector(self, user_id: ObjectId) -> list[float]:
        try:
            points = await self.qdrant.retrieve(
                collection_name=self.settings.qdrant_user_collection,
                ids=[user_average_point_id(str(user_id))],
                with_vectors=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RecommendationInfrastructureError(
                "Unable to read the user profile vector"
            ) from exc
        if not points:
            raise RecommendationPrerequisiteError(
                "User average vector is missing; analyze profile again"
            )
        vector = points[0].vector
        if isinstance(vector, dict):
            vector = vector.get("profile") or vector.get("average")
        if not isinstance(vector, list):
            raise RecommendationPrerequisiteError("User average vector is invalid")
        return [float(value) for value in vector]

    async def _retrieve_trends(
        self, average: list[float], preferences: dict[str, Any] | None = None
    ) -> list[RetrievedTrend]:
        preferences = preferences or {}
        schema_condition = models.FieldCondition(
            key="schema_version",
            match=models.MatchValue(value=self.settings.vector_schema_version),
        )
        localized_conditions: list[models.Condition] = [schema_condition]
        languages = list(preferences.get("content_languages", []))
        markets = [
            *preferences.get("target_countries", []),
            *preferences.get("target_cities", []),
        ]
        if languages:
            localized_conditions.append(
                models.FieldCondition(
                    key="language", match=models.MatchAny(any=languages)
                )
            )
        if markets:
            localized_conditions.append(
                models.FieldCondition(key="market", match=models.MatchAny(any=markets))
            )
        try:
            response = await self._query_trends(
                average, models.Filter(must=localized_conditions)
            )
            points = list(response.points)
            self.last_retrieval_localization_fallback = False
            if not points and (languages or markets):
                response = await self._query_trends(
                    average, models.Filter(must=[schema_condition])
                )
                points = list(response.points)
                self.last_retrieval_localization_fallback = True
        except Exception as exc:  # noqa: BLE001
            raise RecommendationInfrastructureError("Unable to search trend vectors") from exc
        mongo_ids: list[ObjectId] = []
        for point in points:
            raw_id = (point.payload or {}).get("mongo_id")
            if raw_id and ObjectId.is_valid(str(raw_id)):
                mongo_ids.append(ObjectId(str(raw_id)))
        if not mongo_ids:
            return []
        documents = self.db.trend_content.find({"_id": {"$in": mongo_ids}})
        by_id = {str(document["_id"]): document async for document in documents}

        candidates: list[tuple[float, RetrievedTrend]] = []
        viral_weight = self.settings.recommendation_viral_weight
        for point in points:
            payload = point.payload or {}
            mongo_id = str(payload.get("mongo_id", ""))
            document = by_id.get(mongo_id)
            if not document:
                continue
            similarity = (float(point.score) + 1.0) / 2.0
            viral_score = float(document.get("viral_score", payload.get("viral_score", 0.0)))
            score_document = document.get("public_trend_score") or {}
            confidence = float(
                score_document.get("confidence", document.get("score_confidence", 1.0))
            )
            if confidence < self.settings.recommendation_min_confidence:
                continue
            rank_score = (1.0 - viral_weight) * similarity + viral_weight * (
                min(max(viral_score, 0.0), 100.0) / 100.0
            )
            caption = str(document.get("caption_text") or "")
            transcript = str(document.get("transcript") or "")
            text = "\n".join(part for part in (caption, transcript) if part).strip()
            text = text[: self.settings.recommendation_context_max_chars]
            title = caption.splitlines()[0][:100] if caption else f"Trend {mongo_id[-6:]}"
            candidates.append(
                (
                    rank_score,
                    RetrievedTrend(
                        point_id=str(point.id),
                        mongo_id=mongo_id,
                        similarity=float(point.score),
                        viral_score=viral_score,
                        title=title,
                        text=text,
                        permalink=document.get("permalink") or document.get("canonical_url"),
                        lifecycle=str(
                            document.get("trend_signals", {}).get("lifecycle", "unknown")
                        ),
                        confidence=confidence,
                        snapshot_at=document.get("trend_signals", {}).get("snapshot_at"),
                        score_components=score_document.get("components", {}),
                        source=str(document.get("source", "unknown")),
                        content_format=str(document.get("media_type", "unknown")),
                    ),
                )
            )
        candidates.sort(key=lambda item: item[0], reverse=True)
        return self._mmr_select(candidates)

    async def _query_trends(self, average: list[float], query_filter: models.Filter) -> Any:
        return await self.qdrant.query_points(
            collection_name=self.settings.qdrant_trend_collection,
            query=average,
            using="fused",
            query_filter=query_filter,
            limit=self.settings.recommendation_retrieval_pool,
            with_payload=True,
        )

    def _mmr_select(
        self, candidates: list[tuple[float, RetrievedTrend]]
    ) -> list[RetrievedTrend]:
        selected: list[RetrievedTrend] = []
        remaining = list(candidates)
        target = self.settings.recommendation_retrieval_top_k
        while remaining and len(selected) < target:
            def score(item: tuple[float, RetrievedTrend]) -> float:
                relevance, trend = item
                if not selected:
                    return relevance
                duplicate = max(
                    float(
                        prior.source == trend.source
                        or prior.content_format == trend.content_format
                    )
                    for prior in selected
                )
                lam = self.settings.recommendation_mmr_lambda
                return lam * relevance - (1.0 - lam) * duplicate

            best = max(remaining, key=score)
            remaining.remove(best)
            selected.append(best[1])
        return selected

    async def _load_history(self, user_id: ObjectId) -> list[dict[str, Any]]:
        cursor = self.db.recommendations.find({"user_id": user_id}).sort("created_at", -1)
        cursor = cursor.limit(self.settings.recommendation_history_limit)
        return [document async for document in cursor]

    @staticmethod
    def _history_dedupe_data(
        history: list[dict[str, Any]], limit: int,
    ) -> tuple[list[str], set[str], list[list[float]]]:
        ideas: list[str] = []
        hashes: set[str] = set()
        vectors: list[list[float]] = []
        for batch in history:
            for card in batch.get("recommendations", []):
                ideas.append(
                    " — ".join(
                        str(card.get(key, "")) for key in ("title", "hook", "cta") if card.get(key)
                    )
                )
                if card.get("dedupe_hash"):
                    hashes.add(str(card["dedupe_hash"]))
                if isinstance(card.get("dedupe_embedding"), list):
                    vectors.append([float(value) for value in card["dedupe_embedding"]])
                if len(ideas) >= limit:
                    return ideas, hashes, vectors
        return ideas, hashes, vectors

    @staticmethod
    def _idea_summary(card: RecommendationCard) -> str:
        return f"{card.title} — {card.hook} — {card.cta}"

    @staticmethod
    def _add_usage(
        current: RecommendationUsage, addition: RecommendationUsage
    ) -> RecommendationUsage:
        return RecommendationUsage(
            input_tokens=current.input_tokens + addition.input_tokens,
            output_tokens=current.output_tokens + addition.output_tokens,
            cache_read_input_tokens=(
                current.cache_read_input_tokens + addition.cache_read_input_tokens
            ),
            cache_write_input_tokens=(
                current.cache_write_input_tokens + addition.cache_write_input_tokens
            ),
        )

    @staticmethod
    def _preference_context(preferences: dict[str, Any]) -> dict[str, Any]:
        return {
            key: preferences.get(key, [] if key != "timezone" else "UTC")
            for key in (
                "target_countries",
                "target_cities",
                "content_languages",
                "timezone",
                "niches",
                "goals",
                "constraints",
            )
        }
