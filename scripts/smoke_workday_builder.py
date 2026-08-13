from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def assert_redirect(response, fragment: str) -> None:
    location = response.headers.get("location", "")
    if response.status_code != 303 or fragment not in location:
        raise AssertionError(f"Expected redirect containing {fragment!r}, got {response.status_code} {location!r}")


def main() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-workday-smoke-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "workday.sqlite3"),
        APP_SECRET_KEY="workday-smoke-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
    )

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.database import (
        create_app_user,
        get_connection,
        get_roster_day,
        get_roster_day_assignments,
        init_db,
        list_crew_people,
        list_workday_roles,
        workday_vehicle_conflicts,
    )
    from app.main import app, build_timesheet_summary, published_rosters_by_date
    from app.security import encrypt_text, hash_pin

    init_db()
    client = TestClient(app)
    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://example.au.deputy.com/#/",
            "deputy_email": "jayden@example.com",
            "deputy_password": "password",
            "pin": "1234",
            "pin_confirm": "1234",
            "next_url": "/month",
        },
        follow_redirects=False,
    )
    assert_redirect(signup, "/month")
    settings = get_settings()
    gary = create_app_user(
        deputy_email="gary@example.com",
        display_name="Gary McClure",
        pin_hash=hash_pin("2345"),
        deputy_web_url="https://example.au.deputy.com/#/",
        encrypted_email=encrypt_text("gary@example.com", settings),
        encrypted_password=encrypt_text("password", settings),
    )
    olivia = create_app_user(
        deputy_email="olivia@example.com",
        display_name="Olivia Dooley",
        pin_hash=hash_pin("3456"),
        deputy_web_url="https://example.au.deputy.com/#/",
        encrypted_email=encrypt_text("olivia@example.com", settings),
        encrypted_password=encrypt_text("password", settings),
    )
    init_db()
    people = {str(row["canonical_display_name"]): dict(row) for row in list_crew_people()}
    jayden_person = next(row for row in people.values() if row.get("app_user_id") not in {int(gary["id"]), int(olivia["id"])})
    gary_person = next(row for row in people.values() if row.get("app_user_id") == int(gary["id"]))
    olivia_person = next(row for row in people.values() if row.get("app_user_id") == int(olivia["id"]))

    with get_connection() as conn:
        planning_cursor = conn.execute(
            """
            INSERT INTO love_racing_meetings (
                meeting_date, racecourse_key, racecourse, club_name, meeting_id,
                meeting_url, discovery_source, discovered_at, source_url,
                source_hash, raw_text, first_seen_at, last_seen_at, last_synced_at, is_active
            ) VALUES (
                '2026-08-22', 'te-rapa', 'Te Rapa', 'Waikato TR', 'builder-55032',
                'https://loveracing.example/meeting-overview.aspx', 'fixture', '2026-08-01T00:00:00+12:00',
                'https://loveracing.example/calendar', 'builder-planning-fixture', '',
                '2026-08-01T00:00:00+12:00', '2026-08-01T00:00:00+12:00', '2026-08-01T00:00:00+12:00', 1
            )
            """
        )
        planning_id = int(planning_cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO love_racing_meeting_details (
                meeting_id, meeting_date, canonical_venue_key, canonical_venue_label,
                club, meeting_url, lifecycle_status, fetch_status, race_count,
                first_race_time, last_race_time, races_json, parser_diagnostics,
                created_at, updated_at
            ) VALUES (
                'builder-55032', '2026-08-22', 'te-rapa', 'Te Rapa', 'Waikato TR',
                'https://loveracing.example/meeting-overview.aspx', 'complete', 'ok', 8,
                '12:25', '16:38', '[]', '[]',
                '2026-08-01T00:00:00+12:00', '2026-08-01T00:00:00+12:00'
            )
            """
        )
        for location_id, label in enumerate(("684", "685", "Back", "CCU 1 FCR", "Admin", "Abandoned"), start=9000):
            conn.execute(
                "INSERT INTO deputy_schedule_locations(location_id,name,updated_at) VALUES (?,?,?)",
                (location_id, label, "2026-08-10T09:00:00+12:00"),
            )
    planning_builder = client.get(f"/admin/roster-days/new?planning_id={planning_id}")
    for expected in ('value="2026-08-22"', '>Te Rapa</option>', 'value="12:25"', 'value="16:38"', 'value="8"'):
        if expected not in planning_builder.text:
            raise AssertionError(f"Love Racing planning seed omitted {expected!r}.")
    if "Private until published" not in planning_builder.text:
        raise AssertionError("Love Racing planning seed did not remain a private builder draft.")
    if 'name="truck_crew_early" value="1" checked' not in planning_builder.text:
        raise AssertionError("A new race day did not default truck crew early start on.")
    if 'data-value="not_required"' in planning_builder.text:
        raise AssertionError("No transport required remained in the normal new-assignment picker.")

    office = client.post(
        "/admin/roster-days/save",
        data={
            "day_type": "office_day",
            "roster_date": "2026-08-10",
            "title": "Office work",
            "custom_location": "Office / Clow Place",
            "office_start": "09:00",
            "end_time": "16:30",
            "break_minutes": "0",
            "source_reference": "Organised by email",
            "notes": "Equipment checks and planning",
            "role_label": ["", "Equipment preparation", ""],
            "role_key": ["", "equipment-preparation", ""],
            "assignee": [f"person:{jayden_person['id']}", f"person:{gary_person['id']}", f"person:{olivia_person['id']}"],
            "assignment_state": ["assigned", "assigned", "assigned"],
            "transport_mode": ["not_required", "not_required", "self_travel"],
            "vehicle_label": ["", "", ""],
            "custom_transport_text": ["", "", ""],
            "assignment_note": ["", "Prepare equipment", ""],
        },
        follow_redirects=False,
    )
    assert_redirect(office, "/admin/roster-days/")
    office_id = int(office.headers["location"].split("/admin/roster-days/", 1)[1].split("?", 1)[0])
    office_review = client.get(f"/admin/roster-days/{office_id}?mode=review")
    for expected in ("Office work", "Gary McClure", "Olivia Dooley", "Making own way", "Organised by email"):
        if expected not in office_review.text:
            raise AssertionError(f"Office draft review omitted {expected!r}.")
    if '<form class="workday-form"' in office_review.text or ">Edit<" not in office_review.text:
        raise AssertionError("Saved workday did not open in the read-only review step.")
    office_editor = client.get(f"/admin/roster-days/{office_id}")
    if 'data-workday-form' not in office_editor.text or "Save &amp; review" not in office_editor.text:
        raise AssertionError("The explicit edit view did not retain the full builder.")
    if 'data-value="not_required"' not in office_editor.text or "Historical selection" not in office_editor.text:
        raise AssertionError("A historical No transport required selection no longer renders safely.")
    assert_redirect(client.post(f"/admin/roster-days/{office_id}/publish", follow_redirects=False), "Roster+version+1+published")

    for user_id in (int(jayden_person["app_user_id"]), int(gary["id"]), int(olivia["id"])):
        visible = published_rosters_by_date("2026-08-10", "2026-08-10", user_id)
        if len(visible.get("2026-08-10", [])) != 1:
            raise AssertionError(f"Office day was not visible to linked user {user_id}.")
    office_rows = published_rosters_by_date("2026-08-10", "2026-08-10", int(jayden_person["app_user_id"]))["2026-08-10"]
    if office_rows[0]["hours"] != 7.5 or office_rows[0]["position_label"] != "Attending":
        raise AssertionError(f"Roleless office assignment or duration was not retained: {office_rows[0]!r}")
    office_day = client.get("/day/2026-08-10")
    if "Manually rostered" not in office_day.text or "Office / Clow Place" not in office_day.text or "7h 30m" not in office_day.text:
        raise AssertionError("Office day did not use the general workday presentation.")
    for forbidden in ("Love Racing", "Track map", "First race", "Last race"):
        if forbidden in office_day.text:
            raise AssertionError(f"Office day incorrectly rendered race-only content: {forbidden}")
    month = client.get("/month?year=2026&month=8")
    if "Office work" not in month.text or "7h 30m" not in month.text or "published-roster-marker" not in month.text:
        raise AssertionError("Office day was missing from calendar totals or markers.")
    summary = build_timesheet_summary(date(2026, 8, 10), int(jayden_person["app_user_id"]))
    office_summary = next(day for day in summary["days"] if day["iso"] == "2026-08-10")
    if office_summary["total"] != 7.5 or not office_summary["manual_rosters"]:
        raise AssertionError("Office day was missing from timesheet totals.")

    role_labels_before = {str(row["display_label"]) for row in list_workday_roles(include_disabled=True)}
    if "Equipment preparation" in role_labels_before:
        raise AssertionError("A one-day custom role was added to the catalogue without permission.")

    race = client.post(
        "/admin/roster-days/save",
        data={
            "day_type": "race_day",
            "roster_date": "2026-08-15",
            "new_track_label": "Te Rapa",
            "race_type": "thoroughbred",
            "office_start": "08:15",
            "truck_crew_early": "1",
            "on_track_time": "08:45",
            "first_race_time": "11:30",
            "last_race_time": "16:15",
            "race_count": "8",
            "role_label": ["Gimbal", "Gimbal Assist", "", "Sound/VT"],
            "role_key": ["gimbal", "gimbal-assist", "", "sound-vt"],
            "assignee": [f"person:{olivia_person['id']}", "", f"person:{jayden_person['id']}", f"person:{gary_person['id']}"],
            "assignment_state": ["assigned", "open", "assigned", "assigned"],
            "transport_mode": ["self_travel", "unassigned", "unassigned", "vehicle"],
            "vehicle_label": ["", "", "", "684"],
            "custom_transport_text": ["", "", "", ""],
            "assignment_note": ["", "", "Helping generally", ""],
            "save_role_index": ["0"],
        },
        follow_redirects=False,
    )
    race_id = int(race.headers["location"].split("/admin/roster-days/", 1)[1].split("?", 1)[0])
    if int(get_roster_day(race_id)["truck_start_offset_minutes"] or 0) != 15:
        raise AssertionError("Race-day truck start setting was not persisted as 15 minutes.")
    race_editor = client.get(f"/admin/roster-days/{race_id}")
    if "Use another Deputy location" not in race_editor.text:
        raise AssertionError("Exceptional Deputy locations are not available behind the advanced control.")
    normal_selector = race_editor.text.split('name="track_label"', 1)[1].split("</select>", 1)[0]
    if any(label in normal_selector for label in (">Leave<", ">PubHol<", ">Travel<", ">684<", ">685<", ">Back<", ">CCU 1 FCR<", ">Admin<", ">Abandoned<")):
        raise AssertionError("Operational Deputy labels leaked into the normal race venue selector.")
    advanced_selector = race_editor.text.split('data-advanced-track', 1)[1].split("</select>", 1)[0]
    if not all(f">{label}<" in advanced_selector for label in ("684", "685", "Back", "CCU 1 FCR", "Admin", "Abandoned")):
        raise AssertionError("Exceptional Deputy locations were deleted instead of moving behind the advanced path.")
    if 'name="assignment_state"' not in race_editor.text or "TBC / not offered" not in race_editor.text:
        raise AssertionError("Person picker no longer controls the hidden Assigned/Open/TBC state.")
    if "<span>Assignment</span>" in race_editor.text:
        raise AssertionError("Redundant Assignment dropdown remained in advanced controls.")
    if "data-assignment-advanced open" in race_editor.text:
        raise AssertionError("Assignment advanced controls should be collapsed by default.")
    assert_redirect(client.post(f"/admin/roster-days/{race_id}/publish", follow_redirects=False), "Roster+version+1+published")
    race_day = client.get("/day/2026-08-15")
    for expected in ("Gimbal", "Gimbal Assist", "TBC", "Attending", "Making own way", "684"):
        if expected not in race_day.text:
            raise AssertionError(f"Gimbal fixture omitted {expected!r}.")
    if "RTS" in race_day.text:
        raise AssertionError("Removed RTS role was recreated in the published day.")
    race_assignments = [dict(row) for row in get_roster_day_assignments(race_id)]
    if len(race_assignments) != 4 or any(row["role_label"] == "RTS" for row in race_assignments):
        raise AssertionError(f"Dynamic race plan was not preserved: {race_assignments!r}")
    if "Gimbal" not in {str(row["display_label"]) for row in list_workday_roles(include_disabled=True)}:
        raise AssertionError("Explicitly saved custom role did not enter the reusable catalogue.")

    global_month = client.get("/month?year=2026&month=8&scope=global")
    if f"manual_id={race_id}" not in global_month.text or "Te Rapa" not in global_month.text:
        raise AssertionError("Manual race day was missing or ambiguous in global crew view.")
    global_day = client.get(f"/day/2026-08-15?scope=global&manual_id={race_id}")
    if "Gimbal" not in global_day.text or "Te Rapa" not in global_day.text:
        raise AssertionError("Global manual-day link did not retain event identity.")

    with get_connection() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = 'workday_assignments_migrated_v1'")
        cursor = conn.execute(
            """
            INSERT INTO roster_days (
                roster_date, track_key, track_label, race_type, day_type, status,
                created_by_user_id, updated_by_user_id, created_at, updated_at
            ) VALUES ('2026-08-20', 'legacy-test', 'Legacy Test', 'thoroughbred', 'race_day', 'draft', ?, ?, '', '')
            """,
            (int(jayden_person["app_user_id"]), int(jayden_person["app_user_id"])),
        )
        legacy_day_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO roster_day_assignments (
                roster_day_id, position_label, user_id, assignee_label,
                vehicle_label, sort_order, created_at, updated_at
            ) VALUES (?, 'Director', ?, 'Jayden', '685', 0, '', '')
            """,
            (legacy_day_id, int(jayden_person["app_user_id"])),
        )
    init_db()
    init_db()
    migrated = [dict(row) for row in get_roster_day_assignments(legacy_day_id)]
    if len(migrated) != 1 or migrated[0]["role_label"] != "Director" or migrated[0]["transport_mode"] != "vehicle":
        raise AssertionError(f"Legacy assignment migration was not idempotent: {migrated!r}")

    incomplete = client.post(
        "/admin/roster-days/save",
        data={"roster_date": "2026-09-01", "day_type": "race_day", "race_type": ""},
        follow_redirects=False,
    )
    match = re.search(r"/admin/roster-days/(\d+)", incomplete.headers.get("location", ""))
    if incomplete.status_code != 303 or not match:
        raise AssertionError(f"A date-only race-day draft did not save: {incomplete.status_code} {incomplete.headers.get('location')}")
    incomplete_id = int(match.group(1))
    saved_incomplete = dict(get_roster_day(incomplete_id))
    if any(saved_incomplete.get(field) for field in ("track_label", "office_start", "first_race_time", "last_race_time", "race_count")):
        raise AssertionError(f"Incomplete draft invented missing values: {saved_incomplete!r}")
    review = client.get(f"/admin/roster-days/{incomplete_id}?mode=review")
    if "Some information is still TBC" not in review.text:
        raise AssertionError("Incomplete-day review did not present publish warnings.")
    assert_redirect(client.post(f"/admin/roster-days/{incomplete_id}/publish", follow_redirects=False), "Roster+version+1+published")

    with get_connection() as conn:
        vehicle = conn.execute("SELECT id FROM crew_vehicles WHERE stable_key='684'").fetchone()
        if vehicle is None:
            conn.execute("INSERT INTO crew_vehicles(stable_key,display_label,aliases,active,sort_order,source,created_at,updated_at) VALUES ('684','684','[]',1,1,'admin','','')")
            vehicle = conn.execute("SELECT id FROM crew_vehicles WHERE stable_key='684'").fetchone()
        vehicle_id = int(vehicle["id"])
        def add_day(key, start, finish, day='2026-09-02'):
            cursor = conn.execute("INSERT INTO roster_days(roster_date,track_key,track_label,day_type,office_start,end_time,status) VALUES (?,?,?,'race_day',?,?,'draft')", (day,key,key,start,finish))
            return int(cursor.lastrowid)
        target_id = add_day('vehicle-target', '10:30', '18:00')
        overlap_id = add_day('vehicle-overlap', '08:00', '12:30')
        advisory_id = add_day('vehicle-advisory', '18:30', '20:00')
        possible_id = add_day('vehicle-possible', '', '')
        different_id = add_day('vehicle-different-date', '10:30', '18:00', '2026-09-03')
        for day_id, count in ((target_id, 4), (overlap_id, 1), (advisory_id, 1), (possible_id, 1), (different_id, 1)):
            for index in range(count):
                conn.execute("INSERT INTO workday_assignments(roster_day_id,assignment_state,transport_mode,vehicle_id,vehicle_label,sort_order) VALUES (?,'assigned','vehicle',?,'684',?)", (day_id, vehicle_id, index))
    conflicts = workday_vehicle_conflicts(target_id)
    levels = {item["level"] for item in conflicts}
    if levels != {"overlap", "same_day", "possible"} or len(conflicts) != 3:
        raise AssertionError(f"Vehicle conflicts did not distinguish overlap/advisory/TBC or leaked same-workday/different-date reuse: {conflicts!r}")
    conflict_review = client.post(f"/admin/roster-days/{target_id}/publish", follow_redirects=False)
    if conflict_review.status_code != 303 or "mode=review" not in conflict_review.headers.get("location", ""):
        raise AssertionError(f"Publish did not recheck vehicle conflicts server-side: {conflict_review.headers.get('location')}")
    assert_redirect(
        client.post(f"/admin/roster-days/{target_id}/publish?confirm_vehicle_conflicts=1", follow_redirects=False),
        "published",
    )

    print("workday builder smoke ok")


if __name__ == "__main__":
    main()
