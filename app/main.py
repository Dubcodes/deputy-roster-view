from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import require_auth
from .config import get_settings
from .database import (
    clear_all_changed_flags,
    clear_changed_for_date,
    clear_changed_for_shift,
    fetch_deputy_schedule_for_date,
    get_calendar_url,
    get_calendar_url_source,
    get_shift_changes_for_date,
    get_last_deputy_web_capture,
    fetch_shift,
    fetch_shifts_between,
    fetch_shifts_for_date,
    get_last_successful_sync,
    get_next_upcoming_shift,
    get_recent_source_payloads,
    get_recent_sync_logs,
    get_upcoming_shifts,
    init_db,
    update_app_settings,
    update_shift_marks,
)
from .deputy_api import test_deputy_roster_api
from .deputy_web import capture_and_save_deputy_web, format_capture_payload
from .scheduler import get_pre_shift_status, shutdown_scheduler, start_scheduler, sync_roster_sources


APP_DIR = Path(__file__).resolve().parent
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
SECRET_URL_RE = re.compile(r"(calendar\?ap=)[^&\s\"']+")
URL_RE = re.compile(r"https?://\S+")
SUMMARY_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
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
    "TARO": "Te Aroha",
    "TARO-T": "Te Aroha",
    "TAUR": "Tauranga",
    "TAUR-T": "Tauranga",
    "TRAP": "Te Rapa",
    "TRAP-T": "Te Rapa",
    "T-R": "Rotorua",
    "VEH": "Vehicles",
}
RACE_TYPES = {
    "T": "Thoroughbred racing",
    "H": "Harness racing",
}
ROLE_NAMES = {
    "DIR": "Director",
    "SOUND": "Sound",
    "SVT": "Sound VT",
}
DEFAULT_RACE_TYPE_BY_CODE = {
    "CAM": "H",
}
CHANGE_FIELD_LABELS = {
    "title": "Roster title",
    "description": "Roster notes",
    "location": "Location",
    "start_at": "Start time",
    "end_at": "End time",
    "raw_hours": "Raw hours",
    "break_minutes": "Break",
    "paid_hours": "Hours",
    "source_link": "Deputy link",
    "source_status": "Status",
    "deleted_from_source": "Cancelled",
}
TIMING_LINE_PATTERNS = (
    ("Trucks", re.compile(r"^trucks?\s+(.+)$", re.IGNORECASE)),
    ("Office", re.compile(r"^office\s+(.+)$", re.IGNORECASE)),
    ("Clow Place", re.compile(r"^clow\s+place\s+(.+)$", re.IGNORECASE)),
    ("On track", re.compile(r"^on\s+track\s+(.+)$", re.IGNORECASE)),
    ("First cross", re.compile(r"^first\s+cross\s+(.+)$", re.IGNORECASE)),
)
INLINE_TIMING_RE = re.compile(r"\b(first race|last race|first cross)\s+([0-9: ]{3,5}\s*(?:am|pm)?)", re.IGNORECASE)
RACE_COUNT_RE = re.compile(r"\b(\d+)\s+races?\b", re.IGNORECASE)
RACE_COUNT_WITH_TIMES_RE = re.compile(
    r"\b(\d+)\s+races?\s+([0-9: ]{3,5}\s*(?:am|pm)?)\s*[-–]\s*([0-9: ]{3,5}\s*(?:am|pm)?)",
    re.IGNORECASE,
)
CREW_LINE_RE = re.compile(r"^([A-Za-z]{1,8}\d{0,3}|\d{3,4})\s+(.+)$")
NON_CREW_LABELS = {"office", "trucks", "truck", "clow", "on", "first", "last", "race", "races", "breaks"}
TIMING_LABELS = {
    "first cross": "First cross",
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


app = FastAPI(
    title="Deputy Roster View",
    dependencies=[Depends(require_auth)],
)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


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


def redact_secret_text(value: str) -> str:
    return SECRET_URL_RE.sub(r"\1[redacted]", value)


def description_lines(description: str) -> list[str]:
    lines = []
    for line in (description or "").splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue
        clean_line = re.split(r"(?i)\bbreaks:\s*", clean_line)[0].strip()
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


def role_chain_label(segments: list[dict[str, str]]) -> str:
    labels = []
    for segment in segments:
        if segment.get("kind") == "vehicle":
            continue
        role = segment.get("role") or "Shift"
        if not labels or labels[-1] != role:
            labels.append(role)
    if not labels:
        labels = [segment.get("role") or "Shift" for segment in segments]
    return " -> ".join(labels)


def role_is_vehicleish(role_label: str | None) -> bool:
    normalised = re.sub(r"\s+", "", (role_label or "").strip().upper())
    if not normalised:
        return False
    return bool(re.fullmatch(r"\d{3,4}", normalised) or re.fullmatch(r"RAV\d+", normalised) or normalised in VEHICLE_ROLE_LABELS)


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


def decorate_shift(row: object) -> dict[str, object]:
    shift = dict(row)
    parsed_title = parse_shift_title(str(shift.get("title") or ""))
    start_at = parse_iso_datetime(shift.get("start_at"))
    end_at = parse_iso_datetime(shift.get("end_at"))
    shift["start_label"] = start_at.strftime("%H:%M") if start_at else ""
    shift["end_label"] = end_at.strftime("%H:%M") if end_at else ""
    shift["time_range"] = f"{shift['start_label']}-{shift['end_label']}"
    if start_at and end_at and end_at.date() > start_at.date():
        shift["time_range"] += " +1d"
    shift["paid_label"] = format_hours(shift.get("paid_hours"))
    shift["raw_label"] = format_hours(shift.get("raw_hours"))
    shift["mark_badges"] = [label for field, label in MARK_FIELDS if int(shift.get(field) or 0)]
    shift.update(parsed_title)
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
    }
    shift["role_segments"] = [role_segment]
    shift["role_chain_label"] = role_chain_label(shift["role_segments"])
    colour = clean_colour(str(shift.get("custom_colour") or ""))
    shift["colour_style"] = f"--shift-colour: {colour};" if colour else ""
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
    left_role = str(left.get("role_label") or "")
    right_role = str(right.get("role_label") or "")
    if role_is_vehicleish(left_role) and not role_is_vehicleish(right_role):
        return right
    if role_is_vehicleish(right_role) and not role_is_vehicleish(left_role):
        return left
    return right if float(right.get("raw_hours") or 0) >= float(left.get("raw_hours") or 0) else left


