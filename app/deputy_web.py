from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Settings
from .database import get_next_upcoming_shift


SECRET_KEY_RE = re.compile(
    r"(password|token|secret|session|cookie|auth|bearer|csrf|xsrf|email|mobile|phone|pin|photo|pronoun|referral)",
    re.IGNORECASE,
)
URL_SECRET_RE = re.compile(r"([?&][A-Za-z0-9_%-]*(?:token|key|secret|session|ap|auth)[A-Za-z0-9_%-]*=)[^&\s\"']+", re.IGNORECASE)
INTERESTING_URL_RE = re.compile(r"/api/", re.IGNORECASE)
EMAIL_VALUE_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_VALUE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
SUMMARY_CODE_RE = re.compile(r"^\[([^\]]+)\]")
MAX_CAPTURED_RESPONSES = 80
MAX_SAMPLE_DEPTH = 4
MAX_SAMPLE_LIST_ITEMS = 6
MAX_SAMPLE_TEXT = 500
MAX_VISIBLE_TEXT = 16000
TRACK_NAMES = {
    "CAM": "Cambridge",
    "CAMBRIDGE": "Cambridge",
    "CAMS": "Cambridge Synthetic",
    "ELLE": "Ellerslie",
    "MATA": "Matamata",
    "PUKE": "Pukekohe",
    "R": "Rotorua",
    "ROTORUA": "Rotorua",
    "TARO": "Te Aroha",
    "TAUR": "Tauranga",
    "TRAP": "Te Rapa",
}
DEFAULT_RACE_TYPE_BY_CODE = {
    "CAM": "H",
}


@dataclass(frozen=True)
class DeputyWebCaptureResult:
    status: str
    message: str
    payload: dict[str, Any]


def redacted_text(value: str) -> str:
    return URL_SECRET_RE.sub(r"\1[redacted]", value)


def _safe_visible_text(value: str) -> str:
    cleaned = redacted_text(value)
    cleaned = EMAIL_VALUE_RE.sub("[redacted email]", cleaned)
    cleaned = PHONE_VALUE_RE.sub("[redacted phone]", cleaned)
    lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) > MAX_VISIBLE_TEXT:
        return cleaned[:MAX_VISIBLE_TEXT].rstrip() + "\n..."
    return cleaned


def _clean_url(value: str) -> str:
    parsed = urlsplit(value)
    query_items = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if SECRET_KEY_RE.search(key):
            query_items.append((key, "[redacted]"))
        else:
            query_items.append((key, item[:80]))
    return redacted_text(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_items), "")))


def _is_deputy_api_url(value: str, settings: Settings) -> bool:
    parsed = urlsplit(value)
    origin = urlsplit(_origin_url(settings))
    return parsed.netloc == origin.netloc and bool(INTERESTING_URL_RE.search(parsed.path))


