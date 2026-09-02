from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Iterable

from .roster_note_interpretation import (
    allocations_from_shifts,
    canonical_vehicle_label,
    identity_key,
    resolve_note_allocations,
)
from .travel_cohorts import is_travel_participant_cohort, travel_family_locations_match


VEHICLE_RE = re.compile(r"^(?:\d{3,4}|rav\w*|rp\d+|ob|tender|transit)$", re.IGNORECASE)


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


def _explicit_vehicle_from_row(row: dict[str, object]) -> str:
    """Read recorded normalized vehicle evidence without classifying a row.

    This deliberately supports only the stable normalized compatibility keys.
    It does not infer a value from arbitrary raw Deputy payload fields.
    """
    payload = _payload(row)
    normalised = payload.get("normalised")
    values = (
        row.get("vehicle_label"),
        payload.get("vehicle_label"),
        payload.get("vehicle"),  # legacy already-normalized persisted payload
        normalised.get("vehicle_label") if isinstance(normalised, dict) else None,
    )
    for value in values:
        if isinstance(value, str) and value.strip():
            return canonical_vehicle_label(value)
    return ""


def _title_parts(value: object) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^\[([^]]+)]\s*(.*)$", text)
    return (match.group(1), match.group(2) or "Shift") if match else (text or "Work day", "Shift")


def _vehicle_from_row(row: dict[str, object]) -> str:
    explicit = str(row.get("resolved_vehicle") or "").strip()
    if explicit:
        return canonical_vehicle_label(explicit)
    explicit = _explicit_vehicle_from_row(row)
    if explicit:
        return explicit
    _, title_role = _title_parts(row.get("title"))
    role = str(row.get("role_label") or row.get("area_name") or title_role or "").strip()
    return canonical_vehicle_label(role) if VEHICLE_RE.fullmatch(role) else ""


def _row_is_vehicle(row: dict[str, object]) -> bool:
    _, title_role = _title_parts(row.get("title"))
    role = str(row.get("role_label") or row.get("area_name") or title_role or "").strip()
    return bool(
        VEHICLE_RE.fullmatch(role)
        or re.fullmatch(r"(?:Travel|Vehicles?)", role, re.I)
        or is_travel_participant_cohort(role)
    )


def _current_vehicle_candidates(
    evidence: list[dict[str, object]], target_structured: list[dict[str, object]], *, owns_raw_evidence: bool,
) -> tuple[list[str], list[dict[str, object]]]:
    """Collect every explicit current vehicle fact for one person/workday.

    Vehicle-only and production rows remain independent dimensions: a direct
    vehicle value on a CCU1 row is just as structured as a separate Travel
    companion.  Raw personal rows are included only for their owning identity.
    """
    candidates: list[tuple[str, dict[str, object]]] = []
    for row in target_structured:
        value = _explicit_vehicle_from_row(row)
        if not value and _row_is_vehicle(row):
            value = _vehicle_from_row(row)
        if value:
            candidates.append((value, row))
    if owns_raw_evidence:
        for row in evidence:
            value = _explicit_vehicle_from_row(row)
            if not value and _row_is_vehicle(row):
                value = _vehicle_from_row(row)
            if value:
                candidates.append((value, row))
    values: list[str] = []
    rows: list[dict[str, object]] = []
    for value, row in candidates:
        if value not in values:
            values.append(value)
        rows.append(row)
    return values, rows