def can_merge_shift(left: dict[str, object], right: dict[str, object]) -> bool:
    if int(left.get("deleted_from_source") or 0) or int(right.get("deleted_from_source") or 0):
        return False
    left_end = parse_iso_datetime(str(left.get("end_at") or ""))
    right_start = parse_iso_datetime(str(right.get("start_at") or ""))
    if not left_end or not right_start or left_end != right_start:
        return False
    return (
        left.get("date") == right.get("date")
        and left.get("track_label") == right.get("track_label")
        and left.get("location") == right.get("location")
        and left.get("race_type_label") == right.get("race_type_label")
    )


def merge_shift_pair(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    primary = choose_primary_shift(left, right)
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
    merged["description_lines"] = unique_description_lines(
        list(left.get("description_lines") or []),
        list(right.get("description_lines") or []),
    )
    merged["roster_summary"] = parse_roster_summary(list(merged.get("description_lines") or []))
    merged["role_segments"] = list(left.get("role_segments") or []) + list(right.get("role_segments") or [])
    merged["role_chain_label"] = role_chain_label(list(merged.get("role_segments") or []))
    merged["changed_since_viewed"] = int(left.get("changed_since_viewed") or 0) or int(right.get("changed_since_viewed") or 0)
    merged["combined_shift_ids"] = list(left.get("combined_shift_ids") or [left["id"]]) + list(
        right.get("combined_shift_ids") or [right["id"]]
    )
    return merged


def combine_adjacent_shifts(shifts: list[dict[str, object]]) -> list[dict[str, object]]:
    combined: list[dict[str, object]] = []
    for shift in sorted(shifts, key=lambda item: (str(item.get("start_at") or ""), int(item.get("id") or 0))):
        if combined and can_merge_shift(combined[-1], shift):
            combined[-1] = merge_shift_pair(combined[-1], shift)
        else:
            combined.append(shift)
    return combined


def display_schedule_area(value: str | None) -> str:
    value = (value or "").strip()
    match = re.match(r"^(.+?)([TH])-[A-Za-z].*$", value, flags=re.IGNORECASE)
    if match and match.group(1).strip():
        value = match.group(1).strip()
    value = re.sub(r"\s+", " ", value)
    compact_key = re.sub(r"\s+", "", value.upper())
    return ROLE_NAMES.get(compact_key, ROLE_NAMES.get(value.upper(), value or "Role"))


def schedule_area_is_vehicle(value: str | None) -> bool:
    return role_is_vehicleish(value)


def schedule_sort_value(value: object) -> int:
    if value in (None, ""):
        return 999999
    try:
        return int(value)
    except (TypeError, ValueError):
        return 999999


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
    item["is_vehicle_area"] = schedule_area_is_vehicle(str(item.get("area_display") or ""))
    item["changed"] = bool(int(item.get("changed_since_viewed") or 0))
    item["change_summary"] = str(item.get("change_summary") or "")
    return item


def append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def schedule_people(rows: list[object]) -> list[dict[str, object]]:
    people_by_key: dict[str, dict[str, object]] = {}
    open_entries: list[dict[str, object]] = []
    for row in rows:
        item = decorate_schedule_row(row)
        area_label = str(item.get("area_display") or "Role")
        area_sort = schedule_sort_value(item.get("area_sort_order"))
        employee_name = str(item.get("employee_name") or "").strip()
        is_vehicle = bool(item.get("is_vehicle_area"))

        if not employee_name:
            open_entries.append(
                {
                    "employee_name": "Open shift",
                    "position_label": "Open shift" if is_vehicle else area_label,
                    "vehicle_label": area_label if is_vehicle else "",
                    "sort_order": area_sort,
                    "changed": bool(item.get("changed")),
                    "change_summary": item.get("change_summary") or "",
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
                "changed": False,
                "position_sort": 999999,
                "vehicle_sort": 999999,
            },
        )
        if item.get("changed"):
            person["changed"] = True
            append_unique(person["change_parts"], str(item.get("change_summary") or "Changed"))
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
        sort_order = schedule_sort_value(person.get("position_sort"))
        if sort_order == 999999:
            sort_order = schedule_sort_value(person.get("vehicle_sort"))
        people.append(
            {
                "employee_name": person.get("employee_name") or "Open shift",
                "position_label": ", ".join(position_parts) if position_parts else "Vehicle",
                "vehicle_label": ", ".join(vehicle_parts),
                "sort_order": sort_order,
                "changed": bool(person.get("changed")),
                "change_summary": "; ".join(list(person.get("change_parts") or [])),
            }
        )
    people.extend(open_entries)
    return sorted(
        people,
        key=lambda person: (
            schedule_sort_value(person.get("sort_order")),
            str(person.get("position_label") or ""),
            str(person.get("employee_name") or ""),
        ),
    )


def notice_url(path: str, message: str) -> str:
    parts = urlsplit(path)
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    query_items.append(("notice", message))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_items), parts.fragment))


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    shutdown_scheduler()