def _origin_url(settings: Settings) -> str:
    parsed = urlsplit(settings.deputy_web_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _login_url(settings: Settings) -> str:
    parsed = urlsplit(settings.deputy_web_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/login", "noredirectonce=1", ""))


def _roster_url(settings: Settings) -> str:
    origin_url = _origin_url(settings)
    return f"{origin_url}#/roster" if origin_url else ""


def _week_shift_probe_url(settings: Settings) -> str:
    origin_url = _origin_url(settings)
    if not origin_url:
        return ""
    today = datetime.now(settings.timezone).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    start_at = datetime.combine(week_start, time.min, settings.timezone).isoformat()
    end_at = datetime.combine(week_end, time.max, settings.timezone).replace(microsecond=0).isoformat()
    query = urlencode(
        {
            "start": start_at,
            "end": end_at,
            "published": "TRUE",
            "expandMetadata": "true",
        }
    )
    return f"{origin_url}api/management/v2/shifts?{query}"


def _target_schedule_tracks(settings: Settings) -> list[str]:
    now = datetime.now(settings.timezone).replace(microsecond=0).isoformat()
    shift = get_next_upcoming_shift(now)
    if shift is None:
        return []

    names: list[str] = []
    title = str(shift["title"] or "")
    match = SUMMARY_CODE_RE.match(title)
    source_code = match.group(1).strip().upper() if match else ""
    if source_code and source_code != "VEH":
        race_type = ""
        track_code = source_code
        if len(source_code) > 2 and source_code[1] == "-" and source_code[0] in {"T", "H"}:
            race_type = source_code[0]
            track_code = source_code[2:]
        elif len(source_code) > 2 and source_code[-2] == "-" and source_code[-1] in {"T", "H"}:
            race_type = source_code[-1]
            track_code = source_code[:-2]
        if not race_type:
            race_type = DEFAULT_RACE_TYPE_BY_CODE.get(track_code, "")

        track_name = TRACK_NAMES.get(track_code, track_code.replace("-", " ").title())
        if race_type:
            names.append(f"{race_type}-{track_name}")
        names.extend([track_name, source_code])

    location = str(shift["location"] or "").strip()
    if location:
        names.append(location)

    unique_names = []
    seen = set()
    for name in names:
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            unique_names.append(name)
    return unique_names


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


def _extract_management_shifts(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return []
    shifts = []
    for item in data["data"]:
        if not isinstance(item, dict):
            continue
        shifts.append(
            {
                "id": item.get("id"),
                "employee": item.get("employee"),
                "area": item.get("area"),
                "start": item.get("start"),
                "end": item.get("end"),
                "duration": item.get("duration"),
                "isOpen": item.get("isOpen"),
                "isPublished": item.get("isPublished"),
                "mealbreakDuration": item.get("mealbreakDuration"),
                "confirmationStatus": item.get("confirmationStatus"),
                "note": _safe_json_sample(item.get("note") or ""),
            }
        )
    return shifts


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
    extracted_shifts_by_id: dict[str, dict[str, Any]] = {}
    events: list[str] = []
    page_texts: list[dict[str, Any]] = []
    target_tracks = _target_schedule_tracks(settings)
    if target_tracks:
        events.append(f"Target schedule track: {target_tracks[0]}.")
    else:
        events.append("No upcoming shift track found for schedule selection.")

    try:
        async with async_playwright() as playwright:
            browser = None
            context = None
            try:
                browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                context = await browser.new_context(
                    viewport={"width": 1360, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()

                async def capture_page_text(label: str) -> None:
                    try:
                        text = await page.locator("body").inner_text(timeout=8_000)
                    except Exception:
                        return
                    cleaned = _safe_visible_text(text)
                    if cleaned:
                        page_texts.append({"label": label, "length": len(text), "text": cleaned})

                async def wait_for_page_to_settle(label: str, timeout: int = 25_000) -> None:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=timeout)
                    except PlaywrightTimeoutError:
                        events.append(f"{label} kept loading; using captured responses so far.")

                async def current_body_text() -> str:
                    try:
                        return await page.locator("body").inner_text(timeout=8_000)
                    except Exception:
                        return ""

                async def open_schedule_page() -> None:
                    locators = [
                        page.get_by_role("link", name=re.compile(r"^Schedule$", re.IGNORECASE)).first,
                        page.get_by_role("button", name=re.compile(r"^Schedule$", re.IGNORECASE)).first,
                        page.get_by_text("Schedule", exact=True).first,
                    ]
                    for locator in locators:
                        try:
                            if await locator.count() == 0:
                                continue
                            await locator.click(timeout=8_000)
                            events.append("Clicked Schedule navigation.")
                            await wait_for_page_to_settle("Schedule page", timeout=30_000)
                            await page.wait_for_timeout(3_000)
                            return
                        except Exception:
                            continue
                    events.append("Could not click Schedule navigation; trying roster URL.")
                    roster_url = _roster_url(settings)
                    if roster_url:
                        await page.goto(roster_url, wait_until="domcontentloaded", timeout=45_000)
                        events.append("Opened Deputy roster page URL.")
                        await wait_for_page_to_settle("Deputy roster page", timeout=35_000)
                        await page.wait_for_timeout(3_000)

                async def select_target_track() -> None:
                    if not target_tracks:
                        return
                    body_text = await current_body_text()
                    body_lower = body_text.lower()
                    if any(track.lower() in body_lower for track in target_tracks) and "week by area" in body_lower:
                        events.append("Target track already appears selected on the schedule page.")
                        return

                    for target in target_tracks:
                        locators = [
                            page.get_by_role("button", name=re.compile(re.escape(target), re.IGNORECASE)).first,
                            page.get_by_text(target, exact=True).first,
                            page.get_by_text(target, exact=False).first,
                        ]
                        for locator in locators:
                            try:
                                if await locator.count() == 0:
                                    continue
                                await locator.click(timeout=8_000)
                                events.append(f"Clicked schedule track option: {target}.")
                                await wait_for_page_to_settle("Schedule track selection", timeout=30_000)
                                await page.wait_for_timeout(4_000)
                                body_text = await current_body_text()
                                if "week by area" in body_text.lower() or target.lower() in body_text.lower():
                                    return
                            except Exception:
                                continue

                    try:
                        search_input = page.locator(
                            "input[type='search'], input[placeholder*='Search' i], input[aria-label*='Search' i]"
                        ).first
                        if await search_input.count() > 0:
                            await search_input.fill(target_tracks[0], timeout=8_000)
                            events.append(f"Searched schedule page for track: {target_tracks[0]}.")
                            await page.wait_for_timeout(2_000)
                            option = page.get_by_text(target_tracks[0], exact=False).first
                            if await option.count() > 0:
                                await option.click(timeout=8_000)
                                events.append(f"Selected schedule search result: {target_tracks[0]}.")
                                await wait_for_page_to_settle("Schedule search selection", timeout=30_000)
                                await page.wait_for_timeout(4_000)
                    except Exception:
                        events.append("Could not select the target track automatically.")

                async def capture_response(response: Any) -> None:
                    if len(captured) >= MAX_CAPTURED_RESPONSES:
                        return
                    response_url = response.url
                    if not _is_deputy_api_url(response_url, settings):
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
                    if "/api/management/v2/shifts" in response_url:
                        for shift in _extract_management_shifts(data):
                            shift_id = str(shift.get("id") or "")
                            if shift_id:
                                extracted_shifts_by_id[shift_id] = shift

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

                await wait_for_page_to_settle("Login", timeout=25_000)

                await page.goto(settings.deputy_web_url, wait_until="domcontentloaded", timeout=45_000)
                events.append("Opened Deputy web app.")
                await wait_for_page_to_settle("Deputy web app", timeout=35_000)
                await capture_page_text("Deputy web app")

                await open_schedule_page()
                await capture_page_text("Deputy schedule page")
                await select_target_track()
                await capture_page_text("Deputy selected schedule page")

                probe_url = _week_shift_probe_url(settings)
                if probe_url:
                    probe = await page.evaluate(
                        """
                        async (url) => {
                          const response = await fetch(url, { credentials: "include" });
                          const contentType = response.headers.get("content-type") || "";
                          return {
                            status: response.status,
                            ok: response.ok,
                            data: contentType.includes("json") ? await response.json() : null
                          };
                        }
                        """,
                        probe_url,
                    )
                    if isinstance(probe, dict):
                        status = probe.get("status")
                        events.append(f"Probed weekly schedule shifts without employee filter: HTTP {status}.")
                        data = probe.get("data")
                        if data is not None and len(captured) < MAX_CAPTURED_RESPONSES:
                            captured.append(
                                {
                                    "url": _clean_url(probe_url),
                                    "method": "GET",
                                    "status": status,
                                    "shape": _top_level_shape(data),
                                    "sample": _safe_json_sample(data),
                                }
                            )
                            for shift in _extract_management_shifts(data):
                                shift_id = str(shift.get("id") or "")
                                if shift_id:
                                    extracted_shifts_by_id[shift_id] = shift

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
        "page_texts": page_texts,
        "extracted_shifts": sorted(
            extracted_shifts_by_id.values(),
            key=lambda item: (str(item.get("start") or ""), str(item.get("id") or "")),
        ),
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
    if not isinstance(payload.get("events"), list):
        payload["events"] = []
    if not isinstance(payload.get("responses"), list):
        payload["responses"] = []
    if not isinstance(payload.get("extracted_shifts"), list):
        payload["extracted_shifts"] = []
    if not isinstance(payload.get("page_texts"), list):
        payload["page_texts"] = []
    payload["captured_at"] = str(payload.get("captured_at") or "")
    payload["status"] = str(payload.get("status") or "unknown")
    for response in payload["responses"]:
        if not isinstance(response, dict):
            continue
        if not isinstance(response.get("shape"), dict):
            response["shape"] = {"kind": "unknown"}
        response["method"] = str(response.get("method") or "")
        response["status"] = str(response.get("status") or "")
        response["url"] = str(response.get("url") or "")
        response["sample"] = _safe_json_sample(response.get("sample"))
    return payload
