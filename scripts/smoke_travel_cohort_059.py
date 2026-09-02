from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-09-01"
START = f"{DATE}T12:00:00+12:00"
END = f"{DATE}T17:00:00+12:00"
COHORT_DATE = "2026-08-30"
COHORT_START = f"{COHORT_DATE}T12:00:00+12:00"
COHORT_END = f"{COHORT_DATE}T17:00:00+12:00"
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
        fetch_personal_assignment_evidence_for_date,
        get_shift_changes_for_date,
        init_db,
        save_deputy_web_schedule,
    )
    from app.deputy_web import _extract_management_shifts, _travel_family_location_ids
    from app.interpreted_workdays import interpret_deputy_workdays, interpret_deputy_workdays_for_people
    import app.main as main_module
    from app.main import (
        app,
        combine_adjacent_shifts,
        decorate_shift,
        effective_schedule_items,
        reconcile_personal_assignment_evidence,
        schedule_people,
        travel_cohort_schedule_rows,
        travel_personal_assignment_evidence,
    )
    from app.roster_note_interpretation import note_vehicle_allocations_from_text
    from app.scheduler import _combined_sync_status
    from app.security import hash_pin
    from app.travel_cohorts import travel_family_locations_match
    from fastapi.testclient import TestClient

    init_db()
    with sqlite3.connect(get_settings().db_path) as conn:
        conn.execute(
            """INSERT INTO app_users
               (id, deputy_email, display_name, pin_hash, deputy_web_url, is_admin,
                is_active, created_at, updated_at)
                VALUES (1, 'jayden@example.test', 'Jayden-lee', ?, 'https://example.test', 1, 1, ?, ?)""",
            (hash_pin("1234"), "2026-08-31T15:29:00+12:00", "2026-08-31T15:29:00+12:00"),
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

    expected_truck = [{
        "vehicle": "Truck", "people": ["Dylan", "Matt"],
        "raw": "Dylan and Matt driving trucks", "vehicle_type": "truck",
        "vehicle_specificity": "generic",
    }]
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
    if dylan["vehicle"] != "Truck" or matt["vehicle"] != "Truck":
        raise AssertionError(f"Unique cohort aliases did not receive the conservative truck evidence: {workdays!r}")
    unresolved = dylan["vehicle_evidence"]["note_only_people"]
    if {item["name"] for item in unresolved} != {"Unknown"}:
        raise AssertionError(f"Resolved shorthand or driving leaked into unresolved display evidence: {unresolved!r}")

    july_date = "2026-07-31"
    july_start = f"{july_date}T13:00:00+12:00"
    july_end = f"{july_date}T17:00:00+12:00"
    july_note = "Accommodation Beachfront\n684 Jnr, Lans Josh, Grant\n685 Gaz, Jayden, Campbell, Todd"
    july_workdays = interpret_deputy_workdays_for_people(
        [{**note_rows[0], "date": july_date, "start_at": july_start, "end_at": july_end, "description": july_note}],
        structured_rows=[
            {**row, "date": july_date, "start_at": july_start, "end_at": july_end}
            for row in structured
        ],
        identity_records=identities,
    )
    if set(july_workdays) != {employee_id for employee_id, _name in PEOPLE} or any(
        len(days) != 1 for days in july_workdays.values()
    ):
        raise AssertionError(f"31 July allocation note changed the structured Travel membership: {july_workdays!r}")

    def wire_schedule(area_name: str) -> list[dict[str, object]]:
        return [
            {
                "id": 9100 + index, "employee": employee_id, "employeeName": name,
                "area": 700, "areaName": area_name, "areaLocationId": 7000,
                "location": 7000, "locationName": "T-Travel", "role": area_name,
                "start": COHORT_START, "end": COHORT_END, "duration": 18000, "isPublished": True, "note": "",
            }
            for index, (employee_id, name) in enumerate(PEOPLE, start=1)
        ]

    base_schedule_payload = {
        "captured_at": "2026-08-31T15:29:00+12:00",
        "areas": [{"id": 700, "name": "Travel then Overnighter", "locationId": 7000, "rosterSortOrder": 1}],
        "locations": [{"id": 7000, "name": "T-Travel", "address": ""}],
        "extracted_shifts": [],
        "own_roster_coverage": [],
        "schedule_coverage": [{"start_date": COHORT_DATE, "end_date": COHORT_DATE, "mode": "all", "location_ids": []}],
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
        saved_count = conn.execute("SELECT COUNT(*) FROM deputy_schedule_shifts WHERE date = ?", (COHORT_DATE,)).fetchone()[0]
        false_events = conn.execute("SELECT COUNT(*) FROM deputy_schedule_event_changes WHERE date = ?", (COHORT_DATE,)).fetchone()[0]
    if saved_count != len(PEOPLE) or false_events:
        raise AssertionError(f"Travel cohort persistence or event history was collapsed: {(saved_count, false_events)!r}")

    travel_area_refs = {
        "1412": {"id": 1412, "name": "Travel then Overnighter", "locationId": 105},
        "1045": {"id": 1045, "name": "Out of Region", "locationId": 105},
    }
    if _travel_family_location_ids(travel_area_refs) != [105]:
        raise AssertionError("Travel selected-location scope was not derived from participant-area references.")
    if not travel_family_locations_match("T-Travel", "Overnighter", "Travel", "Travel then Overnighter"):
        raise AssertionError("The proven Travel/T-Travel participant alias was not recognised.")
    if travel_family_locations_match("T-Ruakaka", "Director", "Ruakaka", "Director"):
        raise AssertionError("Travel matching broadened to an unrelated production location.")
    if _combined_sync_status({}, {"status": "ok", "payload": {"schedule_coverage": [{}], "travel_schedule_coverage": [{"status": "partial"}]}}) != "partial":
        raise AssertionError("Incomplete dedicated Travel capture was not surfaced as partial sync coverage.")

    shared_people = tuple(person for person in PEOPLE if person[0] != 17)
    mixed_shared = [
        {
            "id": 10100 + index, "employee": employee_id, "employeeName": name,
            "area": 1412, "areaName": "Travel then Overnighter", "areaLocationId": 105,
            "location": 105, "locationName": "Travel", "role": "Travel then Overnighter",
            "start": START, "end": END, "duration": 18000, "isPublished": True, "note": "",
        }
        for index, (employee_id, name) in enumerate(shared_people, start=1)
    ]
    mixed_before = _extract_management_shifts({"data": [{
        "id": 33598, "employee": 17, "area": 1762, "areaName": "Travel then Overnighter", "areaLocationId": 158,
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
        "locations": [{"id": 105, "name": "Travel", "address": ""}, {"id": 158, "name": "T-Travel", "address": ""}],
        "extracted_shifts": mixed_before,
        "own_roster_coverage": [{"start_date": DATE, "end_date": DATE, "employee_id": 17,
                                  "status": "complete", "pagination_complete": True, "records_returned": 1}],
        # Production-shaped capture A: ALL succeeds with an ordinary row but
        # omits location 105; the dedicated selected scope supplies it.
        "extracted_schedule_shifts": mixed_shared + [{
            "id": 12001, "employee": 301, "employeeName": "Ordinary Crew", "area": 901,
            "areaName": "Director", "areaLocationId": 64, "location": 64,
            "locationName": "T-Ruakaka", "role": "Director", "start": START,
            "end": END, "duration": 18000, "isPublished": True,
        }],
        "schedule_coverage": [
            {"start_date": DATE, "end_date": DATE, "mode": "all", "location_ids": [], "excluded_location_ids": [105]},
            {"start_date": DATE, "end_date": DATE, "mode": "selected", "location_ids": [105]},
        ],
        "travel_schedule_coverage": [{"start_date": DATE, "end_date": DATE, "location_ids": [105], "status": "complete"}],
    }
    save_deputy_web_schedule(mixed_payload, owner_user_id=1)
    save_deputy_web_schedule({**mixed_payload, "captured_at": "2026-08-31T15:30:00+12:00", "extracted_shifts": mixed_after}, owner_user_id=1)
    persisted_personal = [dict(row) for row in fetch_personal_assignment_evidence_for_date(DATE, [158])]
    if len(persisted_personal) != 1 or (
        persisted_personal[0]["deputy_employee_id"],
        persisted_personal[0]["raw_role_label"],
        persisted_personal[0]["evidence_type"],
        persisted_personal[0]["production_position"],
        persisted_personal[0]["participant_evidence"],
        persisted_personal[0]["cohort_type"],
    ) != (17, "Overnighter", "participant_cohort", 0, 1, "travel"):
        raise AssertionError(f"Personal Travel evidence was not preserved and classified safely: {persisted_personal!r}")
    with sqlite3.connect(get_settings().db_path) as conn:
        captured_cohort_count = conn.execute(
            "SELECT COUNT(*) FROM deputy_schedule_shifts WHERE date=? AND area_location_id=105", (DATE,)
        ).fetchone()[0]
    if captured_cohort_count != len(shared_people):
        raise AssertionError(f"Dedicated Travel capture did not persist the cohort exactly once: {captured_cohort_count!r}")
    # Production-shaped capture B: ALL succeeds but the dedicated scope is
    # incomplete. Its old Travel observations must remain active.
    incomplete_result = save_deputy_web_schedule({
        **mixed_payload,
        "captured_at": "2026-08-31T15:31:00+12:00", "extracted_shifts": [],
        "extracted_schedule_shifts": [mixed_payload["extracted_schedule_shifts"][-1]],
        "schedule_coverage": [{"start_date": DATE, "end_date": DATE, "mode": "all", "location_ids": [], "excluded_location_ids": [105]}],
        "travel_schedule_coverage": [{"start_date": DATE, "end_date": DATE, "location_ids": [105], "status": "partial"}],
    }, owner_user_id=1)
    with sqlite3.connect(get_settings().db_path) as conn:
        retained_cohort_count = conn.execute(
            "SELECT COUNT(*) FROM deputy_schedule_shifts WHERE date=? AND area_location_id=105", (DATE,)
        ).fetchone()[0]
    if incomplete_result["schedule_removed"] or retained_cohort_count != len(shared_people):
        raise AssertionError("Incomplete dedicated Travel capture destructively retired prior cohort evidence.")
    mixed_shift = next(decorate_shift(row) for row in fetch_shifts_for_date(DATE, owner_user_id=1) if ":33598" in str(row["source_uid"]))
    mixed_rows = travel_cohort_schedule_rows(DATE, [mixed_shift])
    mixed_people = schedule_people(mixed_rows, include_vehicle_only=True, include_placeholders=False)
    if (
        not any(int(row["source_shift_id"]) == 10101 and int(row["schedule_location_id"]) == 105 for row in mixed_rows)
        or {row["employee_name"] for row in mixed_people} != {name for _employee_id, name in shared_people}
    ):
        raise AssertionError(f"Mixed T-Travel internal IDs did not retain the older shared cohort: {mixed_rows!r}")
    shared_abc = [row for row in mixed_rows if int(row["employee_id"]) in {101, 102, 103}]
    personal_union_rows = [
        {"deputy_employee_id": 102, "employee_name": "Elliot", "position_label": "Overnighter", "raw_role_label": "Overnighter", "evidence_type": "participant_cohort", "cohort_type": "travel", "location_name": "T-Travel", "area_location_id": 158, "start_at": START, "end_at": END},
        {"deputy_employee_id": 106, "employee_name": "Dylan Holden", "position_label": "Overnighter", "raw_role_label": "Overnighter", "evidence_type": "participant_cohort", "cohort_type": "travel", "location_name": "T-Travel", "area_location_id": 158, "start_at": START, "end_at": END},
        {"deputy_employee_id": 110, "employee_name": "Matt Blackmore", "position_label": "Overnighter", "raw_role_label": "Overnighter", "evidence_type": "participant_cohort", "cohort_type": "travel", "location_name": "T-Travel", "area_location_id": 158, "start_at": START, "end_at": END},
        {"deputy_employee_id": 999, "employee_name": "Unrelated Travel", "position_label": "Overnighter", "raw_role_label": "Overnighter", "evidence_type": "participant_cohort", "cohort_type": "travel", "location_name": "Other", "area_location_id": 999, "start_at": START, "end_at": END},
    ]
    original_personal_fetch = main_module.fetch_personal_assignment_evidence_for_date
    main_module.fetch_personal_assignment_evidence_for_date = lambda _date: personal_union_rows
    try:
        event_personal_rows = travel_personal_assignment_evidence(DATE, [mixed_shift], shared_abc)
    finally:
        main_module.fetch_personal_assignment_evidence_for_date = original_personal_fetch
    union_people = schedule_people(shared_abc, include_vehicle_only=True, include_placeholders=False)
    reconcile_personal_assignment_evidence(
        union_people,
        event_personal_rows,
        event_start_at=START,
        event_end_at=END,
        travel_participant_union=True,
    )
    if {int(person["employee_id"]) for person in union_people} != {101, 102, 103, 106, 110}:
        raise AssertionError(f"Shared and personal Deputy Travel evidence did not form the exact union: {union_people!r}")
    isolated_people: list[dict[str, object]] = []
    reconcile_personal_assignment_evidence(
        isolated_people, personal_union_rows[:1], event_start_at=END,
        event_end_at=f"{DATE}T18:00:00+12:00", travel_participant_union=True,
    )
    reconcile_personal_assignment_evidence(
        isolated_people, personal_union_rows[:1], event_start_at=START,
        event_end_at=END, travel_participant_union=False,
    )
    reconcile_personal_assignment_evidence(
        isolated_people,
        [{**personal_union_rows[0], "deputy_employee_id": None, "canonical_person_id": None}],
        event_start_at=START, event_end_at=END, travel_participant_union=True,
    )
    if isolated_people:
        raise AssertionError(f"Travel personal evidence leaked across event or context boundaries: {isolated_people!r}")
    mixed_workday = interpret_deputy_workdays(
        [mixed_shift], structured_rows=mixed_rows,
        person_identity={"deputy_employee_id": 17, "aliases": ["Jayden-lee"]}, identity_records=identities,
    )[0]
    if mixed_workday["production_position"] != "Shift":
        raise AssertionError(f"Travel participant evidence leaked into production position: {mixed_workday!r}")
    mixed_people_workdays = interpret_deputy_workdays_for_people(
        [mixed_shift], structured_rows=mixed_rows, identity_records=identities,
    )
    mixed_dylan = next(item for item in mixed_people_workdays[106] if item["date"] == DATE)
    mixed_matt = next(item for item in mixed_people_workdays[110] if item["date"] == DATE)
    if (
        mixed_dylan["vehicle"] != "Truck"
        or mixed_matt["vehicle"] != "Truck"
        or mixed_dylan["vehicle_evidence"].get("vehicle_specificity") != "generic"
        or {item["name"] for item in mixed_dylan["vehicle_evidence"]["note_only_people"]} != {"Unknown"}
        or not any(row["field_name"] == "role" for row in get_shift_changes_for_date(DATE))
    ):
        raise AssertionError(
            "Mixed Travel cohort lost note resolution or the genuine personal role change: "
            f"{(mixed_dylan, mixed_matt, get_shift_changes_for_date(DATE))!r}"
        )

    client = TestClient(app, follow_redirects=False)
    login = client.post("/login", data={"deputy_email": "jayden@example.test", "pin": "1234"})
    if login.status_code != 303:
        raise AssertionError(f"Travel day-view login failed: {login.status_code}")
    def rendered_travel_people(note: str) -> str:
        with sqlite3.connect(get_settings().db_path) as conn:
            conn.execute("UPDATE shifts SET description=? WHERE owner_user_id=1 AND date=?", (note, DATE))
            conn.commit()
        day_page = client.get(f"/day/{DATE}")
        if day_page.status_code != 200:
            raise AssertionError(f"Travel day view did not render: {day_page.status_code}")
        return day_page.text

    for note in (
        "Dylan and Matt driving trucks\nUnknown trucks",
        "",
        "Please call the office before leaving.",
        "Mystery Person trucks",
    ):
        page_text = rendered_travel_people(note)
        for _employee_id, name in PEOPLE:
            if page_text.count(name) != 1:
                raise AssertionError(f"Travel roster note changed structured cohort membership for {name!r}: {page_text!r}")
        if '>driving<' in page_text.lower() or '>Driving<' in page_text:
            raise AssertionError("Final day view rendered a fake driving person.")
    page_text = rendered_travel_people("Dylan and Matt driving trucks\nUnknown trucks")
    for name in ("Dylan Holden", "Matt Blackmore"):
        if "Unresolved note-only person" in page_text and name.split()[0] + "</strong>" in page_text:
            raise AssertionError(f"Final day view duplicated resolved Travel note person {name!r}.")
    rendered_crew_names = re.findall(r'class="crew-name"[^>]*>\s*<span>([^<]+)</span>', page_text)
    if "Unknown" in rendered_crew_names:
        raise AssertionError("Unresolved note-only evidence became a fabricated Travel crew member.")
    global_page = client.get(f"/day/{DATE}?scope=global&location_id=105")
    if global_page.status_code != 200:
        raise AssertionError(f"Global Travel day view did not render: {global_page.status_code}")
    for _employee_id, name in PEOPLE:
        if global_page.text.count(name) != 1:
            raise AssertionError(f"Global Travel union did not contain {name!r} exactly once.")

    personal_source = _extract_management_shifts({"data": [
        {"id": 9401, "employee": 17, "area": 684, "areaName": "684", "areaLocationId": 64,
         "location": 64, "locationName": "T-Ruakaka", "role": "684",
         "start": "2026-09-02T09:00:00+12:00", "end": "2026-09-02T09:30:00+12:00", "duration": 1800, "isPublished": True},
        {"id": 9402, "employee": 17, "area": 685, "areaName": "DIR", "areaLocationId": 64,
         "location": 64, "locationName": "T-Ruakaka", "role": "DIR",
         "start": "2026-09-02T10:00:00+12:00", "end": "2026-09-02T22:00:00+12:00", "duration": 43200, "isPublished": True},
        {"id": 9403, "employee": 17, "area": 686, "areaName": "685", "areaLocationId": 65,
         "location": 65, "locationName": "T-Te Aroha", "role": "SVT",
         "start": "2026-09-03T08:45:00+12:00", "end": "2026-09-03T09:45:00+12:00", "duration": 3600, "isPublished": True},
        {"id": 9404, "employee": 17, "area": 687, "areaName": "SVT", "areaLocationId": 65,
         "location": 65, "locationName": "T-Te Aroha", "role": "SVT",
         "start": "2026-09-03T09:45:00+12:00", "end": "2026-09-03T19:00:00+12:00", "duration": 33300, "isPublished": True},
    ]})
    if [row["id"] for row in personal_source] != [9401, 9402, 9403, 9404]:
        raise AssertionError(f"Management extraction lost the returned own-roster rows: {personal_source!r}")
    save_deputy_web_schedule({
        "captured_at": "2026-08-31T15:29:00+12:00",
        "areas": [
            {"id": 684, "name": "684", "locationId": 64, "rosterSortOrder": 1},
            {"id": 685, "name": "DIR", "locationId": 64, "rosterSortOrder": 2},
            {"id": 686, "name": "685", "locationId": 65, "rosterSortOrder": 1},
            {"id": 687, "name": "SVT", "locationId": 65, "rosterSortOrder": 2},
        ],
        "locations": [{"id": 64, "name": "T-Ruakaka", "address": ""}, {"id": 65, "name": "T-Te Aroha", "address": ""}],
        "extracted_shifts": personal_source,
        "own_roster_coverage": [{"start_date": "2026-09-02", "end_date": "2026-09-08", "employee_id": 17,
                                  "status": "complete", "pagination_complete": True, "records_returned": 4}],
        "extracted_schedule_shifts": [],
        "schedule_coverage": [],
    }, owner_user_id=1)
    personal_rows = [dict(row) for row in fetch_shifts_between("2026-09-02", "2026-09-03", owner_user_id=1)]
    if len(personal_rows) != 4:
        raise AssertionError(f"Own-roster persistence/retrieval lost a returned row: {personal_rows!r}")
    interpreted = interpret_deputy_workdays(personal_rows)
    ruakaka = next(item for item in interpreted if item["date"] == "2026-09-02")
    te_aroha = next(item for item in interpreted if item["date"] == "2026-09-03")
    if (ruakaka["production_position"], ruakaka["rostered_start"], ruakaka["rostered_finish"], ruakaka["vehicle"]) != ("DIR", "09:00", "22:00", "684"):
        raise AssertionError(f"The short Deputy handoff did not form the expected Ruakaka workday: {ruakaka!r}")
    if (te_aroha["production_position"], te_aroha["rostered_start"], te_aroha["rostered_finish"], te_aroha["vehicle"]) != ("SVT", "08:45", "19:00", "685"):
        raise AssertionError(f"Independent Te Aroha SVT workday was lost or joined: {te_aroha!r}")
    rendered_handoffs = combine_adjacent_shifts([decorate_shift(row) for row in personal_rows])
    rendered_ruakaka = next(item for item in rendered_handoffs if item["date"] == "2026-09-02")
    if [(segment["role"], segment["start_label"], segment["end_label"])
            for segment in rendered_ruakaka["role_segments"]] != [("684", "09:00", "09:30"), ("Director", "10:00", "22:00")]:
        raise AssertionError(f"The 30-minute Deputy gap was fabricated or lost: {rendered_ruakaka!r}")

    role_before = [row for row in personal_source if row["id"] == 9402]
    role_after = [{**role_before[0], "areaName": "Overnighter", "roleName": "Overnighter"}]
    save_deputy_web_schedule({
        "captured_at": "2026-08-31T15:31:00+12:00", "areas": [{"id": 685, "name": "Overnighter", "locationId": 64, "rosterSortOrder": 2}],
        "locations": [{"id": 64, "name": "T-Ruakaka", "address": ""}], "extracted_shifts": role_after,
        "own_roster_coverage": [{"start_date": "2026-09-02", "end_date": "2026-09-08", "employee_id": 17,
                                  "status": "complete", "pagination_complete": True, "records_returned": 4}],
        "extracted_schedule_shifts": [], "schedule_coverage": [],
    }, owner_user_id=1)
    if not any(row["field_name"] == "role" for row in get_shift_changes_for_date("2026-09-02")):
        raise AssertionError("A real personal Deputy role-label change was hidden with cohort replacement noise.")

    print("Travel cohort, conservative truck-note, and fresh own-roster regression smoke passed.")


if __name__ == "__main__":
    main()