def _moment(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def _location_key(row: dict[str, object]) -> str:
    title_match = re.match(r"^\[([^]]+)]", str(row.get("title") or "").strip())
    title_location = title_match.group(1) if title_match else ""
    location = str(row.get("location_name") or title_location or "").casefold()
    return re.sub(r"[^a-z0-9]+", "", location)


def _touching_complements(current: list[dict[str, object]], row: dict[str, object]) -> bool:
    """Join an exact boundary only for same-event vehicle/travel + production evidence."""
    if not current or not _location_key(row):
        return False
    same_location = any(_location_key(item) == _location_key(row) for item in current)
    if not same_location:
        return False
    current_has_vehicle = any(_row_is_vehicle(item) for item in current)
    current_has_production = any(not _row_is_vehicle(item) for item in current)
    return (current_has_vehicle and not _row_is_vehicle(row)) or (current_has_production and _row_is_vehicle(row))


def _vehicle_handoff_gap_complements(
    current: list[dict[str, object]], row: dict[str, object], start: datetime | None, current_end: datetime | None,
) -> bool:
    if not current or not start or not current_end or not (timedelta() < start - current_end <= timedelta(minutes=30)):
        return False
    if _row_is_vehicle(row) or not _location_key(row) or not any(_location_key(item) == _location_key(row) for item in current):
        return False
    vehicle_rows = [item for item in current if _row_is_vehicle(item)]
    return bool(vehicle_rows) and all(
        (end := _moment(item.get("end_at") or item.get("end"))) is not None
        and (begin := _moment(item.get("start_at") or item.get("start"))) is not None
        and end - begin <= timedelta(minutes=30)
        for item in vehicle_rows
    )


def _cohorts(rows: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Build connected workdays from overlap or semantic boundary adjacency."""
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
            separated = bool(
                current and start and current_end and start > current_end
                and not _vehicle_handoff_gap_complements(current, row, start, current_end)
            )
            touching_unrelated = bool(
                current and start and current_end and start == current_end and not _touching_complements(current, row)
            )
            if separated or touching_unrelated:
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
    return bool(row_name and identity_key(row_name) in aliases)


def _overlaps_or_touches(row: dict[str, object], start: datetime | None, end: datetime | None) -> bool:
    row_start = _moment(row.get("start_at") or row.get("start"))
    row_end = _moment(row.get("end_at") or row.get("end"))
    return bool(start and end and row_start and row_end and row_start <= end and start <= row_end)


def _structured_row_connects_evidence(row: dict[str, object], evidence: dict[str, object]) -> bool:
    row_location = _location_key(row)
    evidence_location = _location_key(evidence)
    if not row_location or not evidence_location or row_location == evidence_location:
        return True
    return travel_family_locations_match(
        evidence.get("location_name") or _title_parts(evidence.get("title"))[0],
        evidence.get("role_label") or evidence.get("area_name") or _title_parts(evidence.get("title"))[1],
        row.get("location_name") or _title_parts(row.get("title"))[0],
        row.get("role_label") or row.get("area_name") or _title_parts(row.get("title"))[1],
    )


def _cohort_people(
    rows: list[dict[str, object]], identity_records: list[dict[str, object]], person: dict[str, object],
) -> list[dict[str, object]]:
    people: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        employee_id = row.get("employee_id", row.get("employee"))
        employee_name = str(row.get("employee_name", row.get("employeeName")) or "").strip()
        if employee_id in (None, "") and not employee_name:
            continue
        key = f"id:{employee_id}" if employee_id not in (None, "") else f"name:{identity_key(employee_name)}"
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, object] = {"employee_id": employee_id, "employee_name": employee_name}
        matches = [
            identity for identity in identity_records
            if (employee_id not in (None, "") and str(identity.get("deputy_employee_id") or "") == str(employee_id))
            or (employee_id in (None, "") and identity_key(employee_name) in {
                identity_key(identity.get("canonical_display_name")), identity_key(identity.get("current_deputy_name")),
            })
        ]
        if len(matches) == 1:
            item.update(matches[0])
        people.append(item)
    target_id = person.get("deputy_employee_id")
    target_aliases = {identity_key(value) for value in person.get("aliases", []) if identity_key(value)}
    if not any(
        (target_id not in (None, "") and str(item.get("employee_id") or item.get("deputy_employee_id") or "") == str(target_id))
        or (target_aliases and identity_key(item.get("employee_name")) in target_aliases)
        for item in people
    ):
        people.append({
            "employee_id": target_id,
            "employee_name": str(next(iter(person.get("aliases", [])), "")),
            **person,
        })
    return people


def _target_indexes(people: list[dict[str, object]], person: dict[str, object]) -> set[int]:
    employee_id = person.get("deputy_employee_id")
    aliases = {identity_key(value) for value in person.get("aliases", []) if identity_key(value)}
    result = set()
    for index, item in enumerate(people):
        item_id = item.get("employee_id", item.get("deputy_employee_id"))
        if employee_id not in (None, "") and item_id not in (None, "") and str(item_id) == str(employee_id):
            result.add(index)
            continue
        names = [item.get("employee_name"), item.get("canonical_display_name"), item.get("current_deputy_name")]
        if aliases.intersection(identity_key(name) for name in names if identity_key(name)):
            result.add(index)
    return result


def _is_travel_context(row: dict[str, object]) -> bool:
    location, role = _title_parts(row.get("title"))
    text = " ".join((
        str(row.get("location_name") or location or ""),
        str(row.get("role_label") or row.get("area_name") or role or ""),
    )).casefold()
    return "travel" in text or "overnighter" in text


def _preceding_vehicle(
    preceding_rows: list[dict[str, object]], preceding_structured: list[dict[str, object]],
    identities: list[dict[str, object]], person: dict[str, object], current_date: str,
) -> tuple[str, str, list[dict[str, object]], list[dict[str, object]]]:
    """Return conservative prior Travel evidence without joining unrelated batches."""
    target_id = person.get("deputy_employee_id")
    aliases = {identity_key(value) for value in person.get("aliases", []) if identity_key(value)}
    note_candidates: list[tuple[str, object]] = []
    note_only: list[dict[str, object]] = []
    travel_rows: list[dict[str, object]] = []
    for cohort in _cohorts(preceding_rows):
        if not cohort or not any(_is_travel_context(row) for row in cohort):
            continue
        cohort_date = str(cohort[0].get("date") or cohort[0].get("start_at") or "")[:10]
        if not cohort_date or cohort_date >= current_date:
            continue
        cohort_structured = [
            row for row in preceding_structured
            if str(row.get("date") or row.get("start_at") or "")[:10] == cohort_date
            and _is_travel_context(row)
        ]
        people = _cohort_people(cohort_structured, identities, person)
        target_indexes = _target_indexes(people, person)
        resolution = resolve_note_allocations(allocations_from_shifts(cohort), people)
        note_only.extend(resolution["unresolved"])
        for assignment in resolution["assignments"]:
            if int(assignment["person_index"]) in target_indexes:
                note_candidates.append((canonical_vehicle_label(assignment.get("vehicle")), assignment.get("raw")))
        travel_rows.extend(cohort)
    note_values = {value for value, _raw in note_candidates if value}
    if len(note_values) == 1:
        return next(iter(note_values)), "preceding_travel_note", travel_rows, note_only
    structured_candidates = [
        _vehicle_from_row(row) for row in preceding_structured
        if _is_travel_context(row) and _same_person(row, target_id, aliases) and _vehicle_from_row(row)
    ]
    structured_values = set(structured_candidates)
    if len(structured_values) == 1:
        return next(iter(structured_values)), "preceding_travel_structured", travel_rows, note_only
    return "", "", travel_rows, note_only


def interpret_deputy_workdays(
    rows: Iterable[dict[str, object]], *, structured_rows: Iterable[dict[str, object]] = (),
    person_identity: dict[str, object] | None = None,
    raw_evidence_owner_identity: dict[str, object] | None = None,
    identity_records: Iterable[dict[str, object]] = (),
    preceding_rows: Iterable[dict[str, object]] = (),
    preceding_structured_rows: Iterable[dict[str, object]] = (),
) -> list[dict[str, object]]:
    """Project source evidence into the single final person-level workday interpretation."""
    result = []
    structured = [dict(row) for row in structured_rows]
    preceding = [dict(row) for row in preceding_rows]
    preceding_structured = [dict(row) for row in preceding_structured_rows]
    identities = [dict(row) for row in identity_records]
    person = dict(person_identity or {})
    raw_owner = dict(raw_evidence_owner_identity or {})
    employee_id = person.get("deputy_employee_id")
    aliases = {identity_key(value) for value in person.get("aliases", []) if identity_key(value)}
    for evidence in _cohorts([dict(row) for row in rows]):
        first = evidence[0]
        title_location, title_role = _title_parts(first.get("title"))
        production = [row for row in evidence if not _row_is_vehicle(row)]
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
        connected_structured = [
            row for row in structured
            if _overlaps_or_touches(row, cohort_start, cohort_end)
            and any(_structured_row_connects_evidence(row, source) for source in evidence)
        ]
        target_structured = [row for row in connected_structured if _same_person(row, employee_id, aliases)]
        # Raw rows may be enriched for the viewing account.  They are reusable
        # as shared note evidence, but their resolved vehicle belongs only to
        # that account and must not leak into another person's projection.
        owns_raw_evidence = raw_evidence_owner_identity is None or bool(
            raw_owner and _target_indexes([raw_owner], person)
        )
        structured_values, structured_value_rows = _current_vehicle_candidates(
            evidence, target_structured, owns_raw_evidence=owns_raw_evidence,
        )
        structured_vehicle = structured_values[0] if len(structured_values) == 1 else ""
        structured_conflict = len(structured_values) > 1
        resolution_people = _cohort_people(connected_structured, identities, person)
        target_indexes = _target_indexes(resolution_people, person)
        note_resolution = resolve_note_allocations(allocations_from_shifts(evidence), resolution_people)
        target_note_assignments = [
            item for item in note_resolution["assignments"] if int(item["person_index"]) in target_indexes
        ]
        note_vehicles = {canonical_vehicle_label(item.get("vehicle")) for item in target_note_assignments if item.get("vehicle")}
        note_vehicle = next(iter(note_vehicles)) if len(note_vehicles) == 1 else ""
        note_conflict = len(note_vehicles) > 1
        cross_source_conflict = bool(note_vehicle and structured_vehicle and note_vehicle != structured_vehicle)
        starts = [str(row.get("start_at") or row.get("start") or "") for row in evidence]
        finishes = [str(row.get("end_at") or row.get("end") or "") for row in evidence]
        date_text = str(anchor.get("date") or starts[0] or "")[:10]
        prior_vehicle, prior_source, prior_rows, prior_note_only = _preceding_vehicle(
            preceding, preceding_structured, identities, person, date_text,
        )
        current_vehicle = note_vehicle or structured_vehicle
        prior_blocked = not current_vehicle and (structured_conflict or note_conflict)
        vehicle = canonical_vehicle_label(current_vehicle or ("" if prior_blocked else prior_vehicle))
        vehicle_source = (
            "current_roster_note" if note_vehicle
            else "structured_deputy_conflict" if structured_conflict
            else "current_roster_note_conflict" if note_conflict
            else "structured_deputy" if structured_vehicle
            else prior_source
        )
        source_ids = list(dict.fromkeys(
            row.get("source_shift_id", row.get("id")) for row in evidence
            if row.get("source_shift_id", row.get("id")) not in (None, "")
        ))
        start = min((value[11:16] for value in starts if len(value) >= 16), default="")
        finish = max((value[11:16] for value in finishes if len(value) >= 16), default="")
        identity_source = f"{date_text}|{start}|{finish}|{'|'.join(map(str, source_ids))}"
        note_evidence = [item.get("raw") for item in target_note_assignments]
        item = {
            "date": date_text,
            "location": location,
            "production_position": "/".join(positions) or "Shift",
            "vehicle": vehicle,
            "vehicle_provenance": vehicle_source,
            "structured_vehicle": structured_vehicle,
            "vehicle_conflict": bool(structured_conflict or note_conflict or cross_source_conflict),
            "vehicle_conflict_values": list(dict.fromkeys(
                (structured_values if structured_conflict else ([structured_vehicle] if structured_vehicle else []))
                + (sorted(note_vehicles) if note_vehicles else [])
            )) if (structured_conflict or note_conflict or cross_source_conflict) else [],
            "roster_note_vehicle": note_vehicle,
            "rostered_start": start,
            "rostered_finish": finish,
            "raw_source_shift_ids": source_ids,
            "field_provenance": {
                "rostered_start": "deputy_roster", "rostered_finish": "deputy_roster",
                **({"vehicle": vehicle_source} if vehicle else {}),
            },
            "vehicle_evidence": {
                "final": vehicle, "final_source": vehicle_source,
                "structured_value": structured_vehicle,
                "structured_values": structured_values,
                "structured_conflict": structured_conflict,
                "structured_rows": structured_value_rows,
                "roster_note_conflict": note_conflict,
                "cross_source_conflict": cross_source_conflict,
                "cross_source_conflict_values": [structured_vehicle, note_vehicle] if cross_source_conflict else [],
                "roster_note_value": note_vehicle, "roster_note_rows": note_evidence,
                "linked_travel_rows": [row for row in evidence + target_structured if _row_is_vehicle(row)],
                "preceding_travel_value": prior_vehicle, "preceding_travel_rows": prior_rows,
                "unresolved_roster_note": note_resolution["unresolved"] + prior_note_only,
                "note_only_people": [
                    {"name": item.get("name"), "vehicle": item.get("vehicle"), "raw": item.get("raw")}
                    for item in note_resolution["unresolved"] + prior_note_only
                ],
            },
            "current_roster_note_evidence": note_evidence,
            "structured_deputy_evidence": evidence + target_structured,
            "logical_workday_id": hashlib.sha256(identity_source.encode()).hexdigest()[:24],
        }
        generic_truck = next(
            (assignment for assignment in target_note_assignments if assignment.get("vehicle_specificity") == "generic"),
            None,
        )
        if generic_truck and vehicle == "Truck":
            item["vehicle_evidence"].update({"vehicle_type": "truck", "vehicle_specificity": "generic"})
        item["revision"] = hashlib.sha256(json.dumps({
            "ids": source_ids, "position": item["production_position"], "vehicle": vehicle,
            "start": start, "finish": finish,
        }, sort_keys=True).encode()).hexdigest()
        result.append(item)
    return sorted(result, key=lambda item: (str(item["date"]), str(item["rostered_start"]), str(item["location"])))


def interpret_deputy_workdays_for_people(
    rows: Iterable[dict[str, object]], *, structured_rows: Iterable[dict[str, object]] = (),
    identity_records: Iterable[dict[str, object]] = (),
    preceding_rows: Iterable[dict[str, object]] = (),
    preceding_structured_rows: Iterable[dict[str, object]] = (),
    raw_evidence_owner_identity: dict[str, object] | None = None,
) -> dict[int, list[dict[str, object]]]:
    """Return the canonical workday projection for every known person in a cohort.

    This intentionally delegates every effective-vehicle decision to
    :func:`interpret_deputy_workdays`; shared displays must never recreate a
    parallel note or carryover resolver.
    """
    raw_rows = [dict(row) for row in rows]
    structured = [dict(row) for row in structured_rows]
    identities = [dict(row) for row in identity_records]
    prior_rows = [dict(row) for row in preceding_rows]
    prior_structured = [dict(row) for row in preceding_structured_rows]
    result: dict[int, list[dict[str, object]]] = {}
    for identity in identities:
        person_id = identity.get("id")
        try:
            person_key = int(person_id)
        except (TypeError, ValueError):
            continue
        result[person_key] = interpret_deputy_workdays(
            raw_rows,
            structured_rows=structured,
            person_identity=identity,
            raw_evidence_owner_identity=raw_evidence_owner_identity,
            identity_records=identities,
            preceding_rows=prior_rows,
            preceding_structured_rows=prior_structured,
        )
    return result
