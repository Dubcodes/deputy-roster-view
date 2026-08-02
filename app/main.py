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
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from .auth import clear_trusted_device, current_user, require_admin_user, trusted_device_middleware
from .config import get_settings
from .database import (
    calendar_location_key,
    canonical_travel_base_label,
    canonical_travel_track,
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
    disable_admin_override,
    DEPUTY_AREA_OVERRIDES,
    ensure_user_sync_state,
    fetch_open_deputy_schedule_between,
    fetch_open_deputy_schedule_shifts,
    fetch_published_roster_days_between,
    fetch_deputy_schedule_between,
    fetch_deputy_assignment_history_for_date,
    fetch_deputy_event_changes_for_date,
    fetch_personal_assignment_evidence_for_date,
    fetch_love_racing_meetings_between,
    fetch_love_racing_details_between,
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
    get_roster_day,
    get_roster_day_assignments,
    fetch_shift,
    fetch_shifts_between,
    fetch_shifts_for_date,
    get_last_successful_sync,
    get_next_upcoming_shift,
    get_recent_source_payloads,
    get_roster_integrity_diagnostics,
    get_travel_time_default,
    get_travel_route,
    get_track_map,
    get_track_map_migration_warning,
    get_recent_sync_logs,
    get_upcoming_shifts,
    get_user_sync_state,
    init_db,
    list_admin_overrides,
    list_active_admin_overrides_between,
    list_app_users,
    list_error_reports,
    list_planning_locations,
    list_love_racing_detail_diagnostics,
    list_roster_builder_area_names,
    list_roster_builder_location_labels,
    list_roster_days,
    list_roster_day_versions,
    list_workday_roles,
    list_travel_time_defaults,
    list_travel_routes,
    list_known_place_labels,
    list_crew_work_location_labels,
    list_crew_people,
    crew_identity_records,
    crew_link_change_preview,
    identity_link_diagnostics,
    list_track_maps,
    list_track_map_location_rules,
    list_track_map_migration_warnings,
    list_trusted_devices_for_user,
    mark_user_sync_finished,
    mark_user_sync_started,
    revoke_trusted_device_for_user,
    reset_incomplete_user_syncs,
    reset_user_roster_data,
    purge_app_user,
    purge_old_inactive_records,
    publish_roster_day,
    merge_crew_people,
    reconcile_authenticated_identities,
    resolve_workday_snapshot_assignments,
    update_deputy_user_ical_url,
    update_deputy_user_credentials,
    update_app_settings,
    update_shift_marks,
    set_app_user_active,
    set_planning_location_enabled,
    save_roster_day,
    save_workday_role,
    upsert_travel_time_default,
    delete_travel_time_default,
    update_travel_time_default,
    upsert_travel_route,
    delete_travel_route,
    update_crew_person,
    transfer_app_user_link,
    update_user_display_theme,
    update_user_pin_hash,
    upsert_track_map_location_rule,
    delete_track_map_location_rule,
    user_has_deputy_credentials,
    user_has_ical_url,
    visible_workday_ids_for_user,
)
from .deputy_api import test_deputy_roster_api
from .deputy_web import capture_and_save_deputy_web, format_capture_payload
from .love_racing import LOVE_RACING_URL
from .planning_calendar import (
    preview_love_racing_meeting,
    queue_due_love_racing_details,
    refresh_unresolved_race_days,
    refresh_planning_calendar,
    run_love_racing_detail_jobs,
)
from .admin_overrides import (
    DURATION_FIELDS,
    FIELD_LABELS as ADMIN_OVERRIDE_FIELD_LABELS,
    canonical_override_venue,
)
from .workday_builder import (
    TRANSPORT_LABELS,
    TRANSPORT_MODES,
    WORKDAY_PRESETS,
    WORKDAY_TYPE_LABELS,
    WORKDAY_TYPES,
    canonical_role_key,
    normalise_role_key,
    transport_display,
)
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
from .track_maps import (
    MAX_MANUAL_MAP_BYTES,
    classify_track_map_location,
    effective_track_map_file,
    image_dimensions,
    migrate_existing_track_map_aliases,
    refresh_track_maps,
    reset_manual_track_map,
    save_manual_track_map,
    track_map_storage_key,
    track_map_location_rule_index,
)
from .public_holidays import holiday_for_date


APP_DIR = Path(__file__).resolve().parent
APP_VERSION = "0.5.0"
APP_BUILD = "2026.08.03.1"
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
            ("grant", "Sound/VT"),
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
    "SVT": "Sound/VT",
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
    "track": "Location",
    "role": "Role",
}
HIDDEN_CHANGE_FIELDS = {"break_minutes", "paid_hours"}
ROSTER_TIME_TOKEN_PATTERN = (
    r"(?<![\d.])(?:"
    r"\d{1,2}[.:]\d{2}\s*(?:am|pm)?|"
    r"\d{1,2}\s+\d{2}\s*(?:am|pm)?|"
    r"\d{3,4}\s*(?:am|pm)?|"
    r"\d{1,2}\s*(?:am|pm)"
    r")(?![\d.])"
)
ROSTER_TIME_TOKEN_RE = re.compile(ROSTER_TIME_TOKEN_PATTERN, re.IGNORECASE)
TIMING_LINE_PATTERNS = (
    ("Trucks", re.compile(r"^trucks?\s+(.+)$", re.IGNORECASE)),
    ("Office", re.compile(r"^office\s+(.+)$", re.IGNORECASE)),
    ("Clow Place", re.compile(r"^clow\s+(?:place|pl)\s+(.+)$", re.IGNORECASE)),
    ("On track", re.compile(r"^on\s+track\s+(.+)$", re.IGNORECASE)),
    ("First cross", re.compile(r"^first\s+cross\s+(.+)$", re.IGNORECASE)),
    ("Records", re.compile(r"^records?\s+(.+)$", re.IGNORECASE)),
    ("On Air", re.compile(r"^on\s+air\s+(.+)$", re.IGNORECASE)),
    ("Live", re.compile(r"^live\s+(.+)$", re.IGNORECASE)),
)
TIME_FIRST_TIMING_RE = re.compile(
    rf"^({ROSTER_TIME_TOKEN_PATTERN})\s+"
    r"(trucks?|office|clow\s+(?:place|pl)|on\s+track|first\s+cross|fx|records?|on\s+air|live)"
    r"\b(?:\s+(.+))?$",
    re.IGNORECASE,
)
INLINE_TIMING_RE = re.compile(
    rf"\b(first race|last race|first cross|records?|on\s+air|live)\s+({ROSTER_TIME_TOKEN_PATTERN})",
    re.IGNORECASE,
)
RACE_COUNT_RE = re.compile(r"\b(\d+)\s+races?\b", re.IGNORECASE)
RACE_COUNT_WITH_TIMES_RE = re.compile(
    rf"\b(\d+)\s+races?\s+({ROSTER_TIME_TOKEN_PATTERN})\s*(?:[-–]|\|)\s*({ROSTER_TIME_TOKEN_PATTERN})",
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
    "record": "Records",
    "records": "Records",
    "on air": "On air",
    "live": "Live",
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
    "soundvt": ("soundvt", "Sound/VT"),
    "svt": ("soundvt", "Sound/VT"),
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
ASSIGNED_ONLY_SCHEDULE_POSITION_KEYS = {"rts", "fm"}
PLACEHOLDER_SCHEDULE_POSITION_KEYS = set(SCHEDULE_POSITION_ORDER) - {
    "northern",
    *ASSIGNED_ONLY_SCHEDULE_POSITION_KEYS,
}
ROSTER_RACE_TYPES = (
    ("thoroughbred", "Thoroughbred racing"),
    ("harness", "Harness racing"),
    ("greyhound", "Greyhound racing"),
    ("trials", "Trials"),
)
ROSTER_RACE_TYPE_LABELS = dict(ROSTER_RACE_TYPES)


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
    key, _label = canonical_travel_track(value, value)
    return key


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


def travel_default_for_shift(
    shift: dict[str, object], base_label: str = "Office / Clow Place"
) -> dict[str, object] | None:
    keys = travel_default_keys_for_shift(shift)
    row = get_travel_time_default(keys, base_label=base_label)
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


def parse_roster_time_token(value: str) -> str:
    cleaned = re.sub(r"\s+", "", str(value or "").strip().lower().rstrip(".,"))
    if not cleaned:
        return ""
    meridiem = ""
    if cleaned.endswith(("am", "pm")):
        meridiem = cleaned[-2:]
        cleaned = cleaned[:-2]

    hour = ""
    minute = ""
    if ":" in cleaned or "." in cleaned:
        separator = ":" if ":" in cleaned else "."
        hour, minute = cleaned.split(separator, 1)
    elif len(cleaned) <= 2:
        hour, minute = cleaned, "00"
    elif len(cleaned) == 3:
        hour, minute = cleaned[:1], cleaned[1:]
    elif len(cleaned) == 4:
        hour, minute = cleaned[:2], cleaned[2:4]
    else:
        return ""

    if not hour.isdigit() or not minute.isdigit():
        return ""
    hour_int = int(hour)
    minute_int = int(minute)
    if meridiem and not 1 <= hour_int <= 12:
        return ""
    if meridiem == "pm" and hour_int < 12:
        hour_int += 12
    elif meridiem == "am" and hour_int == 12:
        hour_int = 0
    if hour_int > 23 or minute_int > 59:
        return ""
    return f"{hour_int:02d}:{minute_int:02d}"


def extract_roster_time_token(value: str) -> str:
    text = str(value or "").strip()
    if not text or re.fullmatch(r"\d+\s+races?", text, re.IGNORECASE):
        return ""
    match = ROSTER_TIME_TOKEN_RE.search(text)
    return parse_roster_time_token(match.group(0)) if match else ""


def normalise_roster_time(value: str) -> str:
    return parse_roster_time_token(value)


def clean_timing_value(value: str) -> str:
    return extract_roster_time_token(value)


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


def deputy_race_count(summary: dict[str, object]) -> int | None:
    for note in summary.get("production_notes") or []:
        match = re.fullmatch(r"\s*(\d+)\s+races?\s*", str(note or ""), re.IGNORECASE)
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
    return None


def resolve_race_timing_fields(
    shift: dict[str, object],
    meeting_detail: dict[str, object] | None,
    admin_overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    admin_overrides = admin_overrides or {}
    summary = shift.get("roster_summary") if isinstance(shift.get("roster_summary"), dict) else {}
    timings = timing_lookup(summary)
    deputy_count = deputy_race_count(summary)
    deputy_first = timings.get("first race", "")
    deputy_last = timings.get("last race", "")
    adjustment = clean_time_value(str(shift.get("timing_adjustment_time") or ""))
    user_last = (
        adjustment
        if adjustment and int(shift.get("timing_adjustment_last_race") or 0)
        else ""
    )
    love_count = None
    love_first = ""
    love_last = ""
    if (
        meeting_detail
        and str(shift.get("race_type_label") or "") == "Thoroughbred racing"
        and str(meeting_detail.get("lifecycle_status") or "")
        in {"partial", "complete", "historical"}
    ):
        love_count = safe_int(meeting_detail.get("race_count"))
        love_first = clean_time_value(str(meeting_detail.get("first_race_time") or ""))
        love_last = clean_time_value(str(meeting_detail.get("last_race_time") or ""))

    admin_count = safe_int((admin_overrides.get("race_count") or {}).get("normalized_value"))
    admin_first = clean_time_value(
        str((admin_overrides.get("first_race") or {}).get("normalized_value") or "")
    )
    admin_last = clean_time_value(
        str((admin_overrides.get("last_race") or {}).get("normalized_value") or "")
    )
    race_count = admin_count or deputy_count or love_count
    first_race = admin_first or deputy_first or love_first
    last_race = admin_last or user_last or deputy_last or love_last
    sources = {
        "race_count": (
            "Admin override"
            if admin_count
            else ("Deputy" if deputy_count is not None else ("Love Racing" if love_count else ""))
        ),
        "first_race_time": (
            "Admin override"
            if admin_first
            else ("Deputy" if deputy_first else ("Love Racing" if love_first else ""))
        ),
        "last_race_time": (
            "Admin override"
            if admin_last
            else (
                "User"
                if user_last
                else ("Deputy" if deputy_last else ("Love Racing" if love_last else ""))
            )
        ),
    }
    love_fields = [field for field, source in sources.items() if source == "Love Racing"]
    source_note = ""
    admin_fields = [field for field, source in sources.items() if source == "Admin override"]
    if admin_fields:
        source_note = "Admin overrides take priority for the corrected race-day values."
    elif love_fields:
        if (
            sources["race_count"] == "Deputy"
            and sources["first_race_time"] == "Love Racing"
            and sources["last_race_time"] == "Love Racing"
        ):
            source_note = "Race count from Deputy · Race times from Love Racing"
        elif set(love_fields) <= {"first_race_time", "last_race_time"}:
            source_note = "Race times filled from Love Racing"
        else:
            source_note = "Race details filled from Love Racing"
    return {
        "race_count": race_count,
        "first_race_time": first_race,
        "last_race_time": last_race,
        "sources": sources,
        "source_note": source_note,
        "meeting_id": str((meeting_detail or {}).get("meeting_id") or ""),
        "admin_overrides": admin_overrides,
    }


def enrich_shifts_with_love_racing(
    shifts: list[dict[str, object]],
    start_date: str,
    end_date: str,
) -> None:
    details = {
        (
            str(row["meeting_date"]),
            str(row["canonical_venue_key"]),
        ): dict(row)
        for row in fetch_love_racing_details_between(start_date, end_date)
    }
    active_overrides: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
    for row in list_active_admin_overrides_between(start_date, end_date):
        item = dict(row)
        identity = (str(item.get("target_date") or ""), str(item.get("target_track_key") or ""))
        active_overrides.setdefault(identity, {})[str(item.get("field_key") or "")] = item

    for shift in shifts:
        venue_key, _venue_label = canonical_override_venue(
            shift.get("track_label") or shift.get("location_label")
        )
        shift_overrides = active_overrides.get((str(shift.get("date") or ""), venue_key), {})
        is_thoroughbred = str(shift.get("race_type_label") or "") == "Thoroughbred racing"
        if not is_thoroughbred and not shift_overrides:
            continue
        detail = details.get(
            (
                str(shift.get("date") or ""),
                calendar_location_key(shift.get("track_label") or shift.get("location_label")),
            )
        ) if is_thoroughbred else None
        shift["admin_timing_overrides"] = shift_overrides
        shift["effective_race_timing"] = resolve_race_timing_fields(
            shift,
            detail,
            shift_overrides,
        )
        apply_timing_math(shift)


def accommodation_base_labels_for_shift(shift: dict[str, object]) -> list[str]:
    labels: list[str] = []
    for line in list(shift.get("description_lines") or []):
        match = re.match(r"(?i)^\s*accommodation\s*[:\-]?\s*(.+?)\s*$", str(line or ""))
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1).strip(" -:.,"))
        if not label:
            continue
        append_unique(labels, label)
        if not re.search(r"(?i)\b(hotel|motel|lodge|apartments?)\b", label):
            append_unique(labels, f"{label} Motel")
            append_unique(labels, f"{label} Hotel")
    return labels


def accommodation_default_for_shift(shift: dict[str, object]) -> dict[str, object] | None:
    base_labels = accommodation_base_labels_for_shift(shift)
    for base_label in base_labels:
        row = travel_default_for_shift(shift, base_label)
        if row:
            return row
    track_label = str(shift.get("track_label") or shift.get("location_label") or "").lower()
    if "ruakaka" in track_label and any("beachfront" in label.lower() for label in base_labels):
        return {
            "base_label": "Beachfront Motel",
            "travel_minutes": 30,
            "source": "known accommodation",
        }
    return None


def published_travel_context_for_shift(shift: dict[str, object]) -> dict[str, str]:
    date_text = str(shift.get("date") or "")
    user_id = safe_int(shift.get("owner_user_id"))
    if not date_text or user_id is None:
        return {}
    try:
        day_value = date.fromisoformat(date_text)
    except ValueError:
        return {}
    target_key = travel_default_key(shift.get("track_label") or shift.get("source_code"))
    result: dict[str, str] = {}
    snapshots: dict[int, list[dict[str, object]]] = {-1: [], 0: [], 1: []}
    for row in fetch_published_roster_days_between(
        (day_value - timedelta(days=1)).isoformat(),
        (day_value + timedelta(days=1)).isoformat(),
    ):
        snapshot = parse_roster_snapshot(row["published_snapshot"])
        if not snapshot:
            continue
        try:
            offset = (date.fromisoformat(str(row["roster_date"])) - day_value).days
        except ValueError:
            continue
        if offset in snapshots:
            snapshots[offset].append(snapshot)

    current = next(
        (item for item in snapshots[0] if travel_default_key(item.get("track_label")) == target_key),
        None,
    )
    if current:
        if str(current.get("start_origin") or "").strip():
            result["start_origin"] = str(current["start_origin"]).strip()
            result["start_evidence"] = "published roster travel selection"
        if str(current.get("finish_destination") or "").strip():
            result["finish_destination"] = str(current["finish_destination"]).strip()
            result["finish_evidence"] = "published roster travel selection"

    def hotel_for_user(items: list[dict[str, object]]) -> str:
        for snapshot in items:
            for hotel in snapshot.get("hotel_assignments", []):
                if isinstance(hotel, dict) and safe_int(hotel.get("user_id")) == user_id:
                    label = str(hotel.get("hotel_name") or "").strip()
                    if label:
                        return label
        return ""

    previous_hotel = hotel_for_user(snapshots[-1])
    current_hotel = hotel_for_user([current] if current else [])
    next_hotel = hotel_for_user(snapshots[1])
    if (previous_hotel or current_hotel) and not result.get("start_origin"):
        result["start_origin"] = previous_hotel or current_hotel
        result["start_evidence"] = "published hotel assignment"
    if next_hotel and not result.get("finish_destination"):
        result["finish_destination"] = next_hotel
        result["finish_evidence"] = "next published hotel assignment"
    return result


def adjacent_overnight_origin_for_shift(shift: dict[str, object]) -> str:
    user_id = safe_int(shift.get("owner_user_id"))
    try:
        day_value = date.fromisoformat(str(shift.get("date") or ""))
    except ValueError:
        return ""
    previous_rows = fetch_shifts_between(
        (day_value - timedelta(days=1)).isoformat(),
        (day_value - timedelta(days=1)).isoformat(),
        owner_user_id=user_id,
    )
    for row in previous_rows:
        values = dict(row)
        title_and_note = f"{values.get('title', '')} {values.get('description', '')}".lower()
        if "travel then overnighter" not in title_and_note:
            continue
        labels = accommodation_base_labels_for_shift(
            {"description_lines": description_lines(str(values.get("description") or ""))}
        )
        if labels:
            return labels[0]
    return ""


def resolved_travel_context(shift: dict[str, object], timings: dict[str, str]) -> dict[str, str]:
    track = str(shift.get("track_label") or shift.get("location_label") or "").strip()
    published = published_travel_context_for_shift(shift)
    accommodation_labels = accommodation_base_labels_for_shift(shift)
    explicit_origin = str(shift.get("travel_start_origin") or "").strip()
    explicit_finish = str(shift.get("travel_finish_destination") or "").strip()
    if explicit_origin:
        origin, origin_evidence = explicit_origin, "day-specific travel selection"
    elif published.get("start_origin"):
        origin, origin_evidence = published["start_origin"], published.get("start_evidence", "published roster")
    elif accommodation_labels:
        origin, origin_evidence = accommodation_labels[0], "Deputy accommodation note"
    else:
        adjacent_origin = adjacent_overnight_origin_for_shift(shift)
        if adjacent_origin:
            origin, origin_evidence = adjacent_origin, "adjacent Travel then Overnighter day"
        else:
            origin = "Office / Clow Place"
            origin_evidence = "roster base timing" if timings.get("office") or timings.get("clow place") else "saved route default"
    if explicit_finish:
        finish, finish_evidence = explicit_finish, "day-specific travel selection"
    elif published.get("finish_destination"):
        finish, finish_evidence = published["finish_destination"], published.get("finish_evidence", "published roster")
    else:
        finish, finish_evidence = "Office / Clow Place", "trip ends after this race day"
    return {
        "track": track,
        "start_origin": canonical_travel_base_label(origin),
        "finish_destination": canonical_travel_base_label(finish),
        "start_evidence": origin_evidence,
        "finish_evidence": finish_evidence,
    }


def public_holiday_context(day_value: date) -> dict[str, object]:
    return holiday_for_date(day_value, get_settings().holiday_region)

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
        if not time_value:
            return
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


def safe_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
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
            role = " → ".join(real_parts or role_parts) or "Shift"
        if not labels or labels[-1] != role:
            labels.append(role)
    if not labels:
        labels = [segment.get("role") or "Shift" for segment in segments]
    return " → ".join(labels)


