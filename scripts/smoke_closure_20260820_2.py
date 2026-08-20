from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = Path(tempfile.mkdtemp(prefix="redeputy-closure-"))
os.environ.update(DATA_DIR=str(TEMP), DB_PATH=str(TEMP / "closure.sqlite3"), APP_SECRET_KEY="closure-fixture")

from app.database import get_connection, init_db, save_deputy_web_schedule
from app.deputy_web import _extract_management_shifts, _extract_schedule_shifts
from app.interpreted_workdays import interpret_deputy_workdays, interpret_deputy_workdays_for_people
from app.roster_note_interpretation import note_vehicle_allocations_from_text, resolve_note_allocations


def row(uid: str, start: str, end: str, role: str = "Director", **extra: object) -> dict[str, object]:
    return {
        "id": uid, "source_shift_id": uid, "date": start[:10],
        "title": f"[T-Test] {role}", "location_name": "Test Park",
        "role_label": role, "start_at": start, "end_at": end, **extra,
    }


# A: same date/location but two non-overlapping duties remain two workdays.
split = interpret_deputy_workdays([
    row("morning", "2026-08-25T07:00:00+12:00", "2026-08-25T10:00:00+12:00"),
    row("evening", "2026-08-25T15:00:00+12:00", "2026-08-25T18:00:00+12:00"),
])
assert len(split) == 2 and [item["rostered_start"] for item in split] == ["07:00", "15:00"]

# B: overlapping production and vehicle rows are one workday.
combined = interpret_deputy_workdays([
    row("production", "2026-08-26T09:00:00+12:00", "2026-08-26T17:00:00+12:00"),
    row("vehicle", "2026-08-26T08:30:00+12:00", "2026-08-26T17:30:00+12:00", "Rav91"),
])
assert len(combined) == 1 and combined[0]["production_position"] == "Director" and combined[0]["vehicle"] == "Rav91"

# Exact boundary adjacency joins only complementary vehicle/travel + production evidence.
touching = interpret_deputy_workdays([
    row(
        "taupo-vehicle", "2026-08-16T07:30:00+12:00", "2026-08-16T09:30:00+12:00", "Rav91",
        title="[Taupo] Rav91", location_name="Taupo", employee_name="Jayden Smith",
    ),
    row(
        "taupo-production", "2026-08-16T09:30:00+12:00", "2026-08-16T19:30:00+12:00", "Sound/VT",
        title="[Taupo] Sound/VT", location_name="Taupo", employee_name="Jayden Smith",
    ),
], person_identity={"aliases": ["Jayden Smith", "Jayden"]})
assert len(touching) == 1
assert (touching[0]["location"], touching[0]["production_position"], touching[0]["vehicle"]) == ("Taupo", "Sound/VT", "Rav91")
assert (touching[0]["rostered_start"], touching[0]["rostered_finish"]) == ("07:30", "19:30")

# The shared parser handles real multi-person, vehicle-first/last and qua684 lines.
taupo_allocations = []
for line in ("684 james grant lans", "Rav Alf jayden and josh", "Matt and Troy trucks"):
    taupo_allocations.extend(note_vehicle_allocations_from_text(line))
assert [(item["vehicle"], item["people"]) for item in taupo_allocations] == [
    ("684", ["james", "grant", "lans"]),
    ("Rav91", ["Alf", "jayden", "josh"]),
    ("Truck (unspecified)", ["Matt", "Troy"]),
]
assert note_vehicle_allocations_from_text("qua684 Jayden")[0]["vehicle"] == "684"
assert note_vehicle_allocations_from_text("QUA690 Olivia")[0]["vehicle"] == "QUA690"
assert note_vehicle_allocations_from_text("Rav Olivia, Alf and Todd")[0]["people"] == ["Olivia", "Alf", "Todd"]
assert note_vehicle_allocations_from_text("685 Jr, Lans and Josh")[0]["people"] == ["Jr", "Lans", "Josh"]
assert not note_vehicle_allocations_from_text("Troy ,Gaz, Jayden and Nate")  # no invented vehicle context

travel_allocations = []
for line in (
    "Trucks Dylan and Esq",
    "Grant, Todd, Lans and Junior Rav91",
    "Josh Jayden Nate qua684",
):
    travel_allocations.extend(note_vehicle_allocations_from_text(line))
