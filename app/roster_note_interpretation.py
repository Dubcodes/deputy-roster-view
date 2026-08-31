from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Iterable

VEHICLE_ALLOCATION_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*\d*|(?<!\d)\d{3}(?!\d)")
VEHICLE_ALLOCATION_TOKEN_RE = re.compile(
    r"^(?:\d{3}|rav(?:\d+)?|rp\d+|ob|tender|transit)$", re.IGNORECASE,
)
CONNECTORS = {"and", "plus", "with", "the"}
TRUCK_LABEL = "Truck (unspecified)"
TRUCK_ACTION_RE = re.compile(r"(?i)^(.+?)\s+(?:driving|drive)\s+trucks?$")
_vehicle_alias_cache: tuple[float, dict[str, str]] = (0.0, {})


def clear_vehicle_alias_cache() -> None:
    global _vehicle_alias_cache
    _vehicle_alias_cache = (0.0, {})


def identity_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def vehicle_note_label(value: object) -> str:
    clean = str(value or "").strip()
    upper = clean.upper()
    if upper == "RAV":
        return "Rav91"
    if upper == "OB":
        return "OB"
    if upper.startswith("RAV") and upper[3:].isdigit():
        return f"Rav{upper[3:]}"
    if upper.startswith("RP") and upper[2:].isdigit():
        return f"RP{upper[2:]}"
    if upper == "TENDER":
        return "Tender"
    if upper == "TRANSIT":
        return "Transit"
    return clean


def vehicle_aliases() -> dict[str, str]:
    global _vehicle_alias_cache
    cached_at, known = _vehicle_alias_cache
    if time.monotonic() - cached_at <= 30:
        return known
    # Database imports the workday interpreter, so defer this import until the
    # parser is actually asked to resolve the configured vehicle catalogue.
    from .database import list_crew_vehicles

    known = {"rav": "Rav91", "rav91": "Rav91"}
    try:
        catalogue = list_crew_vehicles(include_inactive=False)
    except sqlite3.OperationalError:
        # Parsing is also used by migrations and isolated fixtures before the
        # schedule tables exist; built-in conservative aliases still apply.
        catalogue = []
    for item in catalogue:
        canonical = str(item.get("display_label") or "").strip()
        labels = [canonical]
        try:
            aliases = json.loads(str(item.get("aliases") or "[]"))
        except (TypeError, ValueError):
            aliases = []
        labels.extend(str(alias) for alias in aliases if isinstance(alias, str))
        for label in labels:
            key = identity_key(label)
            if key:
                known[key] = canonical or label.strip()
    _vehicle_alias_cache = (time.monotonic(), known)
    return known


def canonical_vehicle_label(value: object) -> str:
    label = str(value or "").strip()
    if not label:
        return ""
    key = identity_key(label)
    known = vehicle_aliases()
    if key in known:
        return known[key]
    # `qua684` is an established human shorthand for vehicle 684.  Other
    # QUA-number labels may be real, distinct catalogue labels, so never
    # generalise this into a prefix-stripping rule.
    if key == "qua684":
        return known.get("684", "684")
    suffix = re.fullmatch(r"(?!qua)[a-z]{1,4}(\d{3})", key)
    if suffix:
        suffix_key = suffix.group(1)
        return known.get(suffix_key, suffix_key)
    return vehicle_note_label(label)


def _vehicle_token(token: str) -> str:
    canonical = canonical_vehicle_label(token)
    key = identity_key(token)
    if VEHICLE_ALLOCATION_TOKEN_RE.fullmatch(token) or key in vehicle_aliases():
        return canonical
    if key == "qua684" or re.fullmatch(r"(?!qua)[a-z]{1,4}\d{3}", key):
        return canonical
    if re.fullmatch(r"qua\d{3}", key):
        # Preserve unregistered QUA labels exactly; they must not silently
        # become another vehicle merely because their numeric suffix matches.
        return str(token).strip()
    return ""


def _person_tokens(tokens: Iterable[str]) -> list[str]:
    return [
        token.strip(" ,") for token in tokens
        if token.strip(" ,") and token.casefold() not in CONNECTORS and not _vehicle_token(token)
    ]


def _truck_people(value: str) -> list[str]:
    """Accept the established short name-list grammar for generic truck notes."""
    people = _person_tokens(VEHICLE_ALLOCATION_WORD_RE.findall(value))
    if not people or any(not re.fullmatch(r"[A-Z][A-Za-z'-]*", person) for person in people):
        return []
    return people