def role_is_vehicleish(role_label: str | None) -> bool:
    raw_value = (role_label or "").strip()
    if "->" in raw_value:
        role_parts = [part.strip() for part in raw_value.split("->") if part.strip()]
        return bool(role_parts) and all(role_is_vehicleish(part) for part in role_parts)
    normalised = re.sub(r"\s+", "", raw_value.upper())
    if not normalised:
        return False
    return bool(
        re.fullmatch(r"\d{3}", normalised)
        or re.fullmatch(r"RAV\d+", normalised)
        or normalised in VEHICLE_ROLE_LABELS
        or normalised in {"VEHICLE", "VEHICLES"}
    )


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


def compact_change_time_values(old_value: object, new_value: object) -> tuple[str, str] | None:
    old_at = parse_iso_datetime(str(old_value or ""))
    new_at = parse_iso_datetime(str(new_value or ""))
    if not old_at or not new_at or old_at.date() != new_at.date():
        return None
    return old_at.strftime("%H:%M"), new_at.strftime("%H:%M")


def decorate_change(row: object) -> dict[str, object]:
    change = dict(row)
    field_name = str(change.get("field_name") or "")
    change["field_label"] = CHANGE_FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())
    change["old_display"] = format_change_value(field_name, str(change.get("old_value") or ""))
    change["new_display"] = format_change_value(field_name, str(change.get("new_value") or ""))
    if field_name in {"start_at", "end_at"}:
        change["field_label"] = "Start time" if field_name == "start_at" else "Finish time"
        compact_values = compact_change_time_values(change.get("old_value"), change.get("new_value"))
        if compact_values:
            change["old_display"], change["new_display"] = compact_values
    return change


def merge_description_change_lines(shift: dict[str, object]) -> None:
    current_lines = list(shift.get("description_lines") or [])
    seen = {line.strip().lower() for line in current_lines if str(line or "").strip()}
    added = False
    for change in list(shift.get("changes") or []):
        if str(change.get("field_name") or "") != "description":
            continue
        for source_key in ("old_value", "new_value"):
            for line in description_lines(str(change.get(source_key) or "")):
                key = line.strip().lower()
                if not key or key in seen:
                    continue
                current_lines.append(line)
                seen.add(key)
                added = True
    if not added:
        return
    shift["description_lines"] = current_lines
    shift["roster_summary"] = parse_roster_summary(current_lines)
    shift["race_day_summary"] = build_race_day_summary(shift, {})
    shift["race_day_calculation"] = build_race_day_calculation(shift)

def build_shift_change_summary(changes: list[dict[str, object]]) -> str:
    parts: list[str] = []
    time_changes: dict[str, dict[str, object]] = {}
    for change in changes:
        field_name = str(change.get("field_name") or "")
        if field_name in {"start_at", "end_at"}:
            time_changes.setdefault(field_name, change)
    compact_times = []
    for field_name, label in (("start_at", "Start"), ("end_at", "Finish")):
        change = time_changes.get(field_name)
        if change:
            compact_times.append(
                f"{label} {change.get('old_display') or 'blank'} → {change.get('new_display') or 'blank'}"
            )
    if compact_times:
        parts.append(" · ".join(compact_times))

    remaining = [
        change for change in changes
        if str(change.get("field_name") or "") not in {"start_at", "end_at"}
    ]
    visible_limit = max(0, 4 - len(time_changes))
    for change in remaining[:visible_limit]:
        label = str(change.get("field_label") or "Change")
        field_name = str(change.get("field_name") or "")
        old_value = str(change.get("old_display") or "blank")
        new_value = str(change.get("new_display") or "blank")
        if field_name == "description":
            parts.append("Roster notes changed")
        elif len(old_value) > 48 or len(new_value) > 48:
            parts.append(f"{label} changed")
        else:
            parts.append(f"{label}: {old_value} → {new_value}")
    shown_count = len(time_changes) + min(len(remaining), visible_limit)
    if len(changes) > shown_count:
        parts.append(f"+{len(changes) - shown_count} more")
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
    admin_overrides = (
        shift.get("admin_timing_overrides")
        if isinstance(shift.get("admin_timing_overrides"), dict)
        else {}
    )

    def admin_value(field_key: str) -> str:
        item = admin_overrides.get(field_key)
        return str(item.get("normalized_value") or "") if isinstance(item, dict) else ""

    base_label = "Office" if timings.get("office") else "Clow Place"
    base_clock = timings.get("office") or timings.get("clow place")
    admin_start_clock = clean_time_value(admin_value("start"))
    admin_finish_clock = clean_time_value(admin_value("finish"))
    admin_on_track_clock = clean_time_value(admin_value("on_track"))
    on_track_clock = admin_on_track_clock or timings.get("on track")
    if admin_start_clock:
        base_clock = admin_start_clock
    adjustment_time = clean_time_value(str(shift.get("timing_adjustment_time") or ""))
    use_last_race_adjustment = bool(int(shift.get("timing_adjustment_last_race") or 0)) and adjustment_time
    use_finished_adjustment = bool(int(shift.get("timing_adjustment_day_finished") or 0)) and adjustment_time
    effective_race_timing = (
        shift.get("effective_race_timing")
        if isinstance(shift.get("effective_race_timing"), dict)
        else {}
    )
    last_race_clock = (
        clean_time_value(admin_value("last_race"))
        or (
            adjustment_time
            if use_last_race_adjustment
            else clean_time_value(str(effective_race_timing.get("last_race_time") or ""))
            or timings.get("last race")
        )
    )

    result: dict[str, object] = {
        "available": False,
        "lines": [],
        "formula": "",
    }
    context = resolved_travel_context(shift, timings)
    start_origin = context["start_origin"]
    finish_destination = context["finish_destination"]
    track_label = context["track"]
    outbound_route = get_travel_route(start_origin, track_label)
    outbound_default = dict(outbound_route) if outbound_route else None
    if outbound_default is None:
        if accommodation_base_labels_for_shift(shift):
            outbound_default = accommodation_default_for_shift(shift)
        else:
            outbound_default = travel_default_for_shift(shift, start_origin)
    default_travel_minutes = int(outbound_default["travel_minutes"]) if outbound_default else 0
    default_source = str(outbound_default.get("source") or "saved route") if outbound_default else ""
    admin_outbound_minutes = safe_int(admin_value("outbound_travel"))
    admin_return_minutes = safe_int(admin_value("return_travel"))
    admin_packup_minutes = safe_int(admin_value("pack_up_duration"))
    if admin_outbound_minutes:
        default_travel_minutes = admin_outbound_minutes
        default_source = "Admin override"

    roster_start_at = parse_iso_datetime(str(shift.get("start_at") or ""))
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

    if admin_finish_clock or use_finished_adjustment:
        finished_clock = admin_finish_clock or adjustment_time
        finished_at = clock_datetime_for_shift(shift, finished_clock, start_at)
        if finished_at is None:
            return result
        rounded_end = finished_at if admin_finish_clock else ceil_datetime_to_quarter(finished_at)
        hours = max(0.0, round((rounded_end - start_at).total_seconds() / 3600, 2))
        finish_source = "an Admin override" if admin_finish_clock else "the changed finish time"
        result.update(
            {
                "available": True,
                "complete": True,
                "source": "manual_finished",
                "start_label": start_at.strftime("%H:%M"),
                "end_label": rounded_end.strftime("%H:%M"),
                "hours": hours,
                "hours_label": format_hours(hours),
                "lines": [
                    {"label": f"Start · {start_origin}", "value": start_at.strftime("%H:%M")},
                    {"label": f"Finish · {finish_destination}", "value": finished_at.strftime("%H:%M")},
                    {"label": "Rounded end", "value": rounded_end.strftime("%H:%M")},
                    {"label": "Calculated total", "value": format_hours(hours)},
                ],
                "formula": (
                    f"Finish {finished_at.strftime('%H:%M')} comes from {finish_source}. "
                    f"{start_origin} {start_at.strftime('%H:%M')} to {finish_destination} "
                    f"{rounded_end.strftime('%H:%M')} is {format_hours(hours)}."
                ),
            }
        )
        return result

    if not on_track_clock and start_at is not None and default_travel_minutes:
        on_track_at = start_at + timedelta(minutes=default_travel_minutes)
        on_track_clock = on_track_at.strftime("%H:%M")
        inferred_on_track = True

    admin_outbound_conflict = False
    if admin_outbound_minutes and on_track_clock:
        on_track_for_override = clock_datetime_for_shift(shift, on_track_clock, start_at)
        if on_track_for_override is not None:
            if admin_start_clock and admin_on_track_clock:
                actual_minutes = max(
                    0,
                    int(round((on_track_for_override - start_at).total_seconds() / 60)),
                )
                admin_outbound_conflict = actual_minutes != admin_outbound_minutes
            elif admin_start_clock:
                on_track_for_override = start_at + timedelta(minutes=admin_outbound_minutes)
                on_track_clock = on_track_for_override.strftime("%H:%M")
            else:
                start_at = on_track_for_override - timedelta(minutes=admin_outbound_minutes)
                base_clock = start_at.strftime("%H:%M")
                inferred_start = True

    if not on_track_clock or not last_race_clock:
        return result

    on_track_at = clock_datetime_for_shift(shift, on_track_clock, start_at)
    last_race_at = clock_datetime_for_shift(shift, last_race_clock, on_track_at)
    if on_track_at is None or last_race_at is None:
        return result

    outbound_minutes = (
        (None if admin_outbound_conflict else admin_outbound_minutes)
        or max(0, int(round((on_track_at - start_at).total_seconds() / 60)))
    )
    race_clear_at = ceil_datetime_to_quarter(last_race_at + timedelta(minutes=RACE_RUN_MINUTES))
    packup_minutes = admin_packup_minutes or PACKUP_MINUTES
    packup_done_at = race_clear_at + timedelta(minutes=packup_minutes)
    return_route = get_travel_route(track_label, finish_destination)
    return_minutes = (
        admin_return_minutes
        if admin_return_minutes
        else (int(return_route["travel_minutes"]) if return_route else None)
    )
    calculated_end_at = packup_done_at + timedelta(minutes=return_minutes) if return_minutes is not None else None
    hours = max(0.0, round((calculated_end_at - start_at).total_seconds() / 3600, 2)) if calculated_end_at else None
    roster_start_conflict = bool(
        inferred_start
        and roster_start_at
        and on_track_at
        and roster_start_at >= on_track_at
    )
    calculation_lines = [
        {"label": f"Start · {start_origin}{' · inferred' if inferred_start else ''}", "value": start_at.strftime("%H:%M")},
        {"label": f"On track{' inferred' if inferred_on_track else ''}", "value": on_track_at.strftime("%H:%M")},
        {"label": "Outbound travel", "value": format_minutes_duration(outbound_minutes)},
    ]
    if roster_start_conflict and roster_start_at:
        calculation_lines.append({"label": "Deputy roster start", "value": roster_start_at.strftime("%H:%M")})
    calculation_lines.extend(
        [
            {"label": "Last race", "value": last_race_at.strftime("%H:%M")},
            {"label": "Race cleared", "value": race_clear_at.strftime("%H:%M")},
            {"label": "Pack-up done", "value": packup_done_at.strftime("%H:%M")},
        ]
    )
    if calculated_end_at is not None and return_minutes is not None:
        calculation_lines.extend([
            {"label": "Return travel", "value": format_minutes_duration(return_minutes)},
            {"label": f"Finish · {finish_destination}", "value": calculated_end_at.strftime("%H:%M")},
            {"label": "Calculated total", "value": format_hours(hours)},
        ])
    else:
        calculation_lines.extend([
            {"label": "Return travel not configured", "value": "Incomplete"},
            {"label": f"Finish · {finish_destination}", "value": "Not calculated"},
        ])
    complete = calculated_end_at is not None
    result.update(
        {
            "available": True,
            "complete": complete,
            "source": "race_day",
            "used_default_travel": bool(inferred_start or inferred_on_track),
            "default_travel_source": default_source,
            "start_label": start_at.strftime("%H:%M"),
            "on_track_label": on_track_at.strftime("%H:%M"),
            "last_race_label": last_race_at.strftime("%H:%M"),
            "race_clear_label": race_clear_at.strftime("%H:%M"),
            "packup_done_label": packup_done_at.strftime("%H:%M"),
            "end_label": calculated_end_at.strftime("%H:%M") if calculated_end_at else "",
            "travel_label": format_minutes_duration(outbound_minutes),
            "outbound_travel_label": format_minutes_duration(outbound_minutes),
            "return_travel_label": format_minutes_duration(return_minutes) if return_minutes is not None else "Not configured",
            "start_origin": start_origin,
            "finish_destination": finish_destination,
            "start_origin_evidence": context["start_evidence"],
            "finish_destination_evidence": context["finish_evidence"],
            "warning": "" if complete else "Return travel not configured",
            "hours": hours,
            "hours_label": format_hours(hours) if hours is not None else "Incomplete",
            "lines": calculation_lines,
            "roster_start_conflict": roster_start_conflict,
            "formula": (
                f"{start_origin} {start_at.strftime('%H:%M')} to {track_label} at {on_track_at.strftime('%H:%M')} "
                f"uses {format_minutes_duration(outbound_minutes)} outbound travel. Last race "
                f"{last_race_at.strftime('%H:%M')} + {RACE_RUN_MINUTES}m rounds to "
                f"{race_clear_at.strftime('%H:%M')}; {format_minutes_duration(packup_minutes)} pack-up "
                f"to {packup_done_at.strftime('%H:%M')}; "
                + (
                    f"{format_minutes_duration(return_minutes)} return travel to {finish_destination} gives {calculated_end_at.strftime('%H:%M')}."
                    if calculated_end_at is not None and return_minutes is not None
                    else f"return travel from {track_label} to {finish_destination} is not configured, so the finish and total are incomplete."
                )
            ),
        }
    )
    if effective_race_timing.get("sources", {}).get("last_race_time") == "Admin override":
        result["formula"] = (
            f"Last race {last_race_at.strftime('%H:%M')} comes from an Admin override. "
            f"{result['formula']}"
        )
    elif use_last_race_adjustment:
        result["formula"] = f"Using changed last race time. {result['formula']}"
    elif (
        effective_race_timing.get("sources", {}).get("last_race_time") == "Love Racing"
    ):
        result["formula"] = f"Using the scheduled last race from Love Racing. {result['formula']}"
    applied_admin_parts = []
    for field_key in (
        "start",
        "on_track",
        "pack_up_duration",
        "outbound_travel",
        "return_travel",
    ):
        value = admin_value(field_key)
        if not value:
            continue
        display_value = (
            format_minutes_duration(int(value))
            if field_key in DURATION_FIELDS and value.isdigit()
            else value
        )
        applied_admin_parts.append(
            f"{ADMIN_OVERRIDE_FIELD_LABELS.get(field_key, field_key)} {display_value}"
        )
    if applied_admin_parts:
        result["formula"] = f"Admin override applied: {', '.join(applied_admin_parts)}. {result['formula']}"
    if admin_outbound_conflict:
        result["formula"] = (
            "Admin start, on-track, and outbound travel values conflict; the timeline keeps "
            f"the two Admin clock times and shows their actual interval. {result['formula']}"
        )
    if inferred_start or inferred_on_track:
        result["formula"] = f"Using {default_source or 'saved'} default travel time. {result['formula']}"
    if roster_start_conflict and roster_start_at:
        result["formula"] = (
            f"Deputy starts the shift at {roster_start_at.strftime('%H:%M')}, after the "
            f"{on_track_at.strftime('%H:%M')} on-track note. The breakdown follows the roster note "
            f"and accommodation travel time; check this discrepancy. {result['formula']}"
        )
    return result


def build_race_day_summary(shift: dict[str, object], _race_day: dict[str, object]) -> dict[str, object]:
    rows: list[dict[str, str]] = []
    wanted_patterns = (
        re.compile(
            r"^(trucks?|office|clow\s+(?:place|pl)|on\s+track|first\s+cross|fx|records?|on\s+air|live)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(records?|on\s+air|live|first race|last race|\d+\s+races?)\b", re.IGNORECASE),
    )
    simple_timing_re = re.compile(
        r"^(trucks?|office|clow\s+(?:place|pl)|on\s+track|first\s+cross|fx|records?|on\s+air|live)\s+(.+)$",
        re.IGNORECASE,
    )
    paired_timing_re = re.compile(
        rf"\b(records?|on\s+air|live|first\s+cross|first\s+race|last\s+race|fx)\s+"
        rf"({ROSTER_TIME_TOKEN_PATTERN})",
        re.IGNORECASE,
    )

    def add_row(label: str, value: str, source: str = "") -> None:
        label_text = label.strip()
        value_text = value.strip()
        row = {"label": label_text, "value": value_text}
        if source:
            row["source"] = source
        if label_text and row not in rows:
            rows.append(row)

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
            "record": "Records",
            "records": "Records",
            "on air": "On air",
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
        if re.match(
            r"^(trucks?|office|clow\s+(?:place|pl)|on\s+track)\b",
            line_text,
            re.IGNORECASE,
        ):
            line_text = re.split(r"\s+[-–]\s+", line_text, maxsplit=1)[0].strip()

        race_times = RACE_COUNT_WITH_TIMES_RE.search(line_text)
        if race_times:
            first_race = display_time(race_times.group(2))
            last_race = display_time(race_times.group(3))
            if first_race and last_race:
                add_row(f"{race_times.group(1)} races", f"{first_race} | {last_race}")
            continue

        race_count = RACE_COUNT_RE.search(line_text)
        if race_count:
            add_row(f"{race_count.group(1)} races", "")
            continue

        paired_timings = list(paired_timing_re.finditer(line_text))
        if paired_timings:
            for match in paired_timings:
                value = display_time(match.group(2))
                if value:
                    add_row(display_label(match.group(1)), value)
            continue

        simple_timing = simple_timing_re.match(line_text)
        if simple_timing:
            value = display_time(simple_timing.group(2))
            if value:
                add_row(display_label(simple_timing.group(1)), value)

    effective = (
        shift.get("effective_race_timing")
        if isinstance(shift.get("effective_race_timing"), dict)
        else {}
    )
    if effective:
        rows = [
            row
            for row in rows
            if not re.fullmatch(r"\d+\s+races?", str(row.get("label") or ""), re.IGNORECASE)
            and str(row.get("label") or "").lower() not in {"first race", "last race"}
        ]
        count = safe_int(effective.get("race_count"))
        first_race = clean_time_value(str(effective.get("first_race_time") or ""))
        last_race = clean_time_value(str(effective.get("last_race_time") or ""))
        sources = effective.get("sources") if isinstance(effective.get("sources"), dict) else {}
        has_admin_race_value = any(
            sources.get(key) == "Admin override"
            for key in ("race_count", "first_race_time", "last_race_time")
        )
        if count and first_race and last_race and not has_admin_race_value:
            add_row(f"{count} races", f"{first_race} | {last_race}")
        else:
            if count:
                add_row(
                    "Races",
                    str(count),
                    "Admin override" if sources.get("race_count") == "Admin override" else "",
                )
            if first_race:
                add_row(
                    "First race",
                    first_race,
                    "Admin override" if sources.get("first_race_time") == "Admin override" else "",
                )
            if last_race:
                add_row(
                    "Last race",
                    last_race,
                    "Admin override" if sources.get("last_race_time") == "Admin override" else "",
                )

    admin_overrides = (
        shift.get("admin_timing_overrides")
        if isinstance(shift.get("admin_timing_overrides"), dict)
        else {}
    )
    summary_fields = {
        "on_track": "On track",
        "records": "Records",
        "on_air": "On air",
        "first_cross": "First cross",
        "start": "Start",
        "finish": "Finish",
    }
    for field_key, label in summary_fields.items():
        override = admin_overrides.get(field_key)
        if not isinstance(override, dict):
            continue
        value = clean_time_value(str(override.get("normalized_value") or ""))
        if not value:
            continue
        rows = [row for row in rows if str(row.get("label") or "").lower() != label.lower()]
        add_row(label, value, "Admin override")

    return {
        "rows": rows,
        "has_items": bool(rows),
        "source_note": str(effective.get("source_note") or ""),
    }


