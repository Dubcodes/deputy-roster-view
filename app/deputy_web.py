from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import Settings


SECRET_KEY_RE = re.compile(r"(password|token|secret|session|cookie|auth|bearer|csrf|xsrf)", re.IGNORECASE)
URL_SECRET_RE = re.compile(r"([?&][A-Za-z0-9_%-]*(?:token|key|secret|session|ap|auth)[A-Za-z0-9_%-]*=)[^&\s\"']+", re.IGNORECASE)
MAX_CAPTURED_RESPONSES = 24
MAX_SAMPLE_DEPTH = 4
MAX_SAMPLE_LIST_ITEMS = 6
MAX_SAMPLE_TEXT = 500


@dataclass(frozen=True)
class DeputyWebCaptureResult:
    status: str
    message: str
    payload: dict[str, Any]


def redacted_text(value: str) -> str:
    return URL_SECRET_RE.sub(r"\1[redacted]", value)


def _clean_url(value: str) -> str:
    parsed = urlsplit(value)
    path_only = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return redacted_text(path_only)


def _login_url(settings: Settings) -> str:
    parsed = urlsplit(settings.deputy_web_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/login", "noredirectonce=1", ""))


def _safe_json_sample(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_SAMPLE_DEPTH:
        return "[nested]"
    if isinstance(value, dict):
        sample: dict[str, Any] = {}
        for key, item in list(value.items())[:18]:
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                sample[key_text] = "[redacted]"
            else:
                sample[key_text] = _safe_json_sample(item, depth + 1)
        return sample
    if isinstance(value, list):
        return [_safe_json_sample(item, depth + 1) for item in value[:MAX_SAMPLE_LIST_ITEMS]]
    if isinstance(value, str):
        cleaned = redacted_text(value)
        if len(cleaned) > MAX_SAMPLE_TEXT:
            return cleaned[:MAX_SAMPLE_TEXT].rstrip() + "..."
        return cleaned
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _top_level_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"kind": "list", "count": len(value)}
    if isinstance(value, dict):
        shape: dict[str, Any] = {"kind": "object", "keys": list(value.keys())[:24]}
        for key in ("data", "records", "rows", "items", "Roster", "Employee", "OperationalUnit"):
            item = value.get(key)
            if isinstance(item, list):
                shape[f"{key}_count"] = len(item)
        return shape
    return {"kind": type(value).__name__}


async def run_deputy_web_capture(settings: Settings) -> DeputyWebCaptureResult:
    if not settings.deputy_login_configured:
        return DeputyWebCaptureResult(
            status="missing",
            message="Deputy login env is incomplete. Set DEPUTY_WEB_URL, DEPUTY_LOGIN_EMAIL, and DEPUTY_LOGIN_PASSWORD.",
            payload={},
        )

    login_url = _login_url(settings)
    if not login_url:
        return DeputyWebCaptureResult(
            status="error",
            message="DEPUTY_WEB_URL is not a valid Deputy install URL.",
            payload={},
        )

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError:
        return DeputyWebCaptureResult(
            status="error",
            message="Playwright is not installed in this image yet. Redeploy after pulling the latest repository changes.",
            payload={},
        )

    captured: list[dict[str, Any]] = []
    events: list[str] = []

    try:
        async with async_playwright() as playwright:
            browser = None
            context = None
            try:
                browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844},
                    user_agent=(
                        "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0 Mobile Safari/537.36"
                    ),
                )
                page = await context.new_page()

                async def capture_response(response: Any) -> None:
                    if len(captured) >= MAX_CAPTURED_RESPONSES:
                        return
                    response_url = response.url
                    if "/api/" not in response_url and "deputy.com" not in response_url:
                        return
                    content_type = (response.headers.get("content-type") or "").lower()
                    if "json" not in content_type:
                        return
                    try:
                        data = await response.json()
                    except Exception:
                        return
                    captured.append(
                        {
                            "url": _clean_url(response_url),
                            "method": response.request.method,
                            "status": response.status,
                            "shape": _top_level_shape(data),
                            "sample": _safe_json_sample(data),
                        }
                    )

                page.on("response", capture_response)
                await page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
                events.append("Opened Deputy login page.")

                email_field = page.locator(
                    "input[type='email'], input[name*='email' i], input[id*='email' i], input[type='text']"
                ).first
                password_field = page.locator("input[type='password']").first
                await email_field.fill(settings.deputy_login_email, timeout=20_000)
                await password_field.fill(settings.deputy_login_password, timeout=20_000)
                await password_field.press("Enter")
                events.append("Submitted login form.")

                try:
                    await page.wait_for_load_state("networkidle", timeout=25_000)
                except PlaywrightTimeoutError:
                    events.append("Network stayed active after login; continuing capture.")

                await page.goto(settings.deputy_web_url, wait_until="domcontentloaded", timeout=45_000)
                events.append("Opened Deputy web app.")
                try:
                    await page.wait_for_load_state("networkidle", timeout=35_000)
                except PlaywrightTimeoutError:
                    events.append("Deputy web app kept loading; using captured responses so far.")

                await page.wait_for_timeout(4_000)
                login_still_visible = await page.locator("input[type='password']").count() > 0
                if login_still_visible:
                    events.append("Password field is still visible; login may have failed or needs MFA/SSO.")
            finally:
                if context is not None:
                    await context.close()
                if browser is not None:
                    await browser.close()
    except Exception as exc:
        events.append(f"Capture stopped: {redacted_text(str(exc))}")

    captured_at = datetime.now(settings.timezone).isoformat(timespec="seconds")
    payload = {
        "captured_at": captured_at,
        "status": "ok" if captured else "empty",
        "events": events,
        "responses": captured,
    }
    if captured:
        return DeputyWebCaptureResult(
            status="ok",
            message=f"Captured {len(captured)} Deputy JSON responses. Review the web diagnostics below.",
            payload=payload,
        )
    return DeputyWebCaptureResult(
        status="empty",
        message="No Deputy JSON responses were captured. The login may require MFA/SSO, or the page may not expose roster data through this path.",
        payload=payload,
    )


def format_capture_payload(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload
