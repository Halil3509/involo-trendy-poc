"""Deterministic Trending Viral Score.

Ranks posts by how fast they are currently spreading: distribution beyond the
creator's audience, engagement quality, and velocity (views per day). Components
are combined additively so that a strong velocity or engagement can compensate
for a weaker distribution ratio.

    weighted_engagement  = likes + comments*Wc + shares*Ws
    engagement_rate      = weighted_engagement / max(views, 1)
    engagement_score     = 1 - exp(-engagement_rate * engagement_multiplier)

    distribution_ratio   = views / max(followers, 1)
    distribution_score   = 1 - exp(-distribution_ratio / distribution_divisor)

    age_days             = max(now - taken_at, 0.25) in days
    views_per_day        = views / age_days
    velocity_score       = min(log10(views_per_day + 1) / velocity_divisor, 1)

    raw_score = Wd*distribution_score
              + We*engagement_score
              + Wv*velocity_score

    trending_viral_score = 100 * (1 - exp(-raw_score))

The raw component values are returned alongside the normalized 0-100 score so
callers can persist them separately (per the document, the score is stored as a
dedicated field while raw metrics are kept as well).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.schemas.trends import ContentMetadata


@dataclass(frozen=True)
class ScoreWeights:
    distribution_weight: float = 0.40
    engagement_weight: float = 0.30
    velocity_weight: float = 0.30
    comment_weight: float = 5.0
    share_weight: float = 12.0
    distribution_ratio_divisor: float = 2.0
    engagement_rate_multiplier: float = 6.0
    velocity_log_divisor: float = 7.0
    # Log divisor used to scale absolute engagement when view_count is missing.
    # Higher values flatten differences; lower values exaggerate them.
    engagement_log_divisor: float = 3.0


@dataclass(frozen=True)
class ScoreComponents:
    distribution_score: float
    engagement_score: float
    velocity_score: float
    weighted_engagement_rate: float
    raw_score: float


@dataclass(frozen=True)
class ProductionScore:
    score: float
    confidence: float
    model_version: str
    components: dict[str, float]
    available_metrics: tuple[str, ...]


@dataclass(frozen=True)
class TrendLifecycleSignals:
    velocity: float | None
    acceleration: float | None
    percentile: float | None
    freshness: float
    lifecycle: str
    confidence: float


def compute_score_components(
    metadata: ContentMetadata,
    now: datetime,
    weights: ScoreWeights,
) -> ScoreComponents:
    views = max(metadata.view_count, 0)
    likes = max(metadata.like_count, 0)
    comments = max(metadata.comment_count, 0)
    shares = max(metadata.share_count, 0)
    followers = max(metadata.owner_follower_count, 1)

    distribution_ratio = views / followers
    distribution_score = 1.0 - math.exp(-distribution_ratio / weights.distribution_ratio_divisor)

    weighted_engagement = (
        likes + (comments * weights.comment_weight) + (shares * weights.share_weight)
    )

    if views == 0 and weighted_engagement > 0:
        # view_count is missing; use log-scaled absolute engagement as a popularity
        # signal instead of faking a 100% engagement rate with max(views, 1.0).
        weighted_engagement_rate = weighted_engagement
        engagement_score = (
            math.log10(weighted_engagement + 1.0) / weights.engagement_log_divisor
        )
    else:
        weighted_engagement_rate = weighted_engagement / max(views, 1.0)
        engagement_score = 1.0 - math.exp(
            -weighted_engagement_rate * weights.engagement_rate_multiplier
        )

    age_days = _age_days(metadata.taken_at, now)
    views_per_day = views / max(age_days, 0.25)
    velocity_score = min(math.log10(views_per_day + 1.0) / weights.velocity_log_divisor, 1.0)

    raw_score = (
        weights.distribution_weight * distribution_score
        + weights.engagement_weight * engagement_score
        + weights.velocity_weight * velocity_score
    )

    return ScoreComponents(
        distribution_score=distribution_score,
        engagement_score=engagement_score,
        velocity_score=velocity_score,
        weighted_engagement_rate=weighted_engagement_rate,
        raw_score=raw_score,
    )


def _age_days(taken_at: datetime | None, now: datetime) -> float:
    if taken_at is None:
        return 0.25
    reference = taken_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=now.tzinfo)
    return max((now - reference).total_seconds() / 86_400.0, 0.25)


def normalize_score(raw_score: float) -> float:
    """Map an unbounded non-negative raw score into a 0-100 range.

    Uses a smooth saturating curve so heavier engagement keeps increasing the
    score without ever exceeding 100.
    """

    if raw_score <= 0:
        return 0.0
    normalized = 100.0 * (1.0 - math.exp(-raw_score))
    return round(min(max(normalized, 0.0), 100.0), 2)


def compute_viral_score(
    metadata: ContentMetadata,
    now: datetime,
    weights: ScoreWeights,
) -> tuple[float, ScoreComponents]:
    components = compute_score_components(metadata, now, weights)
    return normalize_score(components.raw_score), components


def compute_public_trend_score(
    metrics: dict[str, float | int | None],
    *,
    age_hours: float,
    velocity: float | None = None,
    percentile: float | None = None,
) -> ProductionScore:
    """Missingness-aware public score; absent metrics never become synthetic zeroes."""

    supported = ("views", "reach", "likes", "comments", "shares", "saves")
    available = tuple(key for key in supported if metrics.get(key) is not None)
    values = {key: max(float(metrics[key] or 0), 0.0) for key in available}
    denominator = max(values.get("views", values.get("reach", 1.0)), 1.0)
    engagement = (
        values.get("likes", 0.0)
        + 3.0 * values.get("comments", 0.0)
        + 5.0 * values.get("shares", 0.0)
        + 4.0 * values.get("saves", 0.0)
    ) / denominator
    freshness = 0.5 ** (max(age_hours, 0.0) / (24.0 * 7.0))
    velocity_component = max(velocity or 0.0, 0.0)
    percentile_component = min(max(percentile or 0.0, 0.0), 1.0)
    raw = (
        0.5 * math.log1p(engagement * 100.0)
        + 0.25 * math.log1p(velocity_component)
        + 0.25 * percentile_component
    ) * freshness
    coverage = len(available) / len(supported)
    confidence = min(max(coverage * min(1.0, denominator / 1000.0), 0.0), 1.0)
    return ProductionScore(
        score=normalize_score(raw),
        confidence=round(confidence, 4),
        model_version="public-trend-v2",
        components={
            "engagement": engagement,
            "velocity": velocity_component,
            "percentile": percentile_component,
            "freshness": freshness,
        },
        available_metrics=available,
    )


def compute_performance_residual(
    value: float,
    cohort_values: list[float],
    *,
    available_metrics: int,
    expected_metrics: int,
) -> ProductionScore:
    """Score owned media relative to the creator's comparable cohort."""

    if not cohort_values:
        residual = 0.0
    else:
        mean = sum(cohort_values) / len(cohort_values)
        variance = sum((item - mean) ** 2 for item in cohort_values) / len(cohort_values)
        residual = (value - mean) / max(math.sqrt(variance), 1e-9)
    score = 100.0 / (1.0 + math.exp(-residual))
    sample_confidence = min(len(cohort_values) / 10.0, 1.0)
    coverage = min(available_metrics / max(expected_metrics, 1), 1.0)
    return ProductionScore(
        score=round(score, 2),
        confidence=round(sample_confidence * coverage, 4),
        model_version="creator-cohort-residual-v1",
        components={"z_residual": residual, "cohort_size": float(len(cohort_values))},
        available_metrics=(),
    )


def compute_lifecycle(
    snapshots: list[tuple[float, float]], *, now_age_hours: float
) -> TrendLifecycleSignals:
    """Derive velocity/acceleration from ordered (hours, cumulative metric) samples."""

    ordered = sorted(snapshots)
    if len(ordered) < 2:
        return TrendLifecycleSignals(None, None, None, 0.0, "unknown", 0.0)
    rates = [
        (right[1] - left[1]) / max(right[0] - left[0], 1e-9)
        for left, right in zip(ordered, ordered[1:], strict=False)
    ]
    velocity = rates[-1]
    acceleration = rates[-1] - rates[-2] if len(rates) > 1 else None
    freshness = 0.5 ** (max(now_age_hours, 0.0) / (24.0 * 7.0))
    if velocity <= 0:
        lifecycle = "declining"
    elif acceleration is not None and acceleration > 0:
        lifecycle = "rising"
    elif acceleration is not None and acceleration < 0:
        lifecycle = "saturated"
    else:
        lifecycle = "emerging"
    return TrendLifecycleSignals(
        velocity=velocity,
        acceleration=acceleration,
        percentile=None,
        freshness=freshness,
        lifecycle=lifecycle,
        confidence=min(len(ordered) / 4.0, 1.0),
    )
