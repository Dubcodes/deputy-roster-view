from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Settings, get_settings
from .database import (
    get_current_or_next_shift,
    get_upcoming_shifts,
    save_deputy_web_capture_diagnostic,
    save_deputy_web_schedule,
    update_app_settings,
)


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
SCHEDULE_SAMPLE_DEPTH = 8
SCHEDULE_SAMPLE_LIST_ITEMS = 80
SCHEDULE_SAMPLE_TEXT = 4000
MAX_VISIBLE_TEXT = 16000
ALL_LOCATIONS_SCHEDULE_TARGETS = ("All Locations", "All locations", "All locations schedule")
DIRECT_SCHEDULE_LOOKBACK_DAYS = 14
DIRECT_SCHEDULE_LOOKAHEAD_DAYS = 42
DIRECT_SCHEDULE_LOCATION_BATCH_SIZE = 50
SCHEDULE_COUNT_PATTERNS = {
    "empty_count": re.compile(r"\b(\d+)\s+empty\b", re.IGNORECASE),
    "unpublished_count": re.compile(r"\b(\d+)\s+unpublished\b", re.IGNORECASE),
    "published_count": re.compile(r"\b(\d+)\s+published\b", re.IGNORECASE),
    "require_confirmation_count": re.compile(r"\b(\d+)\s+require\s+confirmation\b", re.IGNORECASE),
    "open_shift_count": re.compile(r"\b(\d+)\s+open\s+shifts?\b", re.IGNORECASE),
    "warning_count": re.compile(r"\b(\d+)\s+warnings?\b", re.IGNORECASE),
    "unavailable_count": re.compile(r"\b(\d+)\s+unavailable\b", re.IGNORECASE),
}
FULL_COPY_PATH_RE = re.compile(
    r"/api/(?:schedule/|management/v2/(?:shifts|custom-fields)|v1/my/roster)",
    re.IGNORECASE,
)
LOGIN_ERROR_RE = re.compile(
    r"(incorrect|invalid|wrong|not\s+recognised|not\s+recognized|failed|unable\s+to\s+(?:log|sign)|"
    r"try\s+again|password\s+is\s+required|email\s+is\s+required)",
    re.IGNORECASE,
)
MFA_OR_SSO_RE = re.compile(
    r"(multi[-\s]?factor|two[-\s]?factor|verification\s+code|authenticator|single\s+sign[-\s]?on|"
    r"\bsso\b|check\s+your\s+email)",
    re.IGNORECASE,
)
CAPTURE_PATH_RE = re.compile(
    r"/api/(?:schedule/|management/v2/(?:shifts|areas|custom-fields)|v1/my/roster)",
    re.IGNORECASE,
)
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


def _login_problem_message(page_text: str) -> str:
    safe_text = _safe_visible_text(page_text)
    if LOGIN_ERROR_RE.search(safe_text):
        return "Deputy login was not accepted. Check the Deputy email/password saved for this user, then run sync again."
    if MFA_OR_SSO_RE.search(safe_text):
        return "Deputy is still asking for MFA/SSO verification. This app cannot complete that extra login step yet."
    return "Deputy is still showing the login form after submitting credentials. Check the saved Deputy email/password, or whether this account needs MFA/SSO."


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


def _is_relevant_deputy_api_url(value: str) -> bool:
    return bool(CAPTURE_PATH_RE.search(urlsplit(value).path))


def _is_schedule_api_url(value: str) -> bool:
    return "/api/schedule/" in urlsplit(value).path


