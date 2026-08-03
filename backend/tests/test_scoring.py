from datetime import UTC, datetime, timedelta

from app.schemas.trends import ContentMetadata
from app.services.scoring import ScoreWeights, compute_viral_score, normalize_score

NOW = datetime(2026, 7, 16, tzinfo=UTC)
WEIGHTS = ScoreWeights()


def _metadata(**overrides: object) -> ContentMetadata:
    base = {
        "shortcode": "X",
        "like_count": 1000,
        "comment_count": 50,
        "view_count": 20000,
        "share_count": 10,
        "owner_follower_count": 5000,
        "taken_at": NOW - timedelta(days=1),
    }
    base.update(overrides)
    return ContentMetadata(**base)  # type: ignore[arg-type]


def test_score_is_bounded_between_0_and_100() -> None:
    huge = _metadata(like_count=10_000_000, comment_count=1_000_000, view_count=1)
    score, _ = compute_viral_score(huge, NOW, WEIGHTS)
    assert 0.0 <= score <= 100.0


def test_zero_metrics_scores_zero() -> None:
    empty = _metadata(like_count=0, comment_count=0, view_count=0, share_count=0)
    score, _ = compute_viral_score(empty, NOW, WEIGHTS)
    assert score == 0.0


def test_public_engagement_without_views_has_a_score() -> None:
    public = _metadata(
        like_count=1200,
        comment_count=30,
        share_count=20,
        view_count=0,
        owner_follower_count=0,
    )
    score, components = compute_viral_score(public, NOW, WEIGHTS)
    assert score > 0.0
    assert components.distribution_score == 0.0
    assert components.engagement_score > 0.9


def test_missing_view_count_differentiates_by_engagement() -> None:
    low = _metadata(like_count=10, view_count=0, owner_follower_count=0)
    high = _metadata(like_count=10_000, view_count=0, owner_follower_count=0)
    low_score, _ = compute_viral_score(low, NOW, WEIGHTS)
    high_score, _ = compute_viral_score(high, NOW, WEIGHTS)
    assert high_score > low_score


def test_more_likes_increases_score() -> None:
    low, _ = compute_viral_score(_metadata(like_count=500), NOW, WEIGHTS)
    high, _ = compute_viral_score(_metadata(like_count=5000), NOW, WEIGHTS)
    assert high > low


def test_distribution_score_favors_wider_distribution_relative_to_followers() -> None:
    """Same reach/engagement from a smaller follower base signals wider distribution."""
    large = _metadata(
        like_count=1000,
        comment_count=50,
        share_count=10,
        view_count=50000,
        owner_follower_count=2000000,
    )
    small = _metadata(
        like_count=1000,
        comment_count=50,
        share_count=10,
        view_count=50000,
        owner_follower_count=20000,
    )
    large_score, large_components = compute_viral_score(large, NOW, WEIGHTS)
    small_score, small_components = compute_viral_score(small, NOW, WEIGHTS)
    assert small_components.distribution_score > large_components.distribution_score
    assert small_score > large_score


def test_velocity_score_rewards_faster_accumulation() -> None:
    fresh, comp_fresh = compute_viral_score(
        _metadata(taken_at=NOW - timedelta(days=1)), NOW, WEIGHTS
    )
    old, comp_old = compute_viral_score(_metadata(taken_at=NOW - timedelta(days=120)), NOW, WEIGHTS)
    assert comp_fresh.velocity_score > comp_old.velocity_score
    assert fresh > old


def test_higher_views_per_day_increases_score() -> None:
    slow, _ = compute_viral_score(
        _metadata(view_count=10000, taken_at=NOW - timedelta(days=10)), NOW, WEIGHTS
    )
    fast, _ = compute_viral_score(
        _metadata(view_count=10000, taken_at=NOW - timedelta(days=1)), NOW, WEIGHTS
    )
    assert fast > slow


def test_normalize_score_monotonic_and_capped() -> None:
    assert normalize_score(0) == 0.0
    assert normalize_score(0.5) < normalize_score(2.0)
    assert normalize_score(10_000) <= 100.0


def test_no_division_by_zero_when_followers_zero() -> None:
    score, _ = compute_viral_score(_metadata(owner_follower_count=0), NOW, WEIGHTS)
    assert 0.0 <= score <= 100.0
