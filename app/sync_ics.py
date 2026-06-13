from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from typing import Any

import requests
from icalendar import Calendar

from .config import Settings, get_settings
from .database import (
    get_calendar_url,
    get_connection,
    init_db,
    mark_missing_future_shifts_deleted,
    write_shift_changes,
    write_sync_log,
)
from .models import ShiftEvent


COMPARE_FIELDS = (
    "title",
    "description",
    "location",
    "start_at",
    "end_at",
    "raw_hours",
    "break_minutes",
    "paid_hours",
    "source_link",
    "source_status",
)

URL_RE = re.compile(r"https?://\S+")
BREAK_RE = re.compile(
    r"(?im)^(?=.*break)(?:.*?:\s*)?(\d+)\s*(?:min|mins|minute|minutes)\b"
)


class CalendarSyncError(Exception):
    """Raised when the calendar feed cannot be safely synced."""


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(microsecond=0)


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_datetime(value: Any, settings: Settings) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        raise CalendarSyncError("Calendar event contains an unsupported datetime value.")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=settings.timezone)
    else:
        dt = dt.astimezone(settings.timezone)
    return dt.replace(microsecond=0)


def _calculate_hours(start_at: datetime, end_at: datetime, break_minutes: int) -> tuple[float, float]:
    if end_at <= start_at:
        end_at = end_at + timedelta(days=1)

    raw_hours = round((end_at - start_at).total_seconds() / 3600, 2)
    paid_hours = max(0.0, round(raw_hours - (break_minutes / 60), 2))
    return raw_hours, paid_hours


def _json_safe(value: Any) -> Any:
    if hasattr(value, "dt"):
        return _json_safe(value.dt)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _event_properties(event: Any) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    params: dict[str, Any] = {}

    for name, value in event.items():
        key = str(name).lower()
        item = _json_safe(value)
        if key in properties:
            if not isinstance(properties[key], list):
                properties[key] = [properties[key]]
            properties[key].append(item)
        else:
            properties[key] = item

        property_params = getattr(value, "params", None)
        if property_params:
            params[key] = _json_safe(dict(property_params))

    return {"properties": properties, "params": params}


def _extract_source_link(description: str, event: Any) -> str:
    url_value = _clean_text(event.get("url"))
    if url_value:
        return url_value

    for line in description.splitlines():
        if line.lower().startswith("open in deputy"):
            match = URL_RE.search(line)
            if match:
                return match.group(0).rstrip(".,)")

    match = URL_RE.search(description)
    return match.group(0).rstrip(".,)") if match else ""


def _extract_break_minutes(description: str) -> int:
    minutes = [int(match.group(1)) for match in BREAK_RE.finditer(description)]
    return sum(minutes) if minutes else 0


def _event_payload(event: Any, start_at: datetime, end_at: datetime, break_minutes: int, source_link: str) -> str:
    all_properties = _event_properties(event)
    payload = {
        "normalised": {
            "uid": _clean_text(event.get("uid")),
            "summary": _clean_text(event.get("summary")),
            "description": _clean_text(event.get("description")),
            "location": _clean_text(event.get("location")),
            "dtstart": start_at.isoformat(),
            "dtend": end_at.isoformat(),
            "break_minutes": break_minutes,
            "source_link": source_link,
            "last_modified": _clean_text(event.get("last-modified")),
            "sequence": _clean_text(event.get("sequence")),
            "status": _clean_text(event.get("status")),
            "categories": _clean_text(event.get("categories")),
        },
        "ical": all_properties,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _decode_event_datetime(event: Any, field_name: str) -> Any:
    try:
        return event.decoded(field_name)
    except KeyError as exc:
        raise CalendarSyncError(f"Calendar event is missing {field_name.upper()}.") from exc


def _parse_event(event: Any, source_url_hash: str, settings: Settings) -> ShiftEvent:
    start_at = _normalise_datetime(_decode_event_datetime(event, "dtstart"), settings)

    if event.get("dtend") is not None:
        end_at = _normalise_datetime(_decode_event_datetime(event, "dtend"), settings)
    elif event.get("duration") is not None:
        end_at = start_at + event.decoded("duration")
    else:
        raise CalendarSyncError("Calendar event is missing DTEND.")

    if end_at <= start_at:
        end_at = end_at + timedelta(days=1)

    description = _clean_text(event.get("description"))
    break_minutes = _extract_break_minutes(description)
    raw_hours, paid_hours = _calculate_hours(start_at, end_at, break_minutes)
    source_link = _extract_source_link(description, event)
    payload = _event_payload(event, start_at, end_at, break_minutes, source_link)
    uid = _clean_text(event.get("uid"))
    if not uid:
        uid = "missing-uid-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return ShiftEvent(
        source_uid=uid,
        source_url_hash=source_url_hash,
        title=_clean_text(event.get("summary")),
        description=description,
        location=_clean_text(event.get("location")),
        start_at=start_at,
        end_at=end_at,
        date=start_at.date().isoformat(),
        raw_hours=raw_hours,
        break_minutes=break_minutes,
        paid_hours=paid_hours,
        source_link=source_link,
        source_status=_clean_text(event.get("status")),
        source_payload=payload,
    )


def _fetch_calendar(url: str) -> bytes:
    try:
        response = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        raise CalendarSyncError(f"Deputy calendar fetch failed: {exc.__class__.__name__}.") from exc

    if not response.ok:
        raise CalendarSyncError(f"Deputy calendar fetch failed with HTTP {response.status_code}.")
    return response.content


