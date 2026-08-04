"""Cached, non-mutating external provider readiness probes."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.rate_limit import GraphApiRateLimiter
from app.core.token_crypto import TokenCipher, TokenEncryptionError

Probe = dict[str, object]

logger = logging.getLogger(__name__)


class ProviderRegionMismatchError(RuntimeError):
    pass


def probe_result(ok: bool, reason: str, *, required: bool = True) -> Probe:
    return {"ok": ok, "reason": reason, "required": required}


class ProviderReadinessProber:
    def __init__(
        self,
        settings: Settings,
        *,
        graph_limiter: GraphApiRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.graph_limiter = graph_limiter
        self._cached_at = 0.0
        self._cached: dict[str, Probe] | None = None
        self._lock = asyncio.Lock()

    async def checks(self, db: Any | None = None) -> dict[str, Probe]:
        if not self.settings.provider_readiness_probes_enabled:
            return {
                "provider_live_probes": {
                    "ok": True,
                    "reason": "disabled_by_configuration",
                    "required": False,
                }
            }
        now = time.monotonic()
        if (
            self._cached is not None
            and now - self._cached_at
            < self.settings.provider_readiness_cache_ttl_seconds
        ):
            return self._cached
        async with self._lock:
            now = time.monotonic()
            if (
                self._cached is not None
                and now - self._cached_at
                < self.settings.provider_readiness_cache_ttl_seconds
            ):
                return self._cached
            checks = await self._run(db)
            self._cached = checks
            self._cached_at = time.monotonic()
            return checks

    async def _run(self, db: Any | None = None) -> dict[str, Probe]:
        embedding_bucket = self.settings.embedding_media_s3_bucket
        embedding_endpoint = self.settings.embedding_media_s3_endpoint_url
        if (
            not embedding_bucket
            and self.settings.media_s3_region == self.settings.bedrock_embedding_region
        ):
            embedding_bucket = self.settings.media_s3_bucket
            embedding_endpoint = self.settings.transcribe_s3_endpoint_url
        model_stages = {
            "bedrock_embedding": (
                self.settings.bedrock_embedding_region,
                self.settings.bedrock_embedding_model_id,
            ),
            "bedrock_vision": (
                self.settings.bedrock_generation_region,
                self.settings.bedrock_vision_model_id,
            ),
            "bedrock_profile": (
                self.settings.bedrock_generation_region,
                self.settings.bedrock_profile_model_id,
            ),
            "bedrock_recommendation": (
                self.settings.bedrock_generation_region,
                self.settings.bedrock_recommendation_model_id,
            ),
        }
        unique_models = dict.fromkeys(model_stages.values())
        operations: dict[str, Any] = {
            "media_s3": self._probe_s3(
                self.settings.media_s3_bucket,
                self.settings.media_s3_region,
                self.settings.transcribe_s3_endpoint_url,
            ),
            "embedding_media_s3": self._probe_s3(
                embedding_bucket,
                self.settings.embedding_media_s3_region,
                embedding_endpoint,
            ),
            "transcribe_s3": self._probe_s3(
                self.settings.transcribe_s3_bucket,
                self.settings.aws_region,
                self.settings.transcribe_s3_endpoint_url,
            ),
            "meta_token": self._probe_meta("/me", db=db),
            "meta_account": self._probe_meta(
                f"/{self.settings.meta_instagram_business_account_id}"
                if self.settings.meta_instagram_business_account_id
                else None,
                db=db,
            ),
        }
        for index, (region, model_id) in enumerate(unique_models):
            operations[f"_bedrock_{index}"] = self._probe_bedrock(model_id, region)
        names = list(operations)
        results = await asyncio.gather(
            *(self._bounded(operations[name]) for name in names)
        )
        resolved = dict(zip(names, results, strict=True))
        for name, region in (
            ("media_s3", self.settings.media_s3_region),
            ("embedding_media_s3", self.settings.embedding_media_s3_region),
            ("transcribe_s3", self.settings.aws_region),
        ):
            resolved[name] = {**resolved[name], "region": region}
        model_results = {
            model: resolved[f"_bedrock_{index}"]
            for index, model in enumerate(unique_models)
        }
        checks = {
            name: result
            for name, result in resolved.items()
            if not name.startswith("_bedrock_")
        }
        checks.update(
            {
                stage: {**model_results[model], "region": model[0]}
                for stage, model in model_stages.items()
            }
        )
        return checks

    async def _bounded(self, operation: Any) -> Probe:
        try:
            return await asyncio.wait_for(
                operation,
                timeout=self.settings.provider_readiness_timeout_seconds,
            )
        except TimeoutError:
            return probe_result(False, "timeout")
        except Exception as exc:  # noqa: BLE001
            return probe_result(False, _reason_code(exc))

    async def _probe_s3(
        self, bucket: str | None, expected_region: str, endpoint_url: str | None
    ) -> Probe:
        if not bucket:
            return probe_result(False, "not_configured")
        await asyncio.to_thread(
            self._head_bucket, bucket, expected_region, endpoint_url
        )
        return probe_result(True, "ok")

    def _head_bucket(
        self, bucket: str, expected_region: str, endpoint_url: str | None
    ) -> None:
        import boto3
        from botocore.config import Config  # type: ignore[import-untyped]

        options: dict[str, Any] = {
            "region_name": expected_region,
            "endpoint_url": endpoint_url,
            "config": Config(
                connect_timeout=self.settings.provider_readiness_timeout_seconds,
                read_timeout=self.settings.provider_readiness_timeout_seconds,
                retries={"max_attempts": 1, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        }
        if endpoint_url and self.settings.transcribe_s3_access_key_id:
            options["aws_access_key_id"] = self.settings.transcribe_s3_access_key_id
        if endpoint_url and self.settings.transcribe_s3_secret_access_key:
            options["aws_secret_access_key"] = (
                self.settings.transcribe_s3_secret_access_key.get_secret_value()
            )
        client = boto3.client("s3", **options)
        client.head_bucket(Bucket=bucket)
        if endpoint_url is None:
            location = client.get_bucket_location(Bucket=bucket).get(
                "LocationConstraint"
            )
            actual_region = str(location or "us-east-1")
            if actual_region != expected_region:
                raise ProviderRegionMismatchError

    async def _probe_bedrock(self, model_id: str, region: str) -> Probe:
        if not model_id:
            return probe_result(False, "not_configured")
        await asyncio.to_thread(self._bedrock_metadata, model_id, region)
        return probe_result(True, "ok")

    def _bedrock_metadata(self, model_id: str, region: str) -> None:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "bedrock",
            region_name=region,
            config=Config(
                connect_timeout=self.settings.provider_readiness_timeout_seconds,
                read_timeout=self.settings.provider_readiness_timeout_seconds,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )
        kind, identifier = _bedrock_identifier(model_id)
        if kind == "inference_profile":
            client.get_inference_profile(inferenceProfileIdentifier=identifier)
        else:
            client.get_foundation_model(modelIdentifier=identifier)

    async def _probe_meta(self, path: str | None, db: Any | None = None) -> Probe:
        # Meta trend tokens are only required in production; staging can boot without
        # a live token while still verifying app credentials are configured.
        meta_required = self.settings.environment == "production"
        app_id = self.settings.effective_facebook_app_id
        app_secret = self.settings.effective_facebook_app_secret
        if not (path and app_id and app_secret):
            return probe_result(False, "not_configured", required=meta_required)
        token = await self._meta_access_token(db)
        if not token:
            return probe_result(False, "not_configured", required=meta_required)
        url = (
            f"https://graph.facebook.com/{self.settings.instagram_graph_api_version}"
            f"{path}"
        )
        if self.graph_limiter is not None:
            try:
                await self.graph_limiter.acquire()
            except Exception:
                return probe_result(False, "throttled", required=meta_required)
        async with httpx.AsyncClient(
            timeout=self.settings.provider_readiness_timeout_seconds
        ) as client:
            response = await client.get(
                url,
                params={"fields": "id", "access_token": token},
            )
        if response.is_success:
            return probe_result(True, "ok", required=meta_required)
        return probe_result(False, _http_reason(response.status_code), required=meta_required)

    async def _meta_access_token(self, db: Any | None = None) -> str | None:
        """Prefer the encrypted managed token, but fall back to settings."""
        if db is not None and self.settings.instagram_token_encryption_key:
            try:
                cipher = TokenCipher(
                    self.settings.instagram_token_encryption_key.get_secret_value()
                )
                doc = await db.meta_access_tokens.find_one({"_id": "trend"})
                if doc and doc.get("access_token_encrypted"):
                    return cipher.decrypt(str(doc["access_token_encrypted"]))
            except TokenEncryptionError:
                pass
            except Exception:
                logger.warning("Could not decrypt managed Meta token for readiness", exc_info=True)
        if self.settings.meta_trend_access_token:
            return self.settings.meta_trend_access_token.get_secret_value()
        return None


def _bedrock_identifier(model_id: str) -> tuple[str, str]:
    if "inference-profile/" in model_id or "application-inference-profile/" in model_id:
        return "inference_profile", model_id
    prefix = model_id.split(".", maxsplit=1)[0]
    if prefix in {"us", "eu", "apac", "global"}:
        return "inference_profile", model_id
    if "foundation-model/" in model_id:
        return "foundation_model", model_id.rsplit("foundation-model/", maxsplit=1)[1]
    return "foundation_model", model_id


def _http_reason(status_code: int) -> str:
    if status_code in {401, 403}:
        return "access_denied"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "throttled"
    return "provider_unavailable"


def _reason_code(exc: Exception) -> str:
    if isinstance(exc, ProviderRegionMismatchError):
        return "region_mismatch"
    response = getattr(exc, "response", {})
    code = str((response.get("Error") or {}).get("Code", "")).lower()
    if any(value in code for value in ("credential", "unauthorized", "accessdenied")):
        return "access_denied"
    if any(value in code for value in ("notfound", "nosuch", "validation")):
        return "not_found"
    if any(value in code for value in ("throttl", "limitexceeded", "slowdown")):
        return "throttled"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "provider_unavailable"
    return "provider_error"