@app.get("/")
def home() -> RedirectResponse:
    return RedirectResponse(url="/month", status_code=303)


@app.get("/month")
def month_view(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    view: str = "month",
    notice: str | None = None,
) -> object:
    settings = get_settings()
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
    rows = fetch_shifts_between(grid_start, grid_end)

    shifts_by_date: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        shifts_by_date.setdefault(row["date"], []).append(decorate_shift(row))
    for date_key, day_shifts in list(shifts_by_date.items()):
        shifts_by_date[date_key] = combine_adjacent_shifts(day_shifts)

    weeks = []
    active_days = []
    month_total = 0.0
    for week in month_weeks:
        days = []
        week_total = 0.0
        for day_item in week:
            day_shifts = shifts_by_date.get(day_item.isoformat(), [])
            day_total = sum(
                float(shift.get("paid_hours") or 0)
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
                    "total": day_total,
                }
            )
            if day_item.month == month and day_shifts:
                active_days.append(
                    {
                        "date": day_item,
                        "iso": day_item.isoformat(),
                        "shifts": day_shifts,
                        "total": day_total,
                    }
                )
        weeks.append({"days": days, "total": week_total})

    prev_year, prev_month = add_months(year, month, -1)
    next_year, next_month = add_months(year, month, 1)
    first_day = date(year, month, 1)
    now_iso = datetime.now(settings.timezone).replace(microsecond=0).isoformat()
    upcoming_shifts = combine_adjacent_shifts(
        [decorate_shift(row) for row in get_upcoming_shifts(now_iso, limit=10)]
    )[:5]

    return templates.TemplateResponse(
        "month.html",
        {
            "request": request,
            "notice": notice,
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


@app.get("/day/{date_text}")
def day_view(request: Request, date_text: str, notice: str | None = None) -> object:
    try:
        day_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invalid date") from exc

    shifts = combine_adjacent_shifts([decorate_shift(row) for row in fetch_shifts_for_date(date_text)])
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
    deputy_schedule_people = schedule_people(fetch_deputy_schedule_for_date(date_text))
    deputy_schedule_changed = any(bool(person.get("changed")) for person in deputy_schedule_people)
    day_total = sum(
        float(shift.get("paid_hours") or 0)
        for shift in shifts
        if not int(shift.get("deleted_from_source") or 0)
    )
    has_changed = any(int(shift.get("changed_since_viewed") or 0) for shift in shifts) or deputy_schedule_changed
    return templates.TemplateResponse(
        "day.html",
        {
            "request": request,
            "notice": notice,
            "date_text": date_text,
            "day_date": day_date,
            "month_year": day_date.year,
            "month_number": day_date.month,
            "shifts": shifts,
            "deputy_schedule_people": deputy_schedule_people,
            "deputy_schedule_changed": deputy_schedule_changed,
            "day_total": day_total,
            "has_changed": has_changed,
            "mark_fields": MARK_FIELDS,
        },
    )


@app.post("/day/{date_text}/mark-viewed")
def mark_day_viewed(date_text: str) -> RedirectResponse:
    clear_changed_for_date(date_text)
    return RedirectResponse(
        url=notice_url(f"/day/{date_text}", "Changed flags cleared for this day."),
        status_code=303,
    )


@app.get("/shift/{shift_id}")
def shift_view(shift_id: int) -> RedirectResponse:
    shift = fetch_shift(shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    return RedirectResponse(url=f"/day/{shift['date']}#shift-{shift_id}", status_code=303)


@app.post("/shift/{shift_id}/marks")
async def save_shift_marks(shift_id: int, request: Request) -> RedirectResponse:
    shift = fetch_shift(shift_id)
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
    update_shift_marks(shift_id, values)
    return RedirectResponse(
        url=notice_url(f"/day/{shift['date']}", "Notes saved.") + f"#shift-{shift_id}",
        status_code=303,
    )


@app.post("/shift/{shift_id}/mark-viewed")
def mark_shift_viewed(shift_id: int) -> RedirectResponse:
    shift = fetch_shift(shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    clear_changed_for_shift(shift_id)
    return RedirectResponse(
        url=notice_url(f"/day/{shift['date']}", "Changed flag cleared.") + f"#shift-{shift_id}",
        status_code=303,
    )


@app.get("/settings")
def settings_view(request: Request, notice: str | None = None) -> object:
    settings = get_settings()
    now = datetime.now(settings.timezone).replace(microsecond=0)
    next_shift = get_next_upcoming_shift(now.isoformat())
    pre_shift = get_pre_shift_status(settings)
    calendar_url = get_calendar_url(settings)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "notice": notice,
            "header_mode": "settings",
            "settings": settings,
            "calendar_url_configured": bool(calendar_url),
            "calendar_url_source": get_calendar_url_source(settings),
            "last_successful_sync": get_last_successful_sync(),
            "next_shift": decorate_shift(next_shift) if next_shift else None,
            "pre_shift": pre_shift,
            "sync_logs": get_recent_sync_logs(),
            "source_payload_shifts": [
                decorate_shift(row)
                for row in get_recent_source_payloads()
            ],
            "deputy_web_capture": format_capture_payload(get_last_deputy_web_capture()),
        },
    )


@app.post("/settings/calendar")
async def save_calendar_settings(request: Request) -> RedirectResponse:
    form = await request.form()
    if form.get("clear_calendar_url"):
        update_app_settings({"deputy_ical_url": ""})
        return RedirectResponse(
            url=notice_url("/settings", "Saved calendar URL cleared."),
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

    update_app_settings({"deputy_ical_url": calendar_url})
    return RedirectResponse(
        url=notice_url("/settings", "Calendar URL saved. Use Sync Now to refresh the roster."),
        status_code=303,
    )


@app.post("/settings/clear-changed")
def clear_all_changed() -> RedirectResponse:
    changed = clear_all_changed_flags()
    return RedirectResponse(
        url=notice_url("/settings", f"Cleared changed flags on {changed} shifts."),
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
async def capture_deputy_web() -> RedirectResponse:
    result = await capture_and_save_deputy_web(get_settings())
    return RedirectResponse(url=notice_url("/settings", str(result["message"])), status_code=303)


@app.api_route("/sync-now", methods=["GET", "POST"])
def sync_now(next: str | None = None) -> RedirectResponse:
    summary = sync_roster_sources()
    calendar = summary.get("calendar") if isinstance(summary.get("calendar"), dict) else {}
    web = summary.get("web") if isinstance(summary.get("web"), dict) else {}
    if calendar.get("status") == "ok":
        message = (
            "Sync complete: "
            f"{calendar.get('events_created', 0)} new, "
            f"{calendar.get('events_updated', 0)} changed, "
            f"{calendar.get('events_marked_deleted', 0)} cancelled."
        )
        if web.get("status") not in {None, "skipped"}:
            message += f" Deputy crew captured: {web.get('saved_schedule_rows', 0)} rows."
    else:
        message = f"Sync failed: {calendar.get('message', 'Unknown error')}"
    redirect_path = next if next and next.startswith("/") and not next.startswith("//") else "/settings"
    return RedirectResponse(url=notice_url(redirect_path, message), status_code=303)


templates.env.filters["datetime"] = format_datetime
templates.env.filters["time"] = format_time
templates.env.filters["day_short"] = format_day_short
templates.env.filters["hours"] = format_hours