travel_people = [
    {"employee_id": 7, "employee_name": "Danny Hunter", "aliases": ["Esq"]},
    {"employee_id": 13, "employee_name": "Gary McClure", "aliases": ["Jr", "Jnr", "Junior"]},
    {"employee_id": 14, "employee_name": "Gary Russo", "aliases": ["Gaz", "Gazz"]},
    {"employee_id": 20, "employee_name": "Grant Woolston"},
    {"employee_id": 21, "employee_name": "Lans"},
    {"employee_id": 22, "employee_name": "Joshua", "aliases": ["Josh"]},
    {"employee_id": 23, "employee_name": "Jayden"},
    {"employee_id": 24, "employee_name": "Nate"},
    {"employee_id": 25, "employee_name": "Dylan"},
]
travel_resolution = resolve_note_allocations(travel_allocations, travel_people)
assigned = {
    travel_people[int(item["person_index"])]["employee_id"]: item["vehicle"]
    for item in travel_resolution["assignments"]
}
assert assigned == {7: "Truck (unspecified)", 13: "Rav91", 20: "Rav91", 21: "Rav91", 22: "684", 23: "684", 24: "684", 25: "Truck (unspecified)"}
assert any(item["name"] == "Todd" for item in travel_resolution["unresolved"])
assert 14 not in assigned  # Gaz/Gary Russo is never confused with Gary McClure #13.

# Production-shaped Rotorua and Travel cohorts: the shared projection uses the
# same per-person resolver as a personal workday, never an independent parser.
identities = [
    {"id": 7, "deputy_employee_id": 7, "canonical_display_name": "Danny Hunter", "aliases": ["Esq"]},
    {"id": 13, "deputy_employee_id": 13, "canonical_display_name": "Gary McClure", "aliases": ["Junior", "Jr"]},
    {"id": 14, "deputy_employee_id": 14, "canonical_display_name": "Gary Russo", "aliases": ["Gaz", "Gazz"]},
    {"id": 20, "deputy_employee_id": 20, "canonical_display_name": "Grant Woolston", "aliases": ["Grant"]},
    {"id": 21, "deputy_employee_id": 21, "canonical_display_name": "Lans", "aliases": []},
    {"id": 22, "deputy_employee_id": 22, "canonical_display_name": "Joshua", "aliases": ["Josh"]},
    {"id": 23, "deputy_employee_id": 23, "canonical_display_name": "Jayden", "aliases": []},
    {"id": 24, "deputy_employee_id": 24, "canonical_display_name": "Nathan", "aliases": ["Nate"]},
    {"id": 25, "deputy_employee_id": 25, "canonical_display_name": "Dylan", "aliases": []},
]
rotorua = interpret_deputy_workdays_for_people([
    row("rotorua-12", "2026-08-12T09:00:00+12:00", "2026-08-12T18:00:00+12:00", "Sound",
        title="[Rotorua] Sound", location_name="Rotorua", description="Troy ,Gaz, Jayden and Nate"),
], structured_rows=[
    {"employee_id": 14, "employee_name": "Gary Russo", "area_name": "Rav91", "start_at": "2026-08-12T09:00:00+12:00", "end_at": "2026-08-12T18:00:00+12:00"},
    {"employee_id": 23, "employee_name": "Jayden", "area_name": "684", "start_at": "2026-08-12T09:00:00+12:00", "end_at": "2026-08-12T18:00:00+12:00"},
    {"employee_id": 24, "employee_name": "Nathan", "area_name": "685", "start_at": "2026-08-12T09:00:00+12:00", "end_at": "2026-08-12T18:00:00+12:00"},
], identity_records=identities)
assert rotorua[14][0]["vehicle"] == "Rav91" and rotorua[23][0]["vehicle"] == "684" and rotorua[24][0]["vehicle"] == "685"
assert 13 in rotorua and 14 in rotorua  # Gary #13 and Gaz/Gary #14 remain distinct.

travel_workdays = interpret_deputy_workdays_for_people([
    row("travel-14", "2026-08-14T12:00:00+12:00", "2026-08-14T17:00:00+12:00", "Travel then Overnighter",
        title="[Travel] Travel then Overnighter", location_name="Travel",
        description="Trucks Dylan and Esq\nGrant, Todd, Lans and Junior Rav91\nJosh Jayden Nate qua684"),
], structured_rows=[
    {"employee_id": identity["deputy_employee_id"], "employee_name": identity["canonical_display_name"],
     "area_name": "Travel", "location_name": "Travel", "start_at": "2026-08-14T12:00:00+12:00", "end_at": "2026-08-14T17:00:00+12:00"}
    for identity in identities if identity["id"] != 14
], identity_records=identities)
assert {key: travel_workdays[key][0]["vehicle"] for key in (7, 13, 20, 21, 22, 23, 24, 25)} == {
    7: "Truck (unspecified)", 13: "Rav91", 20: "Rav91", 21: "Rav91", 22: "684", 23: "684", 24: "684", 25: "Truck (unspecified)",
}
assert any(item["name"] == "Todd" for item in travel_workdays[20][0]["vehicle_evidence"]["unresolved_roster_note"])

