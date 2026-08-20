from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Iterable


VEHICLE_RE = re.compile(r"^(?:\d{3,4}|rav\w+|rp\d+|ob|tender|transit)$", re.IGNORECASE)


def deputy_shift_is_available(row: dict[str, object]) -> bool:
    """Deputy Open is a raw flag; availability additionally requires no assignee."""
    employee_id = row.get("employee_id", row.get("employee"))
    employee_name = str(row.get("employee_name", row.get("employeeName")) or "").strip()
    is_open = row.get("is_open", row.get("isOpen"))
    return bool(is_open) and employee_id in (None, "") and not employee_name


def _payload(row: dict[str, object]) -> dict[str, object]:
    try:
        value = json.loads(str(row.get("source_payload") or row.get("raw_payload") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _title_parts(value: object) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^\[([^]]+)]\s*(.*)$", text)
    return (match.group(1), match.group(2) or "Shift") if match else (text or "Work day", "Shift")


def _vehicle_from_row(row: dict[str, object]) -> str:
    explicit = str(row.get("resolved_vehicle") or "").strip()
    if explicit:
        return explicit
    payload = _payload(row)
    explicit = str(payload.get("vehicle_label") or payload.get("vehicle") or "").strip()
    if explicit:
        return explicit
    role = str(row.get("role_label") or row.get("area_name") or "").strip()
    return role if VEHICLE_RE.fullmatch(role) else ""


def _row_is_vehicle(row: dict[str, object]) -> bool:
    role = str(row.get("role_label") or row.get("area_name") or "").strip()
    return bool(
        VEHICLE_RE.fullmatch(role)
        or re.fullmatch(r"(?:Travel|Vehicles?)", role, re.I)
    )


def _moment(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _note_vehicle(row: dict[str, object], allowed_aliases: set[str] | None = None) -> str:
    """Resolve only an exact named personal note allocation; never fuzzy-match."""
    payload = _payload(row)
    employee_name = str(
        row.get("employee_name") or payload.get("employeeName") or payload.get("employee_name") or ""
    ).strip()
    aliases = set(allowed_aliases or ())
    if employee_name:
        aliases.add(re.sub(r"\W+", "", employee_name.casefold()))
    text = str(row.get("description") or payload.get("description") or payload.get("note") or "")
    for line in text.splitlines():
        words = re.findall(r"[A-Za-z][A-Za-z'-]*\d*|(?<!\d)\d{3,4}(?!\d)", line)
        if len(words) < 2:
            continue
        candidates = []
        if VEHICLE_RE.fullmatch(words[0]):
            candidates.append((words[0], words[1:]))
        if VEHICLE_RE.fullmatch(words[-1]):
            candidates.append((words[-1], words[:-1]))
        for vehicle, names in candidates:
            name_key = re.sub(r"\W+", "", " ".join(names).casefold())
            if name_key in aliases:
                return vehicle
    return ""


def _cohorts(rows: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Split non-overlapping duties while retaining overlapping production/vehicle rows."""
    by_date: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        day = str(row.get("date") or row.get("start_at") or row.get("start") or "")[:10]
        by_date.setdefault(day, []).append(row)
    result: list[list[dict[str, object]]] = []
    for day in sorted(by_date):
        ordered = sorted(by_date[day], key=lambda row: str(row.get("start_at") or row.get("start") or ""))
        current: list[dict[str, object]] = []
        current_end: datetime | None = None
        for row in ordered:
            start = _moment(row.get("start_at") or row.get("start"))
            end = _moment(row.get("end_at") or row.get("end"))
            if current and start and current_end and start >= current_end:
                result.append(current)
                current, current_end = [], None
            current.append(row)
            if end and (current_end is None or end > current_end):
                current_end = end
        if current:
            result.append(current)
    return result


def _same_person(row: dict[str, object], employee_id: object, aliases: set[str]) -> bool:
    row_id = row.get("employee_id", row.get("employee"))
    if employee_id not in (None, "") and row_id not in (None, ""):
        return str(row_id) == str(employee_id)
    row_name = str(row.get("employee_name", row.get("employeeName")) or "").strip()
    return bool(row_name and re.sub(r"\W+", "", row_name.casefold()) in aliases)


def _overlaps(row: dict[str, object], start: datetime | None, end: datetime | None) -> bool:
    row_start = _moment(row.get("start_at") or row.get("start"))
    row_end = _moment(row.get("end_at") or row.get("end"))
    return bool(start and end and row_start and row_end and row_start < end and start < row_end)


def interpret_deputy_workdays(
    rows: Iterable[dict[str, object]], *, structured_rows: Iterable[dict[str, object]] = (),
    person_identity: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Project traceable Deputy rows into the final person-level workday interpretation."""
    result = []
    structured = [dict(row) for row in structured_rows]
    person = dict(person_identity or {})
    employee_id = person.get("deputy_employee_id")
    aliases = {
        re.sub(r"\W+", "", str(value).casefold())
        for value in person.get("aliases", []) if str(value).strip()
    }
    for evidence in _cohorts([dict(row) for row in rows]):
        first = evidence[0]
        title_location, title_role = _title_parts(first.get("title"))
        production = [
            row for row in evidence
            if not _row_is_vehicle(row)
        ]
        anchor = production[0] if production else first
        location, fallback_role = _title_parts(anchor.get("title"))
        location = str(anchor.get("location_name") or location or title_location or "Work day").strip()
        positions: list[str] = []
        for row in production:
            _, row_fallback = _title_parts(row.get("title"))
            role = str(row.get("role_label") or row.get("area_name") or row_fallback or fallback_role or title_role).strip()
            if role and role not in positions:
                positions.append(role)
        cohort_starts = [_moment(row.get("start_at") or row.get("start")) for row in evidence]
        cohort_ends = [_moment(row.get("end_at") or row.get("end")) for row in evidence]
        cohort_start = min((value for value in cohort_starts if value), default=None)
        cohort_end = max((value for value in cohort_ends if value), default=None)
        overlapping_structured = [row for row in structured if _overlaps(row, cohort_start, cohort_end)]
        cohort_structured = [
            row for row in structured
            if _overlaps(row, cohort_start, cohort_end) and _same_person(row, employee_id, aliases)
        ]
        structured_vehicle = next((_vehicle_from_row(row) for row in evidence if _vehicle_from_row(row)), "")
        if not structured_vehicle:
            structured_vehicle = next(
                (_vehicle_from_row(row) for row in cohort_structured if _row_is_vehicle(row) and _vehicle_from_row(row)), "",
            )
        note_aliases = set(aliases)
        target_names = [str(row.get("employee_name", row.get("employeeName")) or "").strip() for row in cohort_structured]
        target_names.extend(str(value) for value in person.get("aliases", []) if str(value).strip())
        first_names = {name.split()[0].casefold() for name in target_names if name}
        for first_name in first_names:
            matching_people = {
                str(row.get("employee_id", row.get("employee")) or row.get("employee_name", row.get("employeeName")) or "")
                for row in overlapping_structured
                if str(row.get("employee_name", row.get("employeeName")) or "").strip().split()[0:1]
                and str(row.get("employee_name", row.get("employeeName")) or "").strip().split()[0].casefold() == first_name
            }
            if len(matching_people) == 1:
                note_aliases.add(re.sub(r"\W+", "", first_name))
        note_vehicle = next(
            (_note_vehicle(row, note_aliases) for row in evidence if _note_vehicle(row, note_aliases)), "",
        )
        vehicle = note_vehicle or structured_vehicle
        starts = [str(row.get("start_at") or row.get("start") or "") for row in evidence]
        finishes = [str(row.get("end_at") or row.get("end") or "") for row in evidence]
        source_ids = [row.get("source_shift_id", row.get("id")) for row in evidence]
        source_ids = list(dict.fromkeys(item for item in source_ids if item not in (None, "")))
        date_text = str(anchor.get("date") or starts[0] or "")[:10]
        start = min((value[11:16] for value in starts if len(value) >= 16), default="")
        finish = max((value[11:16] for value in finishes if len(value) >= 16), default="")
        identity_source = f"{date_text}|{start}|{finish}|{'|'.join(map(str, source_ids))}"
        item = {
            "date": date_text,
            "location": location,
            "production_position": "/".join(positions) or "Shift",
            "vehicle": vehicle,
            "rostered_start": start,
            "rostered_finish": finish,
            "raw_source_shift_ids": source_ids,
            "field_provenance": {
                "rostered_start": "deputy_roster",
                "rostered_finish": "deputy_roster",
                **({"vehicle": "current_roster_note" if note_vehicle else "structured_deputy"} if vehicle else {}),
            },
            "current_roster_note_evidence": [row for row in evidence if _note_vehicle(row, note_aliases)],
            "structured_deputy_evidence": evidence + cohort_structured,
            "logical_workday_id": hashlib.sha256(identity_source.encode()).hexdigest()[:24],
        }
        item["revision"] = hashlib.sha256(json.dumps({
            "ids": source_ids, "position": item["production_position"], "vehicle": vehicle,
            "start": start, "finish": finish,
        }, sort_keys=True).encode()).hexdigest()
        result.append(item)
    return sorted(result, key=lambda item: (str(item["date"]), str(item["rostered_start"]), str(item["location"])))
