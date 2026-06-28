from __future__ import annotations

import calendar
import json
import re
import threading
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware

from .auth import clear_trusted_device, current_user, require_admin_user, trusted_device_middleware
from .config import get_settings
from .database import (
    clear_all_changed_flags,
    clear_changed_flags_for_user,
    clear_changed_for_date,
    clear_changed_for_shift,
    count_active_admins,
    count_app_users,
    create_admin_override,
    create_app_user,
    create_error_report,
    create_trusted_device,
    DEPUTY_AREA_OVERRIDES,
    ensure_user_sync_state,
    fetch_open_deputy_schedule_between,
    fetch_open_deputy_schedule_shifts,
    fetch_tbc_deputy_schedule_between,
    fetch_love_racing_meetings_between,
    fetch_deputy_schedule_for_date,
    fetch_deputy_schedule_areas_for_locations,
    fetch_shifts_for_travel_learning,
    get_calendar_url,
    get_calendar_url_source,
    get_app_setting,
    get_deputy_user_secret,
    get_deputy_schedule_snapshot,
    get_app_user,
    get_app_user_by_email,
    get_shift_changes_for_date,
    get_last_deputy_web_capture,
    get_latest_deputy_web_capture_for_user,
    get_love_racing_snapshot,
    fetch_shift,
    fetch_shifts_between,
    fetch_shifts_for_date,
    get_last_successful_sync,
    get_next_upcoming_shift,
    get_recent_source_payloads,
    get_travel_time_default,
    get_recent_sync_logs,
    get_upcoming_shifts,
    get_user_sync_state,
    init_db,
    list_known_racecourse_names,
    list_admin_overrides,
    list_app_users,
    list_error_reports,
    list_travel_time_defaults,
    list_trusted_devices_for_user,
    mark_user_sync_finished,
    mark_user_sync_started,
    revoke_trusted_device_for_user,
    reset_incomplete_user_syncs,
    reset_user_roster_data,
    purge_app_user,
    purge_old_inactive_records,
    save_love_racing_meetings,
    update_deputy_user_ical_url,
    update_deputy_user_credentials,
    update_app_settings,
    update_shift_marks,
    set_app_user_active,
    upsert_travel_time_default,
    delete_travel_time_default,
    update_travel_time_default,
    update_user_display_theme,
    update_user_pin_hash,
    user_has_deputy_credentials,
    user_has_ical_url,
)
from .deputy_api import test_deputy_roster_api
from .deputy_web import capture_and_save_deputy_web, format_capture_payload
from .love_racing import LOVE_RACING_URL, fetch_love_racing_meetings
from .scheduler import get_pre_shift_status, shutdown_scheduler, start_scheduler, sync_roster_sources
from .security import (
    SESSION_COOKIE_NAME,
    encrypt_text,
    hash_pin,
    hash_session_token,
    new_session_token,
    session_expires_at,
    verify_pin,
)
from .user_credentials import settings_for_user


APP_DIR = Path(__file__).resolve().parent
APP_VERSION = "0.5.0"
APP_BUILD = "2026.06.29.1"
MARK_FIELDS = (
    ("checked", "Checked"),
    ("confirmed", "Confirmed"),
    ("important", "Important"),
    ("question", "Question"),
    ("early_start", "Early start"),
    ("gear_needed", "Gear"),
    ("travel_needed", "Travel"),
    ("pay_check", "Pay check"),
)
THEME_GROUPS = (
    {
        "id": "dark",
        "label": "Dark",
        "column": "left",
        "themes": (
            {"value": "jade", "label": "Jade dark", "swatches": ("#101114", "#181b20", "#33c4a5", "#ffdd8a")},
            {"value": "steel", "label": "Steel dark", "swatches": ("#0d1114", "#20272d", "#8fc7d5", "#ffd878")},
            {"value": "moss", "label": "Moss dark", "swatches": ("#10130f", "#22291e", "#b7c96d", "#ffda86")},
            {"value": "rose", "label": "Rose dark", "swatches": ("#130f12", "#2a2027", "#ef9ca8", "#ffdc83")},
            {"value": "amber", "label": "Amber dark", "swatches": ("#11100c", "#272318", "#e0b858", "#ffe38d")},
        ),
    },
    {
        "id": "bright",
        "label": "Bright",
        "column": "left",
        "themes": (
            {"value": "daylight", "label": "Daylight", "swatches": ("#f7faf8", "#ffffff", "#126f62", "#b5791e")},
            {"value": "paper", "label": "Paper", "swatches": ("#fbfaf6", "#ffffff", "#7c4f18", "#aa781f")},
            {"value": "mint", "label": "Mint", "swatches": ("#f3fbf6", "#ffffff", "#167347", "#b87b1d")},
            {"value": "sky", "label": "Sky", "swatches": ("#f3f8fc", "#ffffff", "#1d638e", "#b37617")},
            {"value": "peach", "label": "Peach", "swatches": ("#fff7f2", "#ffffff", "#9a4a2c", "#a8741a")},
        ),
    },
    {
        "id": "special",
        "label": "Special / Colorful",
        "column": "right",
        "themes": (
            {"value": "track-colours", "label": "Track colours", "swatches": ("#f7f7fb", "#ffffff", "linear-gradient(90deg, #33c4a5, #ef9ca8, #e0b858)", "#ad771d")},
            {"value": "aurora", "label": "Aurora", "swatches": ("#0b1020", "#1d2740", "linear-gradient(90deg, #80e7d3, #9aa8ff)", "#ffe18a")},
            {"value": "sunset", "label": "Sunset", "swatches": ("#170d17", "#332235", "linear-gradient(90deg, #ffb06c, #ef9ca8)", "#ffe28f")},
            {"value": "ocean", "label": "Ocean", "swatches": ("#071316", "#193035", "linear-gradient(90deg, #6bd6ff, #33c4a5)", "#ffe292")},
            {"value": "berry", "label": "Berry", "swatches": ("#160d1f", "#332044", "linear-gradient(90deg, #d7a5ff, #ef9ca8)", "#ffe190")},
            {"value": "candy", "label": "Candy", "swatches": ("#fff7fb", "#ffffff", "linear-gradient(90deg, #9b3272, #d69cff)", "#aa761d")},
            {"value": "high-contrast", "label": "High contrast", "swatches": ("#000000", "#111111", "#ffe45c", "#ffffff")},
            {"value": "race-night", "label": "Race night", "swatches": ("#08090d", "#20222c", "linear-gradient(90deg, #ff5c7a, #00e5ff)", "#ffe48d")},
            {"value": "garden", "label": "Garden", "swatches": ("#f6faf0", "#ffffff", "linear-gradient(90deg, #4f6f20, #7fe08b)", "#ae771a")},
            {"value": "studio", "label": "Studio", "swatches": ("#f6f6f6", "#ffffff", "#3f4f5f", "#a06d18")},
        ),
    },
)
THEME_OPTIONS = tuple(theme for group in THEME_GROUPS for theme in group["themes"])
THEME_VALUES = {str(theme["value"]) for theme in THEME_OPTIONS}
THEME_LABELS = {str(theme["value"]): str(theme["label"]) for theme in THEME_OPTIONS}
LOCATION_COLOUR_COUNT = 10
LOCATION_COLOUR_INDEX_BY_KEY = {
    "ellet": 1,
    "ellerslie": 1,
    "100ascotavenue": 1,
    "64": 2,
    "camst": 2,
    "tcambridge": 2,
    "cambridgesynthetic": 2,
    "40racecourseroad": 2,
    "63": 3,
    "trapt": 3,
    "terapa": 3,
    "12sirtristramavenue": 3,
    "tarot": 4,
    "tearoha": 4,
    "stanleyroadsouth": 4,
    "68": 5,
    "matat": 5,
    "matamata": 5,
    "statehighway27": 5,
    "62": 6,
    "puket": 6,
    "pukekohe": 6,
    "222manukauroad": 6,
    "121": 7,
    "hcambridge": 7,
    "cambridgeharness": 7,
    "cambridge": 7,
    "1taylorstreet": 7,
    "129": 8,
    "rotorua": 8,
    "tr": 8,
    "274278fentonstreetglenholmerotorua3010": 8,
    "66": 9,
    "taurt": 9,
    "tauranga": 9,
    "1383cameronroad": 9,
    "ruakt": 10,
    "ruak": 10,
    "ruakaka": 10,
    "petersnellroad": 10,
}
SECRET_URL_RE = re.compile(r"(calendar\?ap=)[^&\s\"']+")
URL_RE = re.compile(r"https?://\S+")
SUMMARY_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
GENERIC_TRACK_LABELS = {"web", "shift", ""}
GENERIC_ROLE_LABELS = {"shift", ""}
CONTEXT_ONLY_ROLE_KEYS = {
    "",
    "shift",
    "manager",
    "northern",
    "contractor",
    "contractors",
    "northernops",
    "northernopscontractors",
}
KNOWN_SHIFT_CONTEXT_FALLBACKS = (
    {
        "date": "2026-07-03",
        "start": "15:00",
        "end": "21:00",
        "source_code": "H-Cambridge",
        "location": "1 Taylor Street",
        "location_id": 121,
        "role_by_name": (
            ("jayden", "Director"),
            ("josh", "Side 1"),
            ("joshua", "Side 1"),
            ("nate", "Side 2"),
            ("elliot", "Head On"),
            ("olivia", "Back"),
            ("laine", "RTS"),
            ("bj", "Engineer"),
            ("brendan", "Engineer"),
            ("gary", "CCU1"),
            ("lans", "CCU2"),
            ("grant", "Sound VT"),
            ("sharne", "Floor Manager"),
        ),
    },
)
TRACK_NAMES = {
    "CAM": "Cambridge",
    "CAMBRIDGE": "Cambridge",
    "CAMS": "Cambridge Synthetic",
    "CAMS-T": "Cambridge Synthetic",
    "ELLE": "Ellerslie",
    "ELLE-T": "Ellerslie",
    "MATA": "Matamata",
    "MATA-T": "Matamata",
    "PUKE": "Pukekohe",
    "PUKE-T": "Pukekohe",
    "R": "Rotorua",
    "ROTORUA": "Rotorua",
    "RUAK": "Ruakaka",
    "RUAK-T": "Ruakaka",
    "TARO": "Te Aroha",
    "TARO-T": "Te Aroha",
    "TAUR": "Tauranga",
    "TAUR-T": "Tauranga",
    "TRAP": "Te Rapa",
    "TRAP-T": "Te Rapa",
    "T-R": "Rotorua",
    "8PE": "Out of Region",
    "VEH": "Vehicles",
}
RACE_TYPES = {
    "T": "Thoroughbred racing",
    "H": "Harness racing",
    "G": "Greyhound racing",
}
ROLE_NAMES = {
    "DIR": "Director",
    "SOUND": "Sound",
    "SVT": "Sound VT",
}
DEFAULT_RACE_TYPE_BY_CODE = {
    "CAM": "H",
}
TIMESHEET_ANCHOR_DATE = date(2026, 6, 7)
RACE_RUN_MINUTES = 3
PACKUP_MINUTES = 60
CHANGE_FIELD_LABELS = {
    "title": "Roster title",
    "description": "Roster notes",
    "location": "Location",
    "start_at": "Roster start",
    "end_at": "Roster finish",
    "raw_hours": "Rostered hours",
    "break_minutes": "Break",
    "paid_hours": "Rostered hours",
    "source_link": "Deputy link",
    "source_status": "Status",
    "deleted_from_source": "Cancelled",
}
HIDDEN_CHANGE_FIELDS = {"break_minutes", "paid_hours"}
TIMING_LINE_PATTERNS = (
    ("Trucks", re.compile(r"^trucks?\s+(.+)$", re.IGNORECASE)),
    ("Office", re.compile(r"^office\s+(.+)$", re.IGNORECASE)),
    ("Clow Place", re.compile(r"^clow\s+(?:place|pl)\s+(.+)$", re.IGNORECASE)),
    ("On track", re.compile(r"^on\s+track\s+(.+)$", re.IGNORECASE)),
    ("First cross", re.compile(r"^first\s+cross\s+(.+)$", re.IGNORECASE)),
)
TIME_FIRST_TIMING_RE = re.compile(
    r"^([0-9: ]{3,5}\s*(?:am|pm)?)\s+(trucks?|office|clow\s+(?:place|pl)|on\s+track|first\s+cross|fx)\b(?:\s+(.+))?$",
    re.IGNORECASE,
)
INLINE_TIMING_RE = re.compile(
    r"\b(first race|last race|first cross|live)\s+([0-9: ]{3,5}\s*(?:am|pm)?)",
    re.IGNORECASE,
)
RACE_COUNT_RE = re.compile(r"\b(\d+)\s+races?\b", re.IGNORECASE)
RACE_COUNT_WITH_TIMES_RE = re.compile(
    r"\b(\d+)\s+races?\s+([0-9: ]{3,5}\s*(?:am|pm)?)\s*(?:[-–]|\|)\s*([0-9: ]{3,5}\s*(?:am|pm)?)",
    re.IGNORECASE,
)
CREW_LINE_RE = re.compile(r"^([A-Za-z]{1,8}\d{0,3}|\d{3,4})\s+(.+)$")
NON_CREW_LABELS = {"office", "trucks", "truck", "clow", "on", "first", "last", "race", "races", "breaks", "records"}
VEHICLE_ALLOCATION_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*\d*|(?<!\d)\d{3}(?!\d)")
VEHICLE_ALLOCATION_TOKEN_RE = re.compile(r"^(?:\d{3}|rav\d+|rp\d+|ob|tender|transit)$", re.IGNORECASE)
TIMING_LABELS = {
    "truck": "Trucks",
    "trucks": "Trucks",
    "office": "Office",
    "clow place": "Clow Place",
    "clow pl": "Clow Place",
    "on track": "On track",
    "first cross": "First cross",
    "fx": "FX",
    "first race": "First race",
    "last race": "Last race",
}
VEHICLE_ROLE_LABELS = {
    "684",
    "685",
    "OB",
    "RP1",
    "TENDER",
    "TRANSIT",
    "RAV91",
}
NOTE_NAME_ALIASES = {
    "cambo": ("campbell",),
    "gaz": ("gary",),
    "josh": ("joshua",),
}
SCHEDULE_POSITION_ALIASES = {
    "side1": ("side1", "Side 1"),
    "sideone": ("side1", "Side 1"),
    "side1camera": ("side1", "Side 1"),
    "side1cam": ("side1", "Side 1"),
    "sideonecamera": ("side1", "Side 1"),
    "sideonecam": ("side1", "Side 1"),
    "side2": ("side2", "Side 2"),
    "sidetwo": ("side2", "Side 2"),
    "side2camera": ("side2", "Side 2"),
    "side2cam": ("side2", "Side 2"),
    "sidetwocamera": ("side2", "Side 2"),
    "sidetwocam": ("side2", "Side 2"),
    "start": ("start", "Start"),
    "startcamera": ("start", "Start"),
    "startcam": ("start", "Start"),
    "headon": ("headon", "Head On"),
    "headoncamera": ("headon", "Head On"),
    "headoncam": ("headon", "Head On"),
    "back": ("back", "Back"),
    "backcamera": ("back", "Back"),
    "back2": ("back2", "Back2"),
    "backtwo": ("back2", "Back2"),
    "back2camera": ("back2", "Back2"),
    "backtwocamera": ("back2", "Back2"),
    "turn": ("turn", "Turn"),
    "turncamera": ("turn", "Turn"),
    "ivbp": ("ivbp", "IV / BP"),
    "ivandbp": ("ivbp", "IV / BP"),
    "ivbpcamera": ("ivbp", "IV / BP"),
    "rts": ("rts", "RTS"),
    "iv1": ("iv1", "IV1"),
    "ivone": ("iv1", "IV1"),
    "gimbal": ("gimbal", "Gimbal"),
    "gimbals": ("gimbal", "Gimbal"),
    "gimball": ("gimbal", "Gimbal"),
    "gimballs": ("gimbal", "Gimbal"),
    "gimble": ("gimbal", "Gimbal"),
    "gimbalcamera": ("gimbal", "Gimbal"),
    "gimballcamera": ("gimbal", "Gimbal"),
    "gimbalassist": ("gimbalassist", "Gimbal Assist"),
    "gimbalsassist": ("gimbalassist", "Gimbal Assist"),
    "gimballassist": ("gimbalassist", "Gimbal Assist"),
    "gimballsassist": ("gimbalassist", "Gimbal Assist"),
    "gimbalassistant": ("gimbalassist", "Gimbal Assist"),
    "gimbalsassistant": ("gimbalassist", "Gimbal Assist"),
    "gimballassistant": ("gimbalassist", "Gimbal Assist"),
    "gimballsassistant": ("gimbalassist", "Gimbal Assist"),
    "steadi": ("steadi", "Steadi"),
    "steady": ("steadi", "Steadi"),
    "steadicam": ("steadi", "Steadi"),
    "steadycam": ("steadi", "Steadi"),
    "steadycamera": ("steadi", "Steadi"),
    "steadicamera": ("steadi", "Steadi"),
    "steadiassist": ("steadiassist", "Steadi Assist"),
    "steadyassist": ("steadiassist", "Steadi Assist"),
    "steadicamassist": ("steadiassist", "Steadi Assist"),
    "steadycamassist": ("steadiassist", "Steadi Assist"),
    "steadiassistant": ("steadiassist", "Steadi Assist"),
    "steadyassistant": ("steadiassist", "Steadi Assist"),
    "ldho": ("ldho", "LDHO"),
    "director": ("director", "Director"),
    "dir": ("director", "Director"),
    "northern": ("northern", "Northern"),
    "sound": ("sound", "Sound"),
    "soundvt": ("soundvt", "Sound VT"),
    "svt": ("soundvt", "Sound VT"),
    "vt": ("vt", "VT"),
    "ccu1": ("ccu1", "CCU1"),
    "ccuone": ("ccu1", "CCU1"),
    "ccu2": ("ccu2", "CCU2"),
    "ccutwo": ("ccu2", "CCU2"),
    "fm": ("fm", "FM"),
    "eng": ("eng", "ENG"),
    "engineer": ("eng", "ENG"),
}
SCHEDULE_POSITION_ORDER = {
    "side1": 10,
    "side2": 20,
    "start": 30,
    "headon": 40,
    "back": 50,
    "back2": 60,
    "turn": 70,
    "ivbp": 80,
    "rts": 90,
    "iv1": 100,
    "gimbal": 110,
    "gimbalassist": 111,
    "steadi": 120,
    "steadiassist": 121,
    "ldho": 130,
    "director": 200,
    "northern": 210,
    "sound": 220,
    "soundvt": 230,
    "vt": 240,
    "ccu1": 250,
    "ccu2": 260,
    "fm": 270,
    "eng": 280,
}
HIDDEN_SCHEDULE_POSITION_KEYS = {
    "outofregion",
}
PLACEHOLDER_SCHEDULE_POSITION_KEYS = set(SCHEDULE_POSITION_ORDER) - {"northern"}


