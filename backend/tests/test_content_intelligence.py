import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from fakes import FakeDatabase
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.media import Keyframe, StoredMedia
from app.providers.vision import NovaVisionProvider
from app.schemas.intelligence import CreatorPreferences, VisualAnalysis
from app.schemas.recommendations import RecommendationCard
from app.services.intelligence import PreferencesService
from app.services.multimodal import fuse_vectors
from app.services.scoring import (
    compute_lifecycle,
    compute_performance_residual,
    compute_public_trend_score,
)
from app.services.snapshots import SnapshotService


def test_runtime_settings_have_no_test_double_options() -> None:
    # fixture is a supported runtime adapter for local development
    assert Settings.model_fields["scraper_adapter"].annotation is not None
    removed = {
        "transcript_fixture_path",
        "instagram_media_fixture_path",
        "instagram_profile_provider",
        "profile_summary_provider",
        "recommendation_provider",
        "weighted_vector_floor",
    }
    assert removed.isdisjoint(Settings.model_fields)


def test_public_score_preserves_missingness_and_confidence() -> None:
    sparse = compute_public_trend_score({"views": 5000, "likes": 100}, age_hours=12)
    rich = compute_public_trend_score(
        {
            "views": 5000,
            "reach": 4200,
            "likes": 100,
            "comments": 20,
            "shares": 15,
            "saves": 30,
        },
        age_hours=12,
        velocity=20,
        percentile=0.8,
    )

    assert sparse.available_metrics == ("views", "likes")
    assert rich.confidence > sparse.confidence
    assert rich.model_version == "public-trend-v2"


def test_lifecycle_and_creator_residual_are_bounded() -> None:
    signals = compute_lifecycle([(6, 100), (24, 1000), (48, 2500)], now_age_hours=48)
    residual = compute_performance_residual(
        30, [10, 15, 20, 25], available_metrics=8, expected_metrics=9
    )

    assert signals.lifecycle == "rising"
    assert signals.velocity is not None
    assert 0 <= residual.score <= 100
    assert 0 <= residual.confidence <= 1


def test_multimodal_fusion_normalizes_and_falls_back() -> None:
    fused = fuse_vectors([1.0, 0.0], [0.0, 1.0], text_weight=0.5, media_weight=0.5)
    text_only = fuse_vectors([3.0, 4.0], None, text_weight=0.5, media_weight=0.5)

    assert pytest.approx(sum(value * value for value in fused), rel=1e-6) == 1.0
    assert text_only == pytest.approx([0.6, 0.8])


def test_nova_vision_builds_valid_s3_content_blocks_with_owner() -> None:
    provider = NovaVisionProvider(Settings(media_s3_bucket_owner="123456789012"))
    video = StoredMedia("media", "videos/a.mp4", "s3://media/videos/a.mp4", "video/mp4", "a", 1)
    image = StoredMedia(
        "media", "frames/a.jpg", "s3://media/frames/a.jpg", "image/jpeg", "b", 1
    )

    assert provider._video_block(video) == {
        "video": {
            "format": "mp4",
            "source": {
                "s3Location": {
                    "uri": "s3://media/videos/a.mp4",
                    "bucketOwner": "123456789012",
                }
            },
        }
    }
    assert provider._image_block(image)["image"]["format"] == "jpeg"


def test_nova_vision_sanitizes_invalid_visual_analysis_payload() -> None:
    provider = NovaVisionProvider(Settings())
    raw = {
        "opening_frame": "x" * 2000,
        "hook_timing_seconds": -5,
        "ocr_text": ["too", "many"] * 100,
        "faces": ["a"] * 100,
        "objects": ["b"] * 200,
        "shot_changes": list(range(1000)),
        "pacing": "invalid",
        "overlay_style": "y" * 1000,
        "visual_signature": ["c"] * 100,
        "safety_notes": ["d"] * 100,
        "originality_notes": ["e"] * 100,
        "confidence": 1.5,
        "untrusted_extra": "ignored",
    }
    sanitized = provider._sanitize_payload(raw)
    analysis = VisualAnalysis.model_validate(sanitized)
    assert len(analysis.opening_frame) == 1000
    assert analysis.hook_timing_seconds == 0.0
    assert len(analysis.ocr_text) == 100
    assert analysis.pacing == "unknown"
    assert analysis.confidence == 1.0
    assert len(analysis.shot_changes) == 500
    assert "untrusted_extra" not in sanitized


