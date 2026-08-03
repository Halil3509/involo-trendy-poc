from typing import Annotated, Any, cast

import jwt
from bson import ObjectId
from fastapi import Cookie, Depends, HTTPException, Request, WebSocket, status

from app.core.config import Settings
from app.core.security import ACCESS_COOKIE, decode_access_token


def resources(request: Request) -> Any:
    return request.app.state.resources


def settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def require_user(
    request: Request,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> dict[str, Any]:
    if not access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        payload = decode_access_token(access_token, settings(request))
        user_id = ObjectId(payload["sub"])
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token") from None
    resource_set = resources(request)
    assert resource_set.db is not None
    user = await resource_set.db.users.find_one(
        {"_id": user_id, "disabled": {"$ne": True}}
    )
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return cast(dict[str, Any], user)


async def require_admin(
    user: Annotated[dict[str, Any], Depends(require_user)],
) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


async def authorize_admin_websocket(websocket: WebSocket) -> bool:
    """Validate the access cookie for an admin WebSocket connection."""
    settings = cast(Settings, websocket.app.state.settings)
    resources = websocket.app.state.resources
    token = websocket.cookies.get(ACCESS_COOKIE)
    if not token:
        return False
    try:
        payload = decode_access_token(token, settings)
        user_id = ObjectId(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return False
    assert resources.db is not None
    user = await resources.db.users.find_one(
        {"_id": user_id, "disabled": {"$ne": True}}
    )
    return bool(user and user.get("role") == "admin")


CurrentUser = Annotated[dict[str, Any], Depends(require_user)]
AdminUser = Annotated[dict[str, Any], Depends(require_admin)]