def apply_timing_math(shift: dict[str, object]) -> None:
    break_minutes = int(shift.get("break_minutes") or 0)
    roster_start_label = str(shift.get("start_label") or "")
    roster_end_label = str(shift.get("end_label") or "")
    roster_time_range = str(shift.get("time_range") or "")
    roster_hours = shift.get("raw_hours")
    roster_hours_label = str(shift.get("raw_label") or format_hours(roster_hours))
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
    if race_day.get("available") and race_day.get("complete", True):
        shift["calculated_hours"] = race_day.get("hours")
        shift["calculated_label"] = race_day.get("hours_label")
        display_window = {
            "source": "calculated",
            "start_label": str(race_day.get("start_label") or ""),
            "end_label": str(race_day.get("end_label") or ""),
            "hours": race_day.get("hours"),
            "hours_label": str(race_day.get("hours_label") or ""),
        }
    else:
        shift["calculated_hours"] = None
        shift["calculated_label"] = ""
        display_window = {
            "source": "roster",
            "start_label": roster_start_label,
            "end_label": roster_end_label,
            "hours": roster_hours,
            "hours_label": roster_hours_label,
        }
    display_time_range = (
        f"{display_window['start_label']}–{display_window['end_label']}"
        if display_window["start_label"] and display_window["end_label"]
        else roster_time_range
    )
    if (
        display_window["source"] == "roster"
        and roster_time_range.endswith(" +1d")
        and not display_time_range.endswith(" +1d")
    ):
        display_time_range += " +1d"
    display_window["time_range"] = display_time_range
    shift["display_window"] = display_window
    shift["display_start_label"] = display_window["start_label"]
    shift["display_end_label"] = display_window["end_label"]
    shift["time_range"] = display_time_range
    shift["display_hours"] = display_window["hours"]
    shift["display_hours_label"] = display_window["hours_label"]
    shift["timing_math"] = {
        "segments": segments,
        "start_label": roster_start_label,
        "end_label": roster_end_label,
        "raw_label": roster_hours_label,
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


def roster_builder_positions(area_names: list[str]) -> list[str]:
    labels: dict[str, str] = {}
    for key, _order in sorted(SCHEDULE_POSITION_ORDER.items(), key=lambda item: item[1]):
        alias = SCHEDULE_POSITION_ALIASES.get(key)
        if alias and key != "northern":
            labels.setdefault(key, alias[1])
    for raw_name in area_names:
        label = display_schedule_area(raw_name)
        key = schedule_label_key(label)
        if (
            not key
            or key in CONTEXT_ONLY_ROLE_KEYS
            or key in {"hcambridge", "travelthenovernighter"}
            or key.startswith("fcr")
            or schedule_area_is_hidden(label)
            or schedule_area_is_vehicle(label)
        ):
            continue
        labels.setdefault(key, label)
    return sorted(labels.values(), key=lambda label: (SCHEDULE_POSITION_ORDER.get(schedule_label_key(label), 999999), label.lower()))


def roster_builder_vehicles(area_names: list[str]) -> list[str]:
    vehicles = set(VEHICLE_ROLE_LABELS)
    for raw_name in area_names:
        label = display_schedule_area(raw_name)
        if schedule_area_is_vehicle(label) and schedule_label_key(label) not in {"vehicle", "vehicles"}:
            vehicles.add(label)
    return sorted(vehicles, key=lambda value: (not value.isdigit(), value.lower()))


def default_roster_race_type(track_label: object) -> str:
    key = schedule_label_key(str(track_label or ""))
    if "greyhound" in key:
        return "greyhound"
    if "harness" in key:
        return "harness"
    return "thoroughbred"


def parse_hotel_assignments(value: object) -> list[dict[str, object]]:
    if isinstance(value, list):
        rows = value
    else:
        try:
            rows = json.loads(str(value or "[]"))
        except (TypeError, ValueError):
            rows = []
    return [dict(item) for item in rows if isinstance(item, dict) and str(item.get("hotel_name") or "").strip()]


def roster_day_snapshot(roster_day: dict[str, object], assignments: list[dict[str, object]]) -> dict[str, object]:
    return {
        "id": int(roster_day.get("id") or 0),
        "roster_date": str(roster_day.get("roster_date") or ""),
        "track_key": str(roster_day.get("canonical_location_key") or roster_day.get("track_key") or ""),
        "track_label": str(roster_day.get("track_label") or ""),
        "title": str(roster_day.get("title") or ""),
        "custom_location": str(roster_day.get("custom_location") or ""),
        "race_type": str(roster_day.get("race_type") or ""),
        "day_type": str(roster_day.get("day_type") or "race_day"),
        "start_origin": str(roster_day.get("start_origin") or ""),
        "finish_destination": str(roster_day.get("finish_destination") or ""),
        "office_start": str(roster_day.get("office_start") or ""),
        "end_time": str(roster_day.get("end_time") or ""),
        "break_minutes": int(roster_day.get("break_minutes") or 0),
        "on_track_time": str(roster_day.get("on_track_time") or ""),
        "first_race_time": str(roster_day.get("first_race_time") or ""),
        "last_race_time": str(roster_day.get("last_race_time") or ""),
        "race_count": roster_day.get("race_count"),
        "notes": str(roster_day.get("notes") or ""),
        "source_reference": str(roster_day.get("source_reference") or ""),
        "provenance": str(roster_day.get("provenance") or "manual"),
        "linked_deputy_event_id": str(roster_day.get("linked_deputy_event_id") or ""),
        "duplicate_resolution": str(roster_day.get("duplicate_resolution") or "keep_separate"),
        "hotel_assignments": parse_hotel_assignments(roster_day.get("hotel_assignments")),
        "assignments": [
            {
                "person_id": int(item["person_id"]) if item.get("person_id") not in (None, "") else None,
                "user_id": int(item["user_id"]) if item.get("user_id") not in (None, "") else None,
                "assignee_label": str(item.get("display_name") or item.get("person_display_name") or item.get("assignee_label") or "TBC"),
                "role_key": str(item.get("role_key") or ""),
                "role_label": str(item.get("role_label") or item.get("position_label") or ""),
                "position_label": str(item.get("role_label") or item.get("position_label") or ""),
                "assignment_state": str(item.get("assignment_state") or "assigned"),
                "transport_mode": str(item.get("transport_mode") or "unassigned"),
                "vehicle_label": str(item.get("vehicle_label") or ""),
                "custom_transport_text": str(item.get("custom_transport_text") or ""),
                "transport_label": transport_display(
                    item.get("transport_mode"), item.get("vehicle_label"), item.get("custom_transport_text")
                ),
                "assignment_note": str(item.get("assignment_note") or ""),
                "sort_order": int(item.get("sort_order")) if item.get("sort_order") is not None else 999999,
            }
            for item in assignments
        ],
    }


def parse_roster_snapshot(value: object) -> dict[str, object] | None:
    try:
        snapshot = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return None
    return snapshot if isinstance(snapshot, dict) else None


def roster_day_change_review(current: dict[str, object], published: dict[str, object] | None) -> tuple[list[dict[str, str]], set[str], set[str]]:
    if not published:
        return [], set(), set()
    labels = {
        "roster_date": "Date", "track_label": "Location", "title": "Title", "race_type": "Race type", "day_type": "Day type",
        "start_origin": "Start origin", "finish_destination": "Finish destination",
        "office_start": "Start", "end_time": "Finish", "break_minutes": "Break", "on_track_time": "On track",
        "first_race_time": "First race", "last_race_time": "Last race",
        "race_count": "Race count", "notes": "Important notes", "source_reference": "Source/reference", "hotel_assignments": "Hotels",
    }
    changes: list[dict[str, str]] = []
    changed_fields: set[str] = set()
    changed_positions: set[str] = set()
    for key, label in labels.items():
        if published.get(key) == current.get(key):
            continue
        changed_fields.add(key)
        changes.append({
            "label": label,
            "old": str(published.get(key) if published.get(key) not in (None, "") else "Not set"),
            "new": str(current.get(key) if current.get(key) not in (None, "") else "Not set"),
        })
    def assignment_identity(item: dict[str, object], index: int) -> str:
        person = item.get("person_id") or item.get("user_id") or normalise_role_key(item.get("assignee_label"))
        role = item.get("role_key") or normalise_role_key(item.get("role_label") or item.get("position_label"))
        return f"{item.get('assignment_state', 'assigned')}:{person}:{role}:{index if not person and not role else ''}"

    old_rows = {assignment_identity(item, index): item for index, item in enumerate(published.get("assignments", [])) if isinstance(item, dict)}
    new_rows = {assignment_identity(item, index): item for index, item in enumerate(current.get("assignments", [])) if isinstance(item, dict)}
    for key in sorted(set(old_rows) | set(new_rows)):
        old_item, new_item = old_rows.get(key, {}), new_rows.get(key, {})
        old_text = " · ".join(filter(None, [str(old_item.get("assignee_label") or ""), str(old_item.get("role_label") or old_item.get("position_label") or "Attending"), str(old_item.get("transport_label") or old_item.get("vehicle_label") or "")])) or "Not present"
        new_text = " · ".join(filter(None, [str(new_item.get("assignee_label") or ""), str(new_item.get("role_label") or new_item.get("position_label") or "Attending"), str(new_item.get("transport_label") or new_item.get("vehicle_label") or "")])) or "Removed"
        if old_text == new_text:
            continue
        position = str(new_item.get("role_label") or old_item.get("role_label") or "Crew")
        changed_positions.add(key)
        changes.append({"label": position or "Attending", "old": old_text, "new": new_text})
    return changes, changed_fields, changed_positions


def manual_workday_window(item: dict[str, object]) -> tuple[float, str]:
    start = str(item.get("office_start") or "")
    finish = str(item.get("end_time") or "")
    if not finish and str(item.get("day_type") or "race_day") == "race_day":
        on_track = str(item.get("on_track_time") or "")
        last_race = str(item.get("last_race_time") or "")
        try:
            base_day = date(2000, 1, 1)
            start_at = datetime.combine(base_day, datetime.strptime(start, "%H:%M").time())
            on_track_at = datetime.combine(base_day, datetime.strptime(on_track, "%H:%M").time())
            if on_track_at < start_at:
                on_track_at += timedelta(days=1)
            last_race_at = datetime.combine(base_day, datetime.strptime(last_race, "%H:%M").time())
            while last_race_at < on_track_at:
                last_race_at += timedelta(days=1)
            travel_minutes = int((on_track_at - start_at).total_seconds() // 60)
            finish_at = (
                ceil_datetime_to_quarter(last_race_at + timedelta(minutes=RACE_RUN_MINUTES))
                + timedelta(minutes=PACKUP_MINUTES + travel_minutes)
            )
            finish = finish_at.strftime("%H:%M")
        except ValueError:
            finish = ""
    try:
        start_minutes = int(start[:2]) * 60 + int(start[3:5])
        finish_minutes = int(finish[:2]) * 60 + int(finish[3:5])
    except (TypeError, ValueError):
        return 0.0, ""
    if finish_minutes < start_minutes:
        finish_minutes += 24 * 60
    hours = (finish_minutes - start_minutes) / 60
    return max(0.0, round(hours - (int(item.get("break_minutes") or 0) / 60), 2)), finish


def manual_workday_hours(item: dict[str, object]) -> float:
    return manual_workday_window(item)[0]


def published_rosters_by_date(start_date: str, end_date: str, user_id: int | None) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    visible_ids = visible_workday_ids_for_user(start_date, end_date, user_id) if user_id is not None else None
    for row in fetch_published_roster_days_between(start_date, end_date):
        if visible_ids is not None and int(row["id"]) not in visible_ids:
            continue
        snapshot = parse_roster_snapshot(row["published_snapshot"])
        if not snapshot:
            continue
        all_assignments = []
        for assignment in resolve_workday_snapshot_assignments(snapshot.get("assignments", [])):
            assignment["role_label"] = str(
                assignment.get("role_label") or assignment.get("position_label") or ""
            ).strip()
            assignment["assignment_state"] = str(
                assignment.get("assignment_state")
                or (
                    "open"
                    if str(assignment.get("assignee_label") or "").strip().casefold() == "tbc"
                    else "assigned"
                )
            )
            if not assignment.get("transport_mode"):
                assignment["transport_mode"] = (
                    "vehicle" if str(assignment.get("vehicle_label") or "").strip() else "unassigned"
                )
            assignment["transport_label"] = str(
                assignment.get("transport_label")
                or transport_display(
                    assignment.get("transport_mode"),
                    assignment.get("vehicle_label"),
                    assignment.get("custom_transport_text"),
                )
            ).strip()
            all_assignments.append(assignment)
        all_hotels = [item for item in snapshot.get("hotel_assignments", []) if isinstance(item, dict)]
        assignments = all_assignments if user_id is None else [item for item in all_assignments if item.get("user_id") == user_id]
        hotels = all_hotels if user_id is None else [item for item in all_hotels if item.get("user_id") == user_id]
        if not assignments and not hotels:
            continue
        item = dict(snapshot)
        item.update(
            id=int(row["id"]),
            version_number=int(row["version_number"] or 1),
            published_at=str(row["published_at"] or ""),
            assignments=assignments,
            all_assignments=all_assignments,
            hotel_assignments=hotels,
        )
        role_labels = [str(value.get("role_label") or value.get("position_label") or "").strip() for value in assignments if value.get("assignment_state") != "open"]
        item["position_label"] = ", ".join(dict.fromkeys(label or "Attending" for label in role_labels)) or WORKDAY_TYPE_LABELS.get(str(item.get("day_type") or ""), "Work day")
        item["vehicle_label"] = ", ".join(
            dict.fromkeys(
                str(value.get("transport_label") or "").strip()
                for value in assignments
                if str(value.get("transport_mode") or "unassigned") not in {"unassigned", "not_required"}
            )
        )
        item["hotel_label"] = ", ".join(dict.fromkeys(str(value.get("hotel_name") or "").strip() for value in hotels))
        item["day_type_label"] = WORKDAY_TYPE_LABELS.get(str(item.get("day_type") or "race_day"), "Work day")
        item["race_type_label"] = ROSTER_RACE_TYPE_LABELS.get(str(item.get("race_type") or ""), str(item.get("race_type") or "")) if item.get("day_type") == "race_day" else item["day_type_label"]
        item["display_title"] = str(item.get("title") or "").strip() or (str(item.get("track_label") or "Race day") if item.get("day_type") == "race_day" else item["day_type_label"])
        item["location_label"] = str(item.get("custom_location") or item.get("track_label") or "").strip()
        item["hours"], item["display_end_time"] = manual_workday_window(item)
        item["hours_label"] = format_hours(item["hours"])
        item["time_range"] = f"{item.get('office_start') or 'Time TBC'}-{item.get('display_end_time') or 'TBC'}"
        result.setdefault(str(item.get("roster_date") or row["roster_date"]), []).append(item)
    return result


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


def person_focused_schedule_changes(items: list[dict[str, object]]) -> None:
    """Turn Deputy row movements into the person change for each position."""
    previous_people: dict[tuple[object, str], str] = {}
    for item in items:
        if item.get("is_vehicle_area"):
            continue
        current_position = str(item.get("area_display") or "Position")
        location_id = item.get("schedule_location_id")
        current_name = str(item.get("employee_name") or "").strip()
        for part in str(item.get("change_summary") or "").split(";"):
            clean_part = part.strip()
            person_match = re.match(r"^Person:\s*(.*?)\s*->\s*(.*?)$", clean_part)
            if person_match:
                old_name = person_match.group(1).strip()
                if old_name and old_name.lower() != "blank":
                    previous_people[(location_id, schedule_label_key(current_position))] = old_name
                continue
            position_match = re.match(r"^Position:\s*(.*?)\s*->\s*(.*?)$", clean_part)
            if position_match and current_name:
                old_position = display_schedule_area(position_match.group(1).strip())
                previous_people[(location_id, schedule_label_key(old_position))] = current_name

    for item in items:
        if item.get("is_vehicle_area"):
            continue
        position = str(item.get("area_display") or "Position")
        current_name = str(item.get("employee_name") or "").strip() or "TBC"
        old_name = previous_people.get((item.get("schedule_location_id"), schedule_label_key(position)), "")
        if old_name and old_name.lower() != current_name.lower():
            item["assignment_changed"] = True
            item["changed"] = True
            item["assignment_change_summary"] = f"{position}: {old_name} → {current_name}"
        elif item.get("assignment_changed") and str(item.get("assignment_change_summary") or "").startswith("Position:"):
            item["assignment_change_summary"] = f"{position}: now {current_name}"


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
    return person_name_keys(value)


def schedule_person_alias_map(people: list[dict[str, object]]) -> dict[str, list[int]]:
    aliases: dict[str, list[int]] = {}
    identities = crew_identity_records()
    for index, person in enumerate(people):
        for key in person_name_keys(str(person.get("employee_name") or "")):
            aliases.setdefault(key, []).append(index)
        employee_id = safe_int(person.get("employee_id"))
        name_key = role_display_key(str(person.get("deputy_employee_name") or person.get("employee_name") or ""))
        matches = [
            record for record in identities
            if (employee_id is not None and safe_int(record.get("deputy_employee_id")) == employee_id)
            or (
                employee_id is None and name_key and name_key in {
                    role_display_key(str(record.get("canonical_display_name") or "")),
                    role_display_key(str(record.get("current_deputy_name") or "")),
                }
            )
        ]
        if len(matches) == 1:
            for alias in matches[0].get("aliases", []):
                key = role_display_key(str(alias or ""))
                if key:
                    aliases.setdefault(key, []).append(index)
    return aliases


def apply_crew_directory_identity(people: list[dict[str, object]]) -> None:
    identities = crew_identity_records()
    for person in people:
        if person.get("placeholder") or str(person.get("employee_name") or "") == "Open shift":
            continue
        employee_id = safe_int(person.get("employee_id"))
        deputy_name = str(person.get("employee_name") or "").strip()
        name_key = role_display_key(deputy_name)
        matches = [
            record for record in identities
            if (employee_id is not None and safe_int(record.get("deputy_employee_id")) == employee_id)
            or (
                employee_id is None and name_key and name_key in {
                    role_display_key(str(record.get("canonical_display_name") or "")),
                    role_display_key(str(record.get("current_deputy_name") or "")),
                }
            )
        ]
        if len(matches) != 1:
            continue
        person["deputy_employee_name"] = deputy_name
        person["canonical_person_id"] = matches[0]["id"]
        person["employee_name"] = str(matches[0].get("canonical_display_name") or deputy_name)


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
        current_vehicle = ", ".join(
            part.strip()
            for part in current_vehicle.split(",")
            if schedule_label_key(part) not in {"vehicle", "vehicles", "travel"}
        )
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
    return f"{area_label}: {old_name} → {new_name}"


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


def suppress_stale_overlapping_employee_roles(items: list[dict[str, object]]) -> list[dict[str, object]]:
    visible: list[dict[str, object]] = []
    for item in items:
        if item.get("is_vehicle_area") or not item.get("employee_id"):
            visible.append(item)
            continue
        item_position = schedule_item_position_key(item)
        stale = any(
            not other.get("is_vehicle_area")
            and other.get("employee_id") == item.get("employee_id")
            and other.get("schedule_location_id") == item.get("schedule_location_id")
            and schedule_item_position_key(other) != item_position
            and schedule_items_overlap(item, other)
            and str(other.get("captured_at") or "") > str(item.get("captured_at") or "")
            for other in items
        )
        if not stale:
            visible.append(item)
    return visible


def split_sound_vt_assignments(items: list[dict[str, object]]) -> set[tuple[str, object]]:
    """Interpret SVT as Sound when a different person is rostered on VT."""
    contexts: set[tuple[str, object]] = set()
    sound_vt_items = [item for item in items if schedule_item_position_key(item) == "soundvt"]
    vt_items = [item for item in items if schedule_item_position_key(item) == "vt"]
    for sound_vt_item in sound_vt_items:
        sound_employee_id = safe_int(sound_vt_item.get("employee_id"))
        sound_employee_name = schedule_label_key(str(sound_vt_item.get("employee_name") or ""))
        location_id = sound_vt_item.get("schedule_location_id")
        date_text = str(sound_vt_item.get("date") or "")
        if location_id in (None, "") or not date_text:
            continue
        for vt_item in vt_items:
            vt_employee_id = safe_int(vt_item.get("employee_id"))
            vt_employee_name = schedule_label_key(str(vt_item.get("employee_name") or ""))
            same_employee = (
                sound_employee_id is not None
                and vt_employee_id is not None
                and sound_employee_id == vt_employee_id
            ) or (
                sound_employee_name
                and vt_employee_name
                and sound_employee_name == vt_employee_name
            )
            if (
                date_text == str(vt_item.get("date") or "")
                and location_id == vt_item.get("schedule_location_id")
                and (sound_employee_id is not None or sound_employee_name)
                and (vt_employee_id is not None or vt_employee_name)
                and not same_employee
                and schedule_items_overlap(sound_vt_item, vt_item)
            ):
                contexts.add((date_text, location_id))
                break

    for item in sound_vt_items:
        context = (str(item.get("date") or ""), item.get("schedule_location_id"))
        if context not in contexts:
            continue
        item["area_display"] = "Sound"
        item["display_sort_order"] = schedule_display_sort("Sound", item.get("area_sort_order"))
    return contexts


def effective_schedule_items(rows: list[object]) -> tuple[list[dict[str, object]], set[tuple[str, object]]]:
    items = []
    for row in rows:
        item = decorate_schedule_row(row)
        if not schedule_area_is_hidden(str(item.get("area_display") or "Role")):
            items.append(item)
    person_focused_schedule_changes(items)
    items = suppress_stale_overlapping_employee_roles(dedupe_schedule_items(items))
    return items, split_sound_vt_assignments(items)


def apply_schedule_role_context(shifts: list[dict[str, object]], schedule_rows: list[object]) -> None:
    if not shifts or not schedule_rows:
        return
    _items, split_contexts = effective_schedule_items(schedule_rows)
    if not split_contexts:
        return

    for shift in shifts:
        location_ids = shift_location_id_values(shift)
        if not any((str(shift.get("date") or ""), location_id) in split_contexts for location_id in location_ids):
            continue
        role_key = role_display_key(str(shift.get("role_label") or shift.get("role_full_label") or ""))
        if role_key not in {"svt", "soundvt"}:
            continue
        shift["role_label"] = "Sound"
        shift["role_full_label"] = "Sound"
        shift["display_title"] = f"Sound at {shift.get('track_label')}"
        for segment in list(shift.get("role_segments") or []):
            segment_key = role_display_key(str(segment.get("role_short") or segment.get("role") or ""))
            if segment_key in {"svt", "soundvt"}:
                segment["role"] = "Sound"
                segment["role_short"] = "Sound"
        shift["role_chain_label"] = role_chain_label(list(shift.get("role_segments") or []))


def apply_saved_schedule_role_context(shifts: list[dict[str, object]]) -> None:
    dates = sorted({str(shift.get("date") or "") for shift in shifts if shift.get("date")})
    if not dates:
        return
    apply_schedule_role_context(shifts, fetch_deputy_schedule_between(dates[0], dates[-1]))


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


def schedule_people(
    rows: list[object],
    expected_areas: list[object] | None = None,
    *,
    include_vehicle_only: bool = False,
    include_placeholders: bool = True,
    vehicle_only_position_label: str = "Travel",
) -> list[dict[str, object]]:
    people_by_key: dict[str, dict[str, object]] = {}
    open_entries: list[dict[str, object]] = []
    items, _split_contexts = effective_schedule_items(rows)
    placeholders = expected_schedule_placeholders(expected_areas, items) if include_placeholders else []

    for item in items:
        area_label = str(item.get("area_display") or "Role")
        area_sort = schedule_sort_value(item.get("display_sort_order"))
        employee_name = str(item.get("employee_name") or "").strip()
        is_vehicle = bool(item.get("is_vehicle_area"))

        if not employee_name:
            if schedule_item_position_key(item) in ASSIGNED_ONLY_SCHEDULE_POSITION_KEYS:
                continue
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
                "employee_id": item.get("employee_id"),
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
            if schedule_label_key(area_label) not in {"vehicle", "vehicles", "travel"}:
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
            if not include_vehicle_only or not vehicle_parts:
                continue
            position_parts = [vehicle_only_position_label]
        position_label = ", ".join(position_parts)
        vehicle_label = ", ".join(vehicle_parts)
        sort_order = schedule_sort_value(person.get("position_sort"))
        if sort_order == 999999:
            sort_order = schedule_sort_value(person.get("vehicle_sort"))
        people.append(
            {
                "employee_name": person.get("employee_name") or "Open shift",
                "employee_id": person.get("employee_id"),
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
    apply_crew_directory_identity(people)
    return sorted(
        people,
        key=lambda person: (
            schedule_sort_value(person.get("sort_order")),
            str(person.get("position_label") or ""),
            str(person.get("employee_name") or ""),
        ),
    )


def reconcile_personal_assignment_evidence(
    people: list[dict[str, object]],
    evidence_rows: list[object],
    *,
    event_start_at: object = None,
    event_end_at: object = None,
) -> None:
    for raw_evidence in evidence_rows:
        evidence = dict(raw_evidence)
        if event_start_at and event_end_at:
            try:
                event_start = datetime.fromisoformat(str(event_start_at))
                event_end = datetime.fromisoformat(str(event_end_at))
                evidence_start = datetime.fromisoformat(str(evidence.get("start_at") or ""))
                evidence_end = datetime.fromisoformat(str(evidence.get("end_at") or ""))
            except ValueError:
                pass
            else:
                if evidence_end <= event_start or evidence_start >= event_end:
                    continue
        position_key = schedule_label_key(str(evidence.get("position_label") or ""))
        employee_name = str(evidence.get("employee_name") or evidence.get("display_name") or "Crew member").strip()
        evidence_employee_id = safe_int(evidence.get("deputy_employee_id"))
        matching_rows = [
            person for person in people
            if position_key in {
                schedule_label_key(part)
                for part in str(person.get("position_label") or "").split(",")
                if part.strip()
            }
        ]
        if not matching_rows:
            people.append({
                "employee_name": employee_name,
                "employee_id": evidence_employee_id,
                "position_label": str(evidence.get("position_label") or "Position"),
                "vehicle_label": "",
                "sort_order": schedule_display_sort(str(evidence.get("position_label") or "")),
                "changed": False,
                "change_summary": "",
                "change_time_label": "",
                "placeholder": False,
                "personal_evidence": True,
                "provenance_label": "Confirmed from personal roster",
                "possibly_missing": str(evidence.get("status") or "") == "possibly_missing",
            })
            continue
        shared = matching_rows[0]
        if shared.get("placeholder") or schedule_label_key(str(shared.get("employee_name") or "")) in {"tbc", "openshift"}:
            shared.update({
                "employee_name": employee_name,
                "employee_id": evidence_employee_id,
                "placeholder": False,
                "personal_evidence": True,
                "provenance_label": "Confirmed from personal roster",
                "possibly_missing": str(evidence.get("status") or "") == "possibly_missing",
            })
            continue
        same_person = (
            evidence_employee_id is not None
            and safe_int(shared.get("employee_id")) == evidence_employee_id
        ) or (
            evidence.get("canonical_person_id") is not None
            and schedule_label_key(str(shared.get("employee_name") or ""))
            == schedule_label_key(employee_name)
        )
        if same_person:
            shared["personal_evidence"] = True
            continue
        shared["assignment_conflict"] = True
        shared["conflict_warning"] = (
            f"Assignment conflict: personal roster says {evidence.get('position_label')} "
            f"for {employee_name}; crew schedule says {shared.get('employee_name')}."
        )
        shared["personal_evidence_name"] = employee_name
    people.sort(key=lambda person: (
        schedule_sort_value(person.get("sort_order")),
        str(person.get("position_label") or ""),
        str(person.get("employee_name") or ""),
    ))


def canonical_crew_name(
    name: object,
    employee_id: object = None,
    identities: list[dict[str, object]] | None = None,
) -> str:
    display_name = re.sub(r"\s+", " ", str(name or "").strip())
    if not display_name:
        return "TBC"
    identity_rows = identities if identities is not None else crew_identity_records()
    employee_id_value = safe_int(employee_id)
    name_key = role_display_key(display_name)
    matches = []
    for record in identity_rows:
        record_names = {
            role_display_key(str(record.get("canonical_display_name") or "")),
            role_display_key(str(record.get("current_deputy_name") or "")),
            *{
                role_display_key(str(alias or ""))
                for alias in list(record.get("aliases") or [])
            },
        }
        if (
            employee_id_value is not None
            and safe_int(record.get("deputy_employee_id")) == employee_id_value
        ) or (employee_id_value is None and name_key and name_key in record_names):
            matches.append(record)
    if len(matches) == 1:
        return str(matches[0].get("canonical_display_name") or display_name)
    return display_name


def event_change_position(change: dict[str, object], field_name: str) -> str:
    return next(
        (
            str(position).strip()
            for position in list(change.get(field_name) or [])
            if str(position).strip()
        ),
        "",
    )


def event_change_display_line(change: dict[str, object]) -> str:
    change_type = str(change.get("change_type") or "")
    old_position = event_change_position(change, "old_positions")
    new_position = event_change_position(change, "new_positions")
    old_name = str(change.get("old_employee_name") or "TBC")
    new_name = str(change.get("new_employee_name") or "TBC")
    if change_type == "move":
        return f"{new_name} moved {old_position} → {new_position}"
    if change_type in {"replacement", "opened", "filled"}:
        return f"{new_position or old_position or 'Position'} — {old_name} → {new_name}"
    return str(change.get("display_summary") or "Crew assignment changed.")


def decorate_event_changes(rows: list[object]) -> list[dict[str, object]]:
    changes = []
    identities = crew_identity_records()
    for row in rows:
        item = dict(row)
        for field_name in ("old_positions", "new_positions"):
            try:
                values = json.loads(str(item.get(field_name) or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                values = []
            item[field_name] = [str(value) for value in values if str(value).strip()]
        item["old_employee_name"] = canonical_crew_name(
            item.get("old_employee_name"), item.get("old_employee_id"), identities
        )
        item["new_employee_name"] = canonical_crew_name(
            item.get("new_employee_name"), item.get("new_employee_id"), identities
        )
        item["inline_summary"] = str(item.get("inline_summary") or "").replace(" -> ", " → ")
        item["display_summary"] = event_change_display_line(item)
        item["changed_at_label"] = format_datetime(str(item.get("changed_at") or ""), "%d %b %H:%M")
        changes.append(item)
    return changes


def group_event_changes(changes: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    order: list[str] = []
    for index, change in enumerate(changes):
        group_id = str(change.get("group_id") or f"legacy-{index}")
        if group_id not in grouped:
            grouped[group_id] = []
            order.append(group_id)
        grouped[group_id].append(change)

    presentations: list[dict[str, object]] = []
    for group_id in order:
        rows = grouped[group_id]
        consumed: set[int] = set()
        lines: list[str] = []
        inline_changes: list[dict[str, str]] = []
        moves = [
            (index, change)
            for index, change in enumerate(rows)
            if str(change.get("change_type") or "") == "move"
        ]
        for move_index, move in moves:
            moved_name = str(move.get("new_employee_name") or move.get("old_employee_name") or "Crew member")
            old_position = event_change_position(move, "old_positions")
            new_position = event_change_position(move, "new_positions")
            replacement_index = next(
                (
                    index
                    for index, change in enumerate(rows)
                    if index not in consumed
                    and str(change.get("change_type") or "") == "replacement"
                    and schedule_label_key(event_change_position(change, "new_positions"))
                    == schedule_label_key(new_position)
                    and schedule_label_key(str(change.get("new_employee_name") or ""))
                    == schedule_label_key(moved_name)
                ),
                None,
            )
            opened_index = next(
                (
                    index
                    for index, change in enumerate(rows)
                    if index not in consumed
                    and str(change.get("change_type") or "") == "opened"
                    and schedule_label_key(event_change_position(change, "old_positions"))
                    == schedule_label_key(old_position)
                    and schedule_label_key(str(change.get("old_employee_name") or ""))
                    == schedule_label_key(moved_name)
                ),
                None,
            )
            replaced_name = (
                str(rows[replacement_index].get("old_employee_name") or "")
                if replacement_index is not None
                else ""
            )
            move_line = f"{moved_name} moved {old_position} → {new_position}"
            if replaced_name and schedule_label_key(replaced_name) not in {"tbc", "openshift"}:
                move_line += f", replacing {replaced_name}"
            lines.append(move_line)
            target_summary = f"{moved_name} moved from {old_position}"
            if replaced_name and schedule_label_key(replaced_name) not in {"tbc", "openshift"}:
                target_summary += f", replacing {replaced_name}"
            inline_changes.append(
                {"position_key": schedule_label_key(new_position), "summary": target_summary}
            )
            source_summary = f"{moved_name} moved to {new_position}"
            if opened_index is not None:
                lines.append(f"{old_position} is now TBC")
                source_summary += "; position now TBC"
            inline_changes.append(
                {"position_key": schedule_label_key(old_position), "summary": source_summary}
            )
            consumed.add(move_index)
            if replacement_index is not None:
                consumed.add(replacement_index)
            if opened_index is not None:
                consumed.add(opened_index)

        for index, change in enumerate(rows):
            if index not in consumed:
                display_line = event_change_display_line(change)
                append_unique(lines, display_line)
                positions = list(change.get("new_positions") or []) or list(
                    change.get("old_positions") or []
                )
                inline_summary = str(change.get("inline_summary") or "").strip()
                if not inline_summary:
                    inline_summary = display_line.split(" — ", 1)[-1]
                for position in positions:
                    position_key = schedule_label_key(str(position or ""))
                    if not position_key or any(
                        item.get("position_key") == position_key
                        for item in inline_changes
                    ):
                        continue
                    inline_changes.append(
                        {"position_key": position_key, "summary": inline_summary}
                    )

        changed_at = latest_iso_datetime(*(change.get("changed_at") for change in rows))
        presentations.append(
            {
                "group_id": group_id,
                "changed_at": changed_at,
                "changed_at_label": format_datetime(changed_at, "%d %b %H:%M"),
                "changed_since_viewed": any(
                    bool(int(change.get("changed_since_viewed") or 0)) for change in rows
                ),
                "lines": lines,
                "inline_changes": inline_changes,
            }
        )
    return presentations


def apply_event_changes_to_schedule_people(
    people: list[dict[str, object]],
    change_groups: list[dict[str, object]],
) -> None:
    for change_group in change_groups:
        if not bool(change_group.get("changed_since_viewed")):
            continue
        for inline_change in list(change_group.get("inline_changes") or []):
            position_key = str(inline_change.get("position_key") or "")
            if not position_key:
                continue
            for person in people:
                person_positions = {
                    schedule_label_key(position)
                    for position in str(person.get("position_label") or "").split(",")
                    if position.strip()
                }
                if position_key not in person_positions:
                    continue
                person["changed"] = True
                person["change_summary"] = str(inline_change.get("summary") or "Changed")
                person["change_time_label"] = format_datetime(
                    latest_iso_datetime(
                        person.get("last_changed_at"), change_group.get("changed_at")
                    ),
                    "%d %b %H:%M",
                )
                break

def shifts_are_vehicle_travel_context(shifts: list[dict[str, object]]) -> bool:
    visible_shifts = [shift for shift in shifts if not int(shift.get("deleted_from_source") or 0)]
    if not visible_shifts:
        return False
    if is_overnight_travel_day(visible_shifts):
        return True

    has_vehicle_assignment = False
    has_accommodation_note = False
    for shift in visible_shifts:
        note_text = " ".join(
            str(shift.get(key) or "")
            for key in ("note", "roster_note", "raw_note", "raw_roster_note", "notes")
        ).lower()
        if "accommodation" in note_text or "motel" in note_text or "hotel" in note_text:
            has_accommodation_note = True

        role_parts: list[str] = []
        for segment in list(shift.get("role_segments") or []):
            role = str(segment.get("role_short") or segment.get("role") or "").strip()
            if role:
                role_parts.append(role)
        if not role_parts:
            role = str(
                shift.get("role_label")
                or shift.get("role_full_label")
                or shift.get("title")
                or ""
            ).strip()
            if role:
                role_parts.append(role)
        meaningful_parts = [part for part in role_parts if schedule_label_key(part) not in {"travel", "vehicles"}]
        if not meaningful_parts:
            continue
        if all(role_is_vehicleish(part) for part in meaningful_parts):
            has_vehicle_assignment = True
            continue
        return False
    return has_vehicle_assignment and has_accommodation_note


def schedule_rows_are_vehicle_travel_context(rows: list[object]) -> bool:
    items, _split_contexts = effective_schedule_items(rows)
    assigned_items = [item for item in items if str(item.get("employee_name") or "").strip()]
    if not assigned_items:
        return False
    vehicle_items = [item for item in assigned_items if item.get("is_vehicle_area")]
    production_items = [item for item in assigned_items if not item.get("is_vehicle_area")]
    return bool(vehicle_items) and not production_items


def apply_vehicle_carryover_from_people(
    people: list[dict[str, object]],
    vehicle_people: list[dict[str, object]],
) -> None:
    if not people or not vehicle_people:
        return
    vehicle_by_alias: dict[str, set[str]] = {}
    for person in vehicle_people:
        vehicle = str(person.get("vehicle_label") or "").strip()
        name = str(person.get("employee_name") or "").strip()
        if not vehicle or vehicle == "-" or not name:
            continue
        for key in note_person_keys(name):
            vehicle_by_alias.setdefault(key, set()).add(vehicle)

    for person in people:
        current_vehicle = str(person.get("vehicle_label") or "").strip()
        if current_vehicle and current_vehicle != "-":
            continue
        matches: set[str] = set()
        for key in note_person_keys(person.get("employee_name")):
            matches.update(vehicle_by_alias.get(key, set()))
        if len(matches) == 1:
            person["vehicle_label"] = next(iter(matches))


def show_vehicle_assignment_as_travel(shifts: list[dict[str, object]]) -> None:
    for shift in shifts:
        role_label = str(shift.get("role_label") or shift.get("role_full_label") or "").strip()
        if not role_is_vehicleish(role_label):
            continue
        vehicle_label = str(shift.get("header_vehicle_label") or "").strip()
        if not vehicle_label:
            shift["header_vehicle_label"] = role_label
        shift["role_label"] = "Travel"
        shift["role_full_label"] = "Travel"
        track_label = str(shift.get("track_label") or "").strip()
        shift["display_title"] = f"Travel to {track_label}" if track_label else "Travel"
        for segment in list(shift.get("role_segments") or []):
            segment_role = str(segment.get("role_short") or segment.get("role") or "").strip()
            if role_is_vehicleish(segment_role):
                segment["role"] = "Travel"
                segment["role_short"] = "Travel"
        shift["role_chain_label"] = role_chain_label(list(shift.get("role_segments") or []))


def shift_schedule_location_ids(shifts: list[dict[str, object]]) -> list[int]:
    values = []
    for shift in shifts:
        values.extend(list(shift.get("schedule_location_ids") or []))
        values.append(shift.get("schedule_location_id"))
    return unique_ints(values)


def track_maps_for_day(
    shifts: list[dict[str, object]],
    manual_rosters: list[dict[str, object]],
) -> list[dict[str, object]]:
    maps = []
    seen = set()
    rules = track_map_location_rule_index()
    race_manual_rosters = [item for item in manual_rosters if str(item.get("day_type") or "race_day") == "race_day"]
    for item in list(shifts) + race_manual_rosters:
        track_label = str(item.get("track_label") or item.get("location_label") or "").strip()
        classification = classify_track_map_location(track_label, rules)
        if classification.get("classification") not in {"venue", "alias"}:
            continue
        course_key = str(classification.get("canonical_key") or "")
        if not course_key or course_key in seen:
            continue
        row = get_track_map(course_key)
        if row is None:
            continue
        effective = effective_track_map_file(row)
        image_width = int(effective["image_width"] or 0)
        image_height = int(effective["image_height"] or 0)
        byte_size = int(effective["byte_size"] or 0)
        if not image_width or not image_height:
            file_name = Path(str(effective["file_name"] or "")).name
            image_path = Path(get_settings().data_dir) / "track_maps" / file_name
            if file_name and image_path.is_file():
                content = image_path.read_bytes()
                image_width, image_height = image_dimensions(content, str(effective["content_type"] or ""))
                byte_size = byte_size or len(content)
        seen.add(course_key)
        maps.append(
            {
                "track_key": course_key,
                "track_label": str(classification.get("canonical_label") or row["track_label"] or "Track"),
                "course_label": str(row["course_label"] or classification.get("canonical_label") or "Track"),
                "image_url": f"/track-map/{course_key}?v={str(effective['image_hash'])[:12]}",
                "image_width": image_width,
                "image_height": image_height,
                "byte_size": byte_size,
                "is_manual": bool(effective["is_manual"]),
            }
        )
    return maps


def track_map_admin_data() -> dict[str, list[dict[str, object]]]:
    migrate_existing_track_map_aliases()
    rules = track_map_location_rule_index()
    records = {str(row["track_key"]): dict(row) for row in list_track_maps()}
    venues: dict[str, dict[str, object]] = {}
    unclassified: dict[str, dict[str, object]] = {}
    observations = [(label, "crew") for label in list_crew_work_location_labels()]
    observations.extend(
        (str(record.get("track_label") or record.get("course_label") or key), "map")
        for key, record in records.items()
    )
    for label, observation_source in observations:
        classification = classify_track_map_location(label, rules)
        location_key = str(classification.get("location_key") or "")
        kind = str(classification.get("classification") or "unclassified")
        if kind in {"venue", "alias"}:
            key = str(classification.get("canonical_key") or "")
            if not key:
                continue
            venue = venues.setdefault(key, {
                "track_key": key,
                "track_label": str(classification.get("canonical_label") or label),
                "aliases": set(),
            })
            if classification.get("is_alias") or kind == "alias":
                alias = str(classification.get("raw_label") or label)
                if calendar_location_key(alias) != calendar_location_key(venue["track_label"]):
                    venue["aliases"].add(alias)
            continue
        if kind == "unclassified" and location_key:
            unclassified.setdefault(location_key, {
                "location_key": location_key,
                "location_label": str(classification.get("raw_label") or label),
                "source": observation_source,
            })

    for key, record in records.items():
        classification = classify_track_map_location(
            record.get("track_label") or record.get("course_label") or key,
            rules,
        )
        canonical_key = str(classification.get("canonical_key") or "")
        if classification.get("classification") in {"venue", "alias"} and canonical_key:
            venues.setdefault(canonical_key, {
                "track_key": canonical_key,
                "track_label": str(classification.get("canonical_label") or record.get("track_label") or key),
                "aliases": set(),
            })

    map_dir = Path(get_settings().data_dir) / "track_maps"
    result: list[dict[str, object]] = []
    for key, venue in venues.items():
        record = dict(records.get(key) or {})
        effective = effective_track_map_file(record)
        auto_file = Path(str(record.get("file_name") or "")).name
        manual_file = Path(str(record.get("manual_file_name") or "")).name
        result.append({
            **record,
            "track_key": key,
            "track_label": str(venue["track_label"]),
            "aliases": sorted(venue["aliases"], key=str.lower),
            "auto_available": bool(auto_file and (map_dir / auto_file).is_file()),
            "manual_available": bool(manual_file and (map_dir / manual_file).is_file()),
            "effective_width": int(effective["image_width"] or 0),
            "effective_height": int(effective["image_height"] or 0),
            "effective_byte_size": int(effective["byte_size"] or 0),
            "effective_source": "Manual upload" if effective["is_manual"] else ("Automatic" if auto_file else "No image"),
        })
    admin_rules = [
        dict(row) for row in list_track_map_location_rules()
        if str(row["source"] or "") == "admin"
    ]
    return {
        "venues": sorted(result, key=lambda item: str(item["track_label"]).lower()),
        "unclassified": sorted(unclassified.values(), key=lambda item: str(item["location_label"]).lower()),
        "decisions": admin_rules,
        "warnings": [dict(row) for row in list_track_map_migration_warnings()],
    }


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
    meeting["club_name"] = re.sub(r"\s*,\s*", ", ", str(meeting.get("club_name") or "").strip())
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


def distribution_chart(counter: Counter, limit: int = 6) -> dict[str, object]:
    positive = Counter({str(label): float(value) for label, value in counter.items() if float(value) > 0})
    total = sum(positive.values())
    top = positive.most_common(limit)
    remainder = total - sum(value for _label, value in top)
    if remainder > 0:
        top.append(("Other", remainder))
    items = []
    cursor = 0.0
    gradient_parts = []
    for index, (label, value) in enumerate(top):
        percent = (float(value) / total) * 100 if total else 0
        end = cursor + percent
        colour_index = (index % 10) + 1
        colour = f"var(--location-colour-{colour_index})"
        gradient_parts.append(f"{colour} {cursor:.2f}% {end:.2f}%")
        items.append(
            {
                "label": label,
                "value": value,
                "percent": round(percent, 1),
                "colour_style": f"--chart-colour: {colour}; --chart-width: {percent:.2f}%;",
            }
        )
        cursor = end
    return {
        "items": items,
        "gradient": f"conic-gradient({', '.join(gradient_parts)})" if gradient_parts else "var(--surface-raised)",
        "total": total,
    }


def weekday_chart_items(hours: Counter, shifts: Counter) -> list[dict[str, object]]:
    labels = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    maximum = max((float(hours.get(label, 0)) for label in labels), default=0)
    return [
        {
            "label": label,
            "hours": round(float(hours.get(label, 0)), 2),
            "shift_count": int(shifts.get(label, 0)),
            "percent": round((float(hours.get(label, 0)) / maximum) * 100) if maximum else 0,
        }
        for label in labels
    ]


def combine_insight_shifts(shifts: list[dict[str, object]]) -> list[dict[str, object]]:
    by_date: dict[str, list[dict[str, object]]] = {}
    for shift in shifts:
        by_date.setdefault(str(shift.get("date") or ""), []).append(shift)
    return [combined for day_shifts in by_date.values() for combined in combine_adjacent_shifts(day_shifts)]


def inferred_tbc_schedule(start_day: date, end_day: date) -> list[dict[str, object]]:
    grouped_rows: dict[tuple[str, int], list[object]] = {}
    location_names: dict[tuple[str, int], str] = {}
    for row in fetch_deputy_schedule_between(start_day.isoformat(), end_day.isoformat()):
        item = dict(row)
        try:
            location_id = int(item.get("schedule_location_id") or item.get("area_location_id") or 0)
        except (TypeError, ValueError):
            location_id = 0
        date_text = str(item.get("date") or "")
        if not date_text or not location_id:
            continue
        key = (date_text, location_id)
        grouped_rows.setdefault(key, []).append(row)
        location_names[key] = str(item.get("location_name") or location_id)

    location_ids = {location_id for _date_text, location_id in grouped_rows}
    areas_by_location: dict[int, list[object]] = {}
    for area in fetch_deputy_schedule_areas_for_locations(location_ids):
        areas_by_location.setdefault(int(area["location_id"]), []).append(area)

    tbc_rows = []
    seen = set()
    for (date_text, location_id), rows in grouped_rows.items():
        for person in schedule_people(rows, expected_areas=areas_by_location.get(location_id, [])):
            employee_name = str(person.get("employee_name") or "").strip().lower()
            if not person.get("placeholder") and employee_name != "tbc":
                continue
            position = str(person.get("position_label") or "").strip()
            if not position or schedule_label_key(position) in {"fm", "floormanager"}:
                continue
            dedupe_key = (date_text, location_id, schedule_label_key(position))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            try:
                date_label = date.fromisoformat(date_text).strftime("%a %d %b")
            except ValueError:
                date_label = date_text
            tbc_rows.append(
                {
                    "date": date_text,
                    "date_label": date_label,
                    "location_label": parse_shift_title(f"[{location_names.get((date_text, location_id), 'Unknown')}] Shift")["track_label"],
                    "position_label": position,

                }
            )
    return sorted(
        tbc_rows,
        key=lambda item: (str(item["date"]), str(item["location_label"]), str(item["position_label"])),
    )
def date_range_label(start_date: date, end_date: date) -> str:
    if start_date.year == end_date.year:
        return f"{start_date.strftime('%d %b')} to {end_date.strftime('%d %b %Y')}"
    return f"{start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"


def build_roster_insights(owner_user_id: int | None, today: date) -> dict[str, object]:
    start_day = today - timedelta(days=90)
    end_day = today + timedelta(days=180)
    start_date = start_day.isoformat()
    end_date = end_day.isoformat()
    shifts = combine_insight_shifts([
        decorate_shift(row)
        for row in fetch_shifts_between(start_date, end_date, owner_user_id=owner_user_id)
        if not int(row["deleted_from_source"] or 0)
    ])
    shared_shifts = [
        decorate_shift(row)
        for row in fetch_shifts_between(start_date, end_date, owner_user_id=None)
        if not int(row["deleted_from_source"] or 0)
    ]
    tbc_end_day = today + timedelta(days=90)
    past_30_start = today - timedelta(days=30)
    past_90_start = today - timedelta(days=90)
    completed_through = today - timedelta(days=1)
    past_30 = [
        shift for shift in shifts
        if past_30_start.isoformat() <= str(shift.get("date") or "") < today.isoformat()
    ]
    past_90 = [
        shift for shift in shifts
        if past_90_start.isoformat() <= str(shift.get("date") or "") < today.isoformat()
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
    weekday_hours: Counter[str] = Counter()
    for shift in past_90:
        track = str(shift.get("track_label") or "Unknown").strip() or "Unknown"
        role = str(shift.get("role_chain_label") or shift.get("role_full_label") or shift.get("role_label") or "Shift").strip()
        shift_date = str(shift.get("date") or "")
        track_counts[track] += 1
        role_counts[role] += 1
        track_hours[track] += float(shift_hours_value(shift) or 0)
        try:
            weekday_label = date.fromisoformat(shift_date).strftime("%a")
            weekday_counts[weekday_label] += 1
            weekday_hours[weekday_label] += float(shift_hours_value(shift) or 0)
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

    tbc_rows = inferred_tbc_schedule(today, tbc_end_day)
    tbc_position_counts: Counter[str] = Counter()
    tbc_location_counts: Counter[str] = Counter()
    for item in tbc_rows:
        tbc_position_counts[str(item["position_label"])] += 1
        tbc_location_counts[item["location_label"]] += 1

    recent_days = []
    for shift in sorted(past_90, key=lambda item: (str(item.get("date") or ""), str(item.get("start_at") or "")), reverse=True)[:12]:
        shift_date = str(shift.get("date") or "")
        try:
            date_label = date.fromisoformat(shift_date).strftime("%a %d %b")
        except ValueError:
            date_label = shift_date
        recent_days.append({
            "date": shift_date,
            "date_label": date_label,
            "track_label": str(shift.get("track_label") or "Unknown"),
            "position_label": str(shift.get("role_chain_label") or shift.get("role_full_label") or shift.get("role_label") or "Shift"),
            "time_range": str(shift.get("time_range") or "Time unavailable"),
            "hours": float(shift_hours_value(shift) or 0),
        })

    past_30_days = len({str(shift.get("date") or "") for shift in past_30})
    past_90_days = len({str(shift.get("date") or "") for shift in past_90})
    past_90_hours = sum(float(shift_hours_value(shift) or 0) for shift in past_90)

    return {
        "range_label": date_range_label(start_day, end_day),
        "past_30_label": date_range_label(past_30_start, completed_through),
        "past_90_label": date_range_label(past_90_start, completed_through),
        "upcoming_label": date_range_label(today, end_day),
        "tbc_label": date_range_label(today, tbc_end_day),
        "shift_count": len(shifts),
        "past_30_count": len(past_30),
        "past_30_days": past_30_days,
        "past_30_hours": sum(float(shift_hours_value(shift) or 0) for shift in past_30),
        "past_90_count": len(past_90),
        "past_90_days": past_90_days,
        "past_90_hours": past_90_hours,
        "average_shift_hours": past_90_hours / len(past_90) if past_90 else 0,
        "upcoming_count": len(upcoming),
        "changed_count": sum(1 for shift in shifts if int(shift.get("changed_since_viewed") or 0)),
        "track_count": len(track_counts),
        "top_tracks": bar_items(track_counts),
        "top_roles": bar_items(role_counts),
        "track_hours": bar_items(Counter({label: round(value, 2) for label, value in track_hours.items()})),
        "weekday_counts": bar_items(weekday_counts, limit=7),
        "position_mix": distribution_chart(role_counts, limit=6),
        "location_hours_mix": distribution_chart(track_hours, limit=6),
        "weekday_chart": weekday_chart_items(weekday_hours, weekday_counts),
        "recent_days": recent_days,
        "shared_position_mix": distribution_chart(shared_role_counts, limit=7),
        "shared_location_hours_mix": distribution_chart(shared_track_hours, limit=7),
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
    enrich_shifts_with_love_racing(
        [shift for day_shifts in shifts_by_date.values() for shift in day_shifts],
        period_start.isoformat(),
        period_end.isoformat(),
    )
    manual_by_date = published_rosters_by_date(
        period_start.isoformat(),
        period_end.isoformat(),
        owner_user_id,
    )

    day_rows = []
    total_hours = 0.0
    for offset in range(14):
        day_item = period_start + timedelta(days=offset)
        shifts = [
            shift
            for shift in shifts_by_date.get(day_item.isoformat(), [])
            if not int(shift.get("deleted_from_source") or 0)
        ]
        manual_rosters = manual_by_date.get(day_item.isoformat(), [])
        day_total = sum(shift_hours_value(shift) for shift in shifts) + sum(
            float(roster.get("hours") or 0) for roster in manual_rosters
        )
        total_hours += day_total
        locations = []
        notes = []
        for shift in shifts:
            location = str(shift.get("track_label") or shift.get("location") or "Shift")
            if location not in locations:
                locations.append(location)
            private_note = str(shift.get("private_note") or "").strip()
            if private_note and private_note not in notes:
                notes.append(private_note)
        for roster in manual_rosters:
            location = str(roster.get("location_label") or roster.get("display_title") or "Work day")
            if location not in locations:
                locations.append(location)
            roster_note = str(roster.get("notes") or "").strip()
            if roster_note and roster_note not in notes:
                notes.append(roster_note)
        day_rows.append(
            {
                "date": day_item,
                "date_label": day_item.strftime("%a %d %b"),
                "holiday": public_holiday_context(day_item),
                "iso": day_item.isoformat(),
                "total": day_total,
                "locations": ", ".join(locations) if locations else "-",
                "shifts": shifts,
                "manual_rosters": manual_rosters,
                "notes": notes,
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


def aggregate_global_schedule(rows: list[object]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        schedule = dict(row)
        if not int(schedule.get("is_published") or 0):
            continue
        employee_name = str(schedule.get("employee_name") or "").strip()
        if not employee_name:
            continue
        date_key = str(schedule.get("date") or "")
        location_name = str(schedule.get("location_name") or "Unknown").strip()
        track_label = str(parse_shift_title(f"[{location_name}] Shift").get("track_label") or location_name)
        key = (date_key, re.sub(r"[^a-z0-9]+", "", track_label.lower()))
        item = grouped.get(key)
        start_at = parse_iso_datetime(str(schedule.get("start_at") or ""))
        end_at = parse_iso_datetime(str(schedule.get("end_at") or ""))
        if item is None:
            location_id = schedule.get("schedule_location_id") or schedule.get("area_location_id")
            colour_index = stable_location_colour_index(location_id, location_name, track_label)
            item = {
                "id": f"global-{date_key}-{location_id or track_label}",
                "date": date_key,
                "track_label": track_label,
                "schedule_location_id": int(location_id) if str(location_id or "").isdigit() else 0,
                "race_type_label": "",
                "global_event": True,
                "crew_names": set(),
                "global_start_at": start_at,
                "global_end_at": end_at,
                "changed_since_viewed": False,
                "deleted_from_source": False,
                "colour_style": (
                    f"--shift-location-colour: var(--location-colour-{colour_index}); "
                    f"--location-colour: var(--location-colour-{colour_index});"
                ),
            }
            grouped[key] = item
        item["crew_names"].add(employee_name.lower())
        if start_at and (item["global_start_at"] is None or start_at < item["global_start_at"]):
            item["global_start_at"] = start_at
        if end_at and (item["global_end_at"] is None or end_at > item["global_end_at"]):
            item["global_end_at"] = end_at

    events = []
    for item in grouped.values():
        crew_count = len(item.pop("crew_names"))
        start_at = item.pop("global_start_at")
        end_at = item.pop("global_end_at")
        crew_label = "Crew scheduled"
        item["crew_count"] = crew_count
        item["role_chain_label"] = crew_label
        item["role_label"] = crew_label
        item["display_hours_label"] = crew_label
        item["start_label"] = start_at.strftime("%H:%M") if start_at else "TBC"
        item["time_range"] = (
            f"{start_at.strftime('%H:%M')}-{end_at.strftime('%H:%M')}"
            if start_at and end_at
            else item["start_label"]
        )
        events.append(item)
    return sorted(events, key=lambda item: (str(item.get("date") or ""), str(item.get("start_at") or "")))


def infer_display_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0].strip()
    local_part = re.sub(r"[._-]+", " ", local_part)
    display_name = " ".join(part.capitalize() for part in local_part.split() if part)
    return display_name or "Roster User"


def capture_summary(value: str) -> dict[str, object] | None:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "captured_at": str(payload.get("captured_at") or ""),
        "status": str(payload.get("status") or "unknown"),
        "response_count": len(payload.get("responses") or []) if isinstance(payload.get("responses"), list) else 0,
        "shift_count": len(payload.get("extracted_shifts") or []) if isinstance(payload.get("extracted_shifts"), list) else 0,
        "schedule_count": (
            len(payload.get("extracted_schedule_shifts") or [])
            if isinstance(payload.get("extracted_schedule_shifts"), list)
            else 0
        ),
    }


def admin_user_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for user in list_app_users():
        item = dict(user)
        item["devices"] = [dict(device) for device in list_trusted_devices_for_user(int(user["id"]))]
        latest_capture = get_latest_deputy_web_capture_for_user(int(user["id"]))
        item["latest_capture"] = capture_summary(str(latest_capture["payload"] or "")) if latest_capture else None
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
    excluded_track_keys = {
        "contractors",
        "national",
        "northernops",
        "northernopscontractors",
        "office",
        "outofregion",
        "travel",
        "vehicle",
        "vehicles",
        "web",
    }

    def usable_track_label(value: object) -> bool:
        key = travel_default_key(value)
        return bool(key and key not in excluded_track_keys and "contractor" not in key)

    def shift_track_label(shift: dict[str, object]) -> str:
        parsed = parse_shift_title(str(shift.get("title") or ""))
        normalised = source_payload_normalised(str(shift.get("source_payload") or ""))
        raw_label = str(
            parsed.get("track_label")
            or normalised.get("location_name")
            or normalised.get("source_code")
            or ""
        ).strip()
        _key, label = canonical_travel_track(raw_label, raw_label)
        return label

    def add_sample(
        track_label: str,
        base_label: str,
        travel_minutes: int,
        shift_date: str,
        sample_kind: str,
    ) -> None:
        if not track_label or track_label.lower() in GENERIC_TRACK_LABELS or not usable_track_label(track_label):
            return
        track_key, clean_track_label = canonical_travel_track(track_label, track_label)
        if not track_key or travel_minutes <= 0 or travel_minutes > 8 * 60:
            return
        clean_base = canonical_travel_base_label(base_label)
        groups.setdefault((track_key, clean_track_label, clean_base), []).append(
            {
                "travel_minutes": travel_minutes,
                "date": shift_date,
                "sample_kind": sample_kind,
            }
        )

    for existing in list_travel_time_defaults():
        if str(existing["source"] or "") == "learned" and not usable_track_label(existing["track_label"]):
            delete_travel_time_default(int(existing["id"]))

    shifts = [dict(row) for row in fetch_shifts_for_travel_learning()]
    for shift in shifts:
        track_label = shift_track_label(shift)
        summary = parse_roster_summary(description_lines(str(shift.get("description") or "")))
        timings = timing_lookup(summary)
        base_label = "Office / Clow Place"
        base_clock = timings.get("office") or timings.get("clow place")
        on_track_clock = timings.get("on track")
        if not base_clock or not on_track_clock:
            continue
        start_at = clock_datetime_for_shift(shift, base_clock)
        on_track_at = clock_datetime_for_shift(shift, on_track_clock, start_at)
        if start_at is None or on_track_at is None:
            continue
        travel_minutes = int(round((on_track_at - start_at).total_seconds() / 60))
        add_sample(
            track_label,
            base_label,
            travel_minutes,
            str(shift.get("date") or ""),
            "roster_note",
        )

    next_day_by_owner: dict[tuple[int, str], list[dict[str, object]]] = {}
    for shift in shifts:
        owner_user_id = safe_int(shift.get("owner_user_id"))
        shift_date = str(shift.get("date") or "")
        if owner_user_id is None or not shift_date:
            continue
        next_day_by_owner.setdefault((owner_user_id, shift_date), []).append(shift)

    for shift in shifts:
        haystack = " ".join(
            str(shift.get(key) or "") for key in ("title", "description")
        ).lower()
        if "travel then overnighter" not in haystack and "overnighter" not in haystack:
            continue
        owner_user_id = safe_int(shift.get("owner_user_id"))
        travel_date = parse_iso_datetime(str(shift.get("date") or ""))
        if owner_user_id is None or travel_date is None:
            continue
        paid_hours = safe_float(shift.get("paid_hours"))
        if paid_hours is not None and paid_hours > 0:
            travel_minutes = int(round(paid_hours * 60))
        else:
            start_at = parse_iso_datetime(str(shift.get("start_at") or ""))
            end_at = parse_iso_datetime(str(shift.get("end_at") or ""))
            travel_minutes = (
                int(round((end_at - start_at).total_seconds() / 60))
                if start_at is not None and end_at is not None
                else 0
            )
        next_date = (travel_date.date() + timedelta(days=1)).isoformat()
        candidates = next_day_by_owner.get((owner_user_id, next_date), [])
        next_track = next(
            (
                label
                for candidate in candidates
                if (label := shift_track_label(candidate))
                and label.lower() not in GENERIC_TRACK_LABELS
                and usable_track_label(label)
            ),
            "",
        )
        add_sample(
            next_track,
            "Office / Clow Place",
            travel_minutes,
            str(shift.get("date") or ""),
            "overnight_travel",
        )

    saved = 0
    for (track_key, track_label, base_label), samples in groups.items():
        samples_by_event: dict[tuple[str, str], list[int]] = {}
        for sample in samples:
            event_key = (
                str(sample.get("date") or ""),
                str(sample.get("sample_kind") or ""),
            )
            samples_by_event.setdefault(event_key, []).append(int(sample["travel_minutes"]))
        event_samples = [
            {
                "date": event_key[0],
                "sample_kind": event_key[1],
                "travel_minutes": Counter(minutes).most_common(1)[0][0],
            }
            for event_key, minutes in samples_by_event.items()
        ]
        counts = Counter(int(sample["travel_minutes"]) for sample in event_samples)
        highest_count = max(counts.values())
        candidate_minutes = {minutes for minutes, count in counts.items() if count == highest_count}
        newest_first = sorted(event_samples, key=lambda sample: str(sample.get("date") or ""), reverse=True)
        travel_minutes = next(
            int(sample["travel_minutes"])
            for sample in newest_first
            if int(sample["travel_minutes"]) in candidate_minutes
        )
        dates = sorted(str(sample["date"] or "") for sample in event_samples if str(sample["date"] or ""))
        sample_kinds = {str(sample.get("sample_kind") or "") for sample in event_samples}
        if sample_kinds == {"overnight_travel"}:
            note = "Learned from a preceding Travel then Overnighter shift."
        elif "overnight_travel" in sample_kinds:
            note = "Learned from roster notes and preceding overnight travel shifts."
        else:
            note = "Learned from previous roster notes."
        upsert_travel_time_default(
            track_key=track_key,
            track_label=track_label,
            base_label=base_label,
            travel_minutes=travel_minutes,
            source="learned",
            sample_count=len(event_samples),
            first_seen_at=dates[0] if dates else "",
            last_seen_at=dates[-1] if dates else "",
            note=note,
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


def admin_location_rows(
    planning_locations: list[dict[str, object]],
    travel_defaults: list[dict[str, object]],
) -> list[dict[str, object]]:
    locations: dict[str, dict[str, object]] = {}
    for planning in planning_locations:
        key = travel_default_key(planning.get("display_name") or planning.get("location_key"))
        if not key:
            continue
        locations[key] = {
            "location_key": planning.get("location_key") or key,
            "display_name": planning.get("display_name") or key,
            "planning_enabled": bool(int(planning.get("is_enabled") or 0)),
            "meeting_count": int(planning.get("meeting_count") or 0),
            "first_date": planning.get("first_date") or "",
            "last_date": planning.get("last_date") or "",
            "club_names": re.sub(r"\s*,\s*", ", ", str(planning.get("club_names") or "").strip()),
            "travel_defaults": [],
        }

    for travel in travel_defaults:
        key = travel_default_key(travel.get("track_label") or travel.get("track_key"))
        if not key:
            continue
        location = locations.setdefault(
            key,
            {
                "location_key": key,
                "display_name": travel.get("track_label") or key,
                "planning_enabled": None,
                "meeting_count": 0,
                "first_date": "",
                "last_date": "",
                "club_names": "",
                "travel_defaults": [],
            },
        )
        location["travel_defaults"].append(travel)

    for location in locations.values():
        sources = []
        if location.get("planning_enabled") is not None:
            sources.append("Love Racing")
        for travel in location.get("travel_defaults") or []:
            source = str(travel.get("source") or "").title()
            if source and source not in sources:
                sources.append(source)
        location["source_labels"] = sources
    return sorted(locations.values(), key=lambda item: str(item.get("display_name") or "").lower())


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
    migrate_existing_track_map_aliases()
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


def love_racing_view_context(today: date) -> tuple[dict[str, object], dict[str, str]]:
    snapshot = get_love_racing_snapshot(today.isoformat())
    snapshot["upcoming"] = [
        decorate_love_racing_meeting(item)
        for item in snapshot.get("upcoming", [])
    ]
    status = {
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
    return snapshot, status


def roster_day_builder_response(request: Request, roster_day_id: int | None, notice: str | None = None) -> object:
    user = require_admin_user(request)
    settings = get_settings()
    row = get_roster_day(roster_day_id) if roster_day_id is not None else None
    if roster_day_id is not None and row is None:
        raise HTTPException(status_code=404, detail="Roster day not found")
    roster_day = dict(row) if row else {
        "id": 0,
        "roster_date": datetime.now(settings.timezone).date().isoformat(),
        "track_key": "",
        "track_label": "",
        "canonical_location_key": "",
        "title": "",
        "custom_location": "Office / Clow Place",
        "race_type": "thoroughbred",
        "day_type": "race_day",
        "start_origin": "",
        "finish_destination": "",
        "office_start": "",
        "end_time": "",
        "break_minutes": 0,
        "on_track_time": "",
        "first_race_time": "",
        "last_race_time": "",
        "race_count": None,
        "notes": "",
        "source_reference": "",
        "provenance": "manual",
        "linked_deputy_event_id": "",
        "duplicate_resolution": "keep_separate",
        "hotel_assignments": "[]",
        "status": "draft",
        "published_snapshot": "",
        "published_at": "",
    }
    assignments = [dict(item) for item in get_roster_day_assignments(int(roster_day["id"]))] if roster_day.get("id") else []
    hotel_assignments = parse_hotel_assignments(roster_day.get("hotel_assignments"))
    hotel_rows = hotel_assignments + [{} for _index in range(max(1, 3 - len(hotel_assignments)))]
    area_names = list_roster_builder_area_names()
    role_rows = [dict(item) for item in list_workday_roles(include_disabled=True)]
    if not assignments and not row:
        assignments = [
            {
                "role_key": canonical_role_key(role),
                "role_label": role,
                "assignment_state": "unused",
                "transport_mode": "unassigned",
                "sort_order": index,
            }
            for index, role in enumerate(WORKDAY_PRESETS["thoroughbred_standard"]["roles"])
        ]
    assignment_rows = assignments + ([{}] if assignments else [{}, {}])
    locations = list_roster_builder_location_labels()
    if roster_day.get("track_label") and roster_day["track_label"] not in locations:
        locations.append(str(roster_day["track_label"]))
    travel_minutes: dict[str, int] = {}
    for item in travel_default_rows():
        if canonical_travel_base_label(item.get("base_label")) != "Office / Clow Place":
            continue
        travel_minutes.setdefault(str(item.get("track_key") or ""), int(item.get("travel_minutes") or 0))
    track_options = [
        {
            "label": label,
            "key": travel_default_key(label),
            "default_race_type": default_roster_race_type(label),
            "travel_minutes": travel_minutes.get(travel_default_key(label), 0),
        }
        for label in sorted(set(locations), key=str.lower)
    ]
    current_snapshot = roster_day_snapshot(roster_day, assignments)
    published_snapshot = parse_roster_snapshot(roster_day.get("published_snapshot"))
    changes, changed_fields, changed_positions = roster_day_change_review(current_snapshot, published_snapshot)
    directory_people = [person for person in list_crew_people() if int(person.get("is_active") or 0)]
    crew_options = [
        {
            "value": f"person:{person['id']}",
            "label": str(person.get("canonical_display_name") or "Crew"),
            "user_id": person.get("app_user_id"),
            "person_id": person.get("id"),
            "meta": (
                f"Deputy #{person['deputy_employee_id']} · "
                + ("App account linked" if person.get("app_user_id") else "No app login")
                if person.get("deputy_employee_id") else ("App account linked" if person.get("app_user_id") else "Crew identity")
            ),
        }
        for person in directory_people
    ]
    duplicate_candidates = []
    candidate_location_key = calendar_location_key(
        roster_day.get("custom_location") or roster_day.get("track_label") or ""
    )
    if row and candidate_location_key:
        for deputy_row in fetch_shifts_between(str(roster_day["roster_date"]), str(roster_day["roster_date"])):
            shift = decorate_shift(deputy_row)
            if calendar_location_key(shift.get("track_label") or shift.get("location") or "") != candidate_location_key:
                continue
            duplicate_candidates.append(
                {
                    "source_id": str(shift.get("source_uid") or shift.get("id") or ""),
                    "label": f"{shift.get('track_label') or 'Deputy shift'} · {shift.get('time_range') or 'time TBC'} · {shift.get('role_label') or shift.get('title') or 'Shift'}",
                }
            )
    version_history = []
    if roster_day.get("id"):
        version_rows = [dict(item) for item in reversed(list_roster_day_versions(int(roster_day["id"])))]
        previous_version_snapshot = None
        for version_row in version_rows:
            version_snapshot = parse_roster_snapshot(version_row.get("snapshot"))
            version_changes, _fields, _positions = roster_day_change_review(
                version_snapshot or {},
                previous_version_snapshot,
            )
            version_history.append(
                {
                    **version_row,
                    "changes": version_changes,
                }
            )
            previous_version_snapshot = version_snapshot
        version_history.reverse()
    assigned_rows = [item for item in assignments if str(item.get("assignment_state") or "assigned") != "open"]
    review_summary = {
        "assigned_count": len(assigned_rows),
        "roleless_count": sum(1 for item in assigned_rows if not str(item.get("role_label") or item.get("position_label") or "").strip()),
        "open_count": sum(1 for item in assignments if str(item.get("assignment_state") or "") == "open"),
        "self_travel_count": sum(1 for item in assigned_rows if str(item.get("transport_mode") or "") == "self_travel"),
        "vehicles": list(
            dict.fromkeys(
                str(item.get("vehicle_label") or "").strip()
                for item in assigned_rows
                if str(item.get("transport_mode") or "") == "vehicle" and str(item.get("vehicle_label") or "").strip()
            )
        ),
        "unlinked_names": [
            str(item.get("person_display_name") or item.get("assignee_label") or "Crew")
            for item in assigned_rows
            if item.get("user_id") is None
        ],
    }
    return templates.TemplateResponse(
        "roster_day_builder.html",
        {
            "request": request,
            "notice": notice,
            "header_mode": "settings",
            "current_user": user,
            "roster_day": roster_day,
            "assignment_rows": assignment_rows,
            "hotel_rows": hotel_rows,
            "vehicles": roster_builder_vehicles(area_names),
            "users": [dict(item) for item in list_app_users() if int(item["is_active"] or 0)],
            "crew_options": crew_options,
            "track_options": track_options,
            "place_options": list_known_place_labels(),
            "race_types": ROSTER_RACE_TYPES,
            "workday_types": WORKDAY_TYPES,
            "workday_type_labels": WORKDAY_TYPE_LABELS,
            "transport_modes": TRANSPORT_MODES,
            "role_catalogue": role_rows,
            "active_roles": [item for item in role_rows if int(item.get("is_active") or 0)],
            "workday_presets": WORKDAY_PRESETS,
            "changes": changes,
            "changed_fields": changed_fields,
            "changed_positions": changed_positions,
            "has_published_version": published_snapshot is not None,
            "duplicate_candidates": duplicate_candidates,
            "version_history": version_history,
            "review_summary": review_summary,
        },
    )


def admin_page_context(
    request: Request,
    user: dict[str, object],
    *,
    notice: str | None = None,
    love_racing_preview: dict[str, object] | None = None,
    love_racing_backfill: dict[str, object] | None = None,
    backfill_start: str = "",
    backfill_end: str = "",
    identity_reconciliation_report: dict[str, object] | None = None,
    identity_link_review: dict[str, object] | None = None,
) -> dict[str, object]:
    settings = get_settings()
    today = datetime.now(settings.timezone).date()
    travel_defaults = travel_default_rows()
    love_racing_snapshot, love_racing_status = love_racing_view_context(
        today)
    planning_locations = list_planning_locations()
    location_rows = admin_location_rows(planning_locations, travel_defaults)
    track_map_data = track_map_admin_data()
    override_rows = []
    for row in list_admin_overrides():
        item = dict(row)
        item["field_display"] = ADMIN_OVERRIDE_FIELD_LABELS.get(
            str(item.get("field_key") or ""),
            str(item.get("label") or "Unknown field"),
        )
        item["status_display"] = {
            "active": "Active",
            "superseded": "Superseded",
            "disabled": "Disabled",
            "invalid": "Invalid / unapplied",
        }.get(str(item.get("status") or ""), "Invalid / unapplied")
        if str(item.get("field_key") or "") in DURATION_FIELDS and str(
            item.get("normalized_value") or ""
        ).isdigit():
            item["value_display"] = format_minutes_duration(int(item["normalized_value"]))
        else:
            item["value_display"] = str(
                item.get("normalized_value") or item.get("value") or ""
            )
        override_rows.append(item)
    all_crew_people = list_crew_people(include_merged=True)
    canonical_people = [item for item in all_crew_people if item.get("merged_into_person_id") is None]
    return {
        "request": request,
        "notice": notice,
        "header_mode": "settings",
        "current_user": user,
        "settings": settings,
        "app_version": APP_VERSION,
        "app_build": APP_BUILD,
        "users": admin_user_rows(),
        "roster_days": list_roster_days(),
        "overrides": override_rows,
        "active_override_count": sum(
            1 for item in override_rows if str(item.get("status") or "") == "active"
        ),
        "override_fields": [
            {"key": key, "label": label}
            for key, label in ADMIN_OVERRIDE_FIELD_LABELS.items()
        ],
        "error_reports": format_error_reports(),
        "travel_defaults": travel_defaults,
        "love_racing_snapshot": love_racing_snapshot,
        "love_racing_status": love_racing_status,
        "love_racing_detail_diagnostics": list_love_racing_detail_diagnostics(
            (today - timedelta(days=14)).isoformat(),
            (today + timedelta(days=30)).isoformat(),
        ),
        "love_racing_preview": love_racing_preview,
        "love_racing_backfill": love_racing_backfill,
        "backfill_start": backfill_start or (today - timedelta(days=14)).isoformat(),
        "backfill_end": backfill_end or (today + timedelta(days=30)).isoformat(),
        "love_racing_url": LOVE_RACING_URL,
        "planning_locations": planning_locations,
        "location_rows": location_rows,
        "track_maps": track_map_data["venues"],
        "unclassified_track_map_locations": track_map_data["unclassified"],
        "track_map_classification_decisions": track_map_data["decisions"],
        "track_map_migration_warnings": track_map_data["warnings"],
        "travel_routes": [dict(row) for row in list_travel_routes()],
        "known_places": list_known_place_labels(),
        "crew_people": canonical_people,
        "linked_crew_people": [item for item in canonical_people if int(item.get("is_active") or 0) and item.get("app_user_id")],
        "unlinked_crew_people": [item for item in canonical_people if int(item.get("is_active") or 0) and not item.get("app_user_id")],
        "merged_crew_people": [item for item in all_crew_people if item.get("merged_into_person_id") is not None],
        "identity_diagnostics": identity_link_diagnostics(),
        "identity_reconciliation_report": identity_reconciliation_report,
        "identity_link_review": identity_link_review,
        "app_users": [dict(item) for item in list_app_users()],
        "planning_location_enabled_count": sum(
            1 for location in planning_locations if int(location.get("is_enabled") or 0)
        ),
        "integrity": get_roster_integrity_diagnostics(),
    }


@app.get("/admin")
def admin_view(request: Request, notice: str | None = None) -> object:
    user = require_admin_user(request)
    return templates.TemplateResponse(
        "admin.html",
        admin_page_context(request, user, notice=notice),
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
    base_label = canonical_travel_base_label(form.get("base_label"))
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
    base_label = canonical_travel_base_label(form.get("base_label"))
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


@app.post("/admin/travel-routes")
async def admin_save_travel_route(request: Request) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    origin = str(form.get("origin_label") or "").strip()
    destination = str(form.get("destination_label") or "").strip()
    try:
        minutes = int(str(form.get("travel_minutes") or "0"))
    except ValueError:
        minutes = 0
    saved = upsert_travel_route(
        origin_label=origin,
        destination_label=destination,
        travel_minutes=minutes,
        note=str(form.get("note") or "").strip(),
        source="manual",
        also_reverse=bool(form.get("also_reverse")),
    )
    message = "Directed travel route saved." if saved else "Origin, destination, and travel minutes are required."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/travel-routes/{route_id}/delete")
def admin_delete_travel_route(request: Request, route_id: int) -> RedirectResponse:
    require_admin_user(request)
    deleted = delete_travel_route(route_id)
    return RedirectResponse(
        url=notice_url("/admin", "Travel route deleted." if deleted else "Travel route not found."),
        status_code=303,
    )


@app.post("/admin/crew/{person_id}")
async def admin_update_crew_person(request: Request, person_id: int) -> object:
    user = require_admin_user(request)
    form = await request.form()
    app_user_text = str(form.get("app_user_id") or "").strip()
    app_user_id = int(app_user_text) if app_user_text.isdigit() else None
    link_review = crew_link_change_preview(person_id, app_user_id) if app_user_id is not None else None
    if link_review is not None:
        return templates.TemplateResponse(
            "admin.html",
            admin_page_context(
                request,
                user,
                notice="Confirm how this existing account link should be corrected.",
                identity_link_review=link_review,
            ),
        )
    aliases = re.split(r"[,;\n]+", str(form.get("aliases") or ""))
    _saved, message = update_crew_person(
        person_id,
        canonical_display_name=str(form.get("canonical_display_name") or ""),
        app_user_id=app_user_id,
        aliases=aliases,
        is_active=bool(form.get("is_active")),
        admin_note=str(form.get("admin_note") or ""),
    )
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/overrides")
async def admin_create_override(request: Request) -> RedirectResponse:
    user = require_admin_user(request)
    form = await request.form()
    target_date = str(form.get("target_date") or "").strip()
    override_type = str(form.get("override_type") or "timing").strip()
    label = str(form.get("field_key") or form.get("label") or "").strip()
    value = str(form.get("value") or "").strip()
    target_track = str(form.get("target_track") or "").strip()
    if not target_date or not target_track or not label or not value:
        return RedirectResponse(
            url=notice_url("/admin", "Date, track, field, and value are required."),
            status_code=303,
        )
    try:
        create_admin_override(
            created_by_user_id=int(user["id"]),
            target_date=target_date,
            target_track=target_track,
            override_type=override_type,
            label=label,
            value=value,
            note=str(form.get("note") or "").strip(),
        )
    except ValueError as exc:
        return RedirectResponse(
            url=notice_url("/admin", f"Admin override could not be applied: {exc}"),
            status_code=303,
        )
    return RedirectResponse(
        url=notice_url("/admin", "Admin override recorded and applied."),
        status_code=303,
    )


@app.post("/admin/overrides/{override_id}/disable")
async def admin_disable_override(request: Request, override_id: int) -> RedirectResponse:
    user = require_admin_user(request)
    disabled = disable_admin_override(override_id, disabled_by_user_id=int(user["id"]))
    message = (
        "Admin override disabled. The day now uses the next available timing source."
        if disabled
        else "That override is no longer active."
    )
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/identity-reconciliation/preview")
def admin_preview_identity_reconciliation(request: Request) -> object:
    user = require_admin_user(request)
    report = reconcile_authenticated_identities(apply=False, trigger_source="admin_preview", actor_user_id=int(user["id"]))
    return templates.TemplateResponse(
        "admin.html",
        admin_page_context(request, user, identity_reconciliation_report=report),
    )


@app.post("/admin/identity-reconciliation/apply")
def admin_apply_identity_reconciliation(request: Request) -> RedirectResponse:
    user = require_admin_user(request)
    report = reconcile_authenticated_identities(apply=True, trigger_source="admin_apply", actor_user_id=int(user["id"]))
    message = (
        f"Identity reconciliation complete: {report.get('duplicate_identities_merged', 0)} merged, "
        f"{report.get('links_repaired', 0)} linked, {report.get('published_workdays_repaired', 0)} workdays repaired."
    )
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/crew-link/resolve")
async def admin_resolve_crew_link(request: Request) -> RedirectResponse:
    user = require_admin_user(request)
    form = await request.form()
    action = str(form.get("action") or "cancel")
    source_id = safe_int(form.get("source_person_id")) or 0
    target_id = safe_int(form.get("target_person_id")) or 0
    app_user_id = safe_int(form.get("app_user_id")) or 0
    try:
        if action == "merge":
            merge_crew_people(
                source_id,
                target_id,
                merged_by_user_id=int(user["id"]),
                reason="Admin confirmed duplicate app-account identity merge.",
            )
            message = "Duplicate identity merged and personal workday access rebuilt."
        elif action == "transfer":
            transfer_app_user_link(app_user_id, target_id)
            message = "App login transferred to the canonical crew identity."
        else:
            message = "Account link change cancelled."
    except (TypeError, ValueError) as exc:
        message = str(exc)
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.get("/month")
def month_view(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    view: str = "month",
    scope: str = "personal",
    notice: str | None = None,
) -> object:
    settings = get_settings()
    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    today = datetime.now(settings.timezone).date()
    view = "list" if view == "list" else "month"
    global_view = scope == "global"
    year = year or today.year
    month = month or today.month
    if month < 1 or month > 12:
        raise HTTPException(status_code=404, detail="Invalid month")

    cal = calendar.Calendar(firstweekday=0)
    month_weeks = cal.monthdatescalendar(year, month)
    grid_start = month_weeks[0][0].isoformat()
    grid_end = month_weeks[-1][-1].isoformat()
    rows = [] if global_view else fetch_shifts_between(grid_start, grid_end, owner_user_id=owner_user_id)
    schedule_role_rows = fetch_deputy_schedule_between(grid_start, grid_end)
    open_shifts_by_date = {} if global_view else open_schedule_by_date(grid_start, grid_end)
    manual_rosters_by_date = published_rosters_by_date(
        grid_start,
        grid_end,
        None if global_view else owner_user_id,
    )

    shifts_by_date: dict[str, list[dict[str, object]]] = {}
    display_rows = aggregate_global_schedule(schedule_role_rows) if global_view else [decorate_shift(row) for row in rows]
    for row in display_rows:
        shifts_by_date.setdefault(str(row["date"]), []).append(row)
    for date_key, day_shifts in list(shifts_by_date.items()):
        shifts_by_date[date_key] = day_shifts if global_view else combine_adjacent_shifts(day_shifts)
    if not global_view:
        enrich_shifts_with_love_racing(
            [shift for day_shifts in shifts_by_date.values() for shift in day_shifts],
            grid_start,
            grid_end,
        )
        apply_schedule_role_context(
            [shift for day_shifts in shifts_by_date.values() for shift in day_shifts],
            schedule_role_rows,
        )
    love_racing_by_day = {} if global_view else love_racing_by_date(grid_start, grid_end, shifts_by_date)

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
            day_manual_rosters = manual_rosters_by_date.get(day_item.isoformat(), [])
            timesheet = None if global_view else timesheet_marker(day_item)
            day_total = sum(
                shift_hours_value(shift)
                for shift in day_shifts
                if not int(shift.get("deleted_from_source") or 0)
            )
            if not global_view:
                day_total += sum(float(roster.get("hours") or 0) for roster in day_manual_rosters)
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
                    "manual_rosters": day_manual_rosters,
                    "total": day_total,
                    "timesheet": timesheet,
                    "holiday": public_holiday_context(day_item),
                }
            )
            if day_item.month == month and (day_shifts or timesheet or day_open_shifts or day_love_racing or day_manual_rosters):
                active_days.append(
                    {
                        "date": day_item,
                        "iso": day_item.isoformat(),
                        "shifts": day_shifts,
                        "open_shifts": day_open_shifts,
                        "love_racing_meetings": day_love_racing,
                        "manual_rosters": day_manual_rosters,
                        "total": day_total,
                        "timesheet": timesheet,
                        "holiday": public_holiday_context(day_item),
                    }
                )
        weeks.append({"days": days, "total": week_total})

    prev_year, prev_month = add_months(year, month, -1)
    next_year, next_month = add_months(year, month, 1)
    first_day = date(year, month, 1)
    now_iso = datetime.now(settings.timezone).replace(microsecond=0).isoformat()
    upcoming_shifts = []
    upcoming_manual_rosters = []
    if not global_view:
        upcoming_shifts = combine_adjacent_shifts(
            [decorate_shift(row) for row in get_upcoming_shifts(now_iso, limit=10, owner_user_id=owner_user_id)]
        )[:5]
        if upcoming_shifts:
            enrich_shifts_with_love_racing(
                upcoming_shifts,
                min(str(shift["date"]) for shift in upcoming_shifts),
                max(str(shift["date"]) for shift in upcoming_shifts),
            )
        apply_saved_schedule_role_context(upcoming_shifts)
        manual_upcoming_by_date = published_rosters_by_date(
            today.isoformat(),
            (today + timedelta(days=180)).isoformat(),
            owner_user_id,
        )
        upcoming_manual_rosters = sorted(
            [
                {**roster, "date": date_key}
                for date_key, rosters in manual_upcoming_by_date.items()
                for roster in rosters
            ],
            key=lambda item: (str(item.get("date") or ""), str(item.get("office_start") or "")),
        )[:5]
        for roster in upcoming_manual_rosters:
            roster["date_label"] = date.fromisoformat(str(roster["date"])).strftime("%a %d %b")

    upcoming_items = sorted(
        [
            *[{**shift, "upcoming_kind": "deputy"} for shift in upcoming_shifts],
            *[{**roster, "upcoming_kind": "manual"} for roster in upcoming_manual_rosters],
        ],
        key=lambda item: (
            str(item.get("date") or ""),
            str(item.get("office_start") or item.get("display_start_label") or item.get("start_label") or ""),
        ),
    )[:5]

    scope_query = "&scope=global" if global_view else ""

    return templates.TemplateResponse(
        "month.html",
        {
            "request": request,
            "notice": notice,
            "current_user": user,
            "header_context": first_day.strftime("%B %Y") + (" · Crew" if global_view else ""),
            "header_prev_url": f"/month?year={prev_year}&month={prev_month}&view={view}{scope_query}",
            "header_next_url": f"/month?year={next_year}&month={next_month}&view={view}{scope_query}",
            "settings": settings,
            "weeks": weeks,
            "active_days": active_days,
            "upcoming_shifts": upcoming_shifts,
            "upcoming_manual_rosters": upcoming_manual_rosters,
            "upcoming_items": upcoming_items,
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "month_name": first_day.strftime("%B %Y"),
            "month_total": month_total,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "today": today,
            "view": view,
            "global_view": global_view,
            "month_view_url": f"/month?year={year}&month={month}&view=month{scope_query}",
            "list_view_url": f"/month?year={year}&month={month}&view=list{scope_query}",
            "global_view_url": f"/month?year={year}&month={month}&view={view}&scope=global",
            "personal_view_url": f"/month?year={year}&month={month}&view={view}",
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


@app.get("/track-map/{track_key}")
def track_map_image(track_key: str) -> FileResponse:
    course_key = track_map_storage_key(track_key)
    row = get_track_map(course_key) if course_key else None
    if row is None:
        raise HTTPException(status_code=404, detail="Track map not found")
    effective = effective_track_map_file(row)
    file_name = Path(str(effective["file_name"] or "")).name
    map_dir = (Path(get_settings().data_dir) / "track_maps").resolve()
    image_path = (map_dir / file_name).resolve()
    if image_path.parent != map_dir or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Track map file not found")
    return FileResponse(
        image_path,
        media_type=str(effective["content_type"] or "image/jpeg"),
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/admin/track-maps/{track_key}/auto")
def admin_download_auto_track_map(request: Request, track_key: str) -> FileResponse:
    require_admin_user(request)
    course_key = track_map_storage_key(track_key)
    row = get_track_map(course_key) if course_key else None
    if row is None:
        raise HTTPException(status_code=404, detail="Automatic track map not found")
    file_name = Path(str(row["file_name"] or "")).name
    map_dir = (Path(get_settings().data_dir) / "track_maps").resolve()
    image_path = (map_dir / file_name).resolve()
    if not file_name or image_path.parent != map_dir or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Automatic track map file not found")
    return FileResponse(
        image_path,
        media_type=str(row["content_type"] or "image/jpeg"),
        filename=f"{course_key}-automatic{image_path.suffix.lower()}",
    )


@app.get("/admin/track-map-migration-files/{warning_id}")
def admin_download_retained_track_map(request: Request, warning_id: int) -> FileResponse:
    require_admin_user(request)
    warning = get_track_map_migration_warning(warning_id)
    if warning is None:
        raise HTTPException(status_code=404, detail="Retained track map not found")
    file_name = Path(str(warning["retained_file_name"] or "")).name
    map_dir = (Path(get_settings().data_dir) / "track_maps").resolve()
    image_path = (map_dir / file_name).resolve()
    if not file_name or image_path.parent != map_dir or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Retained track map file not found")
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(image_path.suffix.lower(), "application/octet-stream")
    return FileResponse(image_path, media_type=media_type, filename=file_name)


@app.post("/admin/track-maps/{track_key}/upload")
async def admin_upload_track_map(request: Request, track_key: str) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    track_label = str(form.get("track_label") or "").strip()
    expected_key = track_map_storage_key(track_label)
    if not track_label or expected_key != track_map_storage_key(track_key):
        return RedirectResponse(url=notice_url("/admin", "Track map upload did not match that location."), status_code=303)
    upload = form.get("image")
    if upload is None or not hasattr(upload, "read"):
        return RedirectResponse(url=notice_url("/admin", "Choose an image to upload."), status_code=303)
    content = await upload.read(MAX_MANUAL_MAP_BYTES + 1)
    try:
        saved = save_manual_track_map(track_label, content)
        message = f"Manual map uploaded for {track_label} ({saved['image_width']}×{saved['image_height']})."
    except ValueError as exc:
        message = str(exc)
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/track-maps/{track_key}/reset")
def admin_reset_track_map(request: Request, track_key: str) -> RedirectResponse:
    require_admin_user(request)
    reset = reset_manual_track_map(track_key)
    message = "Manual map removed; the automatic map is active again." if reset else "No manual map was set for that track."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/track-map-locations/{location_key}/classify")
async def admin_classify_track_map_location(request: Request, location_key: str) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    location_label = str(form.get("location_label") or "").strip()
    classification = str(form.get("classification") or "").strip().lower()
    if not location_label or calendar_location_key(location_label) != location_key:
        return RedirectResponse(
            url=notice_url("/admin", "That location no longer matches the classification request."),
            status_code=303,
        )
    canonical_key = ""
    canonical_label = ""
    if classification == "venue":
        canonical_key = location_key
        canonical_label = location_label
    elif classification == "alias":
        requested_key = str(form.get("canonical_venue_key") or "").strip()
        venues = {str(item["track_key"]): item for item in track_map_admin_data()["venues"]}
        target = venues.get(requested_key)
        if target is None:
            return RedirectResponse(
                url=notice_url("/admin", "Choose an existing racing venue for that alias."),
                status_code=303,
            )
        canonical_key = requested_key
        canonical_label = str(target["track_label"])
    elif classification != "excluded":
        return RedirectResponse(url=notice_url("/admin", "Choose a location classification."), status_code=303)
    upsert_track_map_location_rule(
        location_key=location_key,
        location_label=location_label,
        classification=classification,
        canonical_venue_key=canonical_key,
        canonical_venue_label=canonical_label,
        source="admin",
    )
    migrate_existing_track_map_aliases()
    message = {
        "venue": f"{location_label} is now a racing venue.",
        "alias": f"{location_label} now uses the {canonical_label} track map.",
        "excluded": f"{location_label} is excluded from track maps.",
    }[classification]
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/track-map-locations/{location_key}/reset")
def admin_reset_track_map_location_classification(request: Request, location_key: str) -> RedirectResponse:
    require_admin_user(request)
    delete_track_map_location_rule(location_key)
    return RedirectResponse(
        url=notice_url("/admin", "Location classification reset to automatic."),
        status_code=303,
    )


@app.get("/day/{date_text}")
def day_view(
    request: Request,
    date_text: str,
    notice: str | None = None,
    scope: str = "personal",
    location_id: int | None = None,
    manual_id: int | None = None,
) -> object:
    try:
        day_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invalid date") from exc

    user = current_user(request)
    owner_user_id = int(user["id"]) if user and user.get("id") is not None else None
    global_view = scope == "global"
    if global_view:
        global_manual_rosters = published_rosters_by_date(date_text, date_text, None).get(date_text, [])
        selected_manual_rosters = [item for item in global_manual_rosters if manual_id is not None and int(item.get("id") or 0) == manual_id]
        global_events = aggregate_global_schedule(fetch_deputy_schedule_between(date_text, date_text))
        selected_event = next(
            (
                event
                for event in global_events
                if location_id is not None and int(event.get("schedule_location_id") or 0) == location_id
            ),
            None,
        )
        if selected_event is None and not selected_manual_rosters and len(global_events) == 1:
            selected_event = global_events[0]
        selected_location_id = int(selected_event.get("schedule_location_id") or 0) if selected_event else 0
        global_schedule_rows = (
            fetch_deputy_schedule_for_date(date_text, location_ids=[selected_location_id])
            if selected_location_id
            else []
        )
        global_schedule_people = schedule_people(
            global_schedule_rows,
            expected_areas=(
                fetch_deputy_schedule_areas_for_locations({selected_location_id})
                if selected_location_id
                else []
            ),
        )
        reconcile_personal_assignment_evidence(
            global_schedule_people,
            fetch_personal_assignment_evidence_for_date(
                date_text, [selected_location_id] if selected_location_id else None
            ),
            event_start_at=selected_event.get("start_at") if selected_event else None,
            event_end_at=selected_event.get("end_at") if selected_event else None,
        )
        for person in global_schedule_people:
            person["changed"] = False
        return templates.TemplateResponse(
            "day.html",
            {
                "request": request,
                "notice": notice,
                "current_user": user,
                "date_text": date_text,
                "day_date": day_date,
                "day_holiday": public_holiday_context(day_date),
                "month_year": day_date.year,
                "month_number": day_date.month,
                "back_to_month_url": f"/month?year={day_date.year}&month={day_date.month}&scope=global",
                "calendar_home_url": f"/month?year={day_date.year}&month={day_date.month}&scope=global",
                "global_view": True,
                "global_events": global_events,
                "global_event": selected_event,
                "shifts": [],
                "open_shifts": [],
                "planning_meetings": [],
                "manual_rosters": selected_manual_rosters,
                "track_maps": track_maps_for_day([], selected_manual_rosters),
                "deputy_schedule_people": global_schedule_people,
                "deputy_schedule_label": (
                    f"Deputy Schedule - {selected_event['track_label']}" if selected_event else "Deputy Schedule"
                ),
                "deputy_schedule_changed": False,
                "deputy_schedule_changes": [],
                "deputy_event_changes": [],
                "deputy_event_change_groups": [],
                "deputy_assignment_history": [],
                "day_total": sum(float(item.get("hours") or 0) for item in selected_manual_rosters),
                "has_changed": False,
                "mark_fields": MARK_FIELDS,
            },
        )
    manual_rosters = published_rosters_by_date(date_text, date_text, owner_user_id).get(date_text, [])
    shifts = combine_adjacent_shifts(
        [decorate_shift(row) for row in fetch_shifts_for_date(date_text, owner_user_id=owner_user_id)]
    )
    enrich_shifts_with_love_racing(shifts, date_text, date_text)


    open_shifts = open_schedule_by_date(date_text, date_text).get(date_text, [])
    planning_meetings = love_racing_by_date(
        date_text,
        date_text,
        {date_text: shifts},
    ).get(date_text, [])
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
        merge_description_change_lines(shift)
        latest_change_at = latest_iso_datetime(
            shift.get("last_changed_at"),
            *(change.get("changed_at") for change in shift.get("changes") or []),
        )
        if latest_change_at:
            shift["change_time_label"] = format_datetime(latest_change_at, "%d %b %H:%M")
            shift["change_badge_label"] = f"Changed · {shift['change_time_label']}"
        shift["change_summary_text"] = build_shift_change_summary(list(shift.get("changes") or []))
    schedule_location_ids = shift_schedule_location_ids(shifts)
    travel_schedule_context = shifts_are_vehicle_travel_context(shifts)
    schedule_expected_areas = [] if travel_schedule_context else fetch_deputy_schedule_areas_for_locations(schedule_location_ids)
    deputy_schedule_label = deputy_schedule_label_for_shifts(
        "Travel / Vehicles" if travel_schedule_context else "Deputy Schedule",
        shifts,
    )
    deputy_schedule_rows = fetch_deputy_schedule_for_date(
        date_text,
        location_ids=schedule_location_ids or None,
    )
    deputy_schedule_people = schedule_people(
        deputy_schedule_rows,
        expected_areas=schedule_expected_areas,
        include_vehicle_only=travel_schedule_context,
        include_placeholders=not travel_schedule_context,
    )
    reconcile_personal_assignment_evidence(
        deputy_schedule_people,
        fetch_personal_assignment_evidence_for_date(
            date_text, schedule_location_ids or None
        ),
    )
    apply_schedule_role_context(shifts, deputy_schedule_rows)
    apply_roster_note_vehicles(deputy_schedule_people, shifts)
    if travel_schedule_context:
        show_vehicle_assignment_as_travel(shifts)
    elif schedule_location_ids:
        previous_day_text = (day_date - timedelta(days=1)).isoformat()
        previous_day_rows = fetch_deputy_schedule_for_date(
            previous_day_text,
            location_ids=schedule_location_ids,
        )
        if schedule_rows_are_vehicle_travel_context(previous_day_rows):
            previous_day_vehicle_people = schedule_people(
                previous_day_rows,
                include_vehicle_only=True,
                include_placeholders=False,
            )
            apply_vehicle_carryover_from_people(deputy_schedule_people, previous_day_vehicle_people)
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
        reconcile_personal_assignment_evidence(
            deputy_schedule_people,
            fetch_personal_assignment_evidence_for_date(
                next_day_text, next_day_location_ids or None
            ),
        )
        apply_roster_note_vehicles(deputy_schedule_people, next_day_shifts)
        if deputy_schedule_people:
            deputy_schedule_label = deputy_schedule_label_for_shifts("Deputy Schedule - Next Day Crew", next_day_shifts)
    deputy_event_changes = decorate_event_changes(
        fetch_deputy_event_changes_for_date(
            date_text,
            location_ids=schedule_location_ids or None,
        )
    )
    deputy_event_change_groups = group_event_changes(deputy_event_changes)
    if deputy_schedule_rows:
        apply_event_changes_to_schedule_people(deputy_schedule_people, deputy_event_change_groups)
    for shift in shifts:
        if str(shift.get("header_vehicle_label") or "").strip():
            continue
        shift["header_vehicle_label"] = vehicle_for_user_from_schedule(deputy_schedule_people, user, shift)
    deputy_schedule_changed = any(bool(person.get("changed")) for person in deputy_schedule_people)
    deputy_schedule_changes = [person for person in deputy_schedule_people if person.get("changed")]
    change_identities = crew_identity_records()
    deputy_assignment_history = [
        {
            "position_label": str(row["position_label"] or "Position"),
            "old_employee_name": canonical_crew_name(
                row["old_employee_name"], identities=change_identities
            ),
            "new_employee_name": canonical_crew_name(
                row["new_employee_name"], identities=change_identities
            ),
            "changed_at": row["changed_at"],
            "changed_at_label": format_datetime(row["changed_at"], "%d %b %H:%M"),
        }
        for row in fetch_deputy_assignment_history_for_date(
            date_text,
            location_ids=schedule_location_ids or None,
        )
    ]
    event_assignments = {
        (schedule_label_key(position), schedule_label_key(str(item.get("new_employee_name") or "TBC")))
        for item in deputy_event_changes
        for position in list(item.get("new_positions") or [])
    }
    deputy_assignment_history = [
        item
        for item in deputy_assignment_history
        if (
            schedule_label_key(item["position_label"]),
            schedule_label_key(item["new_employee_name"]),
        ) not in event_assignments
    ]
    historical_assignments = {
        (schedule_label_key(item["position_label"]), schedule_label_key(item["new_employee_name"]))
        for item in deputy_assignment_history
    } | event_assignments
    deputy_schedule_changes = [
        person
        for person in deputy_schedule_changes
        if (
            schedule_label_key(str(person.get("position_label") or "")),
            schedule_label_key(str(person.get("employee_name") or "")),
        )
        not in historical_assignments
    ]
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
            "day_holiday": public_holiday_context(day_date),
            "month_year": day_date.year,
            "month_number": day_date.month,
            "back_to_month_url": f"/month?year={day_date.year}&month={day_date.month}",
            "calendar_home_url": f"/month?year={day_date.year}&month={day_date.month}",
            "global_view": False,
            "global_events": [],
            "global_event": None,
            "shifts": shifts,
            "open_shifts": open_shifts,
            "planning_meetings": planning_meetings,
            "manual_rosters": manual_rosters,
            "track_maps": track_maps_for_day(shifts, manual_rosters),
            "deputy_schedule_people": deputy_schedule_people,
            "deputy_schedule_label": deputy_schedule_label,
            "deputy_schedule_changed": deputy_schedule_changed,
            "deputy_schedule_changes": deputy_schedule_changes,
            "deputy_event_changes": deputy_event_changes,
            "deputy_event_change_groups": deputy_event_change_groups,
            "deputy_assignment_history": deputy_assignment_history,
            "day_total": day_total,
            "has_changed": has_changed,
            "mark_fields": MARK_FIELDS,
        },
    )


@app.get("/admin/roster-days/new")
def admin_new_roster_day(request: Request, notice: str | None = None) -> object:
    return roster_day_builder_response(request, None, notice)


@app.get("/admin/roster-days/{roster_day_id}")
def admin_edit_roster_day(request: Request, roster_day_id: int, notice: str | None = None) -> object:
    return roster_day_builder_response(request, roster_day_id, notice)


@app.post("/admin/workday-roles/save")
async def admin_save_workday_role(request: Request) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    display_label = str(form.get("display_label") or "").strip()
    role_key = str(form.get("role_key") or display_label).strip()
    aliases = [value.strip() for value in str(form.get("aliases") or "").split(",") if value.strip()]
    order_text = str(form.get("display_order") or "999999").strip()
    try:
        save_workday_role(
            role_key=role_key,
            display_label=display_label,
            aliases=aliases,
            display_order=int(order_text) if order_text.lstrip("-").isdigit() else 999999,
            is_active=str(form.get("is_active") or "") == "1" if form.get("role_key") else True,
        )
        message = "Reusable role saved."
    except ValueError as exc:
        message = str(exc)
    referer = str(request.headers.get("referer") or "")
    referer_path = urlsplit(referer).path
    return_path = referer_path if referer_path.startswith("/admin/roster-days/") else "/admin/roster-days/new"
    return RedirectResponse(url=notice_url(return_path, message), status_code=303)


@app.post("/admin/roster-days/save")
async def admin_save_roster_day(request: Request) -> RedirectResponse:
    user = require_admin_user(request)
    form = await request.form()
    roster_day_id_text = str(form.get("roster_day_id") or "").strip()
    roster_day_id = int(roster_day_id_text) if roster_day_id_text.isdigit() else None
    roster_date = str(form.get("roster_date") or "").strip()
    try:
        date.fromisoformat(roster_date)
    except ValueError:
        return RedirectResponse(url=notice_url("/admin/roster-days/new", "Choose a valid date."), status_code=303)
    day_type = str(
        form.get("day_type")
        or ("travel_day" if str(form.get("is_travel_day") or "") == "1" else "race_day")
    ).strip()
    if day_type not in WORKDAY_TYPE_LABELS:
        day_type = "race_day"
    title = str(form.get("title") or "").strip()[:200]
    custom_location = str(form.get("custom_location") or "").strip()[:200]
    track_label = str(form.get("new_track_label") or form.get("track_label") or custom_location or "").strip()
    race_type = str(form.get("race_type") or "").strip()
    if day_type == "race_day" and (not track_label or race_type not in ROSTER_RACE_TYPE_LABELS):
        target = f"/admin/roster-days/{roster_day_id}" if roster_day_id else "/admin/roster-days/new"
        return RedirectResponse(url=notice_url(target, "Track and race type are required."), status_code=303)
    if day_type != "race_day":
        track_label = custom_location or track_label or "Office / Clow Place"
        race_type = ""
        title = title or WORKDAY_TYPE_LABELS[day_type]
    times = {key: clean_time_value(str(form.get(key) or "")) for key in ("office_start", "end_time", "on_track_time", "first_race_time", "last_race_time")}
    race_count_text = str(form.get("race_count") or "").strip()
    race_count = int(race_count_text) if race_count_text.isdigit() else None
    if race_count is not None and not 1 <= race_count <= 50:
        race_count = None
    active_users = {int(item["id"]): dict(item) for item in list_app_users() if int(item["is_active"] or 0)}
    directory_people = {f"person:{person['id']}": person for person in list_crew_people() if int(person.get("is_active") or 0)}
    directory_by_user = {int(person["app_user_id"]): person for person in directory_people.values() if person.get("app_user_id")}
    hotel_user_ids = list(form.getlist("hotel_user_id"))
    hotel_names = list(form.getlist("hotel_name"))
    hotel_assignments = []
    seen_hotel_users = set()
    for index, hotel_user_value in enumerate(hotel_user_ids):
        hotel_user_text = str(hotel_user_value or "").strip()
        hotel_name = str(hotel_names[index] if index < len(hotel_names) else "").strip()
        if not hotel_user_text.isdigit() or not hotel_name:
            continue
        hotel_user_id = int(hotel_user_text)
        if hotel_user_id not in active_users or hotel_user_id in seen_hotel_users:
            continue
        seen_hotel_users.add(hotel_user_id)
        hotel_assignments.append({
            "user_id": hotel_user_id,
            "assignee_label": str(active_users[hotel_user_id].get("display_name") or active_users[hotel_user_id].get("deputy_email") or "Crew"),
            "hotel_name": hotel_name[:200],
        })
    role_labels = list(form.getlist("role_label")) or list(form.getlist("position_label"))
    role_keys = list(form.getlist("role_key"))
    assignees = list(form.getlist("assignee"))
    assignment_states = list(form.getlist("assignment_state"))
    transport_modes = list(form.getlist("transport_mode"))
    vehicles = list(form.getlist("vehicle_label"))
    custom_transports = list(form.getlist("custom_transport_text"))
    assignment_notes = list(form.getlist("assignment_note"))
    save_roles = set(str(value) for value in form.getlist("save_role_index"))
    role_catalogue = [dict(item) for item in list_workday_roles(include_disabled=True)]
    role_aliases: dict[str, str] = {}
    for role in role_catalogue:
        role_key = str(role.get("role_key") or "")
        role_aliases[normalise_role_key(role.get("display_label"))] = role_key
        try:
            aliases = json.loads(str(role.get("aliases") or "[]"))
        except (TypeError, ValueError):
            aliases = []
        for alias in aliases if isinstance(aliases, list) else []:
            role_aliases[normalise_role_key(alias)] = role_key
    assignments: list[dict[str, object]] = []
    seen_person_roles: set[tuple[int, str]] = set()
    for index, role_value in enumerate(role_labels):
        role_label = str(role_value or "").strip()[:100]
        submitted_role_key = str(role_keys[index] if index < len(role_keys) else "").strip()
        assignee = str(assignees[index] if index < len(assignees) else "").strip()
        state = str(assignment_states[index] if index < len(assignment_states) else "assigned").strip()
        state = "open" if state == "open" else "assigned"
        transport_mode = str(
            transport_modes[index]
            if index < len(transport_modes)
            else ("vehicle" if index < len(vehicles) and str(vehicles[index] or "").strip() else "unassigned")
        ).strip()
        if transport_mode not in TRANSPORT_LABELS:
            transport_mode = "unassigned"
        vehicle = str(vehicles[index] if index < len(vehicles) else "").strip()
        custom_transport = str(custom_transports[index] if index < len(custom_transports) else "").strip()[:200]
        assignment_note = str(assignment_notes[index] if index < len(assignment_notes) else "").strip()[:500]
        if state == "open" and not role_label:
            continue
        if state != "open" and not assignee:
            continue
        user_id = int(assignee) if assignee.isdigit() and int(assignee) in active_users else None
        if user_id is not None:
            directory_person = directory_by_user.get(user_id)
        else:
            directory_person = directory_people.get(assignee)
        person_id = int(directory_person["id"]) if directory_person else None
        if state != "open" and user_id is None and directory_person is None:
            continue
        if directory_person and user_id is None and directory_person.get("app_user_id") in active_users:
            user_id = int(directory_person["app_user_id"])
        role_key = canonical_role_key(submitted_role_key or role_label, role_aliases) if role_label else ""
        if state != "open" and person_id is not None:
            identity_key = (person_id, role_key)
            if identity_key in seen_person_roles:
                continue
            seen_person_roles.add(identity_key)
        if role_label and str(index) in save_roles:
            save_workday_role(
                role_key=role_key,
                display_label=role_label,
                aliases=[],
                display_order=1000 + index,
                is_active=True,
            )
        if transport_mode != "vehicle":
            vehicle = ""
        if transport_mode != "custom":
            custom_transport = ""
        assignments.append({
            "person_id": person_id,
            "user_id": user_id,
            "assignee_label": (
                "TBC" if state == "open"
                else str(active_users[user_id].get("display_name") or active_users[user_id].get("deputy_email") or "Crew") if user_id is not None
                else str(directory_person.get("canonical_display_name") or "Crew")
            ),
            "role_key": role_key,
            "role_label": role_label,
            "assignment_state": state,
            "transport_mode": transport_mode,
            "vehicle_key": normalise_role_key(vehicle),
            "vehicle_label": vehicle,
            "custom_transport_text": custom_transport,
            "assignment_note": assignment_note,
            "sort_order": index,
        })
    track_key, clean_track_label = canonical_travel_track(track_label, track_label)
    saved_id = save_roster_day(
        roster_day_id=roster_day_id,
        roster_date=roster_date,
        track_key=track_key,
        track_label=clean_track_label,
        race_type=race_type,
        day_type=day_type,
        start_origin=str(form.get("start_origin") or "").strip()[:200],
        finish_destination=str(form.get("finish_destination") or "").strip()[:200],
        office_start=times["office_start"],
        on_track_time=times["on_track_time"],
        first_race_time=times["first_race_time"],
        last_race_time=times["last_race_time"],
        race_count=race_count,
        notes=str(form.get("notes") or "").strip()[:4000],
        hotel_assignments=json.dumps(hotel_assignments, separators=(",", ":")),
        title=title,
        custom_location=custom_location or track_label,
        end_time=times["end_time"],
        break_minutes=int(str(form.get("break_minutes") or "0")) if str(form.get("break_minutes") or "0").isdigit() else 0,
        source_reference=str(form.get("source_reference") or "").strip()[:500],
        provenance="manual",
        linked_deputy_event_id=str(form.get("linked_deputy_event_id") or "").strip()[:200],
        duplicate_resolution=str(form.get("duplicate_resolution") or "keep_separate")[:30],
        updated_by_user_id=int(user["id"]),
        assignments=assignments,
    )
    return RedirectResponse(url=notice_url(f"/admin/roster-days/{saved_id}", "Draft saved. Review highlighted changes, then publish when ready."), status_code=303)


@app.post("/admin/roster-days/{roster_day_id}/publish")
def admin_publish_roster_day(request: Request, roster_day_id: int) -> RedirectResponse:
    user = require_admin_user(request)
    row = get_roster_day(roster_day_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Roster day not found")
    assignments = [dict(item) for item in get_roster_day_assignments(roster_day_id)]
    if not assignments and not parse_hotel_assignments(row["hotel_assignments"]):
        return RedirectResponse(url=notice_url(f"/admin/roster-days/{roster_day_id}", "Add at least one person or deliberate open role before publishing."), status_code=303)
    day_type = str(row["day_type"] or "race_day")
    if day_type not in WORKDAY_TYPE_LABELS:
        return RedirectResponse(url=notice_url(f"/admin/roster-days/{roster_day_id}", "Choose a valid day type before publishing."), status_code=303)
    if day_type == "race_day" and (not str(row["track_label"] or "").strip() or str(row["race_type"] or "") not in ROSTER_RACE_TYPE_LABELS):
        return RedirectResponse(url=notice_url(f"/admin/roster-days/{roster_day_id}", "Race days need a track and race type before publishing."), status_code=303)
    if day_type != "race_day":
        start = clean_time_value(str(row["office_start"] or ""))
        finish = clean_time_value(str(row["end_time"] or ""))
        if not start or not finish or manual_workday_hours(dict(row)) <= 0:
            return RedirectResponse(url=notice_url(f"/admin/roster-days/{roster_day_id}", "This work day needs a valid start and finish time before publishing."), status_code=303)
    snapshot = roster_day_snapshot(dict(row), assignments)
    version = publish_roster_day(roster_day_id, json.dumps(snapshot, separators=(",", ":")), int(user["id"]))
    return RedirectResponse(url=notice_url(f"/admin/roster-days/{roster_day_id}", f"Roster version {version} published to assigned crew."), status_code=303)


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
    user_sync_state = get_user_sync_state(owner_user_id) if owner_user_id is not None else None
    account_user = get_app_user(owner_user_id) if owner_user_id is not None else None
    account_secret = get_deputy_user_secret(owner_user_id) if owner_user_id is not None else None
    user_last_sync_at = str(user_sync_state["last_sync_at"] or "") if user_sync_state else ""
    raw_theme = user["display_theme"] if user and user.get("display_theme") else request.cookies.get("roster_theme", "jade")
    current_theme = normalise_theme(raw_theme)
    next_shift_display = decorate_shift(next_shift) if next_shift else None
    if next_shift_display:
        enrich_shifts_with_love_racing(
            [next_shift_display],
            str(next_shift_display["date"]),
            str(next_shift_display["date"]),
        )
        apply_saved_schedule_role_context([next_shift_display])
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
            "next_shift": next_shift_display,
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
            "open_schedule_shifts": visible_open_schedule_shifts(),
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


@app.post("/admin/love-racing-refresh")
def admin_refresh_love_racing_calendar(request: Request) -> RedirectResponse:
    require_admin_user(request)
    result = refresh_planning_calendar()
    return RedirectResponse(url=notice_url("/admin", str(result["message"])), status_code=303)


def unresolved_race_day_candidates(
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, object]], int, int]:
    details = {
        (str(row["meeting_date"]), str(row["canonical_venue_key"])): dict(row)
        for row in fetch_love_racing_details_between(start_date, end_date)
    }
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in fetch_shifts_between(start_date, end_date, owner_user_id=None):
        shift = decorate_shift(row)
        if str(shift.get("race_type_label") or "") != "Thoroughbred racing":
            continue
        venue_label = str(
            shift.get("track_label") or shift.get("location_label") or ""
        ).strip()
        venue_key = calendar_location_key(venue_label)
        shift_date = str(shift.get("date") or "")
        if not shift_date or not venue_key:
            continue
        summary = shift.get("roster_summary") if isinstance(shift.get("roster_summary"), dict) else {}
        timings = timing_lookup(summary)
        deputy_count = deputy_race_count(summary)
        deputy_first = timings.get("first race", "")
        deputy_last = timings.get("last race", "")
        user_last = (
            clean_time_value(str(shift.get("timing_adjustment_time") or ""))
            if int(shift.get("timing_adjustment_last_race") or 0)
            else ""
        )
        key = (shift_date, venue_key)
        item = grouped.setdefault(
            key,
            {
                "date": shift_date,
                "venue_key": venue_key,
                "venue_label": venue_label,
                "deputy_race_count": None,
                "deputy_first_race": "",
                "deputy_last_race": "",
                "user_last_race": "",
            },
        )
        if item["deputy_race_count"] is None and deputy_count is not None:
            item["deputy_race_count"] = deputy_count
        item["deputy_first_race"] = item["deputy_first_race"] or deputy_first
        item["deputy_last_race"] = item["deputy_last_race"] or deputy_last
        item["user_last_race"] = item["user_last_race"] or user_last

    candidates: list[dict[str, object]] = []
    already_complete = 0
    for key, item in sorted(grouped.items()):
        detail = details.get(key, {})
        count = item["deputy_race_count"] or safe_int(detail.get("race_count"))
        first_race = item["deputy_first_race"] or str(detail.get("first_race_time") or "")
        last_race = (
            item["user_last_race"]
            or item["deputy_last_race"]
            or str(detail.get("last_race_time") or "")
        )
        if count and first_race and last_race:
            already_complete += 1
        else:
            candidates.append(item)
    return candidates, len(grouped), already_complete


def format_race_time_backfill(
    rows: list[dict[str, object]],
    *,
    checked: int,
    already_complete: int,
) -> dict[str, object]:
    enriched = 0
    unmatched = 0
    for row in rows:
        filled: list[str] = []
        preserved: list[str] = []
        if row.get("deputy_race_count") is not None:
            preserved.append("Deputy race count")
        elif row.get("love_race_count"):
            filled.append("Race count")
        if row.get("deputy_first_race"):
            preserved.append("Deputy first race")
        elif row.get("love_first_race"):
            filled.append("First race")
        if row.get("user_last_race"):
            preserved.append("User last race")
        elif row.get("deputy_last_race"):
            preserved.append("Deputy last race")
        elif row.get("love_last_race"):
            filled.append("Last race")
        row["fields_filled"] = ", ".join(filled) or "None"
        row["fields_preserved"] = ", ".join(preserved) or "None"
        if filled:
            enriched += 1
        if row.get("match_status") in {"Unmatched", "Fetch failed"}:
            unmatched += 1
    return {
        "rows": rows,
        "checked": checked,
        "already_complete": already_complete,
        "enriched": enriched,
        "unmatched": unmatched,
        "message": (
            f"Checked {checked} meeting{'s' if checked != 1 else ''} · "
            f"{already_complete} already complete · {enriched} enriched · "
            f"{unmatched} unmatched"
            if checked
            else "No thoroughbred meetings were found in the selected date range."
        ),
    }


@app.post("/admin/love-racing-preview")
async def admin_preview_love_racing_meeting(request: Request) -> object:
    user = require_admin_user(request)
    form = await request.form()
    reference = str(form.get("meeting_reference") or "").strip()
    expected_date = str(form.get("expected_date") or "").strip()
    expected_venue = str(form.get("expected_venue") or "").strip()
    preview = await run_in_threadpool(
        preview_love_racing_meeting,
        reference,
        expected_date=expected_date,
        expected_venue=expected_venue,
    )
    return templates.TemplateResponse(
        "admin.html",
        admin_page_context(
            request,
            user,
            love_racing_preview=preview,
        ),
    )


@app.post("/admin/love-racing-unresolved-refresh")
async def admin_refresh_unresolved_race_times(request: Request) -> object:
    user = require_admin_user(request)
    form = await request.form()
    start_text = str(form.get("start_date") or "").strip()
    end_text = str(form.get("end_date") or "").strip()
    try:
        start_day = date.fromisoformat(start_text)
        end_day = date.fromisoformat(end_text)
    except ValueError:
        return templates.TemplateResponse(
            "admin.html",
            admin_page_context(
                request,
                user,
                notice="Choose a valid start and end date.",
                backfill_start=start_text,
                backfill_end=end_text,
            ),
        )
    if end_day < start_day or (end_day - start_day).days > 120:
        return templates.TemplateResponse(
            "admin.html",
            admin_page_context(
                request,
                user,
                notice="Choose a date range of no more than 120 days, with the start before the end.",
                backfill_start=start_text,
                backfill_end=end_text,
            ),
        )
    candidates, checked, already_complete = unresolved_race_day_candidates(
        start_text,
        end_text,
    )
    refreshed = (
        await run_in_threadpool(refresh_unresolved_race_days, candidates)
        if candidates
        else []
    )
    report = format_race_time_backfill(
        refreshed,
        checked=checked,
        already_complete=already_complete,
    )
    if checked and not candidates:
        report["message"] = (
            f"Checked {checked} meeting{'s' if checked != 1 else ''} · "
            f"{already_complete} already complete · 0 enriched · 0 unmatched"
        )
    elif not candidates:
        report["message"] = (
            "No unresolved thoroughbred meetings were found in the selected date range."
        )
    return templates.TemplateResponse(
        "admin.html",
        admin_page_context(
            request,
            user,
            love_racing_backfill=report,
            backfill_start=start_text,
            backfill_end=end_text,
        ),
    )


@app.post("/admin/love-racing-times-refresh")
def admin_refresh_love_racing_times(
    request: Request,
    background_tasks: BackgroundTasks,
) -> RedirectResponse:
    require_admin_user(request)
    queued = queue_due_love_racing_details(
        manual=True,
        reason="admin refresh",
        horizon_days=7,
    )
    if queued["eligible"]:
        background_tasks.add_task(run_love_racing_detail_jobs)
        message = (
            f"Race-time refresh queued for {queued['eligible']} upcoming "
            f"meeting{'s' if queued['eligible'] != 1 else ''}."
        )
    else:
        message = "No unresolved thoroughbred meetings were found in the selected date range."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/track-maps-refresh")
def admin_refresh_track_maps(request: Request) -> RedirectResponse:
    require_admin_user(request)
    result = refresh_track_maps()
    message = (
        f"Track maps checked: {result['checked']}; upgraded {result.get('upgraded', 0)}; "
        f"downloaded {result['downloaded']}; unchanged {result['unchanged']}; "
        f"unavailable {result.get('unavailable', 0)}; failed {result['failed']}."
    )
    errors = list(result.get("errors") or [])
    if errors:
        message += f" First error: {errors[0]}"
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.post("/admin/planning-locations")
async def admin_update_planning_location(request: Request) -> RedirectResponse:
    require_admin_user(request)
    form = await request.form()
    location_key = str(form.get("location_key") or "").strip()
    enabled = str(form.get("enabled") or "").strip() == "1"
    if not set_planning_location_enabled(location_key, enabled):
        message = "Planning location could not be found. Refresh the planning calendar and try again."
    else:
        message = "Planning location included." if enabled else "Planning location ignored."
    return RedirectResponse(url=notice_url("/admin", message), status_code=303)


@app.get("/admin/users/{user_id}/diagnostics.txt")
def admin_user_diagnostics(request: Request, user_id: int) -> PlainTextResponse:
    require_admin_user(request)
    latest_capture = get_latest_deputy_web_capture_for_user(user_id)
    if latest_capture is None:
        raise HTTPException(status_code=404, detail="No Deputy diagnostics saved for this user")
    payload = format_capture_payload(str(latest_capture["payload"] or ""))
    if payload is None:
        raise HTTPException(status_code=404, detail="Deputy diagnostics could not be read")
    return PlainTextResponse(
        str(payload.get("copy_text") or "No diagnostic text was generated."),
        headers={"Cache-Control": "private, no-store"},
    )


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
