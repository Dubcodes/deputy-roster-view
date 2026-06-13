from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from .config import get_settings
from .database import (
    count_app_users,
    get_trusted_device,
    revoke_trusted_device,
    update_app_user_seen,
    update_trusted_device_seen,
)
from .security import SESSION_COOKIE_NAME, hash_session_token, session_expires_at


PUBLIC_PATHS = {
    "/login",
    "/signup",
    "/favicon.ico",
}
PUBLIC_PREFIXES = (
    "/static/",
)


def current_user(request: Request) -> dict[str, object] | None:
    user = getattr(request.state, "current_user", None)
    return dict(user) if user else None


def current_device_id(request: Request) -> int | None:
    device = getattr(request.state, "trusted_device", None)
    if not device:
        return None
    return int(device["id"])


def require_admin_user(request: Request) -> dict[str, object]:
    user = current_user(request)
    if not user or not int(user.get("is_admin") or 0):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def trusted_device_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    path = request.url.path
    request.state.current_user = None
    request.state.trusted_device = None

    if _is_public_path(path):
        return await call_next(request)

    user_count = count_app_users()
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if token:
        device = get_trusted_device(hash_session_token(token))
        if device:
            settings = get_settings()
            expires_at = session_expires_at(settings)
            request.state.trusted_device = device
            request.state.current_user = {
                "id": device["user_id"],
                "display_name": device["display_name"],
                "deputy_email": device["deputy_email"],
                "is_admin": device["is_admin"],
            }
            update_trusted_device_seen(int(device["id"]), expires_at)
            update_app_user_seen(int(device["user_id"]))
            response = await call_next(request)
            response.set_cookie(
                SESSION_COOKIE_NAME,
                token,
                max_age=settings.trusted_device_days * 24 * 60 * 60,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
                path="/",
            )
            return response

    if user_count == 0:
        return RedirectResponse(url=f"/signup?next={quote(str(request.url.path))}", status_code=303)

    return RedirectResponse(url=f"/login?next={quote(str(request.url.path))}", status_code=303)


def clear_trusted_device(request: Request, response: Response) -> None:
    device_id = current_device_id(request)
    if device_id is not None:
        revoke_trusted_device(device_id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)
