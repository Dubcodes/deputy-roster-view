from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-identity-smoke-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "identity.sqlite3"),
        APP_SECRET_KEY="identity-smoke-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
    )

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.database import (
        create_app_user,
        create_trusted_device,
        get_connection,
        get_roster_day,
        get_roster_day_assignments,
        identity_link_diagnostics,
        init_db,
        list_crew_people,
        list_roster_day_versions,
        list_trusted_devices_for_user,
        merge_crew_people,
        publish_roster_day,
        reconcile_authenticated_identities,
        save_roster_day,
        update_crew_person,
    )
    from app.main import app, build_timesheet_summary, published_rosters_by_date, roster_day_snapshot
    from app.security import encrypt_text, hash_pin

    init_db()
    settings = get_settings()

    def add_user(name: str, email: str, pin: str = "1234"):
        return create_app_user(
            deputy_email=email,
            display_name=name,
            pin_hash=hash_pin(pin),
            deputy_web_url="https://example.au.deputy.com/#/",
            encrypted_email=encrypt_text(email, settings),
            encrypted_password=encrypt_text("password", settings),
        )

    jayden = add_user("Jayden Lee Slater", "jayden@example.com")
    otm = add_user("Otm685", "otm685@example.com")
    nate_user = add_user("Nate Hubbard", "nate@example.com")
    gary = add_user("Gary Mcclure", "gary@example.com")
    joshua = add_user("Joshua Druett", "joshua@example.com")
    olivia = add_user("Olivia Dooley", "olivia@example.com")
    ambiguous = add_user("Unknown Operator", "unknown@example.com")
    # Explicitly model legacy account-synthetic rows; current account creation
    # must no longer manufacture these identities.
    with get_connection() as conn:
        for user in (jayden, otm, nate_user, gary, joshua, olivia, ambiguous):
            conn.execute(
                """INSERT INTO crew_people(canonical_display_name,current_deputy_name,app_user_id,is_active,identity_source,created_at,updated_at)
                   VALUES(?,?,?,1,'account_synthetic','','')""",
                (str(user["display_name"]), str(user["display_name"]), int(user["id"])),
            )
    init_db()

    with get_connection() as conn:
        synthetic = {
            str(row["canonical_display_name"]): int(row["id"])
            for row in conn.execute("SELECT id,canonical_display_name FROM crew_people WHERE app_user_id IS NOT NULL")
        }
        targets = {}
        for employee_id, name in ((17, "Jayden-lee"), (9, "Alf"), (23, "Nate")):
            cursor = conn.execute(
                """INSERT INTO crew_people(canonical_display_name,deputy_employee_id,current_deputy_name,is_active,admin_note,created_at,updated_at)
                   VALUES(?,?,?,1,'','','')""",
                (name, employee_id, name),
            )
            targets[employee_id] = int(cursor.lastrowid)
        for user, employee_id in ((jayden, 17), (otm, 9), (nate_user, 23)):
            conn.execute(
                """INSERT INTO deputy_personal_assignment_evidence(
                    owner_user_id,deputy_employee_id,canonical_person_id,source_shift_uid,source_shift_id,date,
                    area_location_id,position_key,position_label,start_at,end_at,first_seen_at,last_seen_at,
                    last_confirmed_at,missing_capture_count,status,provenance)
                    VALUES(?,?,?,?,?,'2026-08-01',1,'crew','Crew','2026-08-01T09:00:00+12:00',
                    '2026-08-01T17:00:00+12:00','2026-08-01T05:00:00+12:00','2026-08-01T05:00:00+12:00',
                    '2026-08-01T05:00:00+12:00',0,'confirmed','{}')""",
                (int(user["id"]), employee_id, targets[employee_id], f"personal:{user['id']}", str(user["id"])),
            )
        for user, employee_id in ((gary, 13), (joshua, 19), (olivia, 24)):
            person_id = int(conn.execute("SELECT id FROM crew_people WHERE app_user_id=?", (int(user["id"]),)).fetchone()["id"])
            conn.execute(
                "UPDATE crew_people SET deputy_employee_id=?,current_deputy_name=? WHERE id=?",
                (employee_id, str(user["display_name"]), person_id),
            )
            conn.execute(
                """INSERT INTO deputy_personal_assignment_evidence(
                    owner_user_id,deputy_employee_id,canonical_person_id,source_shift_uid,source_shift_id,date,
                    area_location_id,position_key,position_label,start_at,end_at,first_seen_at,last_seen_at,
                    last_confirmed_at,missing_capture_count,status,provenance)
                    VALUES(?,?,?,?,?,'2026-08-01',1,'crew','Crew','2026-08-01T09:00:00+12:00',
                    '2026-08-01T17:00:00+12:00','2026-08-01T05:00:00+12:00','2026-08-01T05:00:00+12:00',
                    '2026-08-01T05:00:00+12:00',0,'confirmed','{}')""",
                (int(user["id"]), employee_id, person_id, f"personal:{user['id']}", str(user["id"])),
            )
        for employee_id in (71, 72):
            conn.execute(
                """INSERT INTO deputy_personal_assignment_evidence(
                    owner_user_id,deputy_employee_id,source_shift_uid,source_shift_id,date,area_location_id,
                    position_key,position_label,start_at,end_at,first_seen_at,last_seen_at,last_confirmed_at,
                    missing_capture_count,status,provenance)
                    VALUES(?,?,?,?, '2026-08-01',1,'crew','Crew','2026-08-01T09:00:00+12:00',
                    '2026-08-01T17:00:00+12:00','','','',0,'confirmed','{}')""",
                (int(ambiguous["id"]), employee_id, f"ambiguous:{employee_id}", str(employee_id)),
            )
        extra_people = []
        for index in range(10):
            cursor = conn.execute(
                "INSERT INTO crew_people(canonical_display_name,is_active,admin_note,created_at,updated_at) VALUES(?,1,'','','')",
                (f"Contractor {index + 1}",),
            )
            extra_people.append(int(cursor.lastrowid))

    attendees = [targets[17], targets[9], targets[23], *extra_people]
    assignments = [
        {
            "person_id": person_id,
            "user_id": None,
            "assignee_label": next(
                str(row["canonical_display_name"])
                for row in list_crew_people()
                if int(row["id"]) == person_id
            ),
            "role_key": "",
            "role_label": "",
            "assignment_state": "assigned",
            "transport_mode": "not_required",
            "vehicle_key": "",
            "vehicle_label": "",
            "custom_transport_text": "",
            "assignment_note": "",
            "sort_order": index,
        }
        for index, person_id in enumerate(attendees)
    ]
    office_id = save_roster_day(
        roster_day_id=None,
        roster_date="2026-08-06",
        track_key="office-clow-place",
        track_label="Office / Clow Place",
        race_type="",
        day_type="office_day",
        start_origin="",
        finish_destination="",
        office_start="10:00",
        on_track_time="",
        first_race_time="",
        last_race_time="",
        race_count=None,
        notes="New MDR Training with Matt",
        hotel_assignments="[]",
        title="Office Day",
        custom_location="Office / Clow Place",
        end_time="13:00",
        updated_by_user_id=int(jayden["id"]),
        assignments=assignments,
    )
    row = dict(get_roster_day(office_id))
    current_assignments = [dict(item) for item in get_roster_day_assignments(office_id)]
    snapshot = roster_day_snapshot(row, current_assignments)
    if publish_roster_day(office_id, json.dumps(snapshot), int(jayden["id"])) != 1:
        raise AssertionError("Office fixture did not publish version 1.")
    if published_rosters_by_date("2026-08-06", "2026-08-06", int(jayden["id"])):
        raise AssertionError("Broken fixture unexpectedly gave Jayden personal visibility before repair.")

    # A historical draft against the account-only identity must resolve through
    # the redirect after reconciliation without rewriting a published snapshot.
    historical_id = save_roster_day(
        roster_day_id=None, roster_date="2026-08-05", track_key="office", track_label="Office / Clow Place",
        race_type="", day_type="training_day", start_origin="", finish_destination="", office_start="09:00",
        on_track_time="", first_race_time="", last_race_time="", race_count=None, notes="Historical assignment",
        hotel_assignments="[]", title="Training", custom_location="Office / Clow Place", end_time="10:00",
        updated_by_user_id=int(jayden["id"]), assignments=[{
            **assignments[0], "person_id": synthetic["Otm685"], "user_id": int(otm["id"]),
            "assignee_label": "Otm685",
        }],
    )

    client = TestClient(app)
    login = client.post("/login", data={"deputy_email": "jayden@example.com", "pin": "1234", "next_url": "/month"}, follow_redirects=False)
    if login.status_code != 303:
        raise AssertionError("Jayden test login failed.")
    correction = client.post(
        f"/admin/crew/{targets[9]}",
        data={"app_user_id": str(otm["id"])},
    )
    if correction.status_code != 200 or "This account is currently linked to" not in correction.text or "Merge duplicate into this person" not in correction.text:
        raise AssertionError("An existing app-user link did not present transfer/merge choices.")

    correct_links_before = {
        int(user["id"]): int(next(row for row in list_crew_people() if row.get("app_user_id") == int(user["id"]))["id"])
        for user in (gary, joshua, olivia)
    }

    create_trusted_device(user_id=int(otm["id"]), token_hash="trusted-otm", expires_at="2027-08-01T00:00:00+12:00")
    trusted_before = len(list_trusted_devices_for_user(int(otm["id"])))
    report = reconcile_authenticated_identities(apply=True, trigger_source="identity_smoke")
    if report["duplicate_identities_merged"] != 3:
        raise AssertionError(f"Expected three duplicate identities merged, got {report!r}")
    if report["links_repaired"] != 3 or report["published_workdays_repaired"] != 1 or report["visibility_rows_added"] != 3:
        raise AssertionError(f"Identity reconciliation report counts were not exact: {report!r}")
    if int(report["ambiguous_accounts"]) + int(report["conflicting_accounts"]) < 1:
        raise AssertionError("Contradictory personal evidence was not held for review.")

    people = list_crew_people(include_merged=True)
    active_names = {str(row["canonical_display_name"]) for row in people if int(row["is_active"] or 0)}
    for duplicate in ("Jayden Lee Slater", "Otm685", "Nate Hubbard"):
        if duplicate in active_names:
            raise AssertionError(f"Synthetic identity remained active: {duplicate}")
    for expected in ("Jayden-lee", "Alf", "Nate", "Gary Mcclure", "Joshua Druett", "Olivia Dooley"):
        if expected not in active_names:
            raise AssertionError(f"Canonical identity missing after reconciliation: {expected}")
    if len(list_trusted_devices_for_user(int(otm["id"]))) != trusted_before:
        raise AssertionError("Crew-person merge altered trusted devices.")
    for user in (gary, joshua, olivia):
        person = next(row for row in list_crew_people() if row.get("app_user_id") == int(user["id"]))
        if int(person["id"]) != correct_links_before[int(user["id"])]:
            raise AssertionError(f"Already-correct identity was changed for {user['display_name']}.")
    historical = get_roster_day_assignments(historical_id)
    if len(historical) != 1 or int(historical[0]["person_id"]) != targets[9] or historical[0]["assignee_label"] != "Alf":
        raise AssertionError("Historical account-only assignment did not resolve to Alf.")
    with get_connection() as conn:
        if int(conn.execute("SELECT COUNT(*) n FROM crew_identity_merge_audit").fetchone()["n"]) < 3:
            raise AssertionError("Automatic identity merges were not audited.")

    office_visible = published_rosters_by_date("2026-08-06", "2026-08-06", int(jayden["id"]))
    if len(office_visible.get("2026-08-06", [])) != 1:
        raise AssertionError("Reconciliation did not repair Jayden's office-day visibility.")
    if len(list_roster_day_versions(office_id)) != 1:
        raise AssertionError("Identity repair republished or version-bumped the office day.")
    if str(get_roster_day(office_id)["notes"]) != "New MDR Training with Matt":
        raise AssertionError("Identity repair altered the office-day source content.")

    for path, expected in (
        ("/month?year=2026&month=8", "Office Day"),
        ("/month?year=2026&month=8&view=list", "Office Day"),
        ("/day/2026-08-06", "New MDR Training with Matt"),
    ):
        response = client.get(path)
        if expected not in response.text:
            raise AssertionError(f"Repaired office day missing from {path}.")
    month = client.get("/month?year=2026&month=8")
    if date(2026, 8, 6) >= date.today() and (
        "Next Up" not in month.text or "Office Day" not in month.text.split("Next Up", 1)[-1]
    ):
        raise AssertionError("Repaired future office day did not appear in Next Up.")
    day = client.get("/day/2026-08-06")
    if "No app account linked" in day.text or day.text.count("Contractor ") < 10:
        raise AssertionError("Crew day leaked account-link status or lost unlinked attendees.")
    summary = build_timesheet_summary(date(2026, 8, 6), int(jayden["id"]))
    day_row = next(item for item in summary["days"] if item["iso"] == "2026-08-06")
    if day_row["total"] != 3.0 or not day_row["manual_rosters"]:
        raise AssertionError("Repaired office day did not reach timesheet/weekly totals.")
    global_month = client.get("/month?year=2026&month=8&scope=global")
    if "Office Day" not in global_month.text:
        raise AssertionError("Global crew view was damaged by identity repair.")
    builder = client.get(f"/admin/roster-days/{office_id}")
    for duplicate in (">Jayden Lee Slater ·", ">Otm685 ·", ">Nate Hubbard ·"):
        if duplicate in builder.text:
            raise AssertionError(f"Builder still offered duplicate identity {duplicate!r}.")
    if "10 attendees do not have an app login" not in builder.text or "Show names" not in builder.text:
        raise AssertionError("Admin publish review did not summarize unlinked attendees.")
    admin_page = client.get("/admin")
    if '<span class="badge">No app login</span>' not in admin_page.text:
        raise AssertionError("Admin crew review did not render the spaced No app login badge.")

    diagnostics = {str(item["display_name"]): item for item in identity_link_diagnostics()}
    if diagnostics["Unknown Operator"]["status"] != "conflicting":
        raise AssertionError("Ambiguous account diagnostic was not preserved.")

    future_person_id = None
    with get_connection() as conn:
        cursor = conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,admin_note,created_at,updated_at) VALUES('Future Crew',1,'','','')")
        future_person_id = int(cursor.lastrowid)
    future_id = save_roster_day(
        roster_day_id=None, roster_date="2026-08-12", track_key="office", track_label="Office / Clow Place",
        race_type="", day_type="training_day", start_origin="", finish_destination="", office_start="09:00",
        on_track_time="", first_race_time="", last_race_time="", race_count=None, notes="Future training",
        hotel_assignments="[]", title="Future Training", custom_location="Office / Clow Place", end_time="12:00",
        updated_by_user_id=int(jayden["id"]), assignments=[{**assignments[0], "person_id": future_person_id, "assignee_label": "Future Crew"}],
    )
    future_snapshot = roster_day_snapshot(dict(get_roster_day(future_id)), [dict(item) for item in get_roster_day_assignments(future_id)])
    publish_roster_day(future_id, json.dumps(future_snapshot), int(jayden["id"]))
    future_user = add_user("Future Login", "future@example.com")
    saved, _message = update_crew_person(future_person_id, canonical_display_name="Future Crew", app_user_id=int(future_user["id"]), aliases=[], is_active=True, admin_note="")
    if not saved or not published_rosters_by_date("2026-08-12", "2026-08-12", int(future_user["id"])):
        raise AssertionError("A later app link did not unlock an existing published workday.")

    # Exercise the explicit Admin merge workflow independently of automatic
    # personal-capture reconciliation.
    manual_user = add_user("Manual Login", "manual@example.com")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO crew_people(canonical_display_name,current_deputy_name,app_user_id,is_active,identity_source,created_at,updated_at) VALUES('Manual Login','Manual Login',?,1,'account_synthetic','','')",
            (int(manual_user["id"]),),
        )
    manual_source = next(row for row in list_crew_people() if row.get("app_user_id") == int(manual_user["id"]))
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO crew_people(canonical_display_name,deputy_employee_id,current_deputy_name,is_active,admin_note,created_at,updated_at) VALUES('Manual Canonical',88,'Manual Canonical',1,'','','')"
        )
        manual_target_id = int(cursor.lastrowid)
    manual_draft_id = save_roster_day(
        roster_day_id=None, roster_date="2026-08-13", track_key="office", track_label="Office / Clow Place",
        race_type="", day_type="office_day", start_origin="", finish_destination="", office_start="10:00",
        on_track_time="", first_race_time="", last_race_time="", race_count=None, notes="Manual merge fixture",
        hotel_assignments="[]", title="Office", custom_location="Office / Clow Place", end_time="11:00",
        updated_by_user_id=int(jayden["id"]), assignments=[{
            **assignments[0], "person_id": int(manual_source["id"]), "user_id": int(manual_user["id"]),
            "assignee_label": "Manual Login",
        }],
    )
    review = client.post(f"/admin/crew/{manual_target_id}", data={"app_user_id": str(manual_user["id"])})
    if review.status_code != 200 or "Draft assignments" not in review.text or ">1<" not in review.text:
        raise AssertionError("Manual merge confirmation did not show affected assignment counts.")
    applied = client.post(
        "/admin/crew-link/resolve",
        data={
            "action": "merge", "app_user_id": str(manual_user["id"]),
            "source_person_id": str(manual_source["id"]), "target_person_id": str(manual_target_id),
        },
        follow_redirects=False,
    )
    if applied.status_code != 303:
        raise AssertionError("Manual identity merge route failed.")
    manual_assignment = get_roster_day_assignments(manual_draft_id)[0]
    if int(manual_assignment["person_id"]) != manual_target_id or manual_assignment["assignee_label"] != "Manual Canonical":
        raise AssertionError("Manual merge did not rebase draft assignments.")
    with get_connection() as conn:
        manual_audit = conn.execute(
            "SELECT affected_counts FROM crew_identity_merge_audit WHERE source_person_id=? AND target_person_id=?",
            (int(manual_source["id"]), manual_target_id),
        ).fetchone()
    if manual_audit is None or json.loads(str(manual_audit["affected_counts"])).get("draft_assignments") != 1:
        raise AssertionError("Manual merge audit did not record affected references.")

    # Direct merge remains idempotent and preserves historical redirects.
    alf = next(row for row in list_crew_people() if row["deputy_employee_id"] == 9)
    otm_retired = next(row for row in list_crew_people(include_merged=True) if row["canonical_display_name"] == "Otm685")
    repeated = merge_crew_people(int(otm_retired["id"]), int(alf["id"]), merged_by_user_id=int(jayden["id"]), reason="Repeat smoke")
    if repeated.get("already_merged") != 1:
        raise AssertionError("Repeating an identity merge was not idempotent.")

    print("identity reconciliation smoke ok")


if __name__ == "__main__":
    main()