def test_nova_vision_payload_defaults_missing_fields() -> None:
    provider = NovaVisionProvider(Settings())
    sanitized = provider._sanitize_payload({})
    analysis = VisualAnalysis.model_validate(sanitized)
    assert analysis.opening_frame == ""
    assert analysis.confidence == 0.0
    assert analysis.pacing == "unknown"


def test_structured_provider_and_recommendation_contracts_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        VisualAnalysis.model_validate(
            {
                "opening_frame": "Creator at desk",
                "confidence": 0.8,
                "untrusted_extra": "not accepted",
            }
        )
    card = RecommendationCard(
        title="Behind the scenes",
        hook="Show the surprising result first",
        cta="Save this checklist",
        content_format="reels",
        reasoning="Matches the creator's strongest production pillar.",
        evidence_ids=["trend-point-1"],
        first_frame="Close-up of the finished result",
        hook_0_3s="This took three failed attempts.",
    )
    assert card.evidence_ids == ["trend-point-1"]


def test_nova_vision_extracts_json_payload_from_text() -> None:
    provider = NovaVisionProvider(Settings())
    assert provider._extract_json_payload('{"opening_frame":"cat","confidence":0.9}') == {
        "opening_frame": "cat",
        "confidence": 0.9,
    }
    assert provider._extract_json_payload(
        '```json\n{"opening_frame":"cat","confidence":0.9}\n```'
    ) == {"opening_frame": "cat", "confidence": 0.9}
    assert provider._extract_json_payload(
        'Some words {"opening_frame":"cat","confidence":0.9} extra'
    ) == {"opening_frame": "cat", "confidence": 0.9}
    assert provider._extract_json_payload('not json') is None
    assert provider._extract_json_payload('{}') == {}


def test_nova_vision_repairs_truncated_json_responses() -> None:
    provider = NovaVisionProvider(Settings())

    # Truncated inside a string value (Nova hit max tokens mid-output).
    truncated_string = (
        '{"opening_frame":"Summer isn\\u2019t kind to blemish-prone skin '
        '\\ud83c\\udf1e\\n\\nVinopure Purifying Toner Spray available s'
    )
    result = provider._extract_json_payload(truncated_string)
    assert result is not None
    assert "Vinopure" in result["opening_frame"]

    # Truncated inside an array of repeated OCR tokens.
    truncated_array = (
        '{"opening_frame":"A bottle of Caudalie toner is shown in water.",'
        '"hook_timing_seconds":1.4,"ocr_text":["CAUDALIE","CAUDALIE","'
    )
    result = provider._extract_json_payload(truncated_array)
    assert result is not None
    assert "CAUDALIE" in result["ocr_text"]
    assert result["hook_timing_seconds"] == 1.4

    # Text before the JSON object plus trailing noise.
    assert provider._extract_json_payload(
        'Here is the analysis: {"opening_frame":"bottle","confidence":0.9} thanks'
    ) == {"opening_frame": "bottle", "confidence": 0.9}

    # Exact truncated payload from the production error log (unclosed string in
    # the ocr_text array with Unicode escapes).
    production_truncated = (
        '{"opening_frame":"A man with curly hair and a beard is speaking in a room '
        'with guitars and a bookshelf in the background.","hook_timing_seconds":145.6,'
        '"ocr_text":["var benim bi\\u00e7ok","eminim sizin de","\\u00e7ok ko\\u015fuluyo",'
        '"benim sizin de","\\u00e7ok ko\\u015fuluyo","var benim bi\\u00e7ok",'
        '"eminim sizin de","\\u00e7ok ko\\u015fuluyo","var benim bi\\u00e7ok",'
        '"eminim sizin de","\\u00e7ok ko\\u015fuluyo","var benim bi\\u00e7ok",'
        '"eminim sizin de","\\u00e7ok ko\\u015fuluyo","var benim bi\\u00e7ok","eminim '
    )
    result = provider._extract_json_payload(production_truncated)
    assert result is not None
    assert result["opening_frame"].startswith("A man with curly hair")
    assert "eminim" in result["ocr_text"][-1]

    # Nova emitted a stray closing brace before the array was closed.
    stray_close = '{"opening_frame":"bottle","ocr_text":["one","two"}'
    result = provider._extract_json_payload(stray_close)
    assert result is not None
    assert "one" in result["ocr_text"]
    assert "two" in result["ocr_text"]

    # Missing closing brace after a complete payload.
    missing_close = '{"opening_frame":"bottle","confidence":0.9'
    result = provider._extract_json_payload(missing_close)
    assert result == {"opening_frame": "bottle", "confidence": 0.9}

    # Bare key at the end is completed with a null value.
    bare_key = '{"opening_frame":"bottle","hook_timing_seconds"'
    result = provider._extract_json_payload(bare_key)
    assert result is not None
    assert result["opening_frame"] == "bottle"
    assert result["hook_timing_seconds"] is None


