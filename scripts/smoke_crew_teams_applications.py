from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="redeputy-crew-applications-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "crew-applications.sqlite3"),
        APP_SECRET_KEY="crew-application-smoke-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
    )

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.database import (
        apply_for_open_workday_position,
        create_app_user,
        crew_picker_records,
        get_connection,
        get_default_team_id,
        get_roster_day_assignments,
        init_db,
        list_crew_people,
        list_crew_vehicles,
        list_open_workday_positions,
        publish_roster_day,
        save_crew_team,
        save_crew_vehicle,
        save_roster_day,
        set_crew_person_team,
        withdraw_open_workday_application,
    )
    from app.main import app
    from app.security import encrypt_text, hash_pin

    init_db()
    client = TestClient(app)
    response = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://example.deputy.com/#/",
            "deputy_email": "admin@example.com",
            "deputy_password": "password",
            "pin": "1234",
            "pin_confirm": "1234",
            "next_url": "/month",
        },
        follow_redirects=False,
    )
    if response.status_code != 303:
        raise AssertionError("Admin signup failed in crew/application smoke.")

    settings = get_settings()

    def add_user(email: str, display_name: str, pin: str) -> int:
        row = create_app_user(
            deputy_email=email,
            display_name=display_name,
            pin_hash=hash_pin(pin),
            deputy_web_url="https://example.deputy.com/#/",
            encrypted_email=encrypt_text(email, settings),
            encrypted_password=encrypt_text("password", settings),
        )
        return int(row["id"])

    alf_user = add_user("alf@example.com", "Otm685", "2345")
    campbell_user = add_user("campbell@example.com", "Cambo", "3456")
    dylan_user = add_user("dylan@example.com", "Dylan Holden", "4567")
    other_user = add_user("other@example.com", "Aaron Rangi", "5678")
    init_db()
    people = {int(item["app_user_id"]): dict(item) for item in list_crew_people() if item.get("app_user_id")}
    admin_user = next(user_id for user_id in people if user_id not in {alf_user, campbell_user, dylan_user, other_user})
    alf_person = int(people[alf_user]["id"])
    campbell_person = int(people[campbell_user]["id"])
    dylan_person = int(people[dylan_user]["id"])
    other_person = int(people[other_user]["id"])
    now = "2026-08-10T09:00:00+12:00"
    with get_connection() as conn:
        conn.execute("UPDATE crew_people SET canonical_display_name='Alf',current_deputy_name='Alf' WHERE id=?", (alf_person,))
        conn.execute("UPDATE crew_people SET canonical_display_name='Campbell Stephens',current_deputy_name='Cambo',deputy_employee_id=109 WHERE id=?", (campbell_person,))
        conn.execute("INSERT INTO crew_aliases(person_id,alias,normalized_alias,created_at,updated_at) VALUES (?,?,?,?,?)", (campbell_person, "Cambo", "cambo", now, now))

    northern_team = get_default_team_id()
    if northern_team is None:
        raise AssertionError("Northern Team was not seeded.")
    ok, message, other_team = save_crew_team(
        team_id=None,
        display_name="Central Team",
        active=True,
        sort_order=20,
        actor_user_id=admin_user,
    )
    if not ok or other_team is None:
        raise AssertionError(message)
    for person_id in (alf_person, campbell_person, dylan_person):
        ok, message = set_crew_person_team(
            person_id=person_id,
            team_id=northern_team,
            active=True,
            is_primary=True,
            actor_user_id=admin_user,
        )
        if not ok:
            raise AssertionError(message)
    set_crew_person_team(person_id=other_person, team_id=other_team, active=True, is_primary=True, actor_user_id=admin_user)

    picker = crew_picker_records(northern_team)

    def picker_matches(query: str) -> list[str]:
        needle = "".join(character for character in query.casefold() if character.isalnum())
        return [str(item["canonical_display_name"]) for item in picker if needle in str(item["search_text"])]

    for query in ("camp", "Campbell", "Cambo", "cambo", "109"):
        matches = picker_matches(query)
        if matches != ["Campbell Stephens"]:
            raise AssertionError(f"{query!r} returned {matches!r} instead of one canonical Campbell result.")
    if picker_matches("Otm685") != ["Alf"] or picker_matches("alf") != ["Alf"]:
        raise AssertionError("Linked app identity did not search to canonical Alf.")
    if [item["canonical_display_name"] for item in picker[:3]] != ["Alf", "Campbell Stephens", "Dylan Holden"]:
        raise AssertionError("Northern Team members were not prioritised alphabetically.")
    if picker_matches("Aaron") != ["Aaron Rangi"]:
        raise AssertionError("Search did not include other-team crew.")

    ok, message, vehicle_id = save_crew_vehicle(
        vehicle_id=None,
        display_label="Rav91",
        aliases=["Rav4"],
        active=True,
        sort_order=30,
        team_id=northern_team,
        notes="",
        actor_user_id=admin_user,
    )
    if not ok or vehicle_id is None:
        raise AssertionError(message)
    vehicle = next(item for item in list_crew_vehicles() if int(item["id"]) == vehicle_id)
    if "Rav4" not in json.loads(str(vehicle["aliases"])) or vehicle["display_label"] != "Rav91":
        raise AssertionError("Vehicle alias changed or failed to locate canonical Rav91.")

    def save_day(day: str, key: str, assignments: list[dict[str, object]], start: str = "09:30", finish: str = "18:00") -> int:
        day_id = save_roster_day(
            roster_day_id=None,
            roster_date=day,
            track_key=key,
            track_label="Ruakaka",
            race_type="thoroughbred",
            day_type="race_day",
            start_origin="Office / Clow Place",
            finish_destination="Office / Clow Place",
            office_start=start,
            on_track_time="",
            first_race_time="",
            last_race_time="",
            race_count=None,
            notes="",
            hotel_assignments="[]",
            custom_location="Ruakaka",
            end_time=finish,
            team_id=northern_team,
            updated_by_user_id=admin_user,
            assignments=assignments,
        )
        publish_roster_day(day_id, "{}", admin_user)
        return day_id

    open_day = save_day(
        "2026-08-15",
        "ruakaka-open",
        [
            {"assignment_key": "gimbal", "role_key": "gimbal", "role_label": "Gimbal", "assignment_state": "open", "eligible_team_id": northern_team, "transport_mode": "unassigned", "sort_order": 1},
            {"assignment_key": "floor-manager", "role_key": "fm", "role_label": "FM", "assignment_state": "tbc", "eligible_team_id": northern_team, "transport_mode": "unassigned", "sort_order": 2},
        ],
    )
    open_positions = list_open_workday_positions("2026-08-15", "2026-08-15", app_user_id=dylan_user)
    if [item["role_label"] for item in open_positions] != ["Gimbal"]:
        raise AssertionError("TBC was exposed as an apply-able open position.")
    if any(item.get("person_id") for item in open_positions):
        raise AssertionError("Open Position created a fake crew identity.")

    ok, message, first_application = apply_for_open_workday_position(roster_day_id=open_day, assignment_key="gimbal", app_user_id=dylan_user)
    ok_again, _message, same_application = apply_for_open_workday_position(roster_day_id=open_day, assignment_key="gimbal", app_user_id=dylan_user)
    if not ok or not ok_again or first_application != same_application:
        raise AssertionError("Repeated application was not idempotent.")
    other_ok, _message = withdraw_open_workday_application(roster_day_id=open_day, assignment_key="gimbal", app_user_id=alf_user)
    if other_ok:
        raise AssertionError("Another user withdrew someone else's application.")
    own_ok, _message = withdraw_open_workday_application(roster_day_id=open_day, assignment_key="gimbal", app_user_id=dylan_user)
    if not own_ok:
        raise AssertionError("Applicant could not withdraw their own application.")
    ok, _message, accepted_application = apply_for_open_workday_position(roster_day_id=open_day, assignment_key="gimbal", app_user_id=dylan_user)
    if not ok or accepted_application is None:
        raise AssertionError("Applicant could not apply again after withdrawal.")

    with get_connection() as conn:
        deputy_before = int(conn.execute("SELECT COUNT(*) count FROM shifts").fetchone()["count"])
    accepted = client.post(
        f"/admin/workday-applications/{accepted_application}/review",
        data={"action": "accept"},
        follow_redirects=False,
    )
    if accepted.status_code != 303:
        raise AssertionError("Admin application acceptance route failed.")
    assigned = [dict(item) for item in get_roster_day_assignments(open_day) if item["role_label"] == "Gimbal"]
    if len(assigned) != 1 or int(assigned[0]["person_id"]) != dylan_person or assigned[0]["assignment_state"] != "assigned":
        raise AssertionError("Accepted application did not assign canonical Dylan Holden.")
    with get_connection() as conn:
        deputy_after = int(conn.execute("SELECT COUNT(*) count FROM shifts").fetchone()["count"])
        visible = conn.execute("SELECT 1 FROM workday_user_visibility WHERE roster_day_id=? AND user_id=?", (open_day, dylan_user)).fetchone()
    if deputy_before != deputy_after or not visible:
        raise AssertionError("Application acceptance wrote to Deputy data or failed to rebuild local visibility.")

    conflict_day = save_day(
        "2026-08-16",
        "ruakaka-conflict",
        [{"assignment_key": "side-one", "role_key": "side1", "role_label": "Side 1", "assignment_state": "open", "eligible_team_id": northern_team, "transport_mode": "unassigned", "sort_order": 1}],
    )
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO shifts(source_uid,title,start_at,end_at,date,owner_user_id,deleted_from_source) VALUES (?,?,?,?,?,?,0)",
            ("conflict-fixture", "Confirmed Deputy shift", "2026-08-16T10:00:00+12:00", "2026-08-16T17:00:00+12:00", "2026-08-16", alf_user),
        )
    conflict_ok, conflict_message, _ = apply_for_open_workday_position(roster_day_id=conflict_day, assignment_key="side-one", app_user_id=alf_user)
    if conflict_ok or "rostered" not in conflict_message.lower():
        raise AssertionError("Server-side overlap conflict did not reject application.")

    unknown_confirmed = save_day(
        "2026-08-17",
        "unknown-confirmed",
        [{"assignment_key": "director", "person_id": campbell_person, "role_key": "director", "role_label": "Director", "assignment_state": "assigned", "transport_mode": "unassigned", "sort_order": 1}],
        start="",
        finish="",
    )
    unknown_open = save_day(
        "2026-08-17",
        "unknown-open",
        [{"assignment_key": "sound", "role_key": "sound", "role_label": "Sound", "assignment_state": "open", "eligible_team_id": northern_team, "transport_mode": "unassigned", "sort_order": 1}],
    )
    unknown_ok, unknown_message, _ = apply_for_open_workday_position(roster_day_id=unknown_open, assignment_key="sound", app_user_id=campbell_user)
    if unknown_ok or "that day" not in unknown_message.lower():
        raise AssertionError("Unknown-time same-date assignment was not treated conservatively.")

    vehicle_day = save_day(
        "2026-08-18",
        "vehicle-fixture",
        [{"assignment_key": "camera", "person_id": campbell_person, "role_key": "side1", "role_label": "Side 1", "assignment_state": "assigned", "transport_mode": "vehicle", "vehicle_id": vehicle_id, "vehicle_label": "wrong label", "sort_order": 1}],
    )
    vehicle_assignment = dict(get_roster_day_assignments(vehicle_day)[0])
    if vehicle_assignment["transport_mode"] != "vehicle" or int(vehicle_assignment["vehicle_id"]) != vehicle_id or vehicle_assignment["vehicle_label"] != "Rav91":
        raise AssertionError("Canonical vehicle ID/label was not stored for Crew vehicle selection.")

    print("crew, team, vehicle, open-position and application smoke ok")


if __name__ == "__main__":
    main()