esq_resolution = resolve_note_allocations(
    note_vehicle_allocations_from_text("Trucks Esq"),
    [{"employee_id": 7, "employee_name": "Danny Hunter", "current_deputy_name": "Sir Daniel Hunter ESQ."}],
)
assert esq_resolution["assignments"] == [{
    "vehicle": "Truck (unspecified)", "name": "Esq", "raw": "Trucks Esq", "person_index": 0,
}]

ambiguous = resolve_note_allocations(
    note_vehicle_allocations_from_text("Rav Grant"),
    [{"employee_name": "Grant Woolston"}, {"employee_name": "Grant Another"}],
)
assert not ambiguous["assignments"] and ambiguous["unresolved"][0]["candidate_count"] == 2

# Cohort resolution ignores a simultaneous same-first-name person at another venue.
isolated_grant = interpret_deputy_workdays([
    row(
        "ruakaka-grant", "2026-08-24T09:00:00+12:00", "2026-08-24T17:00:00+12:00",
        title="[Ruakaka] Sound", location_name="Ruakaka", employee_name="Grant Woolston",
        description="Grant, Todd, Lans and Junior Rav91",
    ),
], structured_rows=[
    {"source_shift_id": 801, "employee_id": 20, "employee_name": "Grant Woolston", "area_name": "684",
     "location_name": "Ruakaka", "start_at": "2026-08-24T08:30:00+12:00", "end_at": "2026-08-24T17:30:00+12:00"},
    {"source_shift_id": 802, "employee_id": 26, "employee_name": "Grant Another", "area_name": "685",
     "location_name": "Taupo", "start_at": "2026-08-24T08:30:00+12:00", "end_at": "2026-08-24T17:30:00+12:00"},
], person_identity={"deputy_employee_id": 20, "aliases": ["Grant Woolston"]})
assert isolated_grant[0]["vehicle"] == "Rav91"
assert isolated_grant[0]["field_provenance"]["vehicle"] == "current_roster_note"

# A preceding Travel batch can supply a vehicle across the venue/date boundary,
# without being merged into the following Ruakaka workday itself.
carryover = interpret_deputy_workdays([
    row("ruakaka-production", "2026-08-15T09:00:00+12:00", "2026-08-15T18:00:00+12:00", "Sound",
        title="[T-Ruakaka] Sound", location_name="T-Ruakaka", employee_name="Grant Woolston"),
], person_identity={"deputy_employee_id": 20, "aliases": ["Grant Woolston", "Grant"]}, preceding_rows=[
    row("travel-grant", "2026-08-14T12:00:00+12:00", "2026-08-14T17:00:00+12:00", "Travel then Overnighter",
        title="[T-Travel] Travel then Overnighter", location_name="T-Travel",
        description="Grant, Todd, Lans and Junior Rav91"),
    row("travel-other", "2026-08-14T12:00:00+12:00", "2026-08-14T17:00:00+12:00", "Travel then Overnighter",
        title="[T-Travel] Travel then Overnighter", location_name="T-Travel",
        description="Rav Gary and Olivia"),
], preceding_structured_rows=[
    {"source_shift_id": 901, "date": "2026-08-14", "employee_id": 20, "employee_name": "Grant Woolston",
     "area_name": "Travel then Overnighter", "location_name": "T-Travel",
     "start_at": "2026-08-14T12:00:00+12:00", "end_at": "2026-08-14T17:00:00+12:00"},
])
assert carryover[0]["vehicle"] == "Rav91"
assert carryover[0]["vehicle_provenance"] == "preceding_travel_note"
assert len(carryover[0]["raw_source_shift_ids"]) == 1

note_only = interpret_deputy_workdays([
    row("rotorua", "2026-08-17T09:00:00+12:00", "2026-08-17T17:00:00+12:00", "Sound",
        title="[Rotorua] Sound", location_name="Rotorua", employee_name="Olivia",
        description="Rav Olivia, Alf and Todd"),
], person_identity={"aliases": ["Olivia"]})
assert any(item["name"] == "Todd" and item["vehicle"] == "Rav91" for item in note_only[0]["vehicle_evidence"]["note_only_people"])