def _include_full_response_in_copy(response: dict[str, Any]) -> bool:
    url = str(response.get("url") or "")
    try:
        status = int(response.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    return status >= 400 or bool(FULL_COPY_PATH_RE.search(urlsplit(url).path))


def _origin_url(settings: Settings) -> str:
    parsed = urlsplit(settings.deputy_web_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _api_url(settings: Settings, path: str) -> str:
    origin = _origin_url(settings).rstrip("/")
    if not origin:
        return path
    return f"{origin}{path if path.startswith('/') else '/' + path}"


def _login_url(settings: Settings) -> str:
    parsed = urlsplit(settings.deputy_web_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "/login", "noredirectonce=1", ""))


def _roster_url(settings: Settings) -> str:
    origin_url = _origin_url(settings)
    return f"{origin_url}#/roster" if origin_url else ""


def _shift_track_candidates(shift: object) -> list[str]:
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


def _target_schedule_track_groups(settings: Settings) -> list[list[str]]:
    now = datetime.now(settings.timezone).replace(microsecond=0).isoformat()
    rows = []
    current = get_current_or_next_shift(now)
    if current is not None:
        rows.append(current)
    rows.extend(get_upcoming_shifts(now, limit=16))

    groups = []
    seen = set()
    for shift in rows:
        candidates = _shift_track_candidates(shift)
        if not candidates:
            continue
        date_key = str(shift["date"] or "")
        first_key = candidates[0].lower()
        group_key = f"{date_key}:{first_key}"
        if group_key in seen:
            continue
        seen.add(group_key)
        groups.append(candidates)
    return groups


def _target_schedule_tracks(settings: Settings) -> list[str]:
    groups = _target_schedule_track_groups(settings)
    return groups[0] if groups else []


def _safe_json_sample(
    value: Any,
    depth: int = 0,
    max_depth: int = MAX_SAMPLE_DEPTH,
    max_list_items: int = MAX_SAMPLE_LIST_ITEMS,
    max_text: int = MAX_SAMPLE_TEXT,
) -> Any:
    if depth >= max_depth:
        return "[nested]"
    if isinstance(value, dict):
        sample: dict[str, Any] = {}
        for key, item in list(value.items())[:18]:
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                sample[key_text] = "[redacted]"
            else:
                sample[key_text] = _safe_json_sample(item, depth + 1, max_depth, max_list_items, max_text)
        return sample
    if isinstance(value, list):
        return [_safe_json_sample(item, depth + 1, max_depth, max_list_items, max_text) for item in value[:max_list_items]]
    if isinstance(value, str):
        cleaned = redacted_text(value)
        if len(cleaned) > max_text:
            return cleaned[:max_text].rstrip() + "..."
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
                "areaName": _first_value(
                    item,
                    (
                        "areaName",
                        "AreaName",
                        "operationalUnitName",
                        "OperationalUnitName",
                        "label",
                    ),
                ),
                "roleName": _first_value(item, ("role", "roleName", "RoleName", "title", "Title")),
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


def _extract_area_refs(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    areas_by_id: dict[str, dict[str, Any]] = {}
    root = data.get("data", data)
    for item in _iter_dicts(root):
        area_id = _first_value(item, ("id", "Id"))
        if area_id is None:
            continue
        location_id = _first_value(
            item,
            (
                "locationId",
                "location_id",
                "parentLocationId",
                "parent_location_id",
                "ParentOperationalUnit",
                "parentOperationalUnit",
            ),
        )
        location_obj = item.get("location") or item.get("primaryLocation")
        if location_id is None and isinstance(location_obj, dict):
            location_id = _first_value(location_obj, ("id", "Id"))
        if location_id is None:
            continue

        name = _first_value(
            item,
            (
                "name",
                "Name",
                "displayName",
                "areaName",
                "AreaName",
                "OperationalUnitName",
                "label",
                "title",
            ),
        )
        if name in (None, ""):
            continue

        area_key = str(area_id)
        areas_by_id[area_key] = {
            "id": area_id,
            "name": name or str(area_id),
            "locationId": location_id,
            "rosterSortOrder": _first_value(
                item,
                (
                    "rosterSortOrder",
                    "roster_sort_order",
                    "RosterSortOrder",
                    "sortOrder",
                    "sort_order",
                ),
            ),
        }
    return list(areas_by_id.values())


def _iter_dicts(value: Any) -> Any:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _direct_schedule_location_ids(location_refs_by_id: dict[str, dict[str, Any]]) -> list[int]:
    ids = []
    for location in location_refs_by_id.values():
        location_id = location.get("id")
        name = str(location.get("name") or "").strip()
        compact_name = re.sub(r"\s+", "", name.upper())
        if not re.match(r"^[TH]-", compact_name):
            continue
        try:
            ids.append(int(location_id))
        except (TypeError, ValueError):
            continue
    return sorted(set(ids))


def _chunks(items: list[int], size: int) -> Any:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_location_refs(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    payload = data.get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("primaryLocations"), list):
        return []

    locations = []
    for item in payload["primaryLocations"]:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        locations.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or str(item.get("id")),
                "address": item.get("address") or item.get("streetAddress") or "",
            }
        )
    return locations