app = FastAPI(
    title="Deputy Roster View",
)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.middleware("http")(trusted_device_middleware)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
_sync_worker_lock = threading.Lock()
_sync_state_lock = threading.Lock()
_manual_sync_status_by_scope: dict[str, dict[str, object]] = {}


@app.middleware("http")
async def static_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_datetime(value: str | None, fmt: str = "%a %d %b %H:%M") -> str:
    dt = parse_iso_datetime(value)
    return dt.strftime(fmt) if dt else ""


def latest_iso_datetime(*values: object) -> str:
    latest: datetime | None = None
    latest_text = ""
    for value in values:
        text = str(value or "").strip()
        dt = parse_iso_datetime(text)
        if dt and (latest is None or dt > latest):
            latest = dt
            latest_text = text
    return latest_text


def format_day_short(value: str | None) -> str:
    return format_datetime(value, "%a %d %b")


def format_time(value: str | None) -> str:
    return format_datetime(value, "%H:%M")


def format_hours(value: float | int | None) -> str:
    try:
        total_minutes = int(round(float(value) * 60))
    except (TypeError, ValueError):
        return "0h"
    hours, minutes = divmod(total_minutes, 60)
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes:02d}m"


def format_minutes_duration(minutes: int | float | None) -> str:
    if minutes is None:
        return "0h"
    return format_hours(float(minutes) / 60)


def timesheet_due_date(day_value: date) -> bool:
    days_since_anchor = (day_value - TIMESHEET_ANCHOR_DATE).days
    return days_since_anchor >= 0 and days_since_anchor % 14 == 0


def timesheet_period(day_value: date) -> tuple[date, date]:
    return day_value - timedelta(days=13), day_value


def timesheet_marker(day_value: date) -> dict[str, object] | None:
    if not timesheet_due_date(day_value):
        return None
    period_start, period_end = timesheet_period(day_value)
    return {
        "date": day_value,
        "iso": day_value.isoformat(),
        "label": "Timesheet submission",
        "period_start": period_start,
        "period_end": period_end,
        "period_label": f"{period_start.strftime('%d %b')}-{period_end.strftime('%d %b')}",
        "url": f"/timesheet/{day_value.isoformat()}",
    }


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = (year * 12 + (month - 1)) + delta
    return month_index // 12, (month_index % 12) + 1


