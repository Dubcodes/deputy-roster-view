from __future__ import annotations

import os
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
        get_roster_day_assignments,
        init_db,
        list_crew_people,
        list_workday_roles,
    )
    from app.main import app, build_timesheet_summary, published_rosters_by_date
    from app.security import encrypt_text, hash_pin

    init_db()
    client = TestClient(app)
    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://example.deputy.com/#/",
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
        deputy_web_url="https://example.deputy.com/#/",
        encrypted_email=encrypt_text("gary@example.com", settings),
        encrypted_password=encrypt_text("password", settings),
    )
    olivia = create_app_user(
        deputy_email="olivia@example.com",
        display_name="Olivia Dooley",
        pin_hash=hash_pin("3456"),
        deputy_web_url="https://example.deputy.com/#/",
        encrypted_email=encrypt_text("olivia@example.com", settings),
        encrypted_password=encrypt_text("password", settings),
    )
    init_db()
    people = {str(row["canonical_display_name"]): dict(row) for row in list_crew_people()}
    jayden_person = next(row for row in people.values() if row.get("app_user_id") not in {int(gary["id"]), int(olivia["id"])})
    gary_person = next(row for row in people.values() if row.get("app_user_id") == int(gary["id"]))
    olivia_person = next(row for row in people.values() if row.get("app_user_id") == int(olivia["id"]))

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
    office_review = client.get(f"/admin/roster-days/{office_id}")
    for expected in ("Office work", "Gary McClure", "Olivia Dooley", "Making own way", "Organised by email"):
        if expected not in office_review.text:
            raise AssertionError(f"Office draft review omitted {expected!r}.")
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

    print("workday builder smoke ok")


if __name__ == "__main__":
    main()