def _extract_schedule_shifts(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    payload = data.get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("shifts"), list):
        return []

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    employees = {}
    if isinstance(metadata.get("employee"), list):
        for employee in metadata["employee"]:
            if not isinstance(employee, dict):
                continue
            employee_id = employee.get("id")
            if employee_id is not None:
                employees[str(employee_id)] = employee.get("displayName") or employee.get("name") or str(employee_id)

    shifts = []
    for item in payload["shifts"]:
        if not isinstance(item, dict):
            continue
        employee_id = item.get("employee")
        shifts.append(
            {
                "id": item.get("id"),
                "employee": employee_id,
                "employeeName": employees.get(str(employee_id), ""),
                "area": item.get("area"),
                "areaName": _first_value(
                    item,
                    (
                        "areaName",
                        "AreaName",
                        "operationalUnitName",
                        "OperationalUnitName",
                        "label",
                    ),
                ),
                "roleName": _first_value(item, ("role", "roleName", "RoleName", "title", "Title")),
                "start": item.get("start"),
                "end": item.get("end"),
                "duration": item.get("duration"),
                "isOpen": item.get("isOpen"),
                "isPublished": item.get("isPublished"),
                "note": _safe_json_sample(
                    item.get("note") or "",
                    max_depth=SCHEDULE_SAMPLE_DEPTH,
                    max_list_items=SCHEDULE_SAMPLE_LIST_ITEMS,
                    max_text=SCHEDULE_SAMPLE_TEXT,
                ),
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
    extracted_schedule_shifts_by_id: dict[str, dict[str, Any]] = {}
    area_refs_by_id: dict[str, dict[str, Any]] = {}
    location_refs_by_id: dict[str, dict[str, Any]] = {}
    captured_employee_id = ""
    events: list[str] = []
    page_texts: list[dict[str, Any]] = []
    login_problem_message = ""
    target_track_groups = _target_schedule_track_groups(settings)
    events.append("Target schedule view: All Locations.")
    if target_track_groups:
        target_labels = ", ".join(group[0] for group in target_track_groups[:8])
        extra_count = max(0, len(target_track_groups) - 8)
        suffix = f", +{extra_count} more" if extra_count else ""
        events.append(f"Fallback schedule tracks: {target_labels}{suffix}.")
    else:
        events.append("No upcoming shift track found for fallback schedule selection.")

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

                async def select_target_track(target_tracks: list[str]) -> bool:
                    if not target_tracks:
                        return False
                    body_text = await current_body_text()
                    body_lower = body_text.lower()
                    if any(track.lower() in body_lower for track in target_tracks) and "week by area" in body_lower:
                        events.append(f"Target track already appears selected: {target_tracks[0]}.")
                        return True

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
                                    return True
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
                                return True
                    except Exception:
                        pass
                    events.append(f"Could not select schedule target automatically: {target_tracks[0]}.")
                    return False

                def remember_employee_id(value: Any) -> None:
                    nonlocal captured_employee_id
                    if captured_employee_id or value in (None, ""):
                        return
                    value_text = str(value).strip()
                    if value_text.isdigit():
                        captured_employee_id = value_text

                async def capture_extended_own_roster() -> None:
                    employee_id = captured_employee_id
                    if not employee_id:
                        for shift in extracted_shifts_by_id.values():
                            remember_employee_id(shift.get("employee"))
                            employee_id = captured_employee_id
                            if employee_id:
                                break
                    if not employee_id:
                        events.append("Could not identify the Deputy employee id for extended own-roster capture.")
                        return

                    async def fetch_shift_window(window_start: datetime, window_end: datetime) -> dict[str, Any]:
                        query = urlencode(
                            {
                                "start": window_start.isoformat(),
                                "end": window_end.isoformat(),
                                "employee": employee_id,
                                "published": "TRUE",
                            }
                        )
                        path = f"/api/management/v2/shifts?{query}"
                        return await page.evaluate(
                            """
                            async (path) => {
                              const response = await fetch(path, {
                                credentials: "include",
                                headers: { "accept": "application/json" }
                              });
                              let body = null;
                              try { body = await response.json(); } catch (error) {}
                              return { ok: response.ok, status: response.status, body };
                            }
                            """,
                            path,
                        )

                    now = datetime.now(settings.timezone)
                    lookback_days = max(0, settings.own_roster_lookback_days)
                    lookahead_days = max(1, settings.own_roster_lookahead_days)
                    start_at = (now - timedelta(days=lookback_days)).replace(hour=0, minute=0, second=0, microsecond=0)
                    end_at = (now + timedelta(days=lookahead_days)).replace(hour=23, minute=59, second=59, microsecond=0)
                    initial_shift_ids = set(extracted_shifts_by_id)
                    request_count = 0
                    failed_requests = 0
                    rows_seen = 0
                    paged_windows = 0
                    window_start = start_at

                    while window_start <= end_at:
                        window_end = min(
                            window_start + timedelta(days=6, hours=23, minutes=59, seconds=59),
                            end_at,
                        )
                        try:
                            result = await fetch_shift_window(window_start, window_end)
                        except Exception as exc:
                            failed_requests += 1
                            events.append(
                                "Extended own-roster capture failed for "
                                f"{window_start.date().isoformat()} to {window_end.date().isoformat()}: "
                                f"{redacted_text(str(exc))[:180]}."
                            )
                            window_start = (window_end + timedelta(seconds=1)).replace(
                                hour=0,
                                minute=0,
                                second=0,
                                microsecond=0,
                            )
                            continue

                        request_count += 1
                        body = result.get("body") if isinstance(result, dict) else None
                        status = int(result.get("status") or 0) if isinstance(result, dict) else 0
                        if not isinstance(result, dict) or not result.get("ok"):
                            failed_requests += 1
                            events.append(
                                "Extended own-roster capture returned "
                                f"HTTP {status or 'unknown'} for "
                                f"{window_start.date().isoformat()} to {window_end.date().isoformat()}."
                            )
                            window_start = (window_end + timedelta(seconds=1)).replace(
                                hour=0,
                                minute=0,
                                second=0,
                                microsecond=0,
                            )
                            continue

                        shifts = _extract_management_shifts(body)
                        rows_seen += len(shifts)
                        if isinstance(body, dict) and body.get("nextCursor"):
                            paged_windows += 1
                        for shift in shifts:
                            shift_id = str(shift.get("id") or "")
                            if not shift_id:
                                continue
                            extracted_shifts_by_id[shift_id] = shift
                            remember_employee_id(shift.get("employee"))
                        window_start = (window_end + timedelta(seconds=1)).replace(
                            hour=0,
                            minute=0,
                            second=0,
                            microsecond=0,
                        )

                    added = len(set(extracted_shifts_by_id) - initial_shift_ids)
                    events.append(
                        "Extended own-roster capture covered "
                        f"{start_at.date().isoformat()} to {end_at.date().isoformat()} "
                        f"in {request_count} weekly requests, saw {rows_seen} shift rows, "
                        f"and added {added} shift rows."
                    )
                    if failed_requests:
                        events.append(f"Extended own-roster capture had {failed_requests} failed weekly requests.")
                    if paged_windows:
                        events.append(
                            "Deputy returned pagination cursors inside "
                            f"{paged_windows} weekly own-roster windows."
                        )

                def schedule_capture_bounds() -> tuple[datetime, datetime]:
                    now = datetime.now(settings.timezone)
                    lookback_days = min(max(0, settings.own_roster_lookback_days), DIRECT_SCHEDULE_LOOKBACK_DAYS)
                    lookahead_days = min(max(1, settings.own_roster_lookahead_days), DIRECT_SCHEDULE_LOOKAHEAD_DAYS)
                    start_at = (now - timedelta(days=lookback_days)).replace(
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                    start_at = start_at - timedelta(days=start_at.weekday())
                    end_at = (now + timedelta(days=lookahead_days)).replace(
                        hour=23,
                        minute=59,
                        second=59,
                        microsecond=0,
                    )
                    return start_at, end_at

                async def fetch_schedule_search(body_data: dict[str, Any]) -> dict[str, Any]:
                    return await page.evaluate(
                        """
                        async ({ path, body }) => {
                          const response = await fetch(path, {
                            method: "POST",
                            credentials: "include",
                            headers: {
                              "accept": "application/json",
                              "content-type": "application/json"
                            },
                            body: JSON.stringify({ data: body })
                          });
                          let payload = null;
                          try { payload = await response.json(); } catch (error) {}
                          return { ok: response.ok, status: response.status, body: payload };
                        }
                        """,
                        {"path": "/api/schedule/v2/me/shifts:search", "body": body_data},
                    )

                def store_schedule_search_body(body: Any) -> int:
                    for area in _extract_area_refs(body):
                        area_id = str(area.get("id") or "")
                        if area_id:
                            area_refs_by_id[area_id] = area
                    shifts = _extract_schedule_shifts(body)
                    for shift in shifts:
                        shift_id = str(shift.get("id") or "")
                        if shift_id:
                            extracted_schedule_shifts_by_id[shift_id] = shift
                            remember_employee_id(shift.get("employee"))
                    return len(shifts)

                async def capture_expanded_area_refs() -> None:
                    paths = [
                        "/api/management/v2/areas?limit=50000&excludeSoft=0",
                        "/api/management/v2/areas?limit=50000",
                    ]
                    initial_area_ids = set(area_refs_by_id)
                    request_count = 0
                    failed_requests = 0
                    for path in paths:
                        try:
                            result = await page.evaluate(
                                """
                                async (path) => {
                                  const response = await fetch(path, {
                                    credentials: "include",
                                    headers: { "accept": "application/json" }
                                  });
                                  let payload = null;
                                  try { payload = await response.json(); } catch (error) {}
                                  return { ok: response.ok, status: response.status, body: payload };
                                }
                                """,
                                path,
                            )
                        except Exception as exc:
                            failed_requests += 1
                            events.append(
                                "Expanded area reference capture failed: "
                                f"{redacted_text(str(exc))[:180]}."
                            )
                            continue

                        request_count += 1
                        body = result.get("body") if isinstance(result, dict) else None
                        status = int(result.get("status") or 0) if isinstance(result, dict) else 0
                        if not isinstance(result, dict) or not result.get("ok"):
                            failed_requests += 1
                            events.append(
                                f"Expanded area reference capture returned HTTP {status or 'unknown'}."
                            )
                            continue
                        for area in _extract_area_refs(body):
                            area_id = str(area.get("id") or "")
                            if area_id:
                                area_refs_by_id[area_id] = area

                    added = len(set(area_refs_by_id) - initial_area_ids)
                    events.append(
                        "Expanded area reference capture checked "
                        f"{request_count} area endpoints and added {added} area refs."
                    )
                    if failed_requests:
                        events.append(f"Expanded area reference capture had {failed_requests} failed requests.")

                async def capture_direct_schedule_searches() -> None:
                    location_ids = _direct_schedule_location_ids(location_refs_by_id)
                    if not location_ids:
                        events.append("Direct schedule search skipped because no racing location list was captured.")
                        return

                    start_at, end_at = schedule_capture_bounds()
                    initial_shift_ids = set(extracted_schedule_shifts_by_id)
                    request_count = 0
                    failed_requests = 0
                    rows_seen = 0
                    window_start = start_at

                    while window_start <= end_at:
                        window_end = min(
                            window_start + timedelta(days=6, hours=23, minutes=59, seconds=59),
                            end_at,
                        )
                        for batch_location_ids in _chunks(location_ids, DIRECT_SCHEDULE_LOCATION_BATCH_SIZE):
                            body_data = {
                                "start": window_start.isoformat(),
                                "end": window_end.isoformat(),
                                "expandMetadata": True,
                                "locationMode": "SELECTED",
                                "locationIds": batch_location_ids,
                            }
                            try:
                                result = await fetch_schedule_search(body_data)
                            except Exception as exc:
                                failed_requests += 1
                                events.append(
                                    "Direct schedule search failed for "
                                    f"{window_start.date().isoformat()} to {window_end.date().isoformat()}: "
                                    f"{redacted_text(str(exc))[:180]}."
                                )
                                continue

                            request_count += 1
                            body = result.get("body") if isinstance(result, dict) else None
                            status = int(result.get("status") or 0) if isinstance(result, dict) else 0
                            if not isinstance(result, dict) or not result.get("ok"):
                                failed_requests += 1
                                events.append(
                                    "Direct schedule search returned "
                                    f"HTTP {status or 'unknown'} for "
                                    f"{window_start.date().isoformat()} to {window_end.date().isoformat()}."
                                )
                                continue

                            rows_seen += store_schedule_search_body(body)

                        window_start = (window_end + timedelta(seconds=1)).replace(
                            hour=0,
                            minute=0,
                            second=0,
                            microsecond=0,
                        )

                    added = len(set(extracted_schedule_shifts_by_id) - initial_shift_ids)
                    events.append(
                        "Direct schedule search covered "
                        f"{start_at.date().isoformat()} to {end_at.date().isoformat()} "
                        f"across {len(location_ids)} racing locations in {request_count} batched requests, "
                        f"saw {rows_seen} crew rows, and added {added} crew rows."
                    )
                    if failed_requests:
                        events.append(f"Direct schedule search had {failed_requests} failed batched requests.")

                async def capture_response(response: Any) -> None:
                    try:
                        response_url = response.url
                        if not _is_deputy_api_url(response_url, settings):
                            return
                        if not _is_relevant_deputy_api_url(response_url):
                            return
                        content_type = (response.headers.get("content-type") or "").lower()
                        if "json" not in content_type:
                            return
                        try:
                            data = await response.json()
                        except Exception:
                            return
                        is_schedule_response = _is_schedule_api_url(response_url)
                        sample_kwargs = (
                            {
                                "max_depth": SCHEDULE_SAMPLE_DEPTH,
                                "max_list_items": SCHEDULE_SAMPLE_LIST_ITEMS,
                                "max_text": SCHEDULE_SAMPLE_TEXT,
                            }
                            if is_schedule_response
                            else {}
                        )
                        captured_item = {
                            "url": _clean_url(response_url),
                            "method": response.request.method,
                            "status": response.status,
                            "shape": _top_level_shape(data),
                            "sample": _safe_json_sample(data, **sample_kwargs),
                        }
                        if is_schedule_response and response.request.method.upper() in {"POST", "PUT", "PATCH"}:
                            post_data = response.request.post_data or ""
                            try:
                                captured_item["request_sample"] = _safe_json_sample(json.loads(post_data), **sample_kwargs)
                            except ValueError:
                                captured_item["request_sample"] = redacted_text(post_data[:SCHEDULE_SAMPLE_TEXT])
                        if len(captured) < MAX_CAPTURED_RESPONSES:
                            captured.append(captured_item)
                        if "/api/management/v2/shifts" in response_url:
                            query_params = dict(parse_qsl(urlsplit(response_url).query))
                            remember_employee_id(query_params.get("employee"))
                            for shift in _extract_management_shifts(data):
                                shift_id = str(shift.get("id") or "")
                                if shift_id:
                                    remember_employee_id(shift.get("employee"))
                                    extracted_shifts_by_id[shift_id] = shift
                        if "/api/management/v2/areas" in response_url:
                            query_params = dict(parse_qsl(urlsplit(response_url).query))
                            remember_employee_id(query_params.get("employeeId"))
                            for area in _extract_area_refs(data):
                                area_id = str(area.get("id") or "")
                                if area_id:
                                    area_refs_by_id[area_id] = area
                        if "/api/schedule/v2/components/filters" in response_url:
                            for location in _extract_location_refs(data):
                                location_id = str(location.get("id") or "")
                                if location_id:
                                    location_refs_by_id[location_id] = location
                        if is_schedule_response:
                            for area in _extract_area_refs(data):
                                area_id = str(area.get("id") or "")
                                if area_id:
                                    area_refs_by_id[area_id] = area
                            for shift in _extract_schedule_shifts(data):
                                shift_id = str(shift.get("id") or "")
                                if shift_id:
                                    extracted_schedule_shifts_by_id[shift_id] = shift
                    except Exception as exc:
                        events.append(f"Skipped one Deputy response during capture: {redacted_text(str(exc))[:180]}.")

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
                selected_all_locations = await select_target_track(list(ALL_LOCATIONS_SCHEDULE_TARGETS))
                if selected_all_locations:
                    await capture_page_text("Deputy all locations schedule page")
                elif target_track_groups:
                    events.append("All Locations was not selectable; falling back to upcoming roster locations.")
                    for target_tracks in target_track_groups:
                        selected = await select_target_track(target_tracks)
                        if selected:
                            await capture_page_text(f"Deputy selected schedule page - {target_tracks[0]}")
                else:
                    await capture_page_text("Deputy selected schedule page")

                await page.wait_for_timeout(4_000)
                login_still_visible = await page.locator("input[type='password']").count() > 0
                if login_still_visible:
                    visible_text = await current_body_text()
                    login_problem_message = _login_problem_message(visible_text)
                    page_texts.append(
                        {
                            "label": "Deputy login page after submit",
                            "length": len(visible_text),
                            "text": _safe_visible_text(visible_text),
                        }
                    )
                    events.append(login_problem_message)
                else:
                    await capture_extended_own_roster()
                    await capture_expanded_area_refs()
                    await capture_direct_schedule_searches()
            finally:
                if context is not None:
                    await context.close()
                if browser is not None:
                    await browser.close()
    except Exception as exc:
        events.append(f"Capture stopped: {redacted_text(str(exc))}")

    captured_at = datetime.now(settings.timezone).isoformat(timespec="seconds")
    extracted_schedule_shifts = sorted(
        extracted_schedule_shifts_by_id.values(),
        key=lambda item: (str(item.get("start") or ""), str(item.get("id") or "")),
    )
    used_area_ids = {str(shift.get("area") or "") for shift in extracted_schedule_shifts}
    used_area_ids.update(str(shift.get("area") or "") for shift in extracted_shifts_by_id.values())
    area_refs = sorted(
        (area for area_id, area in area_refs_by_id.items() if area_id in used_area_ids),
        key=lambda item: (
            int(item.get("rosterSortOrder") or 999999),
            str(item.get("name") or ""),
            str(item.get("id") or ""),
        ),
    )
    location_refs = sorted(
        location_refs_by_id.values(),
        key=lambda item: (
            str(item.get("name") or ""),
            str(item.get("id") or ""),
        ),
    )
    for shift in extracted_schedule_shifts:
        area_ref = area_refs_by_id.get(str(shift.get("area") or ""))
        if area_ref:
            shift["areaName"] = area_ref.get("name") or ""
            shift["areaLocationId"] = area_ref.get("locationId")
            shift["areaRosterSortOrder"] = area_ref.get("rosterSortOrder")

    payload = {
        "captured_at": captured_at,
        "status": "login_failed" if login_problem_message else ("ok" if captured else "empty"),
        "events": events,
        "responses": captured,
        "page_texts": page_texts,
        "areas": area_refs,
        "locations": location_refs,
        "extracted_shifts": sorted(
            extracted_shifts_by_id.values(),
            key=lambda item: (str(item.get("start") or ""), str(item.get("id") or "")),
        ),
        "extracted_schedule_shifts": extracted_schedule_shifts,
    }
    if login_problem_message:
        payload["auth_status"] = "login_failed"
        return DeputyWebCaptureResult(
            status="login_failed",
            message=login_problem_message,
            payload=payload,
        )
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


async def capture_and_save_deputy_web(
    settings: Settings | None = None,
    owner_user_id: int | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    saved_own_shift_rows = 0
    own_shift_rows_created = 0
    own_shift_rows_updated = 0
    saved_schedule_rows = 0
    try:
        result = await run_deputy_web_capture(settings)
    except Exception as exc:
        message = f"Deputy web capture failed: {redacted_text(str(exc))[:220]}"
        payload = {
            "captured_at": datetime.now(settings.timezone).isoformat(timespec="seconds"),
            "status": "error",
            "events": [message],
            "responses": [],
        }
        payload_text = json.dumps(payload, ensure_ascii=True)
        update_app_settings({"last_deputy_web_capture": payload_text})
        save_deputy_web_capture_diagnostic(
            owner_user_id=owner_user_id,
            captured_at=str(payload.get("captured_at") or ""),
            status="error",
            message=message,
            payload=payload_text,
        )
        return {
            "status": "error",
            "message": message,
            "saved_own_shift_rows": 0,
            "own_shift_rows_created": 0,
            "own_shift_rows_updated": 0,
            "saved_schedule_rows": 0,
            "payload": payload,
        }

    if result.payload:
        payload_text = json.dumps(result.payload, ensure_ascii=True)
        save_deputy_web_capture_diagnostic(
            owner_user_id=owner_user_id,
            captured_at=str(result.payload.get("captured_at") or ""),
            status=result.status,
            message=result.message,
            payload=payload_text,
        )
        save_result = save_deputy_web_schedule(result.payload, owner_user_id=owner_user_id)
        saved_own_shift_rows = int(save_result.get("own_seen", 0))
        own_shift_rows_created = int(save_result.get("own_created", 0))
        own_shift_rows_updated = int(save_result.get("own_updated", 0))
        saved_schedule_rows = int(save_result.get("schedule_saved", 0))
        if saved_own_shift_rows:
            result.payload.setdefault("events", []).append(
                f"Saved {saved_own_shift_rows} own roster rows locally."
            )
        if saved_schedule_rows:
            result.payload.setdefault("events", []).append(f"Saved {saved_schedule_rows} schedule rows locally.")
        update_app_settings({"last_deputy_web_capture": json.dumps(result.payload, ensure_ascii=True)})

    return {
        "status": result.status,
        "message": result.message,
        "saved_own_shift_rows": saved_own_shift_rows,
        "own_shift_rows_created": own_shift_rows_created,
        "own_shift_rows_updated": own_shift_rows_updated,
        "saved_schedule_rows": saved_schedule_rows,
        "payload": result.payload,
    }


def sync_deputy_web_schedule(settings: Settings | None = None, owner_user_id: int | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    if not settings.deputy_login_configured:
        return {
            "status": "skipped",
            "message": "Deputy web capture skipped because login env is incomplete.",
            "saved_own_shift_rows": 0,
            "own_shift_rows_created": 0,
            "own_shift_rows_updated": 0,
            "saved_schedule_rows": 0,
            "payload": {},
        }
    return asyncio.run(capture_and_save_deputy_web(settings, owner_user_id=owner_user_id))


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
    if not isinstance(payload.get("extracted_schedule_shifts"), list):
        payload["extracted_schedule_shifts"] = []
    if not isinstance(payload.get("areas"), list):
        payload["areas"] = []
    if not isinstance(payload.get("locations"), list):
        payload["locations"] = []
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
        sample_kwargs = (
            {
                "max_depth": SCHEDULE_SAMPLE_DEPTH,
                "max_list_items": SCHEDULE_SAMPLE_LIST_ITEMS,
                "max_text": SCHEDULE_SAMPLE_TEXT,
            }
            if _is_schedule_api_url(response["url"])
            else {}
        )
        response["sample"] = _safe_json_sample(response.get("sample"), **sample_kwargs)
        if "request_sample" in response:
            response["request_sample"] = _safe_json_sample(response.get("request_sample"), **sample_kwargs)
    payload["stats"] = _capture_stats(payload)
    payload["copy_text"] = _capture_copy_text(payload)
    return payload


def _record_dates(records: Any) -> list[str]:
    if not isinstance(records, list):
        return []
    dates = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        start_text = str(record.get("start") or record.get("start_at") or "")
        match = re.match(r"^\d{4}-\d{2}-\d{2}", start_text)
        if match:
            dates.add(match.group(0))
    return sorted(dates)


def _short_date(value: str) -> str:
    try:
        date_value = datetime.fromisoformat(value)
    except ValueError:
        return value
    return f"{date_value.day} {date_value.strftime('%b')}"


def _date_coverage_label(dates: list[str]) -> str:
    if not dates:
        return "None"
    if len(dates) == 1:
        return _short_date(dates[0])
    start_label = _short_date(dates[0])
    end_label = _short_date(dates[-1])
    return f"{start_label} to {end_label}"


def _schedule_page_counts(payload: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    page_texts = payload.get("page_texts") if isinstance(payload.get("page_texts"), list) else []
    for page_text in page_texts:
        if not isinstance(page_text, dict):
            continue
        text = str(page_text.get("text") or "")
        for key, pattern in SCHEDULE_COUNT_PATTERNS.items():
            for match in pattern.finditer(text):
                counts[key] = int(match.group(1))
    return counts


def _capture_stats(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    target_track = ""
    saved_schedule_rows = None
    for event in events:
        event_text = str(event)
        if event_text.startswith("Target schedule view:"):
            target_track = event_text.split(":", 1)[1].strip().rstrip(".")
        elif event_text.startswith("Target schedule tracks:"):
            target_track = event_text.split(":", 1)[1].strip().rstrip(".")
        elif event_text.startswith("Target schedule track:"):
            target_track = event_text.split(":", 1)[1].strip().rstrip(".")
        saved_match = re.search(r"Saved\s+(\d+)\s+schedule rows locally", event_text, re.IGNORECASE)
        if saved_match:
            saved_schedule_rows = int(saved_match.group(1))

    own_shift_dates = _record_dates(payload.get("extracted_shifts"))
    schedule_dates = _record_dates(payload.get("extracted_schedule_shifts"))
    schedule_records = len(payload.get("extracted_schedule_shifts") or [])
    shift_records = len(payload.get("extracted_shifts") or [])
    response_count = len(payload.get("responses") or [])
    schedule_rows = payload.get("extracted_schedule_shifts") if isinstance(payload.get("extracted_schedule_shifts"), list) else []
    row_open_records = sum(
        1
        for row in schedule_rows
        if isinstance(row, dict) and (row.get("isOpen") or not str(row.get("employeeName") or "").strip())
    )
    row_published_records = sum(1 for row in schedule_rows if isinstance(row, dict) and row.get("isPublished"))
    page_counts = _schedule_page_counts(payload)

    stats = {
        "target_track": target_track,
        "responses": response_count,
        "shift_records": shift_records,
        "schedule_records": schedule_records,
        "saved_schedule_rows": saved_schedule_rows if saved_schedule_rows is not None else schedule_records,
        "row_open_records": row_open_records,
        "row_published_records": row_published_records,
        "own_shift_date_count": len(own_shift_dates),
        "own_shift_date_label": _date_coverage_label(own_shift_dates),
        "own_shift_dates": own_shift_dates,
        "schedule_date_count": len(schedule_dates),
        "schedule_date_label": _date_coverage_label(schedule_dates),
        "schedule_dates": schedule_dates,
        "coverage_warning": schedule_records > 0 and len(schedule_dates) <= 1,
    }
    stats.update(page_counts)
    if "open_shift_count" not in stats:
        stats["open_shift_count"] = row_open_records
    if "published_count" not in stats:
        stats["published_count"] = row_published_records
    return stats


def _capture_copy_text(payload: dict[str, Any]) -> str:
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else _capture_stats(payload)
    lines = [
        "Deputy Web Capture",
        f"Captured: {payload.get('captured_at') or 'unknown'}",
        f"Status: {payload.get('status') or 'unknown'}",
        f"Responses: {len(payload.get('responses') or [])}",
        f"Shift records: {len(payload.get('extracted_shifts') or [])}",
        f"Schedule shift records: {len(payload.get('extracted_schedule_shifts') or [])}",
        f"Target schedule view: {stats.get('target_track') or 'unknown'}",
        (
            "Schedule counts: "
            f"{stats.get('published_count', 0)} published, "
            f"{stats.get('open_shift_count', 0)} open, "
            f"{stats.get('unavailable_count', 0)} unavailable, "
            f"{stats.get('warning_count', 0)} warnings"
        ),
        f"Own shift date coverage: {stats.get('own_shift_date_label') or 'None'} ({stats.get('own_shift_date_count') or 0} days)",
        f"Crew schedule date coverage: {stats.get('schedule_date_label') or 'None'} ({stats.get('schedule_date_count') or 0} days)",
        "",
        "Run Log:",
    ]

    events = payload.get("events") or []
    if events:
        lines.extend(f"- {event}" for event in events)
    else:
        lines.append("- No run log entries.")

    page_texts = payload.get("page_texts") or []
    if page_texts:
        lines.extend(["", "Page Text Snapshots:"])
        for index, page_text in enumerate(page_texts, start=1):
            if not isinstance(page_text, dict):
                continue
            lines.extend(
                [
                    "",
                    f"--- Page Text {index}: {page_text.get('label') or 'Snapshot'} ---",
                    str(page_text.get("text") or ""),
                ]
            )

    extracted_shifts = payload.get("extracted_shifts") or []
    if extracted_shifts:
        lines.extend(["", "Extracted Shift Records:"])
        for shift in extracted_shifts:
            lines.append(json.dumps(_safe_json_sample(shift), ensure_ascii=True, sort_keys=True))

    extracted_schedule_shifts = payload.get("extracted_schedule_shifts") or []
    if extracted_schedule_shifts:
        area_refs = {
            str(area.get("id") or ""): area
            for area in payload.get("areas") or []
            if isinstance(area, dict)
        }
        used_area_ids = {str(shift.get("area") or "") for shift in extracted_schedule_shifts if isinstance(shift, dict)}
        if area_refs:
            lines.extend(["", "Schedule Area References:"])
            for area_id in sorted(
                used_area_ids,
                key=lambda item: (
                    int(area_refs.get(item, {}).get("rosterSortOrder") or 999999),
                    str(area_refs.get(item, {}).get("name") or ""),
                    item,
                ),
            ):
                area = area_refs.get(area_id)
                if area:
                    lines.append(json.dumps(_safe_json_sample(area), ensure_ascii=True, sort_keys=True))
        lines.extend(["", "Extracted Schedule Shift Records:"])
        for shift in extracted_schedule_shifts:
            lines.append(json.dumps(_safe_json_sample(shift), ensure_ascii=True, sort_keys=True))

    responses = payload.get("responses") or []
    if responses:
        lines.extend(["", "Captured Response Summary:"])
        for index, response in enumerate(responses, start=1):
            if not isinstance(response, dict):
                continue
            shape = response.get("shape") if isinstance(response.get("shape"), dict) else {}
            shape_bits = [str(shape.get("kind") or "unknown")]
            if shape.get("count") is not None:
                shape_bits.append(f"count={shape.get('count')}")
            keys = shape.get("keys")
            if isinstance(keys, list) and keys:
                shape_bits.append("keys=" + ", ".join(str(key) for key in keys))
            lines.extend(
                [
                    "",
                    f"--- Response {index}: {response.get('method')} {response.get('status')} ---",
                    str(response.get("url") or ""),
                    "Shape: " + " | ".join(shape_bits),
                ]
            )
            if not _include_full_response_in_copy(response):
                continue
            if "request_sample" in response:
                lines.extend(
                    [
                        "Request JSON:",
                        json.dumps(response.get("request_sample"), ensure_ascii=True, indent=2, sort_keys=True),
                    ]
                )
            lines.extend(
                [
                    "Sample JSON:",
                    json.dumps(response.get("sample"), ensure_ascii=True, indent=2, sort_keys=True),
                ]
            )

    return "\n".join(lines).strip()