# C: an exact current-note allocation overrides structured Deputy for that person only.
note_override = interpret_deputy_workdays([
    row(
        "jayden", "2026-08-27T09:00:00+12:00", "2026-08-27T17:00:00+12:00",
        employee_name="Jayden Smith",
        description="684 james grant lans\nRav Alf Jayden and Josh\nMatt and Troy trucks",
    )
], structured_rows=[{
    "source_shift_id": 701, "employee_id": 7, "employee_name": "Jayden Smith", "area_name": "684",
    "start_at": "2026-08-27T08:30:00+12:00", "end_at": "2026-08-27T17:30:00+12:00",
}], person_identity={"deputy_employee_id": 7, "aliases": ["Jayden Smith", "Jayden"]})
assert note_override[0]["vehicle"] == "Rav91"
assert note_override[0]["field_provenance"]["vehicle"] == "current_roster_note"
assert note_override[0]["vehicle_evidence"]["structured_value"] == "684"
assert note_override[0]["vehicle_evidence"]["roster_note_value"] == "Rav91"
unrelated = interpret_deputy_workdays([
    row(
        "jayden-2", "2026-08-28T09:00:00+12:00", "2026-08-28T17:00:00+12:00",
        employee_name="Jayden Smith", description="685 Josh",
    )
], structured_rows=[{
    "source_shift_id": 702, "employee_id": 7, "employee_name": "Jayden Smith", "area_name": "684",
    "start_at": "2026-08-28T08:30:00+12:00", "end_at": "2026-08-28T17:30:00+12:00",
}], person_identity={"deputy_employee_id": 7, "aliases": ["Jayden Smith", "Jayden"]})
assert unrelated[0]["vehicle"] == "684"

# D: personal timing presentation cannot alter roster-backed notification timing.
timing = interpret_deputy_workdays([
    row(
        "timing", "2026-08-29T09:30:00+12:00", "2026-08-29T17:00:00+12:00",
        personal_start_time="08:00", personal_finish_time="18:00",
    )
])
assert timing[0]["rostered_start"] == "09:30" and timing[0]["rostered_finish"] == "17:00"

# Rich management and schedule state survives extraction for raw-payload storage.
rich = {
    "id": 99, "employee": 7, "area": 3, "start": "2026-08-30T09:00:00+12:00",
    "end": "2026-08-30T17:00:00+12:00", "canEdit": False,
    "timesheet": {"id": 44, "status": "approved"}, "approvalRequired": True,
    "approvalStatus": "pending", "confirmationStatus": "confirmed",
    "confirmationNote": "fixture", "mealbreakDuration": 30,
    "mealbreakSlots": [{"start": "12:00", "end": "12:30"}],
    "warning": {"code": "overlap"}, "warningOverrideComment": "fixture only",
    "isOpen": False, "isPublished": True, "publicationStatus": "published",
}
management = _extract_management_shifts({"data": [rich]})[0]
schedule = _extract_schedule_shifts({"data": {"shifts": [rich]}, "metadata": {"employee": []}})[0]
required = {
    "canEdit", "timesheet", "approvalRequired", "approvalStatus", "confirmationStatus",
    "confirmationNote", "mealbreakDuration", "mealbreakSlots", "warning",
    "warningOverrideComment", "isOpen", "isPublished", "publicationStatus",
}
assert required <= management.keys() and required <= schedule.keys()
assert schedule["mealbreakSlots"][0]["start"] == "12:00" and management["timesheet"]["id"] == 44
init_db()
save_deputy_web_schedule({
    "captured_at": "2026-08-30T08:00:00+12:00",
    "locations": [{"id": 1, "name": "Test Park"}],
    "areas": [{"id": 3, "name": "Sound/VT", "locationId": 1}],
    "extracted_schedule_shifts": [schedule],
})
with get_connection() as conn:
    stored = json.loads(conn.execute(
        "SELECT raw_payload FROM deputy_schedule_shifts WHERE source_shift_id=99"
    ).fetchone()["raw_payload"])
assert stored["canEdit"] is False and stored["timesheet"]["id"] == 44
assert stored["mealbreakSlots"][0]["end"] == "12:30" and stored["warning"]["code"] == "overlap"

print("2026.08.21.1 closure fixtures passed")