def note_vehicle_allocations_from_text(value: str) -> list[dict[str, object]]:
    """Parse real roster-note vehicle lines into individual conservative names."""
    text = re.split(r"\s+[-–]\s+", str(value or ""), maxsplit=1)[-1].strip()
    truck = re.match(r"(?i)^trucks?\s+(.+)$", text)
    if not truck:
        truck = TRUCK_ACTION_RE.match(text)
    if not truck:
        truck = re.match(r"(?i)^(.+?)\s+trucks?$", text)
    if truck:
        people = _truck_people(truck.group(1))
        return [{"vehicle": TRUCK_LABEL, "people": people, "raw": value}] if people else []
    tokens = VEHICLE_ALLOCATION_WORD_RE.findall(text)
    vehicle_indexes = [(index, _vehicle_token(token)) for index, token in enumerate(tokens) if _vehicle_token(token)]
    if not vehicle_indexes:
        return []
    allocations: list[dict[str, object]] = []
    if len(vehicle_indexes) == 1:
        index, vehicle = vehicle_indexes[0]
        people = _person_tokens(tokens[:index] + tokens[index + 1:])
        if people:
            allocations.append({"vehicle": vehicle, "people": people, "raw": value})
        return allocations
    for position, (index, vehicle) in enumerate(vehicle_indexes):
        end = vehicle_indexes[position + 1][0] if position + 1 < len(vehicle_indexes) else len(tokens)
        people = _person_tokens(tokens[index + 1:end])
        if not people and position == 0:
            people = _person_tokens(tokens[:index])
        if people:
            allocations.append({"vehicle": vehicle, "people": people, "raw": value})
    return allocations


def allocations_from_shifts(shifts: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for raw_shift in shifts:
        shift = dict(raw_shift)
        lines: list[str] = []
        summary = shift.get("roster_summary") if isinstance(shift.get("roster_summary"), dict) else {}
        for item in summary.get("crew_allocations") or []:
            if isinstance(item, dict):
                vehicle = canonical_vehicle_label(item.get("vehicle"))
                people = _person_tokens(VEHICLE_ALLOCATION_WORD_RE.findall(str(item.get("people") or "")))
                if vehicle and people:
                    result.append({"vehicle": vehicle, "people": people, "raw": item})
        lines.extend(str(line) for line in shift.get("description_lines") or [] if str(line).strip())
        if shift.get("description"):
            lines.extend(str(shift["description"]).splitlines())
        try:
            payload = json.loads(str(shift.get("source_payload") or shift.get("raw_payload") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            for key in ("description", "note"):
                if payload.get(key):
                    lines.extend(str(payload[key]).splitlines())
        for line in lines:
            result.extend(note_vehicle_allocations_from_text(line))
    deduped = []
    for item in result:
        vehicle = canonical_vehicle_label(item.get("vehicle"))
        people = tuple(str(name) for name in item.get("people") or [])
        key = (identity_key(vehicle), tuple(identity_key(name) for name in people))
        if not vehicle or not people or key in seen:
            continue
        seen.add(key)
        deduped.append({**item, "vehicle": vehicle, "people": list(people)})
    return deduped


def _person_aliases(person: dict[str, object]) -> set[str]:
    values = [
        person.get("employee_name"), person.get("canonical_display_name"),
        person.get("current_deputy_name"), person.get("deputy_employee_name"),
    ]
    values.extend(person.get("aliases") or [])
    keys = {identity_key(value) for value in values if identity_key(value)}
    # Retained Deputy display-name history is useful for explicit suffixes
    # such as "Sir Daniel Hunter ESQ.".  These are still exact tokens within
    # an already isolated cohort, never a global fuzzy match.
    for value in (person.get("current_deputy_name"), person.get("deputy_employee_name")):
        keys.update(identity_key(word) for word in re.findall(r"[A-Za-z0-9]+", str(value or "")) if identity_key(word))
    return keys


def resolve_note_allocations(
    allocations: Iterable[dict[str, object]], people: list[dict[str, object]],
) -> dict[str, object]:
    """Resolve within an already isolated cohort; ambiguous names remain unresolved."""
    exact: dict[str, set[int]] = {}
    first: dict[str, set[int]] = {}
    for index, person in enumerate(people):
        for key in _person_aliases(person):
            exact.setdefault(key, set()).add(index)
        names = [person.get("employee_name"), person.get("canonical_display_name"), person.get("current_deputy_name")]
        for name in names:
            words = re.findall(r"[a-z0-9]+", str(name or "").casefold())
            if words:
                first.setdefault(words[0], set()).add(index)
    assignments: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    for allocation in allocations:
        vehicle = canonical_vehicle_label(allocation.get("vehicle"))
        for raw_name in allocation.get("people") or []:
            name = str(raw_name).strip()
            key = identity_key(name)
            matches = set(exact.get(key, set()))
            if not matches:
                matches = set(first.get(key, set()))
            evidence = {"vehicle": vehicle, "name": name, "raw": allocation.get("raw")}
            if len(matches) == 1:
                assignments.append({**evidence, "person_index": matches.pop()})
            else:
                unresolved.append({**evidence, "candidate_count": len(matches)})
    return {"assignments": assignments, "unresolved": unresolved}
