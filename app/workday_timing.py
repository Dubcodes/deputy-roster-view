from __future__ import annotations

from datetime import datetime, timedelta


def effective_personal_start(
    workday: dict[str, object],
    assignment: dict[str, object] | None = None,
) -> str:
    """Return a person's start without changing the event-wide start."""
    assignment = assignment or {}
    explicit = str(assignment.get("personal_start_time") or "").strip()
    if explicit:
        return explicit

    start = str(workday.get("office_start") or "").strip()
    if not start or str(workday.get("day_type") or "race_day") != "race_day":
        return start
    offset = int(workday.get("truck_start_offset_minutes") or 0)
    if offset <= 0 or not bool(assignment.get("vehicle_is_truck")):
        return start
    try:
        start_at = datetime.strptime(start, "%H:%M") - timedelta(minutes=offset)
    except ValueError:
        return start
    return start_at.strftime("%H:%M")


def effective_personal_hours(
    workday: dict[str, object],
    assignment: dict[str, object] | None,
    event_hours: float,
) -> float:
    event_start = str(workday.get("office_start") or "").strip()
    personal_start = effective_personal_start(workday, assignment)
    if not event_start or not personal_start or event_start == personal_start:
        return event_hours
    try:
        event_at = datetime.strptime(event_start, "%H:%M")
        personal_at = datetime.strptime(personal_start, "%H:%M")
    except ValueError:
        return event_hours
    extra = (event_at - personal_at).total_seconds() / 3600
    if extra < 0:
        extra += 24
    return round(max(0.0, event_hours + extra), 2)
