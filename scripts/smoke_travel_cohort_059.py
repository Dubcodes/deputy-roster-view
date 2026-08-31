from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-09-01"
START = f"{DATE}T12:00:00+12:00"
END = f"{DATE}T17:00:00+12:00"
PEOPLE = (
    (101, "Grant Woolston"),
    (102, "Elliot"),
    (103, "Gary McClure"),
    (104, "Lans McGall"),
    (105, "Nate"),
    (106, "Dylan Holden"),
    (107, "Joshua Druett"),
    (108, "Olivia Dooley"),
    (17, "Jayden-lee"),
    (110, "Matt Blackmore"),
)
OUT_OF_REGION_PEOPLE = (
    (201, "Georgia Browne"),
    (202, "Mike McQueen"),
    (203, "Dee Carran"),
    (204, "Mat Clarkson"),
    (205, "Jack Conroy"),
    (206, "Jarrod van Turnhout"),
    (207, "Mark Hathaway"),
    (208, "Glen Macdonald"),
    (209, "Bri Roe"),
)


def main() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-travel-cohort-059-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "travel-cohort.sqlite3"),
        APP_SECRET_KEY="travel-cohort-059",
        TZ="Pacific/Auckland",
    )
    sys.path.insert(0, str(ROOT))

    from app.config import get_settings
    from app.database import (
        fetch_shifts_between,
        fetch_shifts_for_date,
        get_shift_changes_for_date,
        init_db,
        save_deputy_web_schedule,
    )
    from app.deputy_web import _extract_management_shifts
    from app.interpreted_workdays import interpret_deputy_workdays, interpret_deputy_workdays_for_people
    from app.main import decorate_shift, effective_schedule_items, schedule_people, travel_cohort_schedule_rows
    from app.roster_note_interpretation import note_vehicle_allocations_from_text

    init_db()
    with sqlite3.connect(get_settings().db_path) as conn:
        conn.execute(
            """INSERT INTO app_users
               (id, deputy_email, display_name, pin_hash, deputy_web_url, is_admin,
                is_active, created_at, updated_at)
               VALUES (1, 'jayden@example.test', 'Jayden-lee', 'x', 'https://example.test', 1, 1, ?, ?)""",
            ("2026-08-31T15:29:00+12:00", "2026-08-31T15:29:00+12:00"),
        )
        for employee_id, name in PEOPLE:
            conn.execute(
                """INSERT INTO crew_people
                   (deputy_employee_id, canonical_display_name, current_deputy_name,
                    is_active, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (employee_id, name, name, "2026-08-31T15:29:00+12:00", "2026-08-31T15:29:00+12:00"),
            )
        for name, alias in (("Dylan Holden", "Dylan"), ("Matt Blackmore", "Matt")):
            person_id = conn.execute(
                "SELECT id FROM crew_people WHERE canonical_display_name = ?", (name,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO crew_aliases(person_id, alias, normalized_alias, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (person_id, alias, alias.casefold(), "2026-08-31T15:29:00+12:00", "2026-08-31T15:29:00+12:00"),
            )

    def schedule_row(employee_id: int, name: str, source_shift_id: int, area_name: str) -> dict[str, object]:
        return {
            "source_shift_id": source_shift_id,
            "area_id": 700,
            "area_name": area_name,
            "area_location_id": 7000,
            "schedule_location_id": 7000,
            "employee_id": employee_id,
            "employee_name": name,
            "start_at": START,
            "end_at": END,
            "date": DATE,
            "duration": 18000,
            "captured_at": "2026-08-31T15:29:00+12:00",
            "changed_since_viewed": 0,
            "change_summary": "",
        }

    def cohort(area_name: str) -> list[dict[str, object]]:
        return [
            schedule_row(employee_id, name, 9100 + index, area_name)
            for index, (employee_id, name) in enumerate(PEOPLE, start=1)
        ]

    for area_name in ("Travel then Overnighter", "Overnighter"):
        rows, _contexts = effective_schedule_items(cohort(area_name))
        if {int(row["employee_id"]) for row in rows} != {employee_id for employee_id, _name in PEOPLE}:
            raise AssertionError(f"{area_name} cohort was replaced by source recency: {rows!r}")
        if any(row.get("changed") or row.get("assignment_changed") for row in rows):
            raise AssertionError(f"{area_name} cohort created a false assignment change: {rows!r}")
        people = schedule_people(cohort(area_name), include_vehicle_only=True, include_placeholders=False)
        if {str(row["employee_name"]) for row in people} != {name for _employee_id, name in PEOPLE}:
            raise AssertionError(f"Travel/Vehicles people table lost cohort members for {area_name}: {people!r}")

    out_of_region_rows = [
        {
            **schedule_row(employee_id, name, 9800 + index, "Out of Region"),
            "date": "2026-08-22",
            "start_at": "2026-08-22T15:00:00+12:00",
            "end_at": "2026-08-22T18:00:00+12:00",
        }
        for index, (employee_id, name) in enumerate(OUT_OF_REGION_PEOPLE, start=1)
    ]
    out_of_region_effective, _contexts = effective_schedule_items(out_of_region_rows)
    out_of_region_people = schedule_people(out_of_region_rows, include_vehicle_only=True, include_placeholders=False)
    if (
        {row["employee_name"] for row in out_of_region_effective}
        != {name for _employee_id, name in OUT_OF_REGION_PEOPLE}
        or {row["employee_name"] for row in out_of_region_people}
        != {name for _employee_id, name in OUT_OF_REGION_PEOPLE}
    ):
        raise AssertionError(f"Out of Region participant cohort was collapsed: {out_of_region_people!r}")

    normal_rows, _contexts = effective_schedule_items([
        schedule_row(201, "Older Director", 9201, "Director"),
        schedule_row(202, "Current Director", 9202, "Director"),
    ])
    if [row["employee_name"] for row in normal_rows] != ["Current Director"]:
        raise AssertionError(f"Ordinary production replacement stopped deduping: {normal_rows!r}")

    vehicle_rows, _contexts = effective_schedule_items([
        schedule_row(301, "Vehicle One", 9301, "684"),
        schedule_row(302, "Vehicle Two", 9302, "684"),
    ])
    if {row["employee_name"] for row in vehicle_rows} != {"Vehicle One", "Vehicle Two"}:
        raise AssertionError(f"Existing multi-person vehicle semantics changed: {vehicle_rows!r}")

    expected_truck = [{"vehicle": "Truck (unspecified)", "people": ["Dylan", "Matt"], "raw": "Dylan and Matt driving trucks"}]
    if note_vehicle_allocations_from_text("Dylan and Matt driving trucks") != expected_truck:
        raise AssertionError("Driving-trucks grammar did not retain exactly Dylan and Matt.")
    for text in ("Dylan & Matt drive trucks", "Dylan and Matt trucks", "Trucks Dylan and Matt", "Dylan, Matt trucks"):
        allocations = note_vehicle_allocations_from_text(text)
        if len(allocations) != 1 or allocations[0]["people"] != ["Dylan", "Matt"]:
            raise AssertionError(f"Established truck-list grammar regressed for {text!r}: {allocations!r}")
    if note_vehicle_allocations_from_text("We need trucks before lunch"):
        raise AssertionError("Ordinary prose was guessed as a truck crew allocation.")

    identities = [
        {"id": employee_id, "deputy_employee_id": employee_id, "canonical_display_name": name,
         "current_deputy_name": name, "aliases": (["Dylan"] if name == "Dylan Holden" else ["Matt"] if name == "Matt Blackmore" else [])}
        for employee_id, name in PEOPLE
    ]
    note_rows = [{
        "id": "travel-note", "title": "[T-Travel] Travel then Overnighter",
        "description": "Dylan and Matt driving trucks\nUnknown trucks", "location": "Travel",
        "start_at": START, "end_at": END, "date": DATE,
    }]
    structured = [
        {**row, "title": "[T-Travel] Travel then Overnighter", "location_name": "T-Travel", "role_label": "Travel then Overnighter"}
        for row in cohort("Travel then Overnighter")
    ]
    workdays = interpret_deputy_workdays_for_people(note_rows, structured_rows=structured, identity_records=identities)
    dylan = next(item for item in workdays[106] if item["date"] == DATE)
    matt = next(item for item in workdays[110] if item["date"] == DATE)
    if dylan["vehicle"] != "Truck (unspecified)" or matt["vehicle"] != "Truck (unspecified)":
        raise AssertionError(f"Unique cohort aliases did not receive the conservative truck evidence: {workdays!r}")
    unresolved = dylan["vehicle_evidence"]["note_only_people"]
    if {item["name"] for item in unresolved} != {"Unknown"}:
        raise AssertionError(f"Resolved shorthand or driving leaked into unresolved display evidence: {unresolved!r}")

    def wire_schedule(area_name: str) -> list[dict[str, object]]:
        return [
            {
                "id": 9100 + index, "employee": employee_id, "employeeName": name,
                "area": 700, "areaName": area_name, "areaLocationId": 7000,
                "location": 7000, "locationName": "T-Travel", "role": area_name,
                "start": START, "end": END, "duration": 18000, "isPublished": True, "note": "",
            }
            for index, (employee_id, name) in enumerate(PEOPLE, start=1)
        ]

    base_schedule_payload = {
        "captured_at": "2026-08-31T15:29:00+12:00",
        "areas": [{"id": 700, "name": "Travel then Overnighter", "locationId": 7000, "rosterSortOrder": 1}],
        "locations": [{"id": 7000, "name": "T-Travel", "address": ""}],
        "extracted_shifts": [],
        "own_roster_coverage": [],
        "schedule_coverage": [{"start_date": DATE, "end_date": DATE, "mode": "all", "location_ids": []}],
        "extracted_schedule_shifts": wire_schedule("Travel then Overnighter"),
    }
    save_deputy_web_schedule(base_schedule_payload, owner_user_id=1)
    save_deputy_web_schedule({
        **base_schedule_payload,
        "captured_at": "2026-08-31T15:30:00+12:00",
        "areas": [{"id": 700, "name": "Overnighter", "locationId": 7000, "rosterSortOrder": 1}],
        "extracted_schedule_shifts": wire_schedule("Overnighter"),
    }, owner_user_id=1)
    with sqlite3.connect(get_settings().db_path) as conn:
        saved_count = conn.execute("SELECT COUNT(*) FROM deputy_schedule_shifts WHERE date = ?", (DATE,)).fetchone()[0]
        false_events = conn.execute("SELECT COUNT(*) FROM deputy_schedule_event_changes WHERE date = ?", (DATE,)).fetchone()[0]
    if saved_count != len(PEOPLE) or false_events:
        raise AssertionError(f"Travel cohort persistence or event history was collapsed: {(saved_count, false_events)!r}")

    mixed_shared = [
        {
            "id": 10100 + index, "employee": employee_id, "employeeName": name,
            "area": 1412, "areaName": "Travel then Overnighter", "areaLocationId": 105,
            "location": 105, "locationName": "T-Travel", "role": "Travel then Overnighter",
            "start": START, "end": END, "duration": 18000, "isPublished": True, "note": "",
        }
        for index, (employee_id, name) in enumerate(PEOPLE, start=1)
    ]
    mixed_before = _extract_management_shifts({"data": [{
        "id": 10501, "employee": 17, "area": 1762, "areaName": "Travel then Overnighter", "areaLocationId": 158,
        "location": 158, "locationName": "T-Travel", "role": "Travel then Overnighter",
        "start": START, "end": END, "duration": 18000, "isPublished": True,
        "note": "Dylan and Matt driving trucks\nUnknown trucks",
    }]})
    mixed_after = [{**mixed_before[0], "areaName": "Overnighter", "roleName": "Overnighter"}]
    mixed_payload = {
        "captured_at": "2026-08-31T15:29:00+12:00",
        "areas": [
            {"id": 1412, "name": "Travel then Overnighter", "locationId": 105, "rosterSortOrder": 1},
            {"id": 1762, "name": "Overnighter", "locationId": 158, "rosterSortOrder": 1},
        ],
        "locations": [{"id": 105, "name": "T-Travel", "address": ""}, {"id": 158, "name": "T-Travel", "address": ""}],
        "extracted_shifts": mixed_before,
        "own_roster_coverage": [{"start_date": DATE, "end_date": DATE, "employee_id": 17,
                                  "status": "complete", "pagination_complete": True, "records_returned": 1}],
        "extracted_schedule_shifts": mixed_shared,
        "schedule_coverage": [],
    }
    save_deputy_web_schedule(mixed_payload, owner_user_id=1)
    save_deputy_web_schedule({**mixed_payload, "captured_at": "2026-08-31T15:30:00+12:00", "extracted_shifts": mixed_after}, owner_user_id=1)
    mixed_shift = next(decorate_shift(row) for row in fetch_shifts_for_date(DATE, owner_user_id=1) if ":10501" in str(row["source_uid"]))
    mixed_rows = travel_cohort_schedule_rows(DATE, [mixed_shift])
    mixed_people = schedule_people(mixed_rows, include_vehicle_only=True, include_placeholders=False)
    if (
        not any(int(row["source_shift_id"]) == 10101 and int(row["schedule_location_id"]) == 105 for row in mixed_rows)
        or {row["employee_name"] for row in mixed_people} != {name for _employee_id, name in PEOPLE}
    ):
        raise AssertionError(f"Mixed T-Travel internal IDs did not retain the older shared cohort: {mixed_rows!r}")
    mixed_workday = interpret_deputy_workdays(
        [mixed_shift], structured_rows=mixed_rows,
        person_identity={"deputy_employee_id": 17, "aliases": ["Jayden-lee"]}, identity_records=identities,
    )[0]
    if mixed_workday["production_position"] != "Overnighter":
        raise AssertionError(f"Personal Overnighter role was overwritten by shared cohort evidence: {mixed_workday!r}")
    mixed_people_workdays = interpret_deputy_workdays_for_people(
        [mixed_shift], structured_rows=mixed_rows, identity_records=identities,
    )
    mixed_dylan = next(item for item in mixed_people_workdays[106] if item["date"] == DATE)
    mixed_matt = next(item for item in mixed_people_workdays[110] if item["date"] == DATE)
    if (
        mixed_dylan["vehicle"] != "Truck (unspecified)"
        or mixed_matt["vehicle"] != "Truck (unspecified)"
        or {item["name"] for item in mixed_dylan["vehicle_evidence"]["note_only_people"]} != {"Unknown"}
        or not any(row["field_name"] == "role" for row in get_shift_changes_for_date(DATE))
    ):
        raise AssertionError("Mixed Travel cohort lost note resolution or the genuine personal role change.")

    personal_source = _extract_management_shifts({"data": [
        {"id": 9401, "employee": 17, "area": 684, "areaName": "684", "areaLocationId": 64,
         "location": 64, "locationName": "T-Ruakaka", "role": "684",
         "start": "2026-09-02T09:00:00+12:00", "end": "2026-09-02T09:30:00+12:00", "duration": 1800, "isPublished": True},
        {"id": 9402, "employee": 17, "area": 685, "areaName": "DIR", "areaLocationId": 64,
         "location": 64, "locationName": "T-Ruakaka", "role": "DIR",
         "start": "2026-09-02T09:30:00+12:00", "end": "2026-09-02T22:00:00+12:00", "duration": 45000, "isPublished": True},
        {"id": 9403, "employee": 17, "area": 686, "areaName": "SVT", "areaLocationId": 65,
         "location": 65, "locationName": "T-Te Aroha", "role": "SVT",
         "start": "2026-09-03T09:30:00+12:00", "end": "2026-09-03T18:45:00+12:00", "duration": 33300, "isPublished": True},
    ]})
    if [row["id"] for row in personal_source] != [9401, 9402, 9403]:
        raise AssertionError(f"Management extraction lost the returned own-roster rows: {personal_source!r}")
    save_deputy_web_schedule({
        "captured_at": "2026-08-31T15:29:00+12:00",
        "areas": [
            {"id": 684, "name": "684", "locationId": 64, "rosterSortOrder": 1},
            {"id": 685, "name": "DIR", "locationId": 64, "rosterSortOrder": 2},
            {"id": 686, "name": "SVT", "locationId": 65, "rosterSortOrder": 1},
        ],
        "locations": [{"id": 64, "name": "T-Ruakaka", "address": ""}, {"id": 65, "name": "T-Te Aroha", "address": ""}],
        "extracted_shifts": personal_source,
        "own_roster_coverage": [{"start_date": "2026-09-02", "end_date": "2026-09-08", "employee_id": 17,
                                  "status": "complete", "pagination_complete": True, "records_returned": 3}],
        "extracted_schedule_shifts": [],
        "schedule_coverage": [],
    }, owner_user_id=1)
    personal_rows = [dict(row) for row in fetch_shifts_between("2026-09-02", "2026-09-03", owner_user_id=1)]
    if len(personal_rows) != 3:
        raise AssertionError(f"Own-roster persistence/retrieval lost a returned row: {personal_rows!r}")
    interpreted = interpret_deputy_workdays(personal_rows)
    ruakaka = next(item for item in interpreted if item["date"] == "2026-09-02")
    te_aroha = next(item for item in interpreted if item["date"] == "2026-09-03")
    if (ruakaka["production_position"], ruakaka["rostered_start"], ruakaka["rostered_finish"], ruakaka["vehicle"]) != ("DIR", "09:00", "22:00", "684"):
        raise AssertionError(f"Touching 684 + DIR did not form the expected Ruakaka workday: {ruakaka!r}")
    if (te_aroha["production_position"], te_aroha["rostered_start"], te_aroha["rostered_finish"]) != ("SVT", "09:30", "18:45"):
        raise AssertionError(f"Independent Te Aroha SVT workday was lost or joined: {te_aroha!r}")

    role_before = [row for row in personal_source if row["id"] == 9402]
    role_after = [{**role_before[0], "areaName": "Overnighter", "roleName": "Overnighter"}]
    save_deputy_web_schedule({
        "captured_at": "2026-08-31T15:31:00+12:00", "areas": [{"id": 685, "name": "Overnighter", "locationId": 64, "rosterSortOrder": 2}],
        "locations": [{"id": 64, "name": "T-Ruakaka", "address": ""}], "extracted_shifts": role_after,
        "own_roster_coverage": [{"start_date": "2026-09-02", "end_date": "2026-09-08", "employee_id": 17,
                                  "status": "complete", "pagination_complete": True, "records_returned": 3}],
        "extracted_schedule_shifts": [], "schedule_coverage": [],
    }, owner_user_id=1)
    if not any(row["field_name"] == "role" for row in get_shift_changes_for_date("2026-09-02")):
        raise AssertionError("A real personal Deputy role-label change was hidden with cohort replacement noise.")

    print("Travel cohort, conservative truck-note, and fresh own-roster regression smoke passed.")


if __name__ == "__main__":
    main()
