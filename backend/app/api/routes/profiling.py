"""User-facing Instagram connection and profiling endpoints."""

from __future__ import annotations

import json
import secrets
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from bson import ObjectId
from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pymongo.errors import DuplicateKeyError

from app.api.dependencies import CurrentUser, resources, settings
from app.api.responses import job_response
from app.core.config import Settings as AppSettings
from app.core.errors import TransientError
from app.core.token_crypto import TokenCipher
from app.infrastructure.resources import utcnow
from app.providers.instagram_profile import InstagramGraphError, build_instagram_profile_provider
from app.providers.media import build_media_provider
from app.schemas.jobs import JobResponse
from app.schemas.profiling import InstagramStatusResponse, OAuthStartResponse
from app.services.erasure import InstagramErasureError, InstagramErasureService
from app.services.instagram_webhook import InstagramWebhookService
from app.tasks import profile_user

router = APIRouter(tags=["instagram", "profiling"])


def _cipher(request: Request) -> TokenCipher:
    return TokenCipher(settings(request).instagram_token_encryption_key.get_secret_value())


@router.post("/instagram/oauth/start", response_model=OAuthStartResponse)
async def start_oauth(request: Request, user: CurrentUser) -> OAuthStartResponse:
    state = secrets.token_urlsafe(32)
    redis = resources(request).redis
    assert redis is not None
    await redis.set(
        f"involo:instagram:oauth:{state}",
        json.dumps({"user_id": str(user["_id"])}),
        ex=settings(request).instagram_oauth_state_ttl_seconds,
    )
    try:
        provider = build_instagram_profile_provider(
            settings(request), redis=resources(request).redis
        )
    except InstagramGraphError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "instagram_oauth_unavailable",
        ) from exc
    return OAuthStartResponse(authorization_url=provider.authorization_url(state))


@router.get("/instagram/oauth/callback", include_in_schema=True)
async def oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    # Meta's Webhooks product can be pointed at the OAuth callback URL.
    query = request.query_params
    webhook_mode = hub_mode or query.get("hub_mode") or query.get("hub.mode")
    webhook_token = (
        hub_verify_token
        or query.get("hub_verify_token")
        or query.get("hub.verify_token")
    )
    webhook_challenge = (
        hub_challenge or query.get("hub_challenge") or query.get("hub.challenge")
    )
    if webhook_mode == "subscribe":
        return _verify_meta_webhook(
            settings(request), webhook_mode, webhook_token, webhook_challenge
        )

    frontend_url = settings(request).instagram_oauth_success_url
    if error:
        return _redirect(frontend_url, "error", error_description or error)
    if not code or not state:
        return _redirect(frontend_url, "error", "OAuth callback is missing code or state")
    redis = resources(request).redis
    assert redis is not None
    raw = await redis.getdel(f"involo:instagram:oauth:{state}")
    if not raw:
        return _redirect(frontend_url, "error", "OAuth state expired or was already used")
    try:
        user_id = ObjectId(json.loads(raw)["user_id"])
        provider = build_instagram_profile_provider(
            settings(request), redis=resources(request).redis
        )
        token = await provider.exchange_code(code)
        account = await provider.fetch_account(token.access_token)
        now = utcnow()
        await resources(request).db.instagram_connections.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "instagram_user_id": account.id,
                    "instagram_username": account.username,
                    "follower_count": account.follower_count,
                    "access_token_encrypted": _cipher(request).encrypt(token.access_token),
                    "token_expires_at": token.expires_at,
                    "status": "connected",
                    "error": None,
                    "updated_at": now,
                },
                "$setOnInsert": {"connected_at": now},
            },
            upsert=True,
        )
        await _queue_profile_job(request, user_id, scheduled=False)
    except DuplicateKeyError:
        return _redirect(frontend_url, "error", "Bu Instagram hesabı başka kullanıcıya bağlı.")
    except (InstagramGraphError, ValueError, KeyError) as exc:
        return _redirect(frontend_url, "error", str(exc))
    except (TransientError, httpx.HTTPError):
        return _redirect(
            frontend_url,
            "error",
            "Instagram geçici olarak yanıt vermiyor, lütfen tekrar deneyin.",
        )
    return _redirect(frontend_url, "connected")


