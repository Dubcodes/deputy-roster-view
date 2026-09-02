from __future__ import annotations

"""Deterministic regression coverage for Deputy split and combined vehicle rows."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.interpreted_workdays import interpret_deputy_workdays, interpret_deputy_workdays_for_people


JAYDEN = {"id": 1, "deputy_employee_id": 101, "canonical_display_name": "Jayden-lee", "aliases": ["Jayden"]}
OLIVIA = {"id": 2, "deputy_employee_id": 102, "canonical_display_name": "Olivia", "aliases": []}
MATT = {"id": 3, "deputy_employee_id": 103, "canonical_display_name": "Matt", "aliases": []}
IDENTITIES = [JAYDEN, OLIVIA, MATT]


def payload(vehicle: str = "") -> str:
    return json.dumps({"normalised": {"vehicle_label": vehicle}})


def row(
    source_id: str,
    role: str = "CCU1",
    vehicle: str = "",
    *,
    employee_id: int = 101,
    employee_name: str = "Jayden-lee",
    date: str = "2026-08-28",
    start: str = "10:15",
    end: str = "18:00",
    description: str = "",
) -> dict[str, object]:
    start_at = f"{date}T{start}:00+12:00"
    end_at = f"{date}T{end}:00+12:00"
    return {
        "id": source_id,
        "source_shift_id": source_id,
        "date": date,
        "title": f"[T-Cambridge] {role}",
        "location_name": "Cambridge Synthetic",
        "role_label": role,
        "employee_id": employee_id,
        "employee_name": employee_name,
        "start_at": start_at,
        "end_at": end_at,
        "description": description,
        "source_payload": payload(vehicle),
    }


def single(*args: object, **kwargs: object) -> dict[str, object]:
    workdays = interpret_deputy_workdays(*args, **kwargs)
    assert len(workdays) == 1
    return workdays[0]


# Historical split representation: Vehicle/Travel decorates the same person's
# production row and never becomes a fake production position.
split = single(
    [row("split-production")],
    structured_rows=[row("split-travel", "Travel", "684", start="09:45", end="10:15")],
    person_identity=JAYDEN,
)
assert (split["production_position"], split["vehicle"], split["vehicle_conflict"]) == ("CCU1", "684", False)

# This is a synthetic normalized compatibility input, not a claim about the
# unobserved live Deputy JSON shape.  A production row may retain its role and
# contribute an already-normalized explicit vehicle fact.
combined = single([row("combined-production")], structured_rows=[row("combined-production", "CCU1", "684")], person_identity=JAYDEN)
assert (combined["production_position"], combined["vehicle"], combined["vehicle_provenance"]) == ("CCU1", "684", "structured_deputy")

# Same explicit value on a companion is duplicate evidence, not a conflict;
# a blank companion cannot erase a production-row vehicle.
duplicate = single([row("duplicate-production")], structured_rows=[
    row("duplicate-production", "CCU1", "684"), row("duplicate-travel", "Travel", "684"),
], person_identity=JAYDEN)
blank_companion = single([row("blank-production")], structured_rows=[
    row("blank-production", "CCU1", "684"), row("blank-travel", "Travel", ""),
], person_identity=JAYDEN)
assert duplicate["vehicle"] == blank_companion["vehicle"] == "684"
assert not duplicate["vehicle_conflict"] and not blank_companion["vehicle_conflict"]

# Two different current explicit facts are preserved and surfaced, never chosen
# by row order.  A current explicit roster note remains the higher authority.
disagreement = single([row("disagreement-production")], structured_rows=[
    row("disagreement-production", "CCU1", "684"), row("disagreement-travel", "Travel", "685"),
], person_identity=JAYDEN)
assert disagreement["vehicle"] == "" and disagreement["vehicle_provenance"] == "structured_deputy_conflict"
assert disagreement["vehicle_evidence"]["structured_values"] == ["684", "685"]
assert disagreement["vehicle_evidence"]["structured_conflict"] is True
note_override = single([row("note-production", vehicle="684", description="Rav Jayden")], structured_rows=[row("note-production", "CCU1", "684")], person_identity=JAYDEN)
assert (note_override["vehicle"], note_override["vehicle_provenance"]) == ("Rav91", "current_roster_note")
assert note_override["vehicle_conflict"] is True
assert note_override["vehicle_conflict_values"] == ["684", "Rav91"]
assert note_override["vehicle_evidence"]["cross_source_conflict"] is True

# The narrow handoff exception applies only to a short vehicle lead. A long
# Travel participant row followed by a 30-minute gap remains a distinct duty,
# as do unrelated production rows that merely touch at a boundary.
long_travel = interpret_deputy_workdays([
    row("long-travel", "Travel", start="07:00", end="11:30"),
    row("after-long-travel", "CCU1", start="12:00", end="18:00"),
], person_identity=JAYDEN)
touching_production = interpret_deputy_workdays([
    row("touching-one", "Director", start="07:00", end="10:00"),
    row("touching-two", "CCU1", start="10:00", end="18:00"),
], person_identity=JAYDEN)
assert len(long_travel) == 2
assert len(touching_production) == 2

# Personal structured evidence can fill a shared blank for its canonical owner,
# but cannot overwrite a shared explicit disagreement.  Olivia/Matt's separate
# generic truck context never participates in Jayden's evidence.
personal_raw = [row("personal-jayden", "CCU1", "684")]
personal_fill = interpret_deputy_workdays_for_people(
    personal_raw,
    structured_rows=[row("shared-jayden", "CCU1", "")],
    identity_records=IDENTITIES,
    raw_evidence_owner_identity=JAYDEN,
)
assert personal_fill[1][0]["vehicle"] == "684"
assert personal_fill[2][0]["vehicle"] == personal_fill[3][0]["vehicle"] == ""
personal_conflict = interpret_deputy_workdays_for_people(
    personal_raw,
    structured_rows=[
        row("shared-jayden", "CCU1", "685"),
        row("olivia", "Head On", "Truck (unspecified)", employee_id=102, employee_name="Olivia"),
        row("matt", "ENG", "Truck (unspecified)", employee_id=103, employee_name="Matt"),
    ],
    identity_records=IDENTITIES,
    raw_evidence_owner_identity=JAYDEN,
)
assert personal_conflict[1][0]["vehicle"] == "" and personal_conflict[1][0]["vehicle_conflict_values"] == ["685", "684"]
assert personal_conflict[2][0]["vehicle"] == personal_conflict[3][0]["vehicle"] == "Truck"

# Preceding Travel remains a fallback only.  A changed current assignment wins;
# after removal, the old current value is not retained and the valid preceding
# vehicle is used instead.
prior_raw = [row("prior-travel", "Travel", "", date="2026-08-27")]
prior_structured = [row("prior-travel", "Travel", "684", date="2026-08-27")]
changed = single([row("changed", "CCU1", "Rav91")], person_identity=JAYDEN, preceding_rows=prior_raw, preceding_structured_rows=prior_structured)
removed = single([row("removed", "CCU1", "")], person_identity=JAYDEN, preceding_rows=prior_raw, preceding_structured_rows=prior_structured)
assert (changed["vehicle"], changed["vehicle_provenance"]) == ("Rav91", "structured_deputy")
assert (removed["vehicle"], removed["vehicle_provenance"]) == ("684", "preceding_travel_structured")

# Current conflicts remain unresolved and cannot fall through to an older Travel
# vehicle, but the historical evidence remains visible for diagnosis.
conflict_prior_raw = [row("conflict-prior", "Travel", "", date="2026-08-27")]
conflict_prior_structured = [row("conflict-prior", "Travel", "Rav91", date="2026-08-27")]
structured_with_prior_conflict = single(
    [row("structured-conflict-current")],
    structured_rows=[
        row("structured-conflict-current", "CCU1", "684"),
        row("structured-conflict-travel", "Travel", "685"),
    ],
    person_identity=JAYDEN,
    preceding_rows=conflict_prior_raw,
    preceding_structured_rows=conflict_prior_structured,
)
assert structured_with_prior_conflict["vehicle"] == ""
assert structured_with_prior_conflict["vehicle_evidence"]["structured_values"] == ["684", "685"]
assert structured_with_prior_conflict["vehicle_evidence"]["structured_conflict"] is True
assert structured_with_prior_conflict["vehicle_evidence"]["preceding_travel_value"] == "Rav91"
assert structured_with_prior_conflict["vehicle_evidence"]["preceding_travel_rows"]

note_with_prior_conflict = single(
    [row("note-conflict-current", description="684 Jayden\n685 Jayden")],
    person_identity=JAYDEN,
    preceding_rows=conflict_prior_raw,
    preceding_structured_rows=conflict_prior_structured,
)
assert note_with_prior_conflict["vehicle"] == ""
assert note_with_prior_conflict["vehicle_evidence"]["roster_note_conflict"] is True
assert note_with_prior_conflict["vehicle_evidence"]["preceding_travel_value"] == "Rav91"

note_conflict_with_structured = single(
    [row("note-conflict-with-structured", description="684 Jayden\n685 Jayden")],
    structured_rows=[row("note-conflict-with-structured", "CCU1", "684")],
    person_identity=JAYDEN,
    preceding_rows=conflict_prior_raw,
    preceding_structured_rows=conflict_prior_structured,
)
assert (note_conflict_with_structured["vehicle"], note_conflict_with_structured["vehicle_provenance"]) == (
    "684", "structured_deputy",
)
assert note_conflict_with_structured["vehicle_evidence"]["roster_note_conflict"] is True
assert note_conflict_with_structured["vehicle_evidence"]["preceding_travel_value"] == "Rav91"

print("combined Deputy vehicle interpretation smoke ok")