def clean_colour(value: str | None) -> str:
    value = (value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    return ""


def clean_time_value(value: str | None) -> str:
    value = (value or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", value):
        return ""
    hour, minute = (int(part) for part in value.split(":"))
    if hour > 23 or minute > 59:
        return ""
    return value


def normalise_theme(value: object) -> str:
    theme = str(value or "").strip().lower()
    return theme if theme in THEME_VALUES else "jade"


def location_colour_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def stable_location_colour_index(*values: object) -> int:
    for value in values:
        mapped = LOCATION_COLOUR_INDEX_BY_KEY.get(location_colour_key(value))
        if mapped:
            return mapped
    key = "|".join(str(value or "").strip().lower() for value in values if str(value or "").strip())
    if not key:
        return 1
    total = sum((index + 1) * ord(char) for index, char in enumerate(key))
    return (total % LOCATION_COLOUR_COUNT) + 1


def travel_default_key(value: object) -> str:
    return location_colour_key(value)


def travel_default_keys_for_shift(shift: dict[str, object]) -> list[str]:
    values = [
        shift.get("track_label"),
        shift.get("source_code"),
        shift.get("location"),
        shift.get("schedule_location_id"),
    ]
    keys = []
    for value in values:
        key = travel_default_key(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def travel_default_for_shift(shift: dict[str, object], base_label: str = "Clow Place") -> dict[str, object] | None:
    keys = travel_default_keys_for_shift(shift)
    row = get_travel_time_default(keys, base_label=base_label)
    if row is None and base_label.lower() in {"office", "clow place"}:
        fallback_base = "Office" if base_label.lower() == "clow place" else "Clow Place"
        row = get_travel_time_default(keys, base_label=fallback_base)
    return dict(row) if row else None


def redact_secret_text(value: str) -> str:
    return SECRET_URL_RE.sub(r"\1[redacted]", value)


def description_lines(description: str) -> list[str]:
    lines = []
    for line in (description or "").splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue
        clean_line = re.split(r"(?i)\s*breaks:\s*", clean_line)[0].strip()
        if not clean_line:
            continue
        lower_line = clean_line.lower()
        if lower_line.startswith("open in deputy"):
            continue
        if lower_line == "breaks:" or "meal break" in lower_line:
            continue
        lines.append(clean_line)
    return lines


def normalise_roster_time(value: str) -> str:
    cleaned = re.sub(r"\s+", "", value.strip().lower().replace(".", ""))
    if not cleaned:
        return value.strip()
    meridiem = ""
    if cleaned.endswith(("am", "pm")):
        meridiem = cleaned[-2:]
        cleaned = cleaned[:-2]

    hour = ""
    minute = ""
    if ":" in cleaned:
        hour, minute = cleaned.split(":", 1)
    elif len(cleaned) <= 2:
        hour, minute = cleaned, "00"
    elif len(cleaned) == 3:
        hour, minute = cleaned[:1], cleaned[1:]
    else:
        hour, minute = cleaned[:2], cleaned[2:4]

    if not hour.isdigit() or not minute.isdigit():
        return value.strip()
    hour_int = int(hour)
    minute_int = int(minute)
    if meridiem == "pm" and hour_int < 12:
        hour_int += 12
    elif meridiem == "am" and hour_int == 12:
        hour_int = 0
    if hour_int > 23 or minute_int > 59:
        return value.strip()
    return f"{hour_int:02d}:{minute_int:02d}"


def clean_timing_value(value: str) -> str:
    value = value.strip().rstrip(".,")
    match = re.search(r"([0-9: ]{2,5}\s*(?:am|pm)?)", value, flags=re.IGNORECASE)
    return normalise_roster_time(match.group(1)) if match else value


def timing_lookup(summary: dict[str, object]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for item in summary.get("timings") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip().lower()
        time_value = clean_time_value(str(item.get("time") or ""))
        if label and time_value:
            lookup[label] = time_value
    return lookup


def clock_datetime_for_shift(shift: dict[str, object], clock_value: str, after: datetime | None = None) -> datetime | None:
    clock_value = clean_time_value(clock_value)
    if not re.fullmatch(r"\d{2}:\d{2}", clock_value):
        return None
    start_at = parse_iso_datetime(str(shift.get("start_at") or ""))
    if start_at is None:
        try:
            start_at = datetime.fromisoformat(str(shift.get("date") or ""))
        except ValueError:
            return None
    hour, minute = (int(part) for part in clock_value.split(":"))
    result = start_at.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if after is not None:
        while result < after:
            result += timedelta(days=1)
    return result


def ceil_datetime_to_quarter(value: datetime) -> datetime:
    value = value.replace(second=0, microsecond=0)
    remainder = value.minute % 15
    if remainder == 0:
        return value
    return value + timedelta(minutes=15 - remainder)


def shift_hours_value(shift: dict[str, object]) -> float:
    value = shift.get("display_hours")
    if value is None:
        value = shift.get("paid_hours")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def vehicle_note_label(value: str) -> str:
    clean_value = value.strip()
    upper_value = clean_value.upper()
    if upper_value == "OB":
        return "OB"
    if upper_value.startswith("RAV") and upper_value[3:].isdigit():
        return f"Rav{upper_value[3:]}"
    if upper_value.startswith("RP") and upper_value[2:].isdigit():
        return f"RP{upper_value[2:]}"
    if upper_value == "TENDER":
        return "Tender"
    if upper_value == "TRANSIT":
        return "Transit"
    return clean_value


def note_vehicle_allocations_from_text(value: str) -> list[dict[str, object]]:
    text = re.split(r"\s+[-–]\s+", value, maxsplit=1)[-1]
    tokens = VEHICLE_ALLOCATION_WORD_RE.findall(text)
    if not any(VEHICLE_ALLOCATION_TOKEN_RE.match(token) for token in tokens):
        return []

    allocations: dict[str, list[str]] = {}
    current_vehicle = ""
    pending_people: list[str] = []
    for token in tokens:
        if VEHICLE_ALLOCATION_TOKEN_RE.match(token):
            vehicle = vehicle_note_label(token)
            if pending_people and not current_vehicle:
                allocations.setdefault(vehicle, []).extend(pending_people)
                pending_people = []
                current_vehicle = ""
            else:
                current_vehicle = vehicle
                allocations.setdefault(current_vehicle, [])
            continue

        person_token = token.strip(" ,")
        if not person_token:
            continue
        if current_vehicle:
            allocations.setdefault(current_vehicle, []).append(person_token)
        else:
            pending_people.append(person_token)

    return [
        {"vehicle": vehicle, "people": people}
        for vehicle, people in allocations.items()
        if vehicle and people
    ]


def parse_roster_summary(lines: list[str]) -> dict[str, object]:
    timings: list[dict[str, str]] = []
    production_notes: list[str] = []
    crew_allocations: list[dict[str, str]] = []
    other_lines: list[str] = []
    consumed: set[int] = set()

    def add_timing(label: str, value: str) -> None:
        label = TIMING_LABELS.get(label.strip().lower(), label.strip())
        time_value = clean_timing_value(value)
        if not any(item["label"].lower() == label.lower() and item["time"] == time_value for item in timings):
            timings.append({"label": label, "time": time_value})

    for index, line in enumerate(lines):
        for allocation in note_vehicle_allocations_from_text(line):
            crew_allocations.append(
                {
                    "vehicle": str(allocation.get("vehicle") or ""),
                    "people": " ".join(str(name) for name in allocation.get("people") or []),
                }
            )

        time_first = TIME_FIRST_TIMING_RE.match(line)
        if time_first:
            add_timing(time_first.group(2), time_first.group(1))
            consumed.add(index)
            continue

        for label, pattern in TIMING_LINE_PATTERNS:
            match = pattern.match(line)
            if match:
                add_timing(label, match.group(1))
                consumed.add(index)
                break

        race_count = RACE_COUNT_RE.search(line)
        if race_count:
            note = f"{race_count.group(1)} races"
            if note not in production_notes:
                production_notes.append(note)
            consumed.add(index)

        race_times = RACE_COUNT_WITH_TIMES_RE.search(line)
        if race_times:
            add_timing("First race", race_times.group(2))
            add_timing("Last race", race_times.group(3))
            consumed.add(index)

        inline_matches = list(INLINE_TIMING_RE.finditer(line))
        if inline_matches:
            for match in inline_matches:
                label = match.group(1).strip().title()
                add_timing(label, match.group(2))
            consumed.add(index)

    for index, line in enumerate(lines):
        if index in consumed:
            continue
        crew_match = CREW_LINE_RE.match(line)
        if crew_match:
            vehicle = crew_match.group(1).strip()
            lower_vehicle = vehicle.lower()
            if lower_vehicle not in NON_CREW_LABELS:
                crew_allocations.append({"vehicle": vehicle, "people": crew_match.group(2).strip()})
                consumed.add(index)

    for index, line in enumerate(lines):
        if index not in consumed:
            other_lines.append(line)

    return {
        "timings": timings,
        "production_notes": production_notes,
        "crew_allocations": crew_allocations,
        "other_lines": other_lines,
        "has_structured": bool(timings or production_notes or crew_allocations),
    }


def pretty_source_payload(value: str | None) -> str:
    if not value:
        return ""
    try:
        payload = json.loads(value)
        rendered = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        rendered = str(value)
    return redact_secret_text(rendered)


def payload_root(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def truncate_diagnostic_text(value: str, limit: int = 180_000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n\n[diagnostic text truncated after {limit} characters]"


def safe_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def unique_ints(values: list[object]) -> list[int]:
    result = []
    seen = set()
    for value in values:
        int_value = safe_int(value)
        if int_value is None or int_value in seen:
            continue
        seen.add(int_value)
        result.append(int_value)
    return result


def source_payload_normalised(value: str | None) -> dict[str, object]:
    payload = payload_root(value)
    normalised = payload.get("normalised", {})
    return normalised if isinstance(normalised, dict) else {}


def source_payload_diagnostics(value: str | None) -> dict[str, object]:
    if not value:
        return {"fields": [], "description_lines": [], "hidden_links": []}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {"fields": [{"label": "Raw payload", "value": redact_secret_text(str(value))}], "description_lines": [], "hidden_links": []}

    normalised = payload.get("normalised", {}) if isinstance(payload, dict) else {}
    if not isinstance(normalised, dict):
        normalised = {}

    field_labels = (
        ("uid", "UID"),
        ("summary", "Summary"),
        ("dtstart", "Start"),
        ("dtend", "End"),
        ("location", "Location"),
        ("status", "Status"),
        ("sequence", "Sequence"),
        ("last_modified", "Last modified"),
        ("categories", "Categories"),
    )
    fields = []
    for key, label in field_labels:
        value_text = str(normalised.get(key) or "").strip()
        if value_text:
            fields.append({"label": label, "value": redact_secret_text(value_text)})

    hidden_links = []
    source_link = str(normalised.get("source_link") or "").strip()
    if source_link:
        hidden_links.append({"label": "Deputy link", "value": redact_secret_text(source_link)})

    description = str(normalised.get("description") or "")
    return {
        "fields": fields,
        "description_lines": description_lines(description),
        "hidden_links": hidden_links,
    }


def parse_shift_title(title: str | None) -> dict[str, str]:
    title = (title or "").strip()
    match = SUMMARY_RE.match(title)
    if not match:
        return {
            "source_code": "",
            "track_label": title or "Shift",
            "role_label": "",
            "role_full_label": title or "Shift",
            "race_type_label": "",
            "display_title": title or "Shift",
        }

    source_code = match.group(1).strip()
    role_label = match.group(2).strip()
    if source_code.upper() == "VEH":
        return {
            "source_code": source_code,
            "track_label": "Vehicles",
            "role_label": "Maintenance",
            "role_full_label": "Maintenance",
            "race_type_label": "",
            "display_title": "Vehicle maintenance",
        }

    source_code_upper = source_code.upper()
    race_type_code = ""
    track_code = source_code_upper
    if len(source_code_upper) > 2 and source_code_upper[1] == "-" and source_code_upper[0] in RACE_TYPES:
        race_type_code = source_code_upper[0]
        track_code = source_code_upper[2:]
    elif len(source_code_upper) > 2 and source_code_upper[-2] == "-" and source_code_upper[-1] in RACE_TYPES:
        race_type_code = source_code_upper[-1]
        track_code = source_code_upper[:-2]
    if not race_type_code:
        race_type_code = DEFAULT_RACE_TYPE_BY_CODE.get(track_code, "")

    if race_type_code == "T" and track_code == "CAMBRIDGE":
        track_label = "Cambridge Synthetic"
    elif race_type_code == "H" and track_code == "CAMBRIDGE":
        track_label = "Cambridge Harness"
    elif race_type_code == "G" and track_code == "CAMBRIDGE":
        track_label = "Cambridge Greyhound"
    else:
        track_label = TRACK_NAMES.get(track_code)
    if not track_label:
        base_code = track_code.removesuffix("-T")
        track_label = TRACK_NAMES.get(base_code, track_code.replace("-", " ").title())
    race_type_label = RACE_TYPES.get(race_type_code, "")

    return {
        "source_code": source_code,
        "track_label": track_label,
        "role_label": role_label,
        "role_full_label": ROLE_NAMES.get(role_label.upper(), role_label or "Shift"),
        "race_type_label": race_type_label,
        "display_title": f"{role_label} at {track_label}" if role_label else track_label,
    }


def apply_known_area_override(shift: dict[str, object]) -> None:
    normalised_payload = source_payload_normalised(str(shift.get("source_payload") or ""))
    area_id = safe_int(normalised_payload.get("area_id"))
    override = DEPUTY_AREA_OVERRIDES.get(area_id or -1)
    if not override:
        return
    source_code = str(override.get("source_code") or "").strip()
    role_label = str(override.get("role") or "").strip()
    if not source_code and not role_label:
        return
    parsed = parse_shift_title(f"[{source_code or 'WEB'}] {role_label or 'Shift'}")
    for key in ("source_code", "track_label", "role_label", "role_full_label", "race_type_label", "display_title"):
        value = parsed.get(key)
        if value:
            shift[key] = value
    if override.get("location") and not str(shift.get("location") or "").strip():
        shift["location"] = str(override.get("location") or "")
    override_location_id = safe_int(override.get("location_id"))
    if override_location_id is not None:
        shift["schedule_location_id"] = override_location_id
        shift["schedule_location_ids"] = unique_ints(
            [override_location_id] + list(shift.get("schedule_location_ids") or [])
        )


def payload_employee_name(shift: dict[str, object]) -> str:
    payload = payload_root(str(shift.get("source_payload") or ""))
    deputy_web = payload.get("deputy_web", {}) if isinstance(payload, dict) else {}
    if not isinstance(deputy_web, dict):
        deputy_web = {}
    normalised = payload.get("normalised", {}) if isinstance(payload, dict) else {}
    if not isinstance(normalised, dict):
        normalised = {}
    return str(
        deputy_web.get("employeeName")
        or normalised.get("employee_name")
        or shift.get("employee_name")
        or ""
    ).strip()


def shift_time_matches(shift: dict[str, object], fallback: dict[str, object]) -> bool:
    start_at = parse_iso_datetime(str(shift.get("start_at") or ""))
    end_at = parse_iso_datetime(str(shift.get("end_at") or ""))
    if not start_at or not end_at:
        return False
    return (
        str(shift.get("date") or "") == str(fallback.get("date") or "")
        and start_at.strftime("%H:%M") == str(fallback.get("start") or "")
        and end_at.strftime("%H:%M") == str(fallback.get("end") or "")
    )


def fallback_role_for_employee(employee_name: str, fallback: dict[str, object]) -> str:
    employee_key = re.sub(r"[^a-z0-9]+", " ", employee_name.lower())
    for needle, role in fallback.get("role_by_name", ()):
        needle_key = re.sub(r"[^a-z0-9]+", " ", str(needle).lower()).strip()
        if needle_key and re.search(rf"\b{re.escape(needle_key)}\b", employee_key):
            return str(role)
    return ""


def apply_known_shift_context_fallback(shift: dict[str, object]) -> None:
    source_code = str(shift.get("source_code") or "").strip().lower()
    track_label = str(shift.get("track_label") or "").strip().lower()
    role_label = str(shift.get("role_label") or shift.get("role_full_label") or "").strip()
    has_generic_track = track_label in GENERIC_TRACK_LABELS or source_code in {"web", ""}
    has_generic_role = role_is_context_only(role_label) or role_display_key(role_label) in GENERIC_ROLE_LABELS
    if not has_generic_track and not has_generic_role:
        return

    for fallback in KNOWN_SHIFT_CONTEXT_FALLBACKS:
        if not shift_time_matches(shift, fallback):
            continue
        employee_role = fallback_role_for_employee(payload_employee_name(shift), fallback)
        role = employee_role or ("" if has_generic_role else str(shift.get("role_label") or ""))
        if not role or role.lower() in GENERIC_ROLE_LABELS:
            role = "Shift"
        parsed = parse_shift_title(f"[{fallback['source_code']}] {role}")
        for key in ("source_code", "track_label", "role_label", "role_full_label", "race_type_label", "display_title"):
            value = parsed.get(key)
            if value:
                shift[key] = value
        if fallback.get("location"):
            shift["location"] = str(fallback.get("location") or "")
        location_id = safe_int(fallback.get("location_id"))
        if location_id is not None:
            shift["schedule_location_id"] = location_id
            shift["schedule_location_ids"] = unique_ints(
                list(shift.get("schedule_location_ids") or []) + [location_id]
            )
        return


def role_display_key(role_label: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (role_label or "").strip().lower())


def role_is_context_only(role_label: str | None) -> bool:
    return role_display_key(role_label) in CONTEXT_ONLY_ROLE_KEYS


def role_chain_label(segments: list[dict[str, str]]) -> str:
    position_segments = [segment for segment in segments if segment.get("kind") != "vehicle"]
    real_position_segments = [
        segment
        for segment in position_segments
        if not role_is_context_only(str(segment.get("role") or ""))
    ]
    display_segments = real_position_segments or position_segments
    labels = []
    for segment in display_segments:
        role = segment.get("role") or "Shift"
        if "->" in role:
            role_parts = [part.strip() for part in role.split("->") if part.strip()]
            real_parts = [part for part in role_parts if not role_is_context_only(part)]
            role = " -> ".join(real_parts or role_parts) or "Shift"
        if not labels or labels[-1] != role:
            labels.append(role)
    if not labels:
        labels = [segment.get("role") or "Shift" for segment in segments]
    return " -> ".join(labels)


def role_is_vehicleish(role_label: str | None) -> bool:
    raw_value = (role_label or "").strip()
    if "->" in raw_value:
        role_parts = [part.strip() for part in raw_value.split("->") if part.strip()]
        return bool(role_parts) and all(role_is_vehicleish(part) for part in role_parts)
    normalised = re.sub(r"\s+", "", raw_value.upper())
    if not normalised:
        return False
    return bool(re.fullmatch(r"\d{3}", normalised) or re.fullmatch(r"RAV\d+", normalised) or normalised in VEHICLE_ROLE_LABELS)


def shift_header_vehicle_label(segments: list[dict[str, str]]) -> str:
    has_position = any(
        segment.get("kind") != "vehicle"
        and not role_is_context_only(str(segment.get("role") or ""))
        for segment in segments
    )
    if not has_position:
        return ""
    vehicles = []
    for segment in segments:
        if segment.get("kind") != "vehicle":
            continue
        role = str(segment.get("role") or "").strip()
        if role and role not in vehicles:
            vehicles.append(role)
    return ", ".join(vehicles)


def format_change_value(field_name: str, value: str | None) -> str:
    value = redact_secret_text(str(value or ""))
    if value == "":
        return "blank"
    if field_name in {"start_at", "end_at"}:
        return format_datetime(value)
    if field_name in {"raw_hours", "paid_hours"}:
        try:
            return format_hours(float(value))
        except ValueError:
            return value
    if field_name == "break_minutes":
        return f"{value} min"
    if field_name == "deleted_from_source":
        return "Yes" if value == "1" else "No"
    return value


def decorate_change(row: object) -> dict[str, object]:
    change = dict(row)
    field_name = str(change.get("field_name") or "")
    change["field_label"] = CHANGE_FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
    change["old_display"] = format_change_value(field_name, str(change.get("old_value") or ""))
    change["new_display"] = format_change_value(field_name, str(change.get("new_value") or ""))
    return change


def build_shift_change_summary(changes: list[dict[str, object]]) -> str:
    parts = []
    for change in changes[:4]:
        label = str(change.get("field_label") or "Change")
        field_name = str(change.get("field_name") or "")
        old_value = str(change.get("old_display") or "blank")
        new_value = str(change.get("new_display") or "blank")
        if field_name == "description":
            parts.append("Roster notes changed")
        elif len(old_value) > 48 or len(new_value) > 48:
            parts.append(f"{label} changed")
        else:
            parts.append(f"{label}: {old_value} -> {new_value}")
    if len(changes) > 4:
        parts.append(f"+{len(changes) - 4} more")
    return "; ".join(parts)


def compact_shift_changes(changes: list[dict[str, object]]) -> list[dict[str, object]]:
    return [change for change in changes if str(change.get("field_name") or "") not in HIDDEN_CHANGE_FIELDS]


def duration_hours_between(start_text: str | None, end_text: str | None) -> float:
    start_at = parse_iso_datetime(start_text)
    end_at = parse_iso_datetime(end_text)
    if not start_at or not end_at:
        return 0.0
    return max(0.0, round((end_at - start_at).total_seconds() / 3600, 2))


def build_race_day_calculation(shift: dict[str, object]) -> dict[str, object]:
    summary = shift.get("roster_summary") if isinstance(shift.get("roster_summary"), dict) else {}
    timings = timing_lookup(summary)
    base_label = "Office" if timings.get("office") else "Clow Place"
    base_clock = timings.get("office") or timings.get("clow place")
    on_track_clock = timings.get("on track")
    adjustment_time = clean_time_value(str(shift.get("timing_adjustment_time") or ""))
    use_last_race_adjustment = bool(int(shift.get("timing_adjustment_last_race") or 0)) and adjustment_time
    use_finished_adjustment = bool(int(shift.get("timing_adjustment_day_finished") or 0)) and adjustment_time
    last_race_clock = adjustment_time if use_last_race_adjustment else timings.get("last race")

    result: dict[str, object] = {
        "available": False,
        "lines": [],
        "formula": "",
    }
    default_row = travel_default_for_shift(shift, base_label)
    default_travel_minutes = int(default_row["travel_minutes"]) if default_row else 0
    default_source = str(default_row["source"] or "default") if default_row else ""

    start_at = clock_datetime_for_shift(shift, base_clock) if base_clock else None
    inferred_start = False
    inferred_on_track = False
    if start_at is None and on_track_clock and default_travel_minutes:
        on_track_for_start = clock_datetime_for_shift(shift, on_track_clock)
        if on_track_for_start is not None:
            start_at = on_track_for_start - timedelta(minutes=default_travel_minutes)
            inferred_start = True
            base_clock = start_at.strftime("%H:%M")
    if start_at is None:
        return result

    if use_finished_adjustment:
        finished_at = clock_datetime_for_shift(shift, adjustment_time, start_at)
        if finished_at is None:
            return result
        rounded_end = ceil_datetime_to_quarter(finished_at)
        hours = max(0.0, round((rounded_end - start_at).total_seconds() / 3600, 2))
        result.update(
            {
                "available": True,
                "source": "manual_finished",
                "start_label": start_at.strftime("%H:%M"),
                "end_label": rounded_end.strftime("%H:%M"),
                "hours": hours,
                "hours_label": format_hours(hours),
                "lines": [
                    {"label": base_label, "value": start_at.strftime("%H:%M")},
                    {"label": "Finished/back", "value": finished_at.strftime("%H:%M")},
                    {"label": "Rounded end", "value": rounded_end.strftime("%H:%M")},
                    {"label": "Calculated total", "value": format_hours(hours)},
                ],
                "formula": (
                    f"{base_label} {start_at.strftime('%H:%M')} to finished/back "
                    f"{finished_at.strftime('%H:%M')}, rounded to {rounded_end.strftime('%H:%M')}."
                ),
            }
        )
        return result

    if not on_track_clock and start_at is not None and default_travel_minutes:
        on_track_at = start_at + timedelta(minutes=default_travel_minutes)
        on_track_clock = on_track_at.strftime("%H:%M")
        inferred_on_track = True

    if not on_track_clock or not last_race_clock:
        return result

    on_track_at = clock_datetime_for_shift(shift, on_track_clock, start_at)
    last_race_at = clock_datetime_for_shift(shift, last_race_clock, on_track_at)
    if on_track_at is None or last_race_at is None:
        return result

    travel_minutes = max(0, int(round((on_track_at - start_at).total_seconds() / 60)))
    race_clear_at = ceil_datetime_to_quarter(last_race_at + timedelta(minutes=RACE_RUN_MINUTES))
    packup_done_at = race_clear_at + timedelta(minutes=PACKUP_MINUTES)
    calculated_end_at = packup_done_at + timedelta(minutes=travel_minutes)
    hours = max(0.0, round((calculated_end_at - start_at).total_seconds() / 3600, 2))
    result.update(
        {
            "available": True,
            "source": "race_day",
            "used_default_travel": bool(inferred_start or inferred_on_track),
            "default_travel_source": default_source,
            "start_label": start_at.strftime("%H:%M"),
            "on_track_label": on_track_at.strftime("%H:%M"),
            "last_race_label": last_race_at.strftime("%H:%M"),
            "race_clear_label": race_clear_at.strftime("%H:%M"),
            "packup_done_label": packup_done_at.strftime("%H:%M"),
            "end_label": calculated_end_at.strftime("%H:%M"),
            "travel_label": format_minutes_duration(travel_minutes),
            "hours": hours,
            "hours_label": format_hours(hours),
            "lines": [
                {"label": f"{base_label}{' inferred' if inferred_start else ''}", "value": start_at.strftime("%H:%M")},
                {"label": f"On track{' inferred' if inferred_on_track else ''}", "value": on_track_at.strftime("%H:%M")},
                {"label": "Travel each way", "value": format_minutes_duration(travel_minutes)},
                {"label": "Last race", "value": last_race_at.strftime("%H:%M")},
                {"label": "Race cleared", "value": race_clear_at.strftime("%H:%M")},
                {"label": "Pack-up done", "value": packup_done_at.strftime("%H:%M")},
                {"label": "Back at base", "value": calculated_end_at.strftime("%H:%M")},
                {"label": "Calculated total", "value": format_hours(hours)},
            ],
            "formula": (
                f"{base_label} {start_at.strftime('%H:%M')} to on track {on_track_at.strftime('%H:%M')} "
                f"= {format_minutes_duration(travel_minutes)} travel each way. Last race "
                f"{last_race_at.strftime('%H:%M')} + {RACE_RUN_MINUTES}m rounds to "
                f"{race_clear_at.strftime('%H:%M')}; pack-up to {packup_done_at.strftime('%H:%M')}; "
                f"return travel gives {calculated_end_at.strftime('%H:%M')}."
            ),
        }
    )
    if use_last_race_adjustment:
        result["formula"] = f"Using changed last race time. {result['formula']}"
    if inferred_start or inferred_on_track:
        result["formula"] = f"Using {default_source or 'saved'} default travel time. {result['formula']}"
    return result


def build_race_day_summary(shift: dict[str, object], _race_day: dict[str, object]) -> dict[str, object]:
    rows: list[dict[str, str]] = []
    wanted_patterns = (
        re.compile(r"^(trucks?|office|clow\s+(?:place|pl)|on\s+track|first\s+cross|fx)\b", re.IGNORECASE),
        re.compile(r"\b(records|live|first race|last race|\d+\s+races?)\b", re.IGNORECASE),
    )
    simple_timing_re = re.compile(
        r"^(trucks?|office|clow\s+(?:place|pl)|on\s+track|first\s+cross|fx)\s+(.+)$",
        re.IGNORECASE,
    )
    paired_timing_re = re.compile(
        r"\b(records|live|first\s+cross|first\s+race|last\s+race|fx)\s+([0-9: ]{2,5}\s*(?:am|pm)?)",
        re.IGNORECASE,
    )

    def add_row(label: str, value: str) -> None:
        label_text = label.strip()
        value_text = value.strip()
        if label_text and value_text and {"label": label_text, "value": value_text} not in rows:
            rows.append({"label": label_text, "value": value_text})

    def display_label(label: str) -> str:
        label_key = re.sub(r"\s+", " ", label.strip().lower())
        return {
            "truck": "Trucks",
            "trucks": "Trucks",
            "office": "Office",
            "clow place": "Clow Place",
            "clow pl": "Clow Place",
            "on track": "On track",
            "first cross": "First cross",
            "first race": "First race",
            "last race": "Last race",
            "records": "Records",
            "live": "Live",
            "fx": "FX",
        }.get(label_key, label.strip())

    def display_time(value: str) -> str:
        return clean_timing_value(value)

    for line in shift.get("description_lines") or []:
        line_text = str(line or "").strip()
        if not line_text:
            continue
        time_first = TIME_FIRST_TIMING_RE.match(line_text)
        if time_first:
            add_row(display_label(time_first.group(2)), display_time(time_first.group(1)))
            continue
        if not any(pattern.search(line_text) for pattern in wanted_patterns):
            continue
        if re.match(r"^(trucks?|office|clow\s+(?:place|pl)|on\s+track)\b", line_text, re.IGNORECASE):
            line_text = re.split(r"\s+[-–]\s+", line_text, maxsplit=1)[0].strip()

        race_times = RACE_COUNT_WITH_TIMES_RE.search(line_text)
        if race_times:
            add_row(
                f"{race_times.group(1)} races",
                f"{display_time(race_times.group(2))} | {display_time(race_times.group(3))}",
            )
            continue

        paired_timings = list(paired_timing_re.finditer(line_text))
        if paired_timings:
            for match in paired_timings:
                add_row(display_label(match.group(1)), display_time(match.group(2)))
            continue

        simple_timing = simple_timing_re.match(line_text)
        if simple_timing:
            add_row(display_label(simple_timing.group(1)), display_time(simple_timing.group(2)))

    return {
        "rows": rows,
        "has_items": bool(rows),
    }


def apply_timing_math(shift: dict[str, object]) -> None:
    break_minutes = int(shift.get("break_minutes") or 0)
    segments = []
    for segment in shift.get("role_segments") or []:
        if not isinstance(segment, dict):
            continue
        segments.append(
            {
                "time_range": segment.get("time_range") or "",
                "label": segment.get("label") or "",
                "role": segment.get("role") or "",
                "duration_label": segment.get("duration_label") or "",
            }
        )
    if break_minutes:
        formula = f"Paid: {shift.get('raw_label')} - {break_minutes} min = {shift.get('paid_label')}"
    else:
        formula = f"Paid: {shift.get('paid_label')}"
    race_day = build_race_day_calculation(shift)
    if race_day.get("available"):
        shift["calculated_hours"] = race_day.get("hours")
        shift["calculated_label"] = race_day.get("hours_label")
        shift["display_hours"] = race_day.get("hours")
        shift["display_hours_label"] = race_day.get("hours_label")
    else:
        shift["calculated_hours"] = None
        shift["calculated_label"] = ""
        shift["display_hours"] = shift.get("paid_hours")
        shift["display_hours_label"] = shift.get("paid_label") or format_hours(shift.get("paid_hours"))
    shift["timing_math"] = {
        "segments": segments,
        "start_label": shift.get("start_label") or "",
        "end_label": shift.get("end_label") or "",
        "raw_label": shift.get("raw_label") or format_hours(shift.get("raw_hours")),
        "paid_label": shift.get("paid_label") or format_hours(shift.get("paid_hours")),
        "break_minutes": break_minutes,
        "formula": formula,
        "race_day": race_day,
    }
    shift["race_day_summary"] = build_race_day_summary(shift, race_day)


def decorate_shift(row: object) -> dict[str, object]:
    shift = dict(row)
    parsed_title = parse_shift_title(str(shift.get("title") or ""))
    normalised_payload = source_payload_normalised(str(shift.get("source_payload") or ""))
    schedule_location_id = safe_int(normalised_payload.get("area_location_id"))
    start_at = parse_iso_datetime(shift.get("start_at"))
    end_at = parse_iso_datetime(shift.get("end_at"))
    shift["start_label"] = start_at.strftime("%H:%M") if start_at else ""
    shift["end_label"] = end_at.strftime("%H:%M") if end_at else ""
    shift["time_range"] = f"{shift['start_label']}-{shift['end_label']}"
    if start_at and end_at and end_at.date() > start_at.date():
        shift["time_range"] += " +1d"
    shift["paid_label"] = format_hours(shift.get("paid_hours"))
    shift["raw_label"] = format_hours(shift.get("raw_hours"))
    shift["change_time_label"] = format_datetime(str(shift.get("last_changed_at") or ""), "%d %b %H:%M")
    shift["change_badge_label"] = (
        f"Changed · {shift['change_time_label']}" if shift["change_time_label"] else "Changed"
    )
    shift["mark_badges"] = [label for field, label in MARK_FIELDS if int(shift.get(field) or 0)]
    shift["schedule_location_id"] = schedule_location_id
    shift["schedule_location_ids"] = [schedule_location_id] if schedule_location_id is not None else []
    shift.update(parsed_title)
    apply_known_area_override(shift)
    apply_known_shift_context_fallback(shift)
    role_short = str(shift.get("role_label") or shift.get("role_full_label") or "Shift")
    is_vehicle = role_is_vehicleish(role_short)
    role_segment = {
        "time_range": shift["time_range"],
        "role": role_short if is_vehicle else shift.get("role_full_label") or role_short,
        "role_short": role_short,
        "kind": "vehicle" if is_vehicle else "role",
        "label": "Vehicle" if is_vehicle else "Role",
        "start_label": shift["start_label"],
        "end_label": shift["end_label"],
        "duration_label": format_hours(duration_hours_between(shift.get("start_at"), shift.get("end_at"))),
    }
    shift["role_segments"] = [role_segment]
    shift["role_chain_label"] = role_chain_label(shift["role_segments"])
    shift["header_vehicle_label"] = shift_header_vehicle_label(shift["role_segments"])
    location_colour_index = stable_location_colour_index(
        shift.get("schedule_location_id"),
        shift.get("source_code"),
        shift.get("track_label"),
        shift.get("location"),
    )
    shift["colour_style"] = (
        f"--shift-location-colour: var(--location-colour-{location_colour_index}); "
        f"--location-colour: var(--location-colour-{location_colour_index});"
    )
    shift["description_lines"] = description_lines(str(shift.get("description") or ""))
    shift["source_payload_pretty"] = pretty_source_payload(str(shift.get("source_payload") or ""))
    shift["source_diagnostics"] = source_payload_diagnostics(str(shift.get("source_payload") or ""))
    shift["source_link"] = redact_secret_text(str(shift.get("source_link") or ""))
    shift["roster_summary"] = parse_roster_summary(list(shift.get("description_lines") or []))
    timing_time = clean_time_value(str(shift.get("timing_adjustment_time") or ""))
    timing_notes = []
    if timing_time:
        if int(shift.get("timing_adjustment_last_race") or 0):
            timing_notes.append(f"Last race changed to {timing_time}")
        if int(shift.get("timing_adjustment_day_finished") or 0):
            timing_notes.append(f"Finished/back at office {timing_time}")
    shift["timing_adjustment_time"] = timing_time
    shift["timing_adjustment_labels"] = timing_notes
    shift["combined_shift_ids"] = [int(shift["id"])]
    apply_timing_math(shift)
    return shift


def unique_description_lines(*line_groups: list[str]) -> list[str]:
    seen = set()
    lines = []
    for group in line_groups:
        for line in group:
            key = re.sub(r"\s+", " ", line.strip()).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(line)
    return lines


def choose_primary_shift(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    left_generic = shift_has_generic_track(left)
    right_generic = shift_has_generic_track(right)
    if left_generic and not right_generic:
        return right
    if right_generic and not left_generic:
        return left
    left_role = str(left.get("role_label") or "")
    right_role = str(right.get("role_label") or "")
    if role_is_vehicleish(left_role) and not role_is_vehicleish(right_role):
        return right
    if role_is_vehicleish(right_role) and not role_is_vehicleish(left_role):
        return left
    return right if float(right.get("raw_hours") or 0) >= float(left.get("raw_hours") or 0) else left


def shift_has_generic_track(shift: dict[str, object]) -> bool:
    source_code = str(shift.get("source_code") or "").strip().lower()
    track_label = str(shift.get("track_label") or "").strip().lower()
    return source_code == "web" or track_label in GENERIC_TRACK_LABELS


def shift_role_label(shift: dict[str, object]) -> str:
    return str(shift.get("role_label") or shift.get("role_full_label") or "").strip()


def roster_context_signature(shift: dict[str, object]) -> set[str]:
    values = list(shift.get("description_lines") or [])
    if not values:
        values = [str(shift.get("description") or "")]
    signature = set()
    for value in values:
        clean_value = re.sub(r"\s+", " ", str(value or "").strip().lower())
        clean_value = re.split(r"(?i)\s*breaks:\s*", clean_value)[0].strip()
        if clean_value:
            signature.add(clean_value)
    return signature


def shifts_share_roster_context(left: dict[str, object], right: dict[str, object]) -> bool:
    left_context = roster_context_signature(left)
    right_context = roster_context_signature(right)
    if not left_context or not right_context:
        return False
    return bool(left_context & right_context) or left_context.issubset(right_context) or right_context.issubset(left_context)


def shift_duration_hours_value(shift: dict[str, object]) -> float:
    start_at = parse_iso_datetime(str(shift.get("start_at") or ""))
    end_at = parse_iso_datetime(str(shift.get("end_at") or ""))
    if not start_at or not end_at:
        return float(shift.get("raw_hours") or 0)
    return max(0.0, round((end_at - start_at).total_seconds() / 3600, 2))


def shift_has_race_timing_context(shift: dict[str, object]) -> bool:
    text = " ".join(str(line or "") for line in shift.get("description_lines") or [])
    if not text:
        text = str(shift.get("description") or "")
    return bool(
        re.search(
            r"\b(office|clow\s+(?:place|pl)|on\s+track|first\s+cross|first\s+race|last\s+race|\d+\s+races?)\b",
            text,
            re.IGNORECASE,
        )
    )


def shift_location_id_values(shift: dict[str, object]) -> list[int]:
    return unique_ints(list(shift.get("schedule_location_ids") or []) + [shift.get("schedule_location_id")])


def shift_is_vehicle_context_pair(left: dict[str, object], right: dict[str, object]) -> bool:
    if left.get("date") != right.get("date"):
        return False
    left_is_vehicle = role_is_vehicleish(shift_role_label(left))
    right_is_vehicle = role_is_vehicleish(shift_role_label(right))
    if left_is_vehicle == right_is_vehicle:
        return False
    if shifts_share_roster_context(left, right):
        return True
    vehicle_shift = left if left_is_vehicle else right
    role_shift = right if left_is_vehicle else left
    return (
        shift_duration_hours_value(vehicle_shift) <= 3
        and shift_has_race_timing_context(vehicle_shift)
        and shift_has_race_timing_context(role_shift)
    )


def can_merge_shift(left: dict[str, object], right: dict[str, object]) -> bool:
    if int(left.get("deleted_from_source") or 0) or int(right.get("deleted_from_source") or 0):
        return False
    left_end = parse_iso_datetime(str(left.get("end_at") or ""))
    right_start = parse_iso_datetime(str(right.get("start_at") or ""))
    if not left_end or not right_start or left_end != right_start:
        return False
    if (
        left.get("date") == right.get("date")
        and left.get("track_label") == right.get("track_label")
        and left.get("location") == right.get("location")
        and left.get("race_type_label") == right.get("race_type_label")
    ):
        return True
    if shift_is_vehicle_context_pair(left, right):
        return True
    return (
        left.get("date") == right.get("date")
        and shift_has_generic_track(left) != shift_has_generic_track(right)
        and shifts_share_roster_context(left, right)
    )


def clean_merged_role_segments(segments: list[dict[str, object]]) -> list[dict[str, object]]:
    has_real_position = any(
        str(segment.get("kind") or "") != "vehicle"
        and not role_is_context_only(str(segment.get("role") or ""))
        for segment in segments
    )
    if not has_real_position:
        return segments
    return [
        segment
        for segment in segments
        if str(segment.get("kind") or "") == "vehicle"
        or not role_is_context_only(str(segment.get("role") or ""))
    ]


def merge_shift_pair(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    primary = choose_primary_shift(left, right)
    other = right if primary is left else left
    vehicle_context_pair = shift_is_vehicle_context_pair(left, right)
    merged = dict(primary)
    start_at = parse_iso_datetime(str(left.get("start_at") or ""))
    end_at = parse_iso_datetime(str(right.get("end_at") or ""))
    if start_at and end_at:
        raw_hours = round((end_at - start_at).total_seconds() / 3600, 2)
        break_minutes = int(left.get("break_minutes") or 0) + int(right.get("break_minutes") or 0)
        paid_hours = max(0.0, round(raw_hours - (break_minutes / 60), 2))
        merged.update(
            {
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "start_label": start_at.strftime("%H:%M"),
                "end_label": end_at.strftime("%H:%M"),
                "time_range": f"{start_at.strftime('%H:%M')}-{end_at.strftime('%H:%M')}",
                "raw_hours": raw_hours,
                "paid_hours": paid_hours,
                "raw_label": format_hours(raw_hours),
                "paid_label": format_hours(paid_hours),
                "break_minutes": break_minutes,
            }
        )
    if vehicle_context_pair and list(primary.get("description_lines") or []):
        merged["description_lines"] = unique_description_lines(list(primary.get("description_lines") or []))
    else:
        merged["description_lines"] = unique_description_lines(
            list(left.get("description_lines") or []),
            list(right.get("description_lines") or []),
        )
    merged["roster_summary"] = parse_roster_summary(list(merged.get("description_lines") or []))
    merged["role_segments"] = clean_merged_role_segments(
        list(left.get("role_segments") or []) + list(right.get("role_segments") or [])
    )
    merged["role_chain_label"] = role_chain_label(list(merged.get("role_segments") or []))
    merged["header_vehicle_label"] = shift_header_vehicle_label(list(merged.get("role_segments") or []))
    merged["changed_since_viewed"] = int(left.get("changed_since_viewed") or 0) or int(right.get("changed_since_viewed") or 0)
    merged["last_changed_at"] = latest_iso_datetime(left.get("last_changed_at"), right.get("last_changed_at"))
    merged["change_time_label"] = format_datetime(str(merged.get("last_changed_at") or ""), "%d %b %H:%M")
    merged["change_badge_label"] = (
        f"Changed · {merged['change_time_label']}" if merged["change_time_label"] else "Changed"
    )
    merged["combined_shift_ids"] = list(left.get("combined_shift_ids") or [left["id"]]) + list(
        right.get("combined_shift_ids") or [right["id"]]
    )
    primary_location_ids = shift_location_id_values(primary)
    other_location_ids = shift_location_id_values(other)
    if vehicle_context_pair and primary_location_ids:
        merged["schedule_location_ids"] = primary_location_ids
    else:
        merged["schedule_location_ids"] = unique_ints(primary_location_ids + other_location_ids)
    merged["schedule_location_id"] = merged["schedule_location_ids"][0] if merged["schedule_location_ids"] else None
    apply_timing_math(merged)
    return merged


def combine_adjacent_shifts(shifts: list[dict[str, object]]) -> list[dict[str, object]]:
    combined: list[dict[str, object]] = []
    for shift in sorted(shifts, key=lambda item: (str(item.get("start_at") or ""), int(item.get("id") or 0))):
        if combined and can_merge_shift(combined[-1], shift):
            combined[-1] = merge_shift_pair(combined[-1], shift)
        else:
            combined.append(shift)
    return combined


def schedule_label_key(value: str | None) -> str:
    value = (value or "").strip().lower()
    value = value.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", value)


def schedule_label_alias(value: str | None) -> tuple[str, str] | None:
    key = schedule_label_key(value)
    alias = SCHEDULE_POSITION_ALIASES.get(key)
    if alias:
        return alias
    if "gimbal" in key or "gimball" in key or "gimble" in key:
        if "assist" in key or "assistant" in key:
            return "gimbalassist", "Gimbal Assist"
        return "gimbal", "Gimbal"
    if "steadi" in key or "steady" in key:
        if "assist" in key or "assistant" in key:
            return "steadiassist", "Steadi Assist"
        return "steadi", "Steadi"
    return None


def display_schedule_area(value: str | None) -> str:
    value = (value or "").strip()
    match = re.match(r"^(.+?)([TH])-[A-Za-z].*$", value, flags=re.IGNORECASE)
    if match and match.group(1).strip():
        value = match.group(1).strip()
    value = re.sub(r"\s+", " ", value)
    compact_key = re.sub(r"\s+", "", value.upper())
    role_label = ROLE_NAMES.get(compact_key, ROLE_NAMES.get(value.upper(), value or "Role"))
    alias = schedule_label_alias(role_label)
    return alias[1] if alias else role_label


def schedule_area_is_vehicle(value: str | None) -> bool:
    return role_is_vehicleish(value)


def schedule_area_is_hidden(value: str | None) -> bool:
    return schedule_label_key(value) in HIDDEN_SCHEDULE_POSITION_KEYS


def schedule_sort_value(value: object) -> int:
    if value in (None, ""):
        return 999999
    try:
        return int(value)
    except (TypeError, ValueError):
        return 999999


def schedule_display_sort(value: str | None, fallback: object = None) -> int:
    alias = schedule_label_alias(value)
    if alias and alias[0] in SCHEDULE_POSITION_ORDER:
        return SCHEDULE_POSITION_ORDER[alias[0]]
    fallback_sort = schedule_sort_value(fallback)
    if schedule_area_is_vehicle(value):
        return 5000 + fallback_sort
    return 1000 + fallback_sort


def decorate_schedule_row(row: object) -> dict[str, object]:
    item = dict(row)
    start_at = parse_iso_datetime(item.get("start_at"))
    end_at = parse_iso_datetime(item.get("end_at"))
    item["start_label"] = start_at.strftime("%H:%M") if start_at else ""
    item["end_label"] = end_at.strftime("%H:%M") if end_at else ""
    item["time_range"] = f"{item['start_label']}-{item['end_label']}" if item["start_label"] and item["end_label"] else ""
    item["area_display"] = display_schedule_area(str(item.get("area_name") or ""))
    item["duration_label"] = format_hours(item.get("duration"))
    item["area_sort_order"] = schedule_sort_value(item.get("area_roster_sort_order"))
    item["schedule_location_id"] = item.get("schedule_location_id") or item.get("area_location_id")
    item["display_sort_order"] = schedule_display_sort(item["area_display"], item["area_sort_order"])
    item["is_vehicle_area"] = schedule_area_is_vehicle(str(item.get("area_display") or ""))
    item["changed"] = bool(int(item.get("changed_since_viewed") or 0))
    item["change_summary"] = str(item.get("change_summary") or "")
    item["change_time_label"] = format_datetime(str(item.get("last_changed_at") or ""), "%d %b %H:%M")
    item["assignment_changed"] = schedule_assignment_changed(item["change_summary"])
    item["assignment_change_summary"] = schedule_assignment_change_summary(item["change_summary"])
    return item


def schedule_assignment_changed(change_summary: str | None) -> bool:
    change_summary = str(change_summary or "")
    return any(
        marker in change_summary
        for marker in ("Person:", "Position:", "Open shift:")
    )


def schedule_assignment_change_summary(change_summary: str | None) -> str:
    parts = []
    for part in str(change_summary or "").split(";"):
        clean_part = part.strip()
        if clean_part.startswith(("Person:", "Position:", "Open shift:")):
            parts.append(clean_part)
    return "; ".join(parts)


def append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def person_name_keys(value: str | None) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    keys = set()
    if words:
        keys.add("".join(words))
        keys.add(words[0])
    return {key for key in keys if key}


def note_person_keys(value: str | None) -> set[str]:
    keys = person_name_keys(value)
    expanded = set(keys)
    for key in keys:
        expanded.update(NOTE_NAME_ALIASES.get(key, ()))
    return expanded


def schedule_person_alias_map(people: list[dict[str, object]]) -> dict[str, list[int]]:
    aliases: dict[str, list[int]] = {}
    for index, person in enumerate(people):
        for key in person_name_keys(str(person.get("employee_name") or "")):
            aliases.setdefault(key, []).append(index)
    return aliases


def roster_note_vehicle_allocations(shifts: list[dict[str, object]]) -> list[dict[str, str]]:
    allocations = []
    seen = set()
    for shift in shifts:
        summary = shift.get("roster_summary") if isinstance(shift.get("roster_summary"), dict) else {}
        for allocation in summary.get("crew_allocations") or []:
            if not isinstance(allocation, dict):
                continue
            vehicle = str(allocation.get("vehicle") or "").strip()
            people_text = str(allocation.get("people") or "")
            if not vehicle or not people_text:
                continue
            for name in VEHICLE_ALLOCATION_WORD_RE.findall(people_text):
                if VEHICLE_ALLOCATION_TOKEN_RE.match(name):
                    continue
                key = (vehicle.lower(), name.lower())
                if key in seen:
                    continue
                seen.add(key)
                allocations.append({"vehicle": vehicle_note_label(vehicle), "name": name})
    return allocations


def apply_roster_note_vehicles(people: list[dict[str, object]], shifts: list[dict[str, object]]) -> None:
    if not people:
        return
    alias_map = schedule_person_alias_map(people)
    for allocation in roster_note_vehicle_allocations(shifts):
        matched_indexes = set()
        for key in note_person_keys(allocation.get("name")):
            indexes = alias_map.get(key, [])
            if len(indexes) == 1:
                matched_indexes.add(indexes[0])
        if len(matched_indexes) != 1:
            continue
        person = people[matched_indexes.pop()]
        current_vehicle = str(person.get("vehicle_label") or "").strip()
        vehicle = str(allocation.get("vehicle") or "").strip()
        if current_vehicle and current_vehicle != "-":
            vehicle_parts = [part.strip() for part in current_vehicle.split(",") if part.strip()]
            if vehicle not in vehicle_parts:
                vehicle_parts.append(vehicle)
            person["vehicle_label"] = ", ".join(vehicle_parts)
        else:
            person["vehicle_label"] = vehicle


def vehicle_for_user_from_schedule(
    people: list[dict[str, object]],
    user: object | None,
    shift: dict[str, object],
) -> str:
    candidates = person_name_keys(payload_employee_name(shift))
    if isinstance(user, dict):
        candidates.update(person_name_keys(str(user.get("display_name") or "")))
        candidates.update(person_name_keys(str(user.get("deputy_email") or "").split("@", 1)[0]))
    if not candidates:
        return ""

    alias_map = schedule_person_alias_map(people)
    matched_indexes = set()
    for key in candidates:
        indexes = alias_map.get(key, [])
        if len(indexes) == 1:
            matched_indexes.add(indexes[0])
    if len(matched_indexes) != 1:
        return ""
    return str(people[matched_indexes.pop()].get("vehicle_label") or "").strip()


def schedule_item_position_key(item: dict[str, object]) -> str:
    alias = schedule_label_alias(str(item.get("area_display") or ""))
    if alias:
        return alias[0]
    area_id = item.get("area_id")
    if area_id not in (None, ""):
        return f"area:{area_id}"
    return schedule_label_key(str(item.get("area_display") or ""))


def schedule_items_overlap(left: dict[str, object], right: dict[str, object]) -> bool:
    left_start = parse_iso_datetime(str(left.get("start_at") or ""))
    left_end = parse_iso_datetime(str(left.get("end_at") or ""))
    right_start = parse_iso_datetime(str(right.get("start_at") or ""))
    right_end = parse_iso_datetime(str(right.get("end_at") or ""))
    if not left_start or not left_end or not right_start or not right_end:
        return True
    return left_start < right_end and right_start < left_end


def schedule_item_newer(left: dict[str, object], right: dict[str, object]) -> bool:
    left_key = (str(left.get("captured_at") or ""), int(left.get("source_shift_id") or 0))
    right_key = (str(right.get("captured_at") or ""), int(right.get("source_shift_id") or 0))
    return left_key > right_key


def replacement_change_summary(old_item: dict[str, object], new_item: dict[str, object]) -> str:
    old_name = str(old_item.get("employee_name") or "blank").strip() or "blank"
    new_name = str(new_item.get("employee_name") or "blank").strip() or "blank"
    area_label = str(new_item.get("area_display") or old_item.get("area_display") or "Position").strip()
    if old_name == new_name:
        return str(new_item.get("assignment_change_summary") or old_item.get("assignment_change_summary") or "Changed")
    return f"{area_label}: {old_name} -> {new_name}"


def dedupe_schedule_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    for item in items:
        if item.get("is_vehicle_area"):
            deduped.append(item)
            continue
        item_key = (
            item.get("schedule_location_id"),
            schedule_item_position_key(item),
        )
        replacement_index = None
        for index, existing in enumerate(deduped):
            if existing.get("is_vehicle_area"):
                continue
            existing_key = (
                existing.get("schedule_location_id"),
                schedule_item_position_key(existing),
            )
            if existing_key == item_key and schedule_items_overlap(existing, item):
                replacement_index = index
                break
        if replacement_index is None:
            deduped.append(item)
            continue

        existing = deduped[replacement_index]
        if schedule_item_newer(item, existing):
            if existing.get("assignment_changed") or existing.get("changed"):
                item["assignment_changed"] = True
                item["changed"] = True
                item["assignment_change_summary"] = replacement_change_summary(existing, item)
                item["last_changed_at"] = latest_iso_datetime(existing.get("last_changed_at"), item.get("last_changed_at"))
                item["change_time_label"] = format_datetime(str(item.get("last_changed_at") or ""), "%d %b %H:%M")
            deduped[replacement_index] = item
        elif item.get("assignment_changed") and not existing.get("assignment_changed"):
            existing["assignment_changed"] = True
            existing["changed"] = True
            existing["assignment_change_summary"] = replacement_change_summary(item, existing)
            existing["last_changed_at"] = latest_iso_datetime(existing.get("last_changed_at"), item.get("last_changed_at"))
            existing["change_time_label"] = format_datetime(str(existing.get("last_changed_at") or ""), "%d %b %H:%M")
    return deduped


def expected_schedule_placeholders(
    expected_areas: list[object] | None,
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not expected_areas or not items:
        return []
    present_keys = {
        (item.get("schedule_location_id"), schedule_item_position_key(item))
        for item in items
        if not item.get("is_vehicle_area")
    }
    present_position_keys = {position_key for _, position_key in present_keys}
    placeholders = []
    seen = set()
    for area in expected_areas:
        area_row = dict(area)
        area_display = display_schedule_area(str(area_row.get("name") or ""))
        if schedule_area_is_hidden(area_display) or schedule_area_is_vehicle(area_display):
            continue
        alias = schedule_label_alias(area_display)
        position_key = alias[0] if alias else schedule_label_key(area_display)
        if position_key not in PLACEHOLDER_SCHEDULE_POSITION_KEYS:
            continue
        if position_key in {"sound", "vt"} and "soundvt" in present_position_keys:
            continue
        if position_key == "soundvt" and ({"sound", "vt"} & present_position_keys):
            continue
        location_id = area_row.get("location_id")
        placeholder_key = (location_id, position_key)
        if placeholder_key in present_keys or placeholder_key in seen:
            continue
        seen.add(placeholder_key)
        placeholders.append(
            {
                "employee_name": "TBC",
                "position_label": area_display,
                "vehicle_label": "",
                "sort_order": schedule_display_sort(area_display, area_row.get("roster_sort_order")),
                "changed": False,
                "change_summary": "",
                "change_time_label": "",
                "placeholder": True,
            }
        )
    return placeholders


def schedule_people(rows: list[object], expected_areas: list[object] | None = None) -> list[dict[str, object]]:
    people_by_key: dict[str, dict[str, object]] = {}
    open_entries: list[dict[str, object]] = []
    items = []
    for row in rows:
        item = decorate_schedule_row(row)
        if not schedule_area_is_hidden(str(item.get("area_display") or "Role")):
            items.append(item)

    items = dedupe_schedule_items(items)
    placeholders = expected_schedule_placeholders(expected_areas, items)

    for item in items:
        area_label = str(item.get("area_display") or "Role")
        area_sort = schedule_sort_value(item.get("display_sort_order"))
        employee_name = str(item.get("employee_name") or "").strip()
        is_vehicle = bool(item.get("is_vehicle_area"))

        if not employee_name:
            vehicle_label = area_label if is_vehicle else ""
            open_entries.append(
                {
                    "employee_name": "Open shift",
                    "position_label": "Open shift" if is_vehicle else area_label,
                    "vehicle_label": vehicle_label,
                    "sort_order": area_sort,
                    "changed": bool(item.get("assignment_changed")),
                    "change_summary": item.get("assignment_change_summary") or "",
                    "change_time_label": item.get("change_time_label") or "",
                    "placeholder": False,
                }
            )
            continue

        key = str(item.get("employee_id") or employee_name)
        person = people_by_key.setdefault(
            key,
            {
                "employee_name": employee_name,
                "position_parts": [],
                "vehicle_parts": [],
                "change_parts": [],
                "change_times": [],
                "changed": False,
                "position_sort": 999999,
                "vehicle_sort": 999999,
            },
        )
        if item.get("assignment_changed"):
            person["changed"] = True
            append_unique(person["change_parts"], str(item.get("assignment_change_summary") or "Changed"))
            append_unique(person["change_times"], str(item.get("last_changed_at") or ""))
        if is_vehicle:
            append_unique(person["vehicle_parts"], area_label)
            person["vehicle_sort"] = min(schedule_sort_value(person.get("vehicle_sort")), area_sort)
        else:
            append_unique(person["position_parts"], area_label)
            person["position_sort"] = min(schedule_sort_value(person.get("position_sort")), area_sort)

    people = []
    for person in people_by_key.values():
        position_parts = list(person.get("position_parts") or [])
        vehicle_parts = list(person.get("vehicle_parts") or [])
        if not position_parts:
            continue
        position_label = ", ".join(position_parts)
        vehicle_label = ", ".join(vehicle_parts)
        sort_order = schedule_sort_value(person.get("position_sort"))
        if sort_order == 999999:
            sort_order = schedule_sort_value(person.get("vehicle_sort"))
        people.append(
            {
                "employee_name": person.get("employee_name") or "Open shift",
                "position_label": position_label,
                "vehicle_label": vehicle_label,
                "sort_order": sort_order,
                "changed": bool(person.get("changed")),
                "change_summary": "; ".join(list(person.get("change_parts") or [])),
                "change_time_label": format_datetime(latest_iso_datetime(*list(person.get("change_times") or [])), "%d %b %H:%M"),
                "placeholder": False,
            }
        )
    people.extend(placeholders)
    people.extend(open_entries)
    return sorted(
        people,
        key=lambda person: (
            schedule_sort_value(person.get("sort_order")),
            str(person.get("position_label") or ""),
            str(person.get("employee_name") or ""),
        ),
    )


def shift_schedule_location_ids(shifts: list[dict[str, object]]) -> list[int]:
    values = []
    for shift in shifts:
        values.extend(list(shift.get("schedule_location_ids") or []))
        values.append(shift.get("schedule_location_id"))
    return unique_ints(values)


def deputy_schedule_label_for_shifts(base_label: str, shifts: list[dict[str, object]]) -> str:
    labels = []
    for shift in shifts:
        track_label = str(shift.get("track_label") or "").strip()
        if not track_label or track_label in labels:
            continue
        labels.append(track_label)
    if not labels:
        return base_label
    return f"{base_label} - {', '.join(labels)}"


def open_schedule_by_date(start_date: str, end_date: str) -> dict[str, list[dict[str, object]]]:
    by_date: dict[str, list[dict[str, object]]] = {}
    for row in fetch_open_deputy_schedule_between(start_date, end_date):
        item = decorate_schedule_row(row)
        if schedule_area_is_hidden(str(item.get("area_display") or "")):
            continue
        by_date.setdefault(str(item.get("date") or ""), []).append(item)
    return by_date


def visible_open_schedule_shifts(limit: int = 8) -> list[dict[str, object]]:
    shifts = []
    for row in fetch_open_deputy_schedule_shifts(limit=limit):
        item = decorate_schedule_row(row)
        if schedule_area_is_hidden(str(item.get("area_display") or "")):
            continue
        shifts.append(item)
    return shifts


def location_compare_key(*values: object) -> str:
    for value in values:
        key = location_colour_key(value)
        if key and key not in GENERIC_TRACK_LABELS:
            return key
    return ""


def deputy_location_keys_for_shifts(shifts: list[dict[str, object]]) -> set[str]:
    keys: set[str] = set()
    for shift in shifts:
        for value in (
            shift.get("track_label"),
            shift.get("source_code"),
            shift.get("location"),
            shift.get("schedule_location_id"),
        ):
            key = location_colour_key(value)
            if key and key not in GENERIC_TRACK_LABELS:
                keys.add(key)
        track_key = location_colour_key(shift.get("track_label"))
        if "cambridge" in track_key:
            keys.add("cambridge")
    return keys


def decorate_love_racing_meeting(row: object) -> dict[str, object]:
    meeting = dict(row)
    racecourse = str(meeting.get("racecourse") or "Race day").strip()
    racecourse_key = str(meeting.get("racecourse_key") or location_colour_key(racecourse)).strip()
    colour_index = stable_location_colour_index(racecourse_key, racecourse)
    meeting["date"] = str(meeting.get("meeting_date") or meeting.get("date") or "")
    meeting["location_label"] = racecourse
    meeting["source_label"] = "Love Racing"
    meeting["colour_style"] = (
        f"--shift-location-colour: var(--location-colour-{colour_index}); "
        f"--location-colour: var(--location-colour-{colour_index});"
    )
    try:
        meeting_date = date.fromisoformat(str(meeting["date"]))
        meeting["date_label"] = meeting_date.strftime("%a %d %b")
    except ValueError:
        meeting["date_label"] = str(meeting["date"])
    return meeting


def love_racing_by_date(
    start_date: str,
    end_date: str,
    shifts_by_date: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    by_date: dict[str, list[dict[str, object]]] = {}
    for row in fetch_love_racing_meetings_between(start_date, end_date):
        meeting = decorate_love_racing_meeting(row)
        date_key = str(meeting.get("date") or "")
        deputy_keys = deputy_location_keys_for_shifts(shifts_by_date.get(date_key, []))
        meeting_key = location_compare_key(meeting.get("racecourse_key"), meeting.get("racecourse"))
        if meeting_key and (meeting_key in deputy_keys or ("cambridge" in meeting_key and "cambridge" in deputy_keys)):
            continue
        by_date.setdefault(date_key, []).append(meeting)
    return by_date


def bar_items(counter: Counter, limit: int = 8) -> list[dict[str, object]]:
    top = counter.most_common(limit)
    max_value = max((value for _label, value in top), default=0)
    items = []
    for label, value in top:
        items.append(
            {
                "label": label,
                "value": value,
                "percent": round((float(value) / max_value) * 100) if max_value else 0,
            }
        )
    return items


def date_range_label(start_date: date, end_date: date) -> str:
    if start_date.year == end_date.year:
        return f"{start_date.strftime('%d %b')} to {end_date.strftime('%d %b %Y')}"
    return f"{start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"


def build_roster_insights(owner_user_id: int | None, today: date) -> dict[str, object]:
    start_day = today - timedelta(days=90)
    end_day = today + timedelta(days=180)
    start_date = start_day.isoformat()
    end_date = end_day.isoformat()
    shifts = [
        decorate_shift(row)
        for row in fetch_shifts_between(start_date, end_date, owner_user_id=owner_user_id)
        if not int(row["deleted_from_source"] or 0)
    ]
    shared_shifts = [
        decorate_shift(row)
        for row in fetch_shifts_between(start_date, end_date, owner_user_id=None)
        if not int(row["deleted_from_source"] or 0)
    ]
    tbc_end_day = today + timedelta(days=90)
    past_30_start = today - timedelta(days=30)
    past_90_start = today - timedelta(days=90)
    past_30 = [
        shift for shift in shifts
        if past_30_start.isoformat() <= str(shift.get("date") or "") <= today.isoformat()
    ]
    past_90 = [
        shift for shift in shifts
        if past_90_start.isoformat() <= str(shift.get("date") or "") <= today.isoformat()
    ]
    upcoming = [
        shift for shift in shifts
        if str(shift.get("date") or "") >= today.isoformat()
    ]
    shared_upcoming = [
        shift for shift in shared_shifts
        if str(shift.get("date") or "") >= today.isoformat()
    ]
    track_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    track_hours: Counter[str] = Counter()
    weekday_counts: Counter[str] = Counter()
    for shift in shifts:
        track = str(shift.get("track_label") or "Unknown").strip() or "Unknown"
        role = str(shift.get("role_chain_label") or shift.get("role_full_label") or shift.get("role_label") or "Shift").strip()
        shift_date = str(shift.get("date") or "")
        track_counts[track] += 1
        role_counts[role] += 1
        track_hours[track] += float(shift_hours_value(shift) or 0)
        try:
            weekday_counts[date.fromisoformat(shift_date).strftime("%a")] += 1
        except ValueError:
            pass
    shared_track_counts: Counter[str] = Counter()
    shared_role_counts: Counter[str] = Counter()
    shared_track_hours: Counter[str] = Counter()
    shared_owner_ids: set[int] = set()
    for shift in shared_shifts:
        track = str(shift.get("track_label") or "Unknown").strip() or "Unknown"
        role = str(shift.get("role_chain_label") or shift.get("role_full_label") or shift.get("role_label") or "Shift").strip()
        shared_track_counts[track] += 1
        shared_role_counts[role] += 1
        shared_track_hours[track] += float(shift_hours_value(shift) or 0)
        try:
            owner_id = int(shift.get("owner_user_id") or 0)
        except (TypeError, ValueError):
            owner_id = 0
        if owner_id:
            shared_owner_ids.add(owner_id)

    tbc_rows = []
    tbc_position_counts: Counter[str] = Counter()
    tbc_location_counts: Counter[str] = Counter()
    for row in fetch_tbc_deputy_schedule_between(today.isoformat(), tbc_end_day.isoformat(), limit=80):
        item = decorate_schedule_row(row)
        area = str(item.get("area_display") or "").strip()
        if not area or schedule_area_is_hidden(area) or schedule_area_is_vehicle(area):
            continue
        if schedule_label_key(area) in {"fm", "floormanager"}:
            continue
        location = str(item.get("location_name") or item.get("area_location_id") or "Unknown").strip() or "Unknown"
        try:
            item_date = date.fromisoformat(str(item.get("date") or ""))
            item["date_label"] = item_date.strftime("%a %d %b")
        except ValueError:
            item["date_label"] = str(item.get("date") or "")
        item["location_label"] = display_track_label(location)
        tbc_rows.append(item)
        tbc_position_counts[area] += 1
        tbc_location_counts[item["location_label"]] += 1

    return {
        "range_label": date_range_label(start_day, end_day),
        "past_30_label": date_range_label(past_30_start, today),
        "past_90_label": date_range_label(past_90_start, today),
        "upcoming_label": date_range_label(today, end_day),
        "tbc_label": date_range_label(today, tbc_end_day),
        "shift_count": len(shifts),
        "past_30_count": len(past_30),
        "past_30_hours": sum(float(shift_hours_value(shift) or 0) for shift in past_30),
        "past_90_count": len(past_90),
        "past_90_hours": sum(float(shift_hours_value(shift) or 0) for shift in past_90),
        "upcoming_count": len(upcoming),
        "changed_count": sum(1 for shift in shifts if int(shift.get("changed_since_viewed") or 0)),
        "track_count": len(track_counts),
        "top_tracks": bar_items(track_counts),
        "top_roles": bar_items(role_counts),
        "track_hours": bar_items(Counter({label: round(value, 2) for label, value in track_hours.items()})),
        "weekday_counts": bar_items(weekday_counts, limit=7),
        "shared_shift_count": len(shared_shifts),
        "shared_upcoming_count": len(shared_upcoming),
        "shared_people_count": len(shared_owner_ids),
        "shared_hours": sum(float(shift_hours_value(shift) or 0) for shift in shared_shifts),
        "shared_top_tracks": bar_items(shared_track_counts, limit=10),
        "shared_top_roles": bar_items(shared_role_counts, limit=10),
        "shared_track_hours": bar_items(Counter({label: round(value, 2) for label, value in shared_track_hours.items()}), limit=10),
        "tbc_count": len(tbc_rows),
        "tbc_rows": tbc_rows[:16],
        "tbc_positions": bar_items(tbc_position_counts, limit=8),
        "tbc_locations": bar_items(tbc_location_counts, limit=8),
    }


def is_overnight_travel_day(shifts: list[dict[str, object]]) -> bool:
    for shift in shifts:
        haystack = " ".join(
            str(shift.get(key) or "")
            for key in ("title", "role_label", "role_full_label", "display_title")
        ).lower()
        if "travel then overnighter" in haystack or "overnighter" in haystack:
            return True
    return False


def notice_url(path: str, message: str) -> str:
    parts = urlsplit(path)
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    query_items.append(("notice", message))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_items), parts.fragment))


def default_manual_sync_status() -> dict[str, object]:
    return {
        "running": False,
        "label": "Ready",
        "message": "",
        "started_at": "",
        "finished_at": "",
        "status": "ready",
    }


def manual_sync_scope(user_id: int | None = None) -> str:
    return f"user:{user_id}" if user_id is not None else "env"


def get_manual_sync_status(user_id: int | None = None) -> dict[str, object]:
    with _sync_state_lock:
        status = default_manual_sync_status()
        status.update(_manual_sync_status_by_scope.get(manual_sync_scope(user_id), {}))
        return status


def set_manual_sync_status(user_id: int | None = None, **values: object) -> None:
    with _sync_state_lock:
        scope = manual_sync_scope(user_id)
        status = default_manual_sync_status()
        status.update(_manual_sync_status_by_scope.get(scope, {}))
        status.update(values)
        _manual_sync_status_by_scope[scope] = status


def sync_summary_message(summary: dict[str, object]) -> str:
    calendar_result = summary.get("calendar") if isinstance(summary.get("calendar"), dict) else {}
    web_result = summary.get("web") if isinstance(summary.get("web"), dict) else {}
    parts = []
    if calendar_result.get("status") == "ok":
        parts.append(
            "iCal roster: "
            f"{calendar_result.get('events_created', 0)} new, "
            f"{calendar_result.get('events_updated', 0)} changed, "
            f"{calendar_result.get('events_marked_deleted', 0)} cancelled."
        )
    elif calendar_result.get("status") == "skipped":
        parts.append(str(calendar_result.get("message") or "iCal skipped."))
    elif calendar_result:
        parts.append(str(calendar_result.get("message") or "iCal sync failed."))

    if web_result.get("status") == "ok":
        parts.append(
            "Deputy web capture saved "
            f"{web_result.get('saved_own_shift_rows', 0)} roster rows and "
            f"{web_result.get('saved_schedule_rows', 0)} schedule rows."
        )
    elif web_result.get("status") == "skipped":
        parts.append(str(web_result.get("message") or "Deputy web capture skipped."))
    elif web_result:
        parts.append(str(web_result.get("message") or "Deputy web capture failed."))

    message = " ".join(part for part in parts if part).strip()
    return message or "No sync source ran. Add a Deputy login or backup iCal URL."


def run_manual_sync_job(user_id: int | None = None) -> None:
    settings = get_settings()
    if not _sync_worker_lock.acquire(blocking=False):
        set_manual_sync_status(
            user_id,
            running=False,
            label="Ready",
            message="Another roster sync is already running. Try again in a minute.",
            status="ready",
            finished_at=datetime.now(settings.timezone).isoformat(timespec="seconds"),
        )
        return
    user_state_started = False
    try:
        started_at = datetime.now(settings.timezone).isoformat(timespec="seconds")
        if user_id is not None:
            ensure_user_sync_state(user_id)
            user_state_started = mark_user_sync_started(user_id, started_at)
            if not user_state_started:
                set_manual_sync_status(
                    user_id,
                    running=False,
                    label="Ready",
                    message="This account already has a sync running. Try again in a minute.",
                    finished_at=started_at,
                    status="ready",
                )
                return
        set_manual_sync_status(
            user_id,
            running=True,
            label="Scanning Deputy page now",
            message="Sync running.",
            started_at=started_at,
            finished_at="",
            status="running",
        )
        summary = sync_roster_sources(settings, user_id=user_id)
        finished_at = datetime.now(settings.timezone).isoformat(timespec="seconds")
        message = sync_summary_message(summary)
        status = "ready" if summary.get("status") == "ok" else "error"
        set_manual_sync_status(
            user_id,
            running=False,
            label="Ready" if status == "ready" else "Error",
            message=message,
            finished_at=finished_at,
            status=status,
        )
        if user_id is not None and user_state_started:
            mark_user_sync_finished(
                user_id,
                finished_at=finished_at,
                status=status,
                message=message,
            )
    except Exception as exc:
        finished_at = datetime.now(settings.timezone).isoformat(timespec="seconds")
        set_manual_sync_status(
            user_id,
            running=False,
            label="Error",
            message=f"Sync failed: {exc.__class__.__name__}. Check the app logs if this repeats.",
            finished_at=finished_at,
            status="error",
        )
        if user_id is not None and user_state_started:
            mark_user_sync_finished(
                user_id,
                finished_at=finished_at,
                status="error",
                message=f"Sync failed: {exc.__class__.__name__}.",
            )
    finally:
        _sync_worker_lock.release()


def queue_manual_sync(background_tasks: BackgroundTasks, user_id: int | None = None) -> bool:
    status = get_manual_sync_status(user_id)
    if bool(status.get("running")):
        return False
    settings = get_settings()
    set_manual_sync_status(
        user_id,
        running=True,
        label="Scanning Deputy page now",
        message="Sync queued.",
        started_at=datetime.now(settings.timezone).isoformat(timespec="seconds"),
        finished_at="",
        status="running",
    )
    background_tasks.add_task(run_manual_sync_job, user_id)
    return True


def build_timesheet_summary(submission_date: date, owner_user_id: int | None = None) -> dict[str, object]:
    period_start, period_end = timesheet_period(submission_date)
    rows = fetch_shifts_between(
        period_start.isoformat(),
        period_end.isoformat(),
        owner_user_id=owner_user_id,
    )
    shifts_by_date: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        shifts_by_date.setdefault(row["date"], []).append(decorate_shift(row))
    for date_key, day_shifts in list(shifts_by_date.items()):
        shifts_by_date[date_key] = combine_adjacent_shifts(day_shifts)

    day_rows = []
    total_hours = 0.0
    for offset in range(14):
        day_item = period_start + timedelta(days=offset)
        shifts = [
            shift
            for shift in shifts_by_date.get(day_item.isoformat(), [])
            if not int(shift.get("deleted_from_source") or 0)
        ]
        day_total = sum(shift_hours_value(shift) for shift in shifts)
        total_hours += day_total
        locations = []
        for shift in shifts:
            location = str(shift.get("track_label") or shift.get("location") or "Shift")
            if location not in locations:
                locations.append(location)
        day_rows.append(
            {
                "date": day_item,
                "date_label": day_item.strftime("%a %d %b"),
                "iso": day_item.isoformat(),
                "total": day_total,
                "locations": ", ".join(locations) if locations else "-",
                "shifts": shifts,
            }
        )
    return {
        "submission_date": submission_date,
        "period_start": period_start,
        "period_end": period_end,
        "period_label": f"{period_start.strftime('%d %b')}-{period_end.strftime('%d %b %Y')}",
        "days": day_rows,
        "total": total_hours,
    }


def safe_next_url(value: str | None, fallback: str = "/month") -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def infer_display_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0].strip()
    local_part = re.sub(r"[._-]+", " ", local_part)
    display_name = " ".join(part.capitalize() for part in local_part.split() if part)
    return display_name or "Roster User"


def admin_user_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for user in list_app_users():
        item = dict(user)
        item["devices"] = [dict(device) for device in list_trusted_devices_for_user(int(user["id"]))]
        latest_capture = get_latest_deputy_web_capture_for_user(int(user["id"]))
        item["latest_capture"] = format_capture_payload(str(latest_capture["payload"] or "")) if latest_capture else None
        item["latest_capture_message"] = str(latest_capture["message"] or "") if latest_capture else ""
        rows.append(item)
    return rows


def admin_contact_rows() -> list[dict[str, object]]:
    contacts = []
    for user in list_app_users():
        if not int(user["is_admin"] or 0):
            continue
        contacts.append(
            {
                "display_name": user["display_name"] or infer_display_name_from_email(str(user["deputy_email"] or "")),
                "deputy_email": user["deputy_email"],
                "last_seen_at": user["last_seen_at"],
            }
        )
    return contacts


def refresh_learned_travel_defaults() -> int:
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in fetch_shifts_for_travel_learning():
        shift = dict(row)
        parsed = parse_shift_title(str(shift.get("title") or ""))
        normalised = source_payload_normalised(str(shift.get("source_payload") or ""))
        track_label = str(
            parsed.get("track_label")
            or normalised.get("location_name")
            or normalised.get("source_code")
            or ""
        ).strip()
        if not track_label or track_label.lower() in GENERIC_TRACK_LABELS:
            continue
        summary = parse_roster_summary(description_lines(str(shift.get("description") or "")))
        timings = timing_lookup(summary)
        base_label = "Office" if timings.get("office") else "Clow Place"
        base_clock = timings.get("office") or timings.get("clow place")
        on_track_clock = timings.get("on track")
        if not base_clock or not on_track_clock:
            continue
        start_at = clock_datetime_for_shift(shift, base_clock)
        on_track_at = clock_datetime_for_shift(shift, on_track_clock, start_at)
        if start_at is None or on_track_at is None:
            continue
        travel_minutes = int(round((on_track_at - start_at).total_seconds() / 60))
        if travel_minutes <= 0 or travel_minutes > 8 * 60:
            continue
        track_key = travel_default_key(track_label)
        if not track_key:
            continue
        groups.setdefault((track_key, track_label, base_label), []).append(
            {
                "travel_minutes": travel_minutes,
                "date": str(shift.get("date") or ""),
            }
        )

    saved = 0
    for (track_key, track_label, base_label), samples in groups.items():
        counts = Counter(int(sample["travel_minutes"]) for sample in samples)
        travel_minutes, _ = counts.most_common(1)[0]
        dates = sorted(str(sample["date"] or "") for sample in samples if str(sample["date"] or ""))
        upsert_travel_time_default(
            track_key=track_key,
            track_label=track_label,
            base_label=base_label,
            travel_minutes=travel_minutes,
            source="learned",
            sample_count=len(samples),
            first_seen_at=dates[0] if dates else "",
            last_seen_at=dates[-1] if dates else "",
            note="Learned from previous roster notes.",
        )
        saved += 1
    return saved


def travel_default_rows() -> list[dict[str, object]]:
    refresh_learned_travel_defaults()
    rows = []
    for row in list_travel_time_defaults():
        item = dict(row)
        item["travel_label"] = format_minutes_duration(int(item.get("travel_minutes") or 0))
        rows.append(item)
    return rows


def diagnostic_source_payloads(limit: int = 8) -> list[dict[str, object]]:
    payloads = []
    for row in get_recent_source_payloads(limit):
        item = dict(row)
        payloads.append(
            {
                "id": item.get("id"),
                "owner_user_id": item.get("owner_user_id"),
                "source_uid": item.get("source_uid"),
                "title": item.get("title"),
                "date": item.get("date"),
                "start_at": item.get("start_at"),
                "end_at": item.get("end_at"),
                "source_status": item.get("source_status"),
                "payload": pretty_source_payload(str(item.get("source_payload") or "")),
            }
        )
    return payloads


def build_error_report_diagnostics(request: Request, user: dict[str, object] | None) -> str:
    user_id = int(user["id"]) if user and user.get("id") is not None else None
    sync_state = get_user_sync_state(user_id) if user_id is not None else None
    raw_web_capture = redact_secret_text(get_last_deputy_web_capture())
    diagnostics = {
        "captured_at": datetime.now(get_settings().timezone).isoformat(timespec="seconds"),
        "request_path": str(request.url.path),
        "reporter": {
            "id": user_id,
            "display_name": (user or {}).get("display_name"),
            "email": (user or {}).get("deputy_email"),
        },
        "sync_status": get_manual_sync_status(user_id),
        "user_sync_state": dict(sync_state) if sync_state else {},
        "schedule_snapshot": get_deputy_schedule_snapshot(),
        "recent_sync_logs": [dict(row) for row in get_recent_sync_logs(8)],
        "recent_source_payloads": diagnostic_source_payloads(),
        "last_deputy_web_capture": truncate_diagnostic_text(raw_web_capture),
    }
    return json.dumps(diagnostics, ensure_ascii=True, indent=2, sort_keys=True)


def format_error_reports() -> list[dict[str, object]]:
    reports = []
    for row in list_error_reports():
        item = dict(row)
        item["diagnostics_pretty"] = pretty_source_payload(str(item.get("diagnostics") or ""))
        reports.append(item)
    return reports


def set_trusted_device_cookie(response: RedirectResponse, user: object, request: Request) -> None:
    settings = get_settings()
    token = new_session_token()
    create_trusted_device(
        user_id=int(user["id"]),
        token_hash=hash_session_token(token),
        expires_at=session_expires_at(settings),
        label="Trusted device",
        user_agent=request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.trusted_device_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def signup_enabled() -> bool:
    settings = get_settings()
    return settings.signup_enabled


def credential_form_values(form: object, default_web_url: str = "") -> tuple[str, str, str]:
    deputy_email = str(form.get("deputy_email") or "").strip().lower()
    deputy_password = str(form.get("deputy_password") or "").strip()
    deputy_web_url = str(form.get("deputy_web_url") or default_web_url).strip()
    return deputy_email, deputy_password, deputy_web_url


def existing_encrypted_deputy_password(user_id: int) -> str:
    secret = get_deputy_user_secret(user_id)
    return str(secret["encrypted_password"] or "") if secret else ""


def encrypted_deputy_password_for_update(
    *,
    user_id: int,
    submitted_password: str,
) -> tuple[str, bool]:
    settings = get_settings()
    cleaned_password = submitted_password.strip()
    if cleaned_password:
        return encrypt_text(cleaned_password, settings), True
    return existing_encrypted_deputy_password(user_id), False


def row_value(row: object, key: str, default: object = "") -> object:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        return default


def validate_deputy_credentials(
    *,
    deputy_email: str,
    deputy_password: str,
    deputy_web_url: str,
    password_required: bool = True,
) -> str:
    if "@" not in deputy_email:
        return "Enter the Deputy email address."
    if password_required and not deputy_password:
        return "Enter the Deputy password."
    if not deputy_web_url.startswith(("http://", "https://")):
        return "Deputy URL must start with http:// or https://."
    return ""


def credential_save_failed_response(path: str, user_id: int | None, exc: Exception) -> RedirectResponse:
    if user_id is not None:
        try:
            mark_user_sync_finished(
                user_id,
                finished_at=datetime.now(get_settings().timezone).isoformat(timespec="seconds"),
                status="error",
                message=f"Deputy login save failed: {type(exc).__name__}. No password was logged.",
            )
        except Exception:
            pass
    return RedirectResponse(
        url=notice_url(
            path,
            "Deputy login could not be saved. Check the URL/email/password and try again, or ask an admin to update it.",
        ),
        status_code=303,
    )


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    purge_old_inactive_records(days=30)
    reset_incomplete_user_syncs()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    shutdown_scheduler()


@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse(url="/month", status_code=303)


@app.get("/help")
def help_view(request: Request, notice: str | None = None) -> object:
    return templates.TemplateResponse(
        "help.html",
        {
            "request": request,
            "notice": notice,
            "current_user": current_user(request),
            "header_mode": "settings",
            "admin_contacts": admin_contact_rows(),
        },
    )


@app.get("/signup")
def signup_view(request: Request, next: str | None = None, notice: str | None = None) -> object:
    if not signup_enabled() and count_app_users() > 0:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "notice": notice,
            "next_url": safe_next_url(next),
            "default_deputy_web_url": get_settings().deputy_web_url,
        },
    )


@app.post("/signup")
async def signup_submit(request: Request, background_tasks: BackgroundTasks) -> RedirectResponse:
    form = await request.form()
    next_url = safe_next_url(str(form.get("next_url") or ""))
    settings = get_settings()
    if not settings.signup_enabled and count_app_users() > 0:
        return RedirectResponse(url=notice_url("/login", "Signup is currently closed."), status_code=303)

    deputy_email, deputy_password, deputy_web_url = credential_form_values(
        form,
        settings.deputy_web_url,
    )
    pin = str(form.get("pin") or "")
    pin_confirm = str(form.get("pin_confirm") or "")

    if "@" not in deputy_email:
        return RedirectResponse(url=notice_url("/signup", "Enter your Deputy email address."), status_code=303)
    if not deputy_password:
        return RedirectResponse(url=notice_url("/signup", "Enter your Deputy password."), status_code=303)
    if len(pin) < 4 or not pin.isdigit():
        return RedirectResponse(url=notice_url("/signup", "Choose a numeric PIN with at least 4 digits."), status_code=303)
    if pin != pin_confirm:
        return RedirectResponse(url=notice_url("/signup", "PIN entries did not match."), status_code=303)
    if not deputy_web_url.startswith(("http://", "https://")):
        return RedirectResponse(url=notice_url("/signup", "Deputy URL must start with http:// or https://."), status_code=303)
    if get_app_user_by_email(deputy_email) is not None:
        return RedirectResponse(url=notice_url("/login", "That Deputy email is already signed up."), status_code=303)

    user = create_app_user(
        deputy_email=deputy_email,
        display_name=infer_display_name_from_email(deputy_email),
        pin_hash=hash_pin(pin),
        deputy_web_url=deputy_web_url,
        encrypted_email=encrypt_text(deputy_email, settings),
        encrypted_password=encrypt_text(deputy_password, settings),
    )
    queue_manual_sync(background_tasks, user_id=int(user["id"]))
    response = RedirectResponse(url=next_url, status_code=303)
    set_trusted_device_cookie(response, user, request)
    return response


@app.get("/login")
def login_view(request: Request, next: str | None = None, notice: str | None = None) -> object:
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "notice": notice,
            "next_url": safe_next_url(next),
            "signup_enabled": signup_enabled(),
        },
    )


@app.post("/login")
async def login_submit(request: Request) -> RedirectResponse:
    form = await request.form()
    next_url = safe_next_url(str(form.get("next_url") or ""))
    deputy_email = str(form.get("deputy_email") or "").strip().lower()
    pin = str(form.get("pin") or "")
    user = get_app_user_by_email(deputy_email)
    if user is None or not int(user["is_active"] or 0) or not verify_pin(pin, str(user["pin_hash"] or "")):
        return RedirectResponse(url=notice_url("/login", "Email or PIN was not recognised."), status_code=303)

    response = RedirectResponse(url=next_url, status_code=303)
    set_trusted_device_cookie(response, user, request)
    return response


@app.get("/logout")
def logout_view(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    clear_trusted_device(request, response)
    return response


@app.get("/admin")
def admin_view(request: Request, notice: str | None = None) -> object:
    user = require_admin_user(request)
    settings = get_settings()
    travel_defaults = travel_default_rows()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "notice": notice,
            "header_mode": "settings",
            "current_user": user,
            "settings": settings,
            "app_version": APP_VERSION,
            "app_build": APP_BUILD,
            "users": admin_user_rows(),
            "overrides": list_admin_overrides(),
            "error_reports": format_error_reports(),
            "travel_defaults": travel_defaults,
        },
    )


@app.post("/admin/users/{user_id}/devices/{device_id}/revoke")
def admin_revoke_device(request: Request, user_id: int, device_id: int) -> RedirectResponse:
    require_admin_user(request)
    revoked = revoke_trusted_device_for_user(user_id, device_id)
    message = "Trusted device revoked." if revoked else "That device was already revoked or could not be found."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/users/{user_id}/pin")
async def admin_reset_pin(request: Request, user_id: int) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    pin = str(form.get("pin") or "")
    pin_confirm = str(form.get("pin_confirm") or "")
    if len(pin) < 4 or not pin.isdigit():
        return RedirectResponse(url=notice_url("/admin", "PIN must be at least 4 digits."), status_code=303)
    if pin != pin_confirm:
        return RedirectResponse(url=notice_url("/admin", "PIN entries did not match."), status_code=303)
    updated = update_user_pin_hash(user_id, hash_pin(pin))
    message = "PIN reset." if updated else "User not found."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/users/{user_id}/deputy-login")
async def admin_update_user_deputy_login(request: Request, user_id: int) -> RedirectResponse:
    require_admin_user(request)
    settings = get_settings()
    try:
        form = await request.form()
        stored_user = get_app_user(user_id) or get_app_user_by_email(str(form.get("deputy_email") or ""))
        deputy_email, deputy_password, deputy_web_url = credential_form_values(
            form,
            str(row_value(stored_user, "deputy_web_url") or settings.deputy_web_url),
        )
        encrypted_password, password_changed = encrypted_deputy_password_for_update(
            user_id=user_id,
            submitted_password=deputy_password,
        )
        error = validate_deputy_credentials(
            deputy_email=deputy_email,
            deputy_password=deputy_password,
            deputy_web_url=deputy_web_url,
            password_required=not bool(encrypted_password),
        )
        if error:
            return RedirectResponse(url=notice_url("/admin", error), status_code=303)
        existing = get_app_user_by_email(deputy_email)
        if existing and int(existing["id"]) != user_id:
            return RedirectResponse(url=notice_url("/admin", "That Deputy email belongs to another roster user."), status_code=303)
        updated = update_deputy_user_credentials(
            user_id=user_id,
            deputy_email=deputy_email,
            deputy_web_url=deputy_web_url,
            encrypted_email=encrypt_text(deputy_email, settings),
            encrypted_password=encrypted_password,
        )
        if updated:
            password_note = " Password updated." if password_changed else " Existing password kept."
            message = f"Deputy login updated.{password_note} Run Sync This User to test it."
        else:
            message = "User not found."
        return RedirectResponse(url=notice_url("/admin", message), status_code=303)
    except Exception as exc:
        return credential_save_failed_response("/admin", user_id, exc)


@app.post("/admin/users/{user_id}/deactivate")
def admin_deactivate_user(request: Request, user_id: int) -> RedirectResponse:
    admin = require_admin_user(request)
    if int(admin["id"]) == user_id:
        return RedirectResponse(url=notice_url("/admin", "You cannot deactivate your own admin account."), status_code=303)
    target = get_app_user(user_id)
    if target and int(target["is_admin"] or 0) and count_active_admins(excluding_user_id=user_id) < 1:
        return RedirectResponse(url=notice_url("/admin", "Keep at least one active admin account."), status_code=303)
    updated = set_app_user_active(user_id, False)
    message = "User deactivated and trusted devices revoked." if updated else "User not found."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/users/{user_id}/reactivate")
def admin_reactivate_user(request: Request, user_id: int) -> RedirectResponse:
    require_admin_user(request)
    updated = set_app_user_active(user_id, True)
    message = "User reactivated. Ask them to log in again, then sync." if updated else "User not found."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/users/{user_id}/reset-roster")
def admin_reset_user_roster(request: Request, user_id: int) -> RedirectResponse:
    require_admin_user(request)
    result = reset_user_roster_data(user_id)
    message = f"Roster reset: {result['shifts']} shifts, {result['changes']} changes, {result['marks']} marks cleared."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/users/{user_id}/purge")
def admin_purge_user(request: Request, user_id: int) -> RedirectResponse:
    admin = require_admin_user(request)
    if int(admin["id"]) == user_id:
        return RedirectResponse(url=notice_url("/admin", "You cannot purge your own admin account."), status_code=303)
    result = purge_app_user(user_id)
    message = (
        f"Purged inactive user data: {result['shifts']} shifts, {result['devices']} devices."
        if result.get("users")
        else "Only inactive users can be purged."
    )
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/cleanup")
def admin_cleanup_old_records(request: Request) -> RedirectResponse:
    require_admin_user(request)
    result = purge_old_inactive_records(days=30)
    return RedirectResponse(
        url=notice_url("/admin", f"Cleanup complete: purged {result['users']} inactive users and {result['devices']} old revoked devices."),
        status_code=303,
    )


@app.post("/admin/users/{user_id}/sync")
def admin_sync_user(request: Request, user_id: int, background_tasks: BackgroundTasks) -> RedirectResponse:
    require_admin_user(request)
    target_user = get_app_user(user_id)
    if target_user is None:
        return RedirectResponse(url=notice_url("/admin", "User not found or inactive."), status_code=303)
    if not user_has_deputy_credentials(user_id):
        return RedirectResponse(url=notice_url("/admin", "That user does not have saved Deputy login details."), status_code=303)
    started = queue_manual_sync(background_tasks, user_id=user_id)
    message = "User sync started. Refresh Admin in a minute to copy the latest diagnostics." if started else "That user already has a sync running."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/clear-changed")
def admin_clear_changed(request: Request) -> RedirectResponse:
    require_admin_user(request)
    changed = clear_all_changed_flags()
    return RedirectResponse(url=notice_url("/admin", f"Cleared changed flags on {changed} items."), status_code=303)


@app.post("/admin/travel-defaults")
async def admin_save_travel_default(request: Request) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    track_label = str(form.get("track_label") or "").strip()
    base_label = str(form.get("base_label") or "Clow Place").strip() or "Clow Place"
    minutes_text = str(form.get("travel_minutes") or "").strip()
    note = str(form.get("note") or "").strip()
    try:
        travel_minutes = int(minutes_text)
    except ValueError:
        travel_minutes = 0
    if not track_label or travel_minutes <= 0:
        return RedirectResponse(url=notice_url("/admin", "Track and travel minutes are required."), status_code=303)
    upsert_travel_time_default(
        track_key=travel_default_key(track_label),
        track_label=track_label,
        base_label=base_label,
        travel_minutes=travel_minutes,
        source="manual",
        sample_count=0,
        note=note,
    )
    return RedirectResponse(url=notice_url("/admin", "Travel default saved."), status_code=303)


@app.post("/admin/travel-defaults/{default_id}/delete")
def admin_delete_travel_default(request: Request, default_id: int) -> RedirectResponse:
    require_admin_user(request)
    deleted = delete_travel_time_default(default_id)
    message = "Travel default deleted." if deleted else "Travel default not found."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/travel-defaults/{default_id}/edit")
async def admin_edit_travel_default(request: Request, default_id: int) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    track_label = str(form.get("track_label") or "").strip()
    base_label = str(form.get("base_label") or "Clow Place").strip() or "Clow Place"
    minutes_text = str(form.get("travel_minutes") or "").strip()
    note = str(form.get("note") or "").strip()
    try:
        travel_minutes = int(minutes_text)
    except ValueError:
        travel_minutes = 0
    if not track_label or travel_minutes <= 0:
        return RedirectResponse(url=notice_url("/admin", "Track and travel minutes are required."), status_code=303)
    updated = update_travel_time_default(
        default_id,
        track_key=travel_default_key(track_label),
        track_label=track_label,
        base_label=base_label,
        travel_minutes=travel_minutes,
        note=note,
    )
    message = "Travel default updated." if updated else "Travel default not found."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/overrides")
async def admin_create_override(request: Request) -> RedirectResponse:
    user = require_admin_user(request)
    form = await request.form()
    target_date = str(form.get("target_date") or "").strip()
    override_type = str(form.get("override_type") or "").strip()
    label = str(form.get("label") or "").strip()
    value = str(form.get("value") or "").strip()
    if not target_date or not override_type or not label or not value:
        return RedirectResponse(url=notice_url("/admin", "Date, type, label, and value are required."), status_code=303)
    create_admin_override(
        created_by_user_id=int(user["id"]),
        target_date=target_date,
        target_track=str(form.get("target_track") or "").strip(),
        override_type=override_type,
        label=label,
        value=value,
        note=str(form.get("note") or "").strip(),
    )
    return RedirectResponse(url=notice_url("/admin", "Admin override recorded."), status_code=303)


@app.get("/month")
def month_view(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    view: str = "month",
    notice: str | None = None,
) -> object:
    settings = get_settings()
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    today = datetime.now(settings.timezone).date()
    view = "list" if view == "list" else "month"
    year = year or today.year
    month = month or today.month
    if month < 1 or month > 12:
        raise HTTPException(status_code=404, detail="Invalid month")

    cal = calendar.Calendar(firstweekday=0)
    month_weeks = cal.monthdatescalendar(year, month)
    grid_start = month_weeks[0][0].isoformat()
    grid_end = month_weeks[-1][-1].isoformat()
    rows = fetch_shifts_between(grid_start, grid_end, owner_user_id=owner_user_id)
    open_shifts_by_date = open_schedule_by_date(grid_start, grid_end)

    shifts_by_date: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        shifts_by_date.setdefault(row["date"], []).append(decorate_shift(row))
    for date_key, day_shifts in list(shifts_by_date.items()):
        shifts_by_date[date_key] = combine_adjacent_shifts(day_shifts)
    love_racing_by_day = love_racing_by_date(grid_start, grid_end, shifts_by_date)

    weeks = []
    active_days = []
    month_total = 0.0
    for week in month_weeks:
        days = []
        week_total = 0.0
        for day_item in week:
            day_shifts = shifts_by_date.get(day_item.isoformat(), [])
            day_open_shifts = open_shifts_by_date.get(day_item.isoformat(), [])
            day_love_racing = love_racing_by_day.get(day_item.isoformat(), [])
            timesheet = timesheet_marker(day_item)
            day_total = sum(
                shift_hours_value(shift)
                for shift in day_shifts
                if not int(shift.get("deleted_from_source") or 0)
            )
            if day_item.month == month:
                month_total += day_total
            week_total += day_total
            days.append(
                {
                    "date": day_item,
                    "iso": day_item.isoformat(),
                    "day_number": day_item.day,
                    "in_month": day_item.month == month,
                    "is_today": day_item == today,
                    "shifts": day_shifts,
                    "open_shifts": day_open_shifts,
                    "love_racing_meetings": day_love_racing,
                    "total": day_total,
                    "timesheet": timesheet,
                }
            )
            if day_item.month == month and (day_shifts or timesheet or day_open_shifts or day_love_racing):
                active_days.append(
                    {
                        "date": day_item,
                        "iso": day_item.isoformat(),
                        "shifts": day_shifts,
                        "open_shifts": day_open_shifts,
                        "love_racing_meetings": day_love_racing,
                        "total": day_total,
                        "timesheet": timesheet,
                    }
                )
        weeks.append({"days": days, "total": week_total})

    prev_year, prev_month = add_months(year, month, -1)
    next_year, next_month = add_months(year, month, 1)
    first_day = date(year, month, 1)
    now_iso = datetime.now(settings.timezone).replace(microsecond=0).isoformat()
    upcoming_shifts = combine_adjacent_shifts(
        [decorate_shift(row) for row in get_upcoming_shifts(now_iso, limit=10, owner_user_id=owner_user_id)]
    )[:5]

    return templates.TemplateResponse(
        "month.html",
        {
            "request": request,
            "notice": notice,
            "current_user": user,
            "header_context": first_day.strftime("%B %Y"),
            "header_prev_url": f"/month?year={prev_year}&month={prev_month}&view={view}",
            "header_next_url": f"/month?year={next_year}&month={next_month}&view={view}",
            "settings": settings,
            "weeks": weeks,
            "active_days": active_days,
            "upcoming_shifts": upcoming_shifts,
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "month_name": first_day.strftime("%B %Y"),
            "month_total": month_total,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "today": today,
            "view": view,
            "month_view_url": f"/month?year={year}&month={month}&view=month",
            "list_view_url": f"/month?year={year}&month={month}&view=list",
        },
    )


@app.get("/timesheet/{date_text}")
def timesheet_view(request: Request, date_text: str, notice: str | None = None) -> object:
    try:
        submission_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invalid date") from exc

    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    summary = build_timesheet_summary(submission_date, owner_user_id=owner_user_id)
    return templates.TemplateResponse(
        "timesheet.html",
        {
            "request": request,
            "notice": notice,
            "current_user": user,
            "summary": summary,
            "month_year": submission_date.year,
            "month_number": submission_date.month,
        },
    )


@app.get("/day/{date_text}")
def day_view(request: Request, date_text: str, notice: str | None = None) -> object:
    try:
        day_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invalid date") from exc

    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    shifts = combine_adjacent_shifts(
        [decorate_shift(row) for row in fetch_shifts_for_date(date_text, owner_user_id=owner_user_id)]
    )
    open_shifts = open_schedule_by_date(date_text, date_text).get(date_text, [])
    changes_by_shift: dict[int, list[dict[str, object]]] = {}
    for row in get_shift_changes_for_date(date_text):
        change = decorate_change(row)
        changes_by_shift.setdefault(int(change["shift_id"]), []).append(change)
    for shift in shifts:
        combined_ids = [int(shift_id) for shift_id in shift.get("combined_shift_ids", [shift["id"]])]
        shift["changes"] = [
            change
            for shift_id in combined_ids
            for change in changes_by_shift.get(shift_id, [])
        ]
        shift["changes"] = compact_shift_changes(list(shift.get("changes") or []))
        latest_change_at = latest_iso_datetime(
            shift.get("last_changed_at"),
            *(change.get("changed_at") for change in shift.get("changes") or []),
        )
        if latest_change_at:
            shift["change_time_label"] = format_datetime(latest_change_at, "%d %b %H:%M")
            shift["change_badge_label"] = f"Changed · {shift['change_time_label']}"
        shift["change_summary_text"] = build_shift_change_summary(list(shift.get("changes") or []))
    schedule_location_ids = shift_schedule_location_ids(shifts)
    schedule_expected_areas = fetch_deputy_schedule_areas_for_locations(schedule_location_ids)
    deputy_schedule_label = deputy_schedule_label_for_shifts("Deputy Schedule", shifts)
    deputy_schedule_people = schedule_people(
        fetch_deputy_schedule_for_date(
            date_text,
            location_ids=schedule_location_ids or None,
        ),
        expected_areas=schedule_expected_areas,
    )
    apply_roster_note_vehicles(deputy_schedule_people, shifts)
    if not deputy_schedule_people and is_overnight_travel_day(shifts):
        next_day_text = (day_date + timedelta(days=1)).isoformat()
        next_day_shifts = combine_adjacent_shifts(
            [decorate_shift(row) for row in fetch_shifts_for_date(next_day_text, owner_user_id=owner_user_id)]
        )
        next_day_location_ids = shift_schedule_location_ids(next_day_shifts) or schedule_location_ids
        next_day_expected_areas = fetch_deputy_schedule_areas_for_locations(next_day_location_ids)
        deputy_schedule_people = schedule_people(
            fetch_deputy_schedule_for_date(
                next_day_text,
                location_ids=next_day_location_ids or None,
            ),
            expected_areas=next_day_expected_areas,
        )
        apply_roster_note_vehicles(deputy_schedule_people, next_day_shifts)
        if deputy_schedule_people:
            deputy_schedule_label = deputy_schedule_label_for_shifts("Deputy Schedule - Next Day Crew", next_day_shifts)
    for shift in shifts:
        if str(shift.get("header_vehicle_label") or "").strip():
            continue
        shift["header_vehicle_label"] = vehicle_for_user_from_schedule(deputy_schedule_people, user, shift)
    deputy_schedule_changed = any(bool(person.get("changed")) for person in deputy_schedule_people)
    deputy_schedule_changes = [person for person in deputy_schedule_people if person.get("changed")]
    day_total = sum(
        shift_hours_value(shift)
        for shift in shifts
        if not int(shift.get("deleted_from_source") or 0)
    )
    has_changed = any(int(shift.get("changed_since_viewed") or 0) for shift in shifts) or deputy_schedule_changed
    return templates.TemplateResponse(
        "day.html",
        {
            "request": request,
            "notice": notice,
            "current_user": user,
            "date_text": date_text,
            "day_date": day_date,
            "month_year": day_date.year,
            "month_number": day_date.month,
            "shifts": shifts,
            "open_shifts": open_shifts,
            "deputy_schedule_people": deputy_schedule_people,
            "deputy_schedule_label": deputy_schedule_label,
            "deputy_schedule_changed": deputy_schedule_changed,
            "deputy_schedule_changes": deputy_schedule_changes,
            "day_total": day_total,
            "has_changed": has_changed,
            "mark_fields": MARK_FIELDS,
        },
    )


@app.post("/day/{date_text}/mark-viewed")
def mark_day_viewed(request: Request, date_text: str) -> RedirectResponse:
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    clear_changed_for_date(date_text, owner_user_id=owner_user_id)
    return RedirectResponse(
        url=notice_url(f"/day/{date_text}", "Changed flags cleared for this day."),
        status_code=303,
    )


@app.post("/day/{date_text}/mark-viewed.json")
def mark_day_viewed_json(request: Request, date_text: str) -> JSONResponse:
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    cleared = clear_changed_for_date(date_text, owner_user_id=owner_user_id)
    return JSONResponse({"ok": True, "cleared": cleared})


@app.get("/shift/{shift_id}")
def shift_view(request: Request, shift_id: int) -> RedirectResponse:
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    shift = fetch_shift(shift_id, owner_user_id=owner_user_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    return RedirectResponse(url=f"/day/{shift['date']}", status_code=303)


@app.post("/shift/{shift_id}/marks")
async def save_shift_marks(shift_id: int, request: Request) -> RedirectResponse:
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    shift = fetch_shift(shift_id, owner_user_id=owner_user_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")

    form = await request.form()
    values: dict[str, object] = {}
    for field, _label in MARK_FIELDS:
        values[field] = 1 if form.get(field) else 0
    values["private_note"] = str(form.get("private_note") or "").strip()
    values["custom_colour"] = clean_colour(str(form.get("custom_colour") or ""))
    values["timing_adjustment_time"] = clean_time_value(str(form.get("timing_adjustment_time") or ""))
    values["timing_adjustment_last_race"] = 1 if form.get("timing_adjustment_last_race") else 0
    values["timing_adjustment_day_finished"] = 1 if form.get("timing_adjustment_day_finished") else 0
    update_shift_marks(shift_id, values, owner_user_id=owner_user_id)
    return RedirectResponse(
        url=notice_url(f"/day/{shift['date']}", "Notes saved.") + f"#shift-{shift_id}",
        status_code=303,
    )


@app.post("/shift/{shift_id}/mark-viewed")
def mark_shift_viewed(request: Request, shift_id: int) -> RedirectResponse:
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    shift = fetch_shift(shift_id, owner_user_id=owner_user_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    clear_changed_for_shift(shift_id, owner_user_id=owner_user_id)
    return RedirectResponse(
        url=notice_url(f"/day/{shift['date']}", "Changed flag cleared.") + f"#shift-{shift_id}",
        status_code=303,
    )


@app.get("/settings")
def settings_view(request: Request, notice: str | None = None) -> object:
    settings = get_settings()
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    user_can_sync = bool(owner_user_id is not None and user_has_deputy_credentials(owner_user_id))
    now = datetime.now(settings.timezone).replace(microsecond=0)
    next_shift = get_next_upcoming_shift(now.isoformat(), owner_user_id=owner_user_id)
    pre_shift = get_pre_shift_status(settings)
    calendar_url = get_calendar_url(settings)
    user_calendar_url_configured = bool(owner_user_id is not None and user_has_ical_url(owner_user_id))
    legacy_calendar_url_configured = bool(calendar_url)
    calendar_url_source = get_calendar_url_source(settings)
    if owner_user_id is not None:
        calendar_url_source = "This account" if user_calendar_url_configured else "Not saved for this account"
    deputy_web_capture = format_capture_payload(get_last_deputy_web_capture())
    schedule_snapshot = get_deputy_schedule_snapshot()
    capture_stats = deputy_web_capture.get("stats", {}) if deputy_web_capture else {}
    roster_snapshot = {
        "status_label": "Ready" if (settings.deputy_login_configured or user_can_sync) else "Deputy login needed",
        "captured_at": (deputy_web_capture or {}).get("captured_at") or schedule_snapshot.get("captured_at") or "",
        "target": capture_stats.get("target_track") or "All Locations",
        "date_label": capture_stats.get("schedule_date_label") or "",
        "published": int(capture_stats.get("published_count") or schedule_snapshot.get("published_rows") or 0),
        "open": int(capture_stats.get("open_shift_count") or schedule_snapshot.get("open_rows") or 0),
        "unavailable": int(capture_stats.get("unavailable_count") or 0),
        "warnings": int(capture_stats.get("warning_count") or 0),
        "changed": int(schedule_snapshot.get("changed_rows") or 0),
    }
    roster_insights = build_roster_insights(owner_user_id, now.date())
    love_racing_snapshot = get_love_racing_snapshot(now.date().isoformat())
    love_racing_snapshot["upcoming"] = [
        decorate_love_racing_meeting(item)
        for item in love_racing_snapshot.get("upcoming", [])
    ]
    love_racing_status = {
        "status": get_app_setting("love_racing_last_status", ""),
        "checked_at": get_app_setting("love_racing_last_checked_at", ""),
        "message": get_app_setting("love_racing_last_message", ""),
        "error": get_app_setting("love_racing_last_error", ""),
        "fetched_rows": get_app_setting("love_racing_last_fetched_rows", "0"),
        "matched_rows": get_app_setting("love_racing_last_matched_rows", "0"),
        "saved_rows": get_app_setting("love_racing_last_saved_rows", "0"),
        "known_locations": get_app_setting("love_racing_last_known_locations", "0"),
        "source_url": get_app_setting("love_racing_last_source_url", ""),
        "status_code": get_app_setting("love_racing_last_status_code", ""),
        "content_length": get_app_setting("love_racing_last_content_length", ""),
        "attempts": get_app_setting("love_racing_last_attempts", ""),
    }
    user_sync_state = get_user_sync_state(owner_user_id) if owner_user_id is not None else None
    account_user = get_app_user(owner_user_id) if owner_user_id is not None else None
    account_secret = get_deputy_user_secret(owner_user_id) if owner_user_id is not None else None
    user_last_sync_at = str(user_sync_state["last_sync_at"] or "") if user_sync_state else ""
    raw_theme = user["display_theme"] if user and user.get("display_theme") else request.cookies.get("roster_theme", "jade")
    current_theme = normalise_theme(raw_theme)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "notice": notice,
            "current_user": user,
            "account_user": account_user,
            "account_credentials_updated_at": str(account_secret["updated_at"] or "") if account_secret else "",
            "account_has_deputy_credentials": bool(
                account_secret and account_secret["encrypted_email"] and account_secret["encrypted_password"]
            ),
            "header_mode": "settings",
            "settings": settings,
            "can_sync": settings.deputy_login_configured or user_can_sync,
            "trusted_device_days": settings.trusted_device_days,
            "calendar_url_configured": user_calendar_url_configured or (owner_user_id is None and legacy_calendar_url_configured),
            "calendar_url_source": calendar_url_source,
            "legacy_calendar_url_configured": legacy_calendar_url_configured,
            "last_successful_sync": get_last_successful_sync(),
            "user_last_sync_at": user_last_sync_at,
            "next_shift": decorate_shift(next_shift) if next_shift else None,
            "pre_shift": pre_shift,
            "sync_status": get_manual_sync_status(owner_user_id),
            "sync_logs": get_recent_sync_logs(),
            "source_payload_shifts": [
                decorate_shift(row)
                for row in get_recent_source_payloads()
            ],
            "deputy_web_capture": deputy_web_capture,
            "deputy_schedule_snapshot": schedule_snapshot,
            "roster_snapshot": roster_snapshot,
            "roster_insights": roster_insights,
            "love_racing_snapshot": love_racing_snapshot,
            "love_racing_status": love_racing_status,
            "open_schedule_shifts": visible_open_schedule_shifts(),
            "love_racing_url": LOVE_RACING_URL,
            "theme_groups": THEME_GROUPS,
            "current_theme": current_theme,
            "current_theme_label": THEME_LABELS.get(current_theme, "Jade dark"),
        },
    )


@app.post("/settings/theme")
async def save_theme_settings(request: Request) -> RedirectResponse:
    user = current_user(request)
    form = await request.form()
    theme = normalise_theme(form.get("theme"))
    if user and user.get("id") is not None:
        update_user_display_theme(int(user["id"]), theme)
    response = RedirectResponse(url=notice_url("/settings", "Theme saved."), status_code=303)
    response.set_cookie(
        "roster_theme",
        theme,
        max_age=365 * 24 * 60 * 60,
        httponly=False,
        samesite="lax",
    )
    return response


@app.post("/settings/love-racing-refresh")
def refresh_love_racing_calendar(request: Request) -> RedirectResponse:
    settings = get_settings()
    checked_at = datetime.now(settings.timezone).isoformat(timespec="seconds")
    today = datetime.now(settings.timezone).date()
    known_locations = list_known_racecourse_names()
    try:
        result = fetch_love_racing_meetings(known_locations, today=today)
        saved = save_love_racing_meetings(result.meetings, checked_at)
        if saved:
            status = "ok"
            message = f"Love Racing planning calendar updated. {saved} known future race days saved."
        else:
            status = "empty"
            message = (
                "Love Racing scan ran, but no future race days matched known worked locations. "
                f"It saw {result.fetched_rows} public date rows."
            )
        update_app_settings(
            {
                "love_racing_last_status": status,
                "love_racing_last_sync_at": checked_at if saved else get_app_setting("love_racing_last_sync_at", ""),
                "love_racing_last_checked_at": checked_at,
                "love_racing_last_message": message,
                "love_racing_last_error": "",
                "love_racing_last_fetched_rows": str(result.fetched_rows),
                "love_racing_last_matched_rows": str(result.matched_rows),
                "love_racing_last_saved_rows": str(saved),
                "love_racing_last_known_locations": str(len(known_locations)),
                "love_racing_last_source_url": result.source_url,
                "love_racing_last_status_code": str(result.status_code),
                "love_racing_last_content_length": str(result.content_length),
                "love_racing_last_attempts": " | ".join(result.attempts),
            }
        )
    except Exception as exc:
        error_detail = f"{type(exc).__name__}: {str(exc) or '(no message)'}"
        update_app_settings(
            {
                "love_racing_last_status": "error",
                "love_racing_last_checked_at": checked_at,
                "love_racing_last_message": "Love Racing scan failed.",
                "love_racing_last_error": error_detail[:500],
                "love_racing_last_fetched_rows": "0",
                "love_racing_last_matched_rows": "0",
                "love_racing_last_saved_rows": "0",
                "love_racing_last_known_locations": str(len(known_locations)),
                "love_racing_last_source_url": "",
                "love_racing_last_status_code": "",
                "love_racing_last_content_length": "",
                "love_racing_last_attempts": " | ".join(getattr(exc, "attempts", ()) or ()),
            }
        )
        message = f"Love Racing scan failed: {error_detail[:180]}"
    return RedirectResponse(url=notice_url("/settings", message), status_code=303)


@app.post("/settings/pin")
async def change_own_pin(request: Request) -> RedirectResponse:
    user = current_user(request)
    if not user or user.get("id") is None:
        return RedirectResponse(url=notice_url("/login", "Log in before changing your PIN."), status_code=303)
    form = await request.form()
    current_pin = str(form.get("current_pin") or "")
    new_pin = str(form.get("pin") or "")
    pin_confirm = str(form.get("pin_confirm") or "")
    if len(new_pin) < 4 or not new_pin.isdigit():
        return RedirectResponse(url=notice_url("/settings", "New PIN must be at least 4 digits."), status_code=303)
    if new_pin != pin_confirm:
        return RedirectResponse(url=notice_url("/settings", "PIN entries did not match."), status_code=303)
    stored_user = get_app_user(int(user["id"]))
    if stored_user is None or not verify_pin(current_pin, str(stored_user["pin_hash"] or "")):
        return RedirectResponse(url=notice_url("/settings", "Current PIN was not recognised."), status_code=303)
    update_user_pin_hash(int(user["id"]), hash_pin(new_pin))
    return RedirectResponse(url=notice_url("/settings", "PIN changed."), status_code=303)


@app.post("/settings/deputy-login")
async def update_own_deputy_login(request: Request) -> RedirectResponse:
    user = current_user(request)
    if not user or user.get("id") is None:
        return RedirectResponse(url=notice_url("/login", "Log in before updating Deputy login details."), status_code=303)
    settings = get_settings()
    user_id = int(user["id"])
    try:
        stored_user = get_app_user(user_id)
        form = await request.form()
        deputy_email, deputy_password, deputy_web_url = credential_form_values(
            form,
            str(row_value(stored_user, "deputy_web_url") or settings.deputy_web_url),
        )
        encrypted_password, password_changed = encrypted_deputy_password_for_update(
            user_id=user_id,
            submitted_password=deputy_password,
        )
        error = validate_deputy_credentials(
            deputy_email=deputy_email,
            deputy_password=deputy_password,
            deputy_web_url=deputy_web_url,
            password_required=not bool(encrypted_password),
        )
        if error:
            return RedirectResponse(url=notice_url("/settings", error), status_code=303)
        existing = get_app_user_by_email(deputy_email)
        if existing and int(existing["id"]) != user_id:
            return RedirectResponse(url=notice_url("/settings", "That Deputy email belongs to another roster user."), status_code=303)
        updated = update_deputy_user_credentials(
            user_id=user_id,
            deputy_email=deputy_email,
            deputy_web_url=deputy_web_url,
            encrypted_email=encrypt_text(deputy_email, settings),
            encrypted_password=encrypted_password,
        )
        if updated:
            password_note = " Password updated." if password_changed else " Existing password kept."
            message = f"Deputy login updated.{password_note} Run Sync my roster to test it."
        else:
            message = "User not found."
        return RedirectResponse(url=notice_url("/settings", message), status_code=303)
    except Exception as exc:
        return credential_save_failed_response("/settings", user_id, exc)


@app.post("/settings/error-report")
async def submit_error_report(request: Request) -> RedirectResponse:
    user = current_user(request)
    form = await request.form()
    report_text = str(form.get("report_text") or "").strip()
    if len(report_text) < 5:
        return RedirectResponse(url=notice_url("/settings", "Add a few words about what looks wrong."), status_code=303)
    page_url = str(form.get("page_url") or request.headers.get("referer") or request.url.path)
    diagnostics = build_error_report_diagnostics(request, user)
    create_error_report(
        user_id=int(user["id"]) if user and user.get("id") is not None else None,
        report_text=report_text,
        page_url=page_url,
        user_agent=request.headers.get("user-agent", ""),
        diagnostics=diagnostics,
    )
    return RedirectResponse(url=notice_url("/settings", "Error report saved with the latest diagnostics."), status_code=303)


@app.post("/settings/deactivate-account")
def deactivate_own_account(request: Request) -> RedirectResponse:
    user = current_user(request)
    if not user or user.get("id") is None:
        return RedirectResponse(url=notice_url("/login", "Log in before changing account status."), status_code=303)
    user_id = int(user["id"])
    if int(user.get("is_admin") or 0) and count_active_admins(excluding_user_id=user_id) < 1:
        return RedirectResponse(url=notice_url("/settings", "Keep at least one active admin account."), status_code=303)
    set_app_user_active(user_id, False)
    response = RedirectResponse(
        url=notice_url("/login", "Your roster viewer account was deactivated. Its data will be purged after 30 days unless an admin reactivates it."),
        status_code=303,
    )
    clear_trusted_device(request, response)
    return response


@app.post("/settings/calendar")
async def save_calendar_settings(request: Request) -> RedirectResponse:
    form = await request.form()
    user = current_user(request)
    user_id = int(user["id"]) if user and user.get("id") is not None else None
    if form.get("clear_calendar_url"):
        if user_id is not None:
            update_deputy_user_ical_url(user_id, "")
        else:
            update_app_settings({"deputy_ical_url": ""})
        return RedirectResponse(
            url=notice_url("/settings", "Saved iCal URL cleared."),
            status_code=303,
        )

    calendar_url = str(form.get("deputy_ical_url") or "").strip()
    if not calendar_url:
        return RedirectResponse(
            url=notice_url("/settings", "Paste a calendar URL before saving."),
            status_code=303,
        )
    if not calendar_url.startswith(("http://", "https://")):
        return RedirectResponse(
            url=notice_url("/settings", "Calendar URL must start with http:// or https://."),
            status_code=303,
        )

    if user_id is not None:
        update_deputy_user_ical_url(user_id, encrypt_text(calendar_url, get_settings()))
    else:
        update_app_settings({"deputy_ical_url": calendar_url})
    return RedirectResponse(
        url=notice_url("/settings", "iCal URL saved for this account. Use Sync my roster to refresh."),
        status_code=303,
    )


@app.post("/settings/clear-changed")
def clear_all_changed(request: Request) -> RedirectResponse:
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    if owner_user_id is None:
        changed = clear_all_changed_flags()
    else:
        changed = clear_changed_flags_for_user(owner_user_id)
    return RedirectResponse(
        url=notice_url("/settings", f"Cleared changed flags on {changed} of your shifts."),
        status_code=303,
    )


@app.post("/settings/deputy-api-test")
def test_deputy_api() -> RedirectResponse:
    result = test_deputy_roster_api(get_settings())
    message = result.message
    if result.sample:
        fields = ", ".join(key for key, value in result.sample.items() if value not in (None, "", []))
        message = f"{message} First record includes: {fields}."
    return RedirectResponse(url=notice_url("/settings", message), status_code=303)


@app.post("/settings/deputy-web-capture")
async def capture_deputy_web(request: Request) -> RedirectResponse:
    user = current_user(request)
    user_id = int(user["id"]) if user and user.get("id") is not None else None
    settings = get_settings()
    runtime_settings = settings_for_user(user_id, settings) if user_id is not None else None
    result = await capture_and_save_deputy_web(runtime_settings or settings, owner_user_id=user_id)
    return RedirectResponse(url=notice_url("/settings", str(result["message"])), status_code=303)


@app.api_route("/sync-now", methods=["GET", "POST"], response_model=None)
def sync_now(request: Request, background_tasks: BackgroundTasks, next: str | None = None) -> object:
    user = current_user(request)
    user_id = int(user["id"]) if user and user.get("id") is not None else None
    started = queue_manual_sync(background_tasks, user_id=user_id)
    status = get_manual_sync_status(user_id)
    wants_json = request.headers.get("x-requested-with") == "fetch" or "application/json" in request.headers.get("accept", "")
    if wants_json:
        return JSONResponse({"started": started, **status})
    message = "Sync started." if started else "Sync already running."
    redirect_path = next if next and next.startswith("/") and not next.startswith("//") else "/settings"
    return RedirectResponse(url=notice_url(redirect_path, message), status_code=303)


@app.get("/sync-status")
def sync_status(request: Request) -> JSONResponse:
    user = current_user(request)
    user_id = int(user["id"]) if user and user.get("id") is not None else None
    return JSONResponse(get_manual_sync_status(user_id))


templates.env.filters["datetime"] = format_datetime
templates.env.filters["time"] = format_time
templates.env.filters["day_short"] = format_day_short
templates.env.filters["hours"] = format_hours
templates.env.globals["app_build"] = APP_BUILD
templates.env.globals["theme_values"] = THEME_VALUES
