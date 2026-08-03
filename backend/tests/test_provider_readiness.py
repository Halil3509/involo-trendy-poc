from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.application import ready
from app.core.config import Settings
from app.infrastructure.provider_readiness import (
    ProviderReadinessProber,
    ProviderRegionMismatchError,
    _bedrock_identifier,
    _reason_code,
    probe_result,
)


@pytest.mark.asyncio
async def test_provider_probes_can_be_disabled_outside_production() -> None:
    prober = ProviderReadinessProber(
        Settings(provider_readiness_probes_enabled=False)
    )

    checks = await prober.checks()

    assert checks == {
        "provider_live_probes": {
            "ok": True,
            "reason": "disabled_by_configuration",
            "required": False,
        }
    }


@pytest.mark.asyncio
async def test_provider_probes_are_cached_and_bedrock_models_are_deduplicated() -> None:
    prober = ProviderReadinessProber(
        Settings(
            media_s3_bucket="media",
            embedding_media_s3_bucket="embedding-media",
            transcribe_s3_bucket="transcribe",
            instagram_app_id="app",
            instagram_app_secret="secret",
            meta_trend_access_token="token",
            meta_instagram_business_account_id="account",
            provider_readiness_cache_ttl_seconds=60,
        )
    )
    prober._probe_s3 = AsyncMock(return_value=probe_result(True, "ok"))
    prober._probe_meta = AsyncMock(return_value=probe_result(True, "ok"))
    prober._probe_bedrock = AsyncMock(return_value=probe_result(True, "ok"))

    first = await prober.checks()
    second = await prober.checks()

    assert first is second
    assert prober._probe_s3.await_count == 3
    assert prober._probe_meta.await_count == 2
    assert prober._probe_bedrock.await_count == 2
    assert {
        call.args for call in prober._probe_bedrock.await_args_list
    } == {
        ("amazon.nova-2-multimodal-embeddings-v1:0", "us-east-1"),
        ("us.amazon.nova-pro-v1:0", "us-east-1"),
    }
    assert all(check["ok"] for check in first.values())
    assert first["bedrock_embedding"]["region"] == "us-east-1"
    assert first["bedrock_vision"]["region"] == "us-east-1"
    assert first["embedding_media_s3"]["region"] == "us-east-1"


def test_bedrock_identifiers_normalize_foundation_and_inference_profiles() -> None:
    assert _bedrock_identifier("amazon.nova-pro-v1:0") == (
        "foundation_model",
        "amazon.nova-pro-v1:0",
    )
    assert _bedrock_identifier("us.amazon.nova-pro-v1:0") == (
        "inference_profile",
        "us.amazon.nova-pro-v1:0",
    )
    assert _bedrock_identifier(
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    ) == ("foundation_model", "amazon.nova-pro-v1:0")


def test_provider_failure_reasons_are_sanitized() -> None:
    error = SimpleNamespace(
        response={"Error": {"Code": "AccessDeniedException", "Message": "secret body"}}
    )

    assert _reason_code(error) == "access_denied"
    assert _reason_code(ProviderRegionMismatchError()) == "region_mismatch"


def test_production_rejects_disabled_live_provider_probes() -> None:
    with pytest.raises(ValidationError, match="live provider readiness"):
        Settings(
            environment="production",
            provider_readiness_probes_enabled=False,
            jwt_secret="x" * 32,
            instagram_token_encryption_key="y" * 32,
            instagram_app_id="app",
            instagram_app_secret="secret",
        )


@pytest.mark.asyncio
async def test_ready_returns_503_for_required_probe_failure() -> None:
    resources = SimpleNamespace(
        ready=AsyncMock(
            return_value={
                "mongo": probe_result(True, "ok"),
                "bedrock_embedding": probe_result(False, "access_denied"),
            }
        )
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(resources=resources)))

    with pytest.raises(HTTPException) as excinfo:
        await ready(request)

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["checks"]["bedrock_embedding"]["reason"] == "access_denied"
