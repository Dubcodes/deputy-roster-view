from __future__ import annotations

import re
from datetime import date


TIME_FIELDS = {
    "first_race",
    "last_race",
    "on_track",
    "start",
    "finish",
    "records",
    "on_air",
    "first_cross",
}
DURATION_FIELDS = {"pack_up_duration", "outbound_travel", "return_travel"}
FIELD_LABELS = {
    "first_race": "First race",
    "last_race": "Last race",
    "race_count": "Race count",
    "on_track": "On track",
    "start": "Start",
    "finish": "Finish",
    "records": "Records",
    "on_air": "On air",
    "first_cross": "First cross",
    "pack_up_duration": "Pack-up duration",
    "outbound_travel": "Outbound travel",
    "return_travel": "Return travel",
}
OPERATIONAL_VENUE_KEYS = {
    "office",
    "clowplace",
    "officeclowplace",
    "travel",
    "vehicles",
    "web",
    "training",
    "northernopscontractors",
}


def compact_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def canonical_override_venue(value: object) -> tuple[str, str]:
    label = re.sub(r"\s+", " ", str(value or "").strip()).strip(" -")
    if not label:
        return "", ""

    label = re.sub(r"(?i)^[tgh]\s*-\s*", "", label).strip()
    label = re.sub(r"(?i)^(?:trials?|jumpouts?)\s*[-–:]?\s*", "", label).strip()
    label = re.sub(r"(?i)\s*[-–:]?\s*(?:trials?|jumpouts?)$", "", label).strip()
    key = compact_key(label)
    if key in OPERATIONAL_VENUE_KEYS:
        return "", ""
    if key in {"gcambridge", "cambridgegreyhound"}:
        return "cambridgegreyhound", "Cambridge Greyhound"
    if key == "cambridge":
        return "cambridgesynthetic", "Cambridge Synthetic"
    return key, label


def normalise_override_field(override_type: object, label: object) -> tuple[str, str]:
    category = compact_key(override_type)
    if category != "timing":
        return "", f"Override type {str(override_type or '').strip() or 'blank'} is not supported yet."
    key = re.sub(r"[^a-z0-9]+", "_", str(label or "").strip().lower()).strip("_")
    aliases = {
        "firstrace": "first_race",
        "first_race": "first_race",
        "lastrace": "last_race",
        "last_race": "last_race",
        "racecount": "race_count",
        "race_count": "race_count",
        "races": "race_count",
        "ontrack": "on_track",
        "on_track": "on_track",
        "start": "start",
        "finish": "finish",
        "records": "records",
        "record": "records",
        "onair": "on_air",
        "on_air": "on_air",
        "firstcross": "first_cross",
        "first_cross": "first_cross",
        "packupduration": "pack_up_duration",
        "pack_up_duration": "pack_up_duration",
        "packup": "pack_up_duration",
        "outboundtravel": "outbound_travel",
        "outbound_travel": "outbound_travel",
        "returntravel": "return_travel",
        "return_travel": "return_travel",
    }
    field_key = aliases.get(key) or aliases.get(key.replace("_", ""))
    if not field_key:
        return "", f"Timing label {str(label or '').strip() or 'blank'} is not supported."
    return field_key, ""


def normalise_time(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip().lower().rstrip(".,"))
    match = re.fullmatch(r"(\d{1,2})(?:[.:](\d{2}))?(am|pm)", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if hour < 1 or hour > 12 or minute > 59:
            return ""
        if match.group(3) == "pm" and hour != 12:
            hour += 12
        elif match.group(3) == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    if re.fullmatch(r"\d{3,4}", text):
        text = text.zfill(4)
        hour, minute = int(text[:2]), int(text[2:])
    else:
        match = re.fullmatch(r"(\d{1,2})[.:](\d{2})", text)
        if not match:
            return ""
        hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def normalise_duration(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not text:
        return ""
    match = re.fullmatch(
        r"(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours))?"
        r"(?:\s*(\d+)\s*(?:m|min|mins|minute|minutes))?",
        text,
    )
    if not match or not any(match.groups()):
        return ""
    hours = float(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    total = int(round(hours * 60)) + minutes
    if total <= 0 or total > 24 * 60:
        return ""
    return str(total)


def normalise_override_value(field_key: str, value: object) -> tuple[str, str]:
    if field_key in TIME_FIELDS:
        normalised = normalise_time(value)
        return (normalised, "") if normalised else ("", "Enter a valid time, such as 16:38 or 4:38 pm.")
    if field_key == "race_count":
        text = str(value or "").strip()
        if not re.fullmatch(r"\d+", text) or int(text) <= 0:
            return "", "Race count must be a positive whole number."
        return str(int(text)), ""
    if field_key in DURATION_FIELDS:
        normalised = normalise_duration(value)
        return (
            (normalised, "")
            if normalised
            else ("", "Enter a duration with units, such as 30m or 1h 15m.")
        )
    return "", "This override field is not supported."


def validate_override_date(value: object) -> tuple[str, str]:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat(), ""
    except ValueError:
        return "", "Date must use YYYY-MM-DD."