def _verify_meta_webhook(
    settings_obj: AppSettings,
    hub_mode: str | None,
    hub_verify_token: str | None,
    hub_challenge: str | None,
) -> PlainTextResponse:
    expected = settings_obj.instagram_webhook_verify_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "instagram_webhook_not_configured",
        )
    if hub_mode != "subscribe" or hub_verify_token != expected.get_secret_value():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "instagram_webhook_verification_failed",
        )
    return PlainTextResponse(hub_challenge or "", status_code=200)


@router.get("/instagram/webhook")
async def instagram_webhook_verify(
    request: Request,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    query = request.query_params
    return _verify_meta_webhook(
        settings(request),
        hub_mode or query.get("hub_mode") or query.get("hub.mode"),
        hub_verify_token or query.get("hub_verify_token") or query.get("hub.verify_token"),
        hub_challenge or query.get("hub_challenge") or query.get("hub.challenge"),
    )


@router.post("/instagram/webhook")
async def instagram_webhook_event(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    body: bytes = Body(...),
) -> Response:
    service = InstagramWebhookService(settings(request))
    if not service.verify_signature(body, x_hub_signature_256):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_webhook_signature",
        )
    await service.handle_event(resources(request).db, body)
    return Response(status_code=200)


@router.get("/instagram/status", response_model=InstagramStatusResponse)
async def instagram_status(request: Request, user: CurrentUser) -> InstagramStatusResponse:
    connection = await resources(request).db.instagram_connections.find_one(
        {"user_id": user["_id"]}
    )
    if not connection:
        return InstagramStatusResponse(status="disconnected")
    profile = await resources(request).db.user_profiles.find_one({"user_id": user["_id"]})
    return InstagramStatusResponse(
        status=connection.get("status", "connected"),
        instagram_username=connection.get("instagram_username"),
        connected_at=connection.get("connected_at"),
        last_synced_at=(profile or {}).get("last_synced_at") or connection.get("last_synced_at"),
        content_count_analyzed=int((profile or {}).get("content_count_analyzed", 0)),
        ai_profile_summary=(profile or {}).get("ai_profile_summary"),
        vector_std_dev=(profile or {}).get("vector_std_dev"),
        error=connection.get("error"),
        structured_profile=(profile or {}).get("structured_profile"),
    )


@router.delete(
    "/instagram/connection",
    status_code=status.HTTP_204_NO_CONTENT,
    description=(
        "Disconnect Instagram and erase all Instagram-derived business data. "
        "The Involo authentication account is intentionally preserved."
    ),
)
async def disconnect_instagram(request: Request, user: CurrentUser) -> None:
    runtime = resources(request)
    assert runtime.db is not None
    assert runtime.qdrant is not None
    runtime_settings = settings(request)
    try:
        await InstagramErasureService(
            runtime.db,
            runtime.qdrant,
            build_media_provider(runtime_settings)
            if runtime_settings.media_s3_bucket
            else None,
            runtime_settings,
        ).erase(user["_id"])
    except InstagramErasureError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "instagram_disconnect_erasure_unavailable",
        ) from exc


@router.post("/profile/sync", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_profile(request: Request, user: CurrentUser) -> JobResponse:
    connection = await resources(request).db.instagram_connections.find_one(
        {"user_id": user["_id"]}
    )
    if not connection:
        raise HTTPException(status.HTTP_409_CONFLICT, "Instagram account is not connected")
    return await _queue_profile_job(request, user["_id"], scheduled=False)


async def _queue_profile_job(request: Request, user_id: Any, *, scheduled: bool) -> JobResponse:
    task_id = uuid4().hex
    document = {
        "task_id": task_id,
        "kind": "profile_user",
        "state": "queued",
        "counters": {},
        "created_at": utcnow(),
        "user_id": user_id,
        "scheduled": scheduled,
    }
    await resources(request).db.job_runs.insert_one(document)
    await resources(request).db.instagram_connections.update_one(
        {"user_id": user_id},
        {"$set": {"status": "profiling", "error": None, "profiling_queued_at": utcnow()}},
    )
    profile_user.apply_async(args=[str(user_id)], task_id=task_id)
    return job_response(document)


def _redirect(base_url: str, result: str, message: str | None = None) -> RedirectResponse:
    params = {"instagram": result}
    if message:
        params["message"] = message[:300]
    separator = "&" if "?" in base_url else "?"
    return RedirectResponse(f"{base_url}{separator}{urlencode(params)}", status_code=303)
