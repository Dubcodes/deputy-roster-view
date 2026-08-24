from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
import hmac
import ipaddress
import socket
from urllib.parse import quote, urlsplit
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from .config import get_settings
from .database import (
    count_app_users,
    get_trusted_device,
    revoke_trusted_device,
    update_app_user_seen,
    update_trusted_device_seen,
    get_connection,
)
from .security import SESSION_COOKIE_NAME, hash_session_token, session_expires_at


PUBLIC_PATHS = {
    "/login",
    "/signup",
    "/favicon.ico",
    "/manifest.webmanifest",
    "/service-worker.js",
}
PUBLIC_PREFIXES = (
    "/static/",
    "/contractor/invite/",
    "/account/invite/",
)

CONTRACTOR_ALLOWED_PREFIXES = (
    "/contractor",
    "/month",
    "/day/",
    "/timesheet/",
    "/shift/",
    "/track-map/",
    "/settings",
    "/help",
    "/sync-now",
    "/sync-status",
    "/logout",
    "/static/",
    "/manifest.webmanifest",
    "/service-worker.js",
)


def normalized_origin(value: str) -> tuple[str, str, int] | None:
    """Return a strict browser origin (scheme, host, effective port)."""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def _trusted_proxy_peer(request: Request) -> bool:
    peer = str(request.client.host if request.client else "").strip()
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for source in get_settings().trusted_proxy_sources:
        try:
            if peer_ip in ipaddress.ip_network(source, strict=False):
                return True
            continue
        except ValueError:
            pass
        try:
            resolved = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(source, None, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            resolved = set()
        if peer_ip in resolved:
            return True
    return False


def _trusted_external_origin(request: Request) -> tuple[str, str, int] | None:
    if not _trusted_proxy_peer(request):
        return None
    proto = request.headers.get("x-forwarded-proto", "").strip().lower()
    host = request.headers.get("x-forwarded-host", "").strip() or request.headers.get("host", "").strip()
    forwarded_port = request.headers.get("x-forwarded-port", "").strip()
    if not proto or not host or "," in proto or "," in host or "," in forwarded_port:
        return None
    if forwarded_port:
        if not forwarded_port.isdigit() or ":" in host.rsplit("]", 1)[-1]:
            return None
        host = f"{host}:{forwarded_port}"
    return normalized_origin(f"{proto}://{host}")


def request_origin(request: Request) -> tuple[str, str, int] | None:
    external = _trusted_external_origin(request)
    if external is not None:
        return external
    scheme = str(request.url.scheme or "").lower()
    host = str(request.url.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        return None
    try:
        port = request.url.port
    except ValueError:
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


def is_same_origin(request: Request, value: str) -> bool:
    return normalized_origin(value) == request_origin(request)

LOGIN_FAILURE_LIMIT = 5
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_BLOCK = timedelta(minutes=15)


def _login_account_key(email: str) -> str:
    secret = get_settings().app_secret_key.encode("utf-8")
    return hmac.new(secret, email.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()


def login_is_throttled(email: str, *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(get_settings().timezone)
    key = _login_account_key(email)
    with get_connection() as conn:
        conn.execute("DELETE FROM login_throttle WHERE updated_at < ?", ((moment - timedelta(days=1)).isoformat(),))
        row = conn.execute("SELECT blocked_until FROM login_throttle WHERE account_key=?", (key,)).fetchone()
    if row is None or not row["blocked_until"]:
        return False
    return datetime.fromisoformat(str(row["blocked_until"])) > moment


def record_login_failure(email: str, *, now: datetime | None = None) -> None:
    moment = now or datetime.now(get_settings().timezone)
    key = _login_account_key(email)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM login_throttle WHERE account_key=?", (key,)).fetchone()
        if row is None or datetime.fromisoformat(str(row["first_failed_at"])) <= moment - LOGIN_WINDOW:
            failures, first = 1, moment
        else:
            failures, first = int(row["failures"]) + 1, datetime.fromisoformat(str(row["first_failed_at"]))
        blocked = (moment + LOGIN_BLOCK).isoformat() if failures >= LOGIN_FAILURE_LIMIT else None
        conn.execute("""INSERT INTO login_throttle(account_key,failures,first_failed_at,blocked_until,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(account_key) DO UPDATE SET failures=excluded.failures,first_failed_at=excluded.first_failed_at,
            blocked_until=excluded.blocked_until,updated_at=excluded.updated_at""",
            (key, failures, first.isoformat(), blocked, moment.isoformat()))


def clear_login_failures(email: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM login_throttle WHERE account_key=?", (_login_account_key(email),))


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
                "display_theme": device["display_theme"],
                "deputy_email": device["deputy_email"],
                "is_admin": device["is_admin"],
                "account_type": device["account_type"],
                "contractor_person_id": device["contractor_person_id"],
                "last_sync_at": device["last_sync_at"],
                "last_sync_status": device["last_status"],
                "last_sync_message": device["last_message"],
                "sync_in_progress": device["sync_in_progress"],
                "has_deputy_credentials": bool(device["has_deputy_credentials"]),
            }
            _add_sync_notice(request.state.current_user)
            update_trusted_device_seen(int(device["id"]), expires_at)
            update_app_user_seen(int(device["user_id"]))
            if path.startswith("/admin") and not int(device["is_admin"] or 0):
                return Response("Admin access required", status_code=403)
            if str(device["account_type"] or "user") == "contractor" and not any(path.startswith(prefix) for prefix in CONTRACTOR_ALLOWED_PREFIXES):
                return RedirectResponse(url="/contractor", status_code=303)
            if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                if request.headers.get("sec-fetch-site", "same-origin") == "cross-site":
                    return Response("Cross-site request rejected", status_code=403)
                origin = request.headers.get("origin", "").strip()
                if origin and not is_same_origin(request, origin):
                    return Response("Cross-site request rejected", status_code=403)
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


def _add_sync_notice(user: dict[str, object]) -> None:
    settings = get_settings()
    if not bool(user.get("has_deputy_credentials")):
        user["sync_notice_kind"] = "healthy"
        user["sync_notice_text"] = "Deputy roster not connected"
        return
    last_sync_text = str(user.get("last_sync_at") or "").strip()
    status_text = str(user.get("last_sync_status") or "").strip().lower()
    if int(user.get("sync_in_progress") or 0):
        user["sync_notice_kind"] = "syncing"
        user["sync_notice_text"] = "Deputy roster sync in progress"
        return
    if status_text in {"error", "failed"}:
        user["sync_notice_kind"] = "failed"
        user["sync_notice_text"] = "Deputy sync failed · check Deputy before relying on this roster"
        return
    try:
        last_sync = datetime.fromisoformat(last_sync_text)
        if last_sync.tzinfo is None:
            last_sync = last_sync.replace(tzinfo=settings.timezone)
    except (TypeError, ValueError):
        user["sync_notice_kind"] = "stale"
        user["sync_notice_text"] = "Deputy roster has not synced yet"
        return
    now = datetime.now(settings.timezone)
    age = now - last_sync.astimezone(settings.timezone)
    if age > timedelta(hours=36):
        days = max(1, int(age.total_seconds() // 86400))
        age_label = f"{days} day{'s' if days != 1 else ''} ago" if days else "over a day ago"
        user["sync_notice_kind"] = "stale"
        user["sync_notice_text"] = f"Deputy roster may be out of date · last synced {age_label}"
        return
    user["sync_notice_kind"] = "healthy"
    user["sync_notice_text"] = f"Deputy roster synced {last_sync.astimezone(settings.timezone).strftime('%d %b %H:%M')}"