@pytest.mark.asyncio
async def test_nova_vision_analyze_parses_text_response() -> None:
    provider = NovaVisionProvider(Settings())
    video = StoredMedia(
        bucket="aws",
        key="videos/a.mp4",
        uri="s3://media/videos/a.mp4",
        content_type="video/mp4",
        sha256="content",
        size_bytes=1,
    )
    frame = StoredMedia(
        bucket="aws",
        key="frames/a.jpg",
        uri="s3://media/frames/a.jpg",
        content_type="image/jpeg",
        sha256="frame",
        size_bytes=1,
    )
    payload = {"opening_frame": "A creator at a desk", "confidence": 0.85}
    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "output": {"message": {"content": [{"text": json.dumps(payload)}]}}
    }

    with patch("boto3.client", return_value=mock_client):
        result = await provider.analyze(
            video, [Keyframe(offset_seconds=0.0, media=frame)], caption="test"
        )

    assert result.opening_frame == "A creator at a desk"
    assert result.confidence == 0.85
    assert result.pacing == "unknown"


@pytest.mark.asyncio
async def test_preferences_are_normalized_and_user_isolated() -> None:
    db = FakeDatabase()
    first, second = ObjectId(), ObjectId()
    service = PreferencesService(db)
    preferences = CreatorPreferences(
        target_countries=[" TR ", "TR"],
        content_languages=["tr"],
        timezone="Europe/Istanbul",
        niches=["food"],
        goals=["saves"],
    )

    stored = await service.put(first, preferences)
    other = await service.get(second)

    assert stored["target_countries"] == ["TR"]
    assert other["target_countries"] == []
    assert isinstance(stored["updated_at"], datetime)
    assert stored["updated_at"].tzinfo == UTC


@pytest.mark.asyncio
async def test_snapshots_capture_only_crossed_due_window_and_do_not_overwrite() -> None:
    db = FakeDatabase()
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    db.trend_content.docs.extend(
        [
            {
                "_id": 1,
                "taken_at": now - timedelta(hours=6.2),
                "metrics": {"view_count": 100},
            },
            {
                "_id": 2,
                "taken_at": now - timedelta(hours=10),
                "metrics": {"view_count": 200},
            },
            {
                "_id": 3,
                "taken_at": now - timedelta(hours=24.4),
                "metrics": {"view_count": 300},
            },
        ]
    )
    service = SnapshotService(db)

    first = await service.capture_due([6, 24, 48, 72], now=now)
    db.trend_content.docs[0]["metrics"]["view_count"] = 999
    second = await service.capture_due([6, 24, 48, 72], now=now + timedelta(minutes=5))

    assert first == {"processed": 3, "captured": 2, "not_due": 1, "existing": 0}
    assert second["captured"] == 0
    assert second["existing"] == 2
    snapshot_keys = {
        (item["subject_id"], item["offset_hours"])
        for item in db.content_metric_snapshots.docs
    }
    assert snapshot_keys == {
        ("1", 6),
        ("3", 24),
    }
    first_snapshot = next(
        item for item in db.content_metric_snapshots.docs if item["subject_id"] == "1"
    )
    assert first_snapshot["metrics"]["views"] == 100
