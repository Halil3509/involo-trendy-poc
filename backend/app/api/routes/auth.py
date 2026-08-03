from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.api.dependencies import CurrentUser, resources, settings
from app.core.rate_limit import auth_rate_limit
from app.core.security import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    set_auth_cookies,
    verify_password,
)
from app.infrastructure.resources import utcnow
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def user_response(document: dict[str, Any]) -> UserResponse:
    return UserResponse(
        id=str(document["_id"]),
        email=document["email"],
        role=document["role"],
        created_at=document["created_at"],
    )


async def issue_session(
    response: Response,
    user: dict[str, Any],
    request: Request,
    *,
    family_id: str | None = None,
) -> None:
    cfg = settings(request)
    raw_refresh, token_hash = new_refresh_token()
    now = utcnow()
    await resources(request).db.auth_sessions.insert_one(
        {
            "user_id": user["_id"],
            "token_hash": token_hash,
            "family_id": family_id or token_hash,
            "created_at": now,
            "expires_at": now + timedelta(days=cfg.refresh_token_days),
            "revoked_at": None,
        }
    )
    set_auth_cookies(
        response,
        create_access_token(str(user["_id"]), user["role"], cfg),
        raw_refresh,
        cfg,
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit)],
)
async def register(payload: RegisterRequest, response: Response, request: Request) -> AuthResponse:
    now = utcnow()
    user = {
        "email": str(payload.email).lower(),
        "password_hash": hash_password(payload.password),
        "role": "user",
        "created_at": now,
        "updated_at": now,
        "disabled": False,
    }
    try:
        result = await resources(request).db.users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    user["_id"] = result.inserted_id
    await issue_session(response, user, request)
    return AuthResponse(user=user_response(user))


@router.post(
    "/login", response_model=AuthResponse, dependencies=[Depends(auth_rate_limit)]
)
async def login(payload: LoginRequest, response: Response, request: Request) -> AuthResponse:
    user = await resources(request).db.users.find_one({"email": str(payload.email).lower()})
    if not user or not verify_password(user["password_hash"], payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if user.get("disabled"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    await issue_session(response, user, request)
    return AuthResponse(user=user_response(user))


@router.post(
    "/refresh", response_model=AuthResponse, dependencies=[Depends(auth_rate_limit)]
)
async def refresh(
    response: Response,
    request: Request,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> AuthResponse:
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token required")
    now = utcnow()
    old = await resources(request).db.auth_sessions.find_one_and_update(
        {
            "token_hash": hash_refresh_token(refresh_token),
            "revoked_at": None,
            "expires_at": {"$gt": now},
        },
        {"$set": {"revoked_at": now, "revoke_reason": "rotated"}},
        return_document=ReturnDocument.BEFORE,
    )
    if not old:
        clear_auth_cookies(response, settings(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = await resources(request).db.users.find_one(
        {"_id": old["user_id"], "disabled": {"$ne": True}}
    )
    if not user:
        clear_auth_cookies(response, settings(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    await issue_session(response, user, request, family_id=old["family_id"])
    return AuthResponse(user=user_response(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    request: Request,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> None:
    if refresh_token:
        await resources(request).db.auth_sessions.update_one(
            {"token_hash": hash_refresh_token(refresh_token), "revoked_at": None},
            {"$set": {"revoked_at": utcnow(), "revoke_reason": "logout"}},
        )
    clear_auth_cookies(response, settings(request))


@router.get("/me", response_model=AuthResponse)
async def me(user: CurrentUser) -> AuthResponse:
    return AuthResponse(user=user_response(user))