def _load_events(settings: Settings, calendar_url: str) -> list[ShiftEvent]:
    if not calendar_url:
        raise CalendarSyncError("Calendar URL is not configured.")

    source_url_hash = _hash_url(calendar_url)
    feed_bytes = _fetch_calendar(calendar_url)
    try:
        calendar = Calendar.from_ical(feed_bytes)
    except Exception as exc:
        raise CalendarSyncError("Deputy calendar feed could not be parsed.") from exc

    events: list[ShiftEvent] = []
    for component in calendar.walk("VEVENT"):
        events.append(_parse_event(component, source_url_hash, settings))
    return events


def _float_changed(old_value: Any, new_value: float) -> bool:
    try:
        old = round(float(old_value), 2)
    except (TypeError, ValueError):
        return True
    return old != round(float(new_value), 2)


def _event_values(event: ShiftEvent) -> dict[str, Any]:
    return {
        "title": event.title,
        "description": event.description,
        "location": event.location,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "raw_hours": event.raw_hours,
        "break_minutes": event.break_minutes,
        "paid_hours": event.paid_hours,
        "source_link": event.source_link,
        "source_status": event.source_status,
    }


def _event_changes(row: Any, event: ShiftEvent) -> dict[str, tuple[Any, Any]]:
    event_values = _event_values(event)
    changes: dict[str, tuple[Any, Any]] = {}
    for field in COMPARE_FIELDS:
        new_value = event_values[field]
        if field in {"raw_hours", "paid_hours"}:
            if _float_changed(row[field], float(new_value)):
                changes[field] = (row[field], round(float(new_value), 2))
        elif field == "break_minutes":
            if int(row[field] or 0) != int(new_value):
                changes[field] = (row[field], int(new_value))
        elif (row[field] or "") != (new_value or ""):
            changes[field] = (row[field], new_value)
    if int(row["deleted_from_source"] or 0):
        changes["deleted_from_source"] = (row["deleted_from_source"], 0)
    return changes


def _upsert_event(conn: Any, event: ShiftEvent, now_iso: str, owner_user_id: int | None = None) -> str:
    existing = conn.execute(
        "SELECT * FROM shifts WHERE source_uid = ?",
        (event.source_uid,),
    ).fetchone()

    values = (
        event.source_uid,
        event.source_url_hash,
        event.title,
        event.description,
        event.location,
        event.start_at.isoformat(),
        event.end_at.isoformat(),
        event.date,
        event.raw_hours,
        event.break_minutes,
        event.paid_hours,
        now_iso,
        now_iso,
        None,
        0,
        0,
        owner_user_id,
        event.source_link,
        event.source_status,
        event.source_payload,
    )

    if existing is None:
        conn.execute(
            """
            INSERT INTO shifts (
                source_uid, source_url_hash, title, description, location,
                start_at, end_at, date, raw_hours, break_minutes, paid_hours,
                last_synced_at, first_seen_at, last_changed_at,
                changed_since_viewed, deleted_from_source, owner_user_id, source_link,
                source_status, source_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        return "created"

    changes = _event_changes(existing, event)
    changed = bool(changes)
    conn.execute(
        """
        UPDATE shifts
        SET source_url_hash = ?,
            title = ?,
            description = ?,
            location = ?,
            start_at = ?,
            end_at = ?,
            date = ?,
            raw_hours = ?,
            break_minutes = ?,
            paid_hours = ?,
            source_link = ?,
            source_status = ?,
            owner_user_id = ?,
            last_synced_at = ?,
            last_changed_at = CASE WHEN ? THEN ? ELSE last_changed_at END,
            changed_since_viewed = CASE WHEN ? THEN 1 ELSE changed_since_viewed END,
            deleted_from_source = 0,
            source_payload = ?
        WHERE source_uid = ?
        """,
        (
            event.source_url_hash,
            event.title,
            event.description,
            event.location,
            event.start_at.isoformat(),
            event.end_at.isoformat(),
            event.date,
            event.raw_hours,
            event.break_minutes,
            event.paid_hours,
            event.source_link,
            event.source_status,
            owner_user_id,
            now_iso,
            1 if changed else 0,
            now_iso,
            1 if changed else 0,
            event.source_payload,
            event.source_uid,
        ),
    )
    if changed:
        write_shift_changes(conn, int(existing["id"]), now_iso, changes)
    return "updated" if changed else "unchanged"


def sync_deputy_calendar(settings: Settings | None = None, owner_user_id: int | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    init_db(settings)
    started_at = _now(settings).isoformat()
    summary: dict[str, object] = {
        "started_at": started_at,
        "finished_at": started_at,
        "status": "ok",
        "message": "Sync complete.",
        "events_seen": 0,
        "events_created": 0,
        "events_updated": 0,
        "events_marked_deleted": 0,
    }

    try:
        calendar_url = get_calendar_url(settings)
        events = _load_events(settings, calendar_url)
        now_iso = _now(settings).isoformat()
        source_url_hash = _hash_url(calendar_url)
        seen_uids: set[str] = set()

        with get_connection(settings) as conn:
            for event in events:
                seen_uids.add(event.source_uid)
                action = _upsert_event(conn, event, now_iso, owner_user_id=owner_user_id)
                if action == "created":
                    summary["events_created"] = int(summary["events_created"]) + 1
                elif action == "updated":
                    summary["events_updated"] = int(summary["events_updated"]) + 1

            summary["events_seen"] = len(seen_uids)
            summary["events_marked_deleted"] = mark_missing_future_shifts_deleted(
                conn,
                source_url_hash,
                seen_uids,
                now_iso,
                now_iso,
                owner_user_id=owner_user_id,
            )
    except CalendarSyncError as exc:
        summary["status"] = "error"
        summary["message"] = str(exc)
    except Exception as exc:
        summary["status"] = "error"
        summary["message"] = f"Unexpected sync error: {exc.__class__.__name__}."
    finally:
        summary["finished_at"] = _now(settings).isoformat()
        write_sync_log(summary)

    return summary
