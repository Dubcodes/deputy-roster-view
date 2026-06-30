from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def configure_test_environment() -> Path:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-route-smoke-"))
    os.environ["DATA_DIR"] = str(temp_dir)
    os.environ["DB_PATH"] = str(temp_dir / "route_smoke.sqlite3")
    os.environ["APP_SECRET_KEY"] = "route-smoke-secret"
    os.environ["SIGNUP_ENABLED"] = "true"
    os.environ["COOKIE_SECURE"] = "false"
    return temp_dir


def assert_redirect(response, expected_fragment: str) -> None:
    location = response.headers.get("location", "")
    if response.status_code != 303 or expected_fragment not in location:
        raise AssertionError(f"Expected redirect containing {expected_fragment!r}, got {response.status_code} {location!r}")


def main() -> None:
    temp_dir = configure_test_environment()
    with sqlite3.connect(temp_dir / "route_smoke.sqlite3") as legacy_conn:
        legacy_conn.executescript(
            """
            CREATE TABLE travel_time_defaults (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_key TEXT,
                track_label TEXT,
                base_label TEXT DEFAULT 'Clow Place',
                travel_minutes INTEGER,
                source TEXT DEFAULT 'manual',
                sample_count INTEGER DEFAULT 0,
                first_seen_at TEXT,
                last_seen_at TEXT,
                updated_at TEXT,
                note TEXT,
                UNIQUE(track_key, base_label)
            );
            INSERT INTO travel_time_defaults (
                track_key, track_label, base_label, travel_minutes, source,
                sample_count, first_seen_at, last_seen_at, updated_at, note
            ) VALUES
                ('legacyvenue', 'Legacy Venue', 'Office', 60, 'learned', 2, '2026-05-01', '2026-05-02', '2026-05-02T05:00:00+12:00', ''),
                ('legacyvenue', 'Legacy Venue', 'Clow Place', 75, 'manual', 0, '', '', '2026-05-03T05:00:00+12:00', 'manual wins');
            """
        )

    from fastapi.testclient import TestClient

    from app.database import (
        create_app_user,
        fetch_love_racing_meetings_between,
        get_connection,
        get_app_user_by_email,
        init_db,
        list_planning_locations,
        list_travel_time_defaults,
        save_love_racing_meetings,
        upsert_travel_time_default,
    )
    from app.main import (
        app,
        build_race_day_calculation,
        build_race_day_summary,
        parse_roster_summary,
        refresh_learned_travel_defaults,
        schedule_people,
    )
    from app.security import encrypt_text, hash_pin

    init_db()
    migrated_legacy = [
        dict(row) for row in list_travel_time_defaults() if row["track_key"] == "legacyvenue"
    ]
    if len(migrated_legacy) != 1:
        raise AssertionError(f"Expected legacy Office/Clow Place rows to merge, got {migrated_legacy!r}")
    if migrated_legacy[0]["base_label"] != "Office / Clow Place" or migrated_legacy[0]["travel_minutes"] != 75:
        raise AssertionError(f"Expected latest manual legacy value to win migration, got {migrated_legacy!r}")
    client = TestClient(app)

    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://bb12c621103108.au.deputy.com/#/",
            "deputy_email": "admin@example.com",
            "deputy_password": "initial-password",
            "pin": "1234",
            "pin_confirm": "1234",
            "next_url": "/settings",
        },
        follow_redirects=False,
    )
    assert_redirect(signup, "/settings")

    settings_save = client.post(
        "/settings/deputy-login",
        data={
            "deputy_web_url": "https://bb12c621103108.au.deputy.com/#/",
            "deputy_email": "admin@example.com",
            "deputy_password": "changed-password",
        },
        follow_redirects=False,
    )
    assert_redirect(settings_save, "Deputy+login+updated")

    report = client.post(
        "/settings/error-report",
        data={"report_text": "Route smoke report", "page_url": "/month"},
        follow_redirects=False,
    )
    assert_redirect(report, "Error+report+saved")

    other = create_app_user(
        deputy_email="crew@example.com",
        display_name="Crew User",
        pin_hash=hash_pin("1111"),
        deputy_web_url="https://bb12c621103108.au.deputy.com/#/",
        encrypted_email=encrypt_text("crew@example.com"),
        encrypted_password=encrypt_text("old-password"),
    )
    admin_save = client.post(
        f"/admin/users/{int(other['id'])}/deputy-login",
        data={
            "deputy_web_url": "https://bb12c621103108.au.deputy.com/#/",
            "deputy_email": "crew@example.com",
            "deputy_password": "new-password",
        },
        follow_redirects=False,
    )
    assert_redirect(admin_save, "Deputy+login+updated")

    if get_app_user_by_email("crew@example.com") is None:
        raise AssertionError("Expected admin-created crew user to remain queryable.")

    roster_lines = [
        "Trucks 0815",
        "Clow Pl 0830",
        "On track 0930",
        "8 races 1138 | 1550",
    ]
    roster_summary = parse_roster_summary(roster_lines)
    timings = {(item["label"], item["time"]) for item in roster_summary["timings"]}
    if ("Clow Place", "08:30") not in timings or ("On track", "09:30") not in timings:
        raise AssertionError(f"Expected Clow Pl shorthand to parse as Clow Place timing, got {roster_summary!r}")
    race_day = build_race_day_summary({"description_lines": roster_lines}, {})
    if {"label": "Clow Place", "value": "08:30"} not in race_day["rows"]:
        raise AssertionError(f"Expected Race Day summary to show Clow Place, got {race_day!r}")

    upsert_travel_time_default(
        track_key="matamata",
        track_label="Matamata",
        base_label="Clow Place",
        travel_minutes=60,
        source="manual",
        note="route smoke default",
    )
    upsert_travel_time_default(
        track_key="matamata",
        track_label="Matamata",
        base_label="Office",
        travel_minutes=60,
        source="manual",
        note="same physical base",
    )
    upsert_travel_time_default(
        track_key="ruakaka",
        track_label="Ruakaka",
        base_label="Beachfront Hotel",
        travel_minutes=30,
        source="manual",
        note="custom hotel remains separate",
    )
    travel_rows = [dict(row) for row in list_travel_time_defaults()]
    matamata_rows = [row for row in travel_rows if row["track_key"] == "matamata"]
    if len(matamata_rows) != 1 or matamata_rows[0]["base_label"] != "Office / Clow Place":
        raise AssertionError(f"Expected Office and Clow Place to share one default, got {matamata_rows!r}")
    if not any(row["track_key"] == "ruakaka" and row["base_label"] == "Beachfront Hotel" for row in travel_rows):
        raise AssertionError(f"Expected named hotel travel to remain separate, got {travel_rows!r}")
    inferred_summary = parse_roster_summary(["On track 0930", "8 races 1138 | 1550"])
    inferred_calc = build_race_day_calculation(
        {
            "track_label": "Matamata",
            "source_code": "T-Matamata",
            "location": "State Highway 27",
            "start_at": "2026-06-21T09:30:00+12:00",
            "end_at": "2026-06-21T18:00:00+12:00",
            "roster_summary": inferred_summary,
        }
    )
    if not inferred_calc.get("available") or not inferred_calc.get("used_default_travel"):
        raise AssertionError(f"Expected saved travel default to infer missing base timing, got {inferred_calc!r}")
    if inferred_calc.get("start_label") != "08:30":
        raise AssertionError(f"Expected inferred Clow Place start at 08:30, got {inferred_calc!r}")

    settings_page = client.get("/settings")
    if settings_page.status_code != 200 or "Your Roster" not in settings_page.text:
        raise AssertionError("Expected settings page to render roster insights.")
    if "Refresh Planning Calendar" in settings_page.text:
        raise AssertionError("Planning refresh must remain admin-only.")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO shifts (
                source_uid, owner_user_id, title, description, start_at, end_at,
                date, paid_hours, deleted_from_source, source_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                "route-smoke-travel",
                int(other["id"]),
                "[T-Travel] Travel then Overnighter",
                "Travel to Ruakaka",
                "2026-07-17T13:00:00+12:00",
                "2026-07-17T17:00:00+12:00",
                "2026-07-17",
                4.0,
                "{}",
            ),
        )
        conn.execute(
            """
            INSERT INTO shifts (
                source_uid, owner_user_id, title, description, start_at, end_at,
                date, paid_hours, deleted_from_source, source_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                "route-smoke-ruakaka",
                int(other["id"]),
                "[T-Ruakaka] Side 1",
                "",
                "2026-07-18T09:30:00+12:00",
                "2026-07-18T22:00:00+12:00",
                "2026-07-18",
                12.5,
                '{"location_name":"Ruakaka"}',
            ),
        )
    refresh_learned_travel_defaults()
    ruakaka_rows = [dict(row) for row in list_travel_time_defaults() if row["track_key"] == "ruakaka"]
    if not any(row["base_label"] == "Office / Clow Place" and int(row["travel_minutes"]) == 240 for row in ruakaka_rows):
        raise AssertionError(f"Expected overnight travel shift to teach Ruakaka's office journey, got {ruakaka_rows!r}")

    admin_page = client.get("/admin")
    if admin_page.status_code != 200 or "<h2>Locations</h2>" not in admin_page.text or "/admin/travel-defaults/" not in admin_page.text:
        raise AssertionError("Expected admin page to render unified editable locations.")
    if "Office / Clow Place" not in admin_page.text or "Beachfront Hotel" not in admin_page.text:
        raise AssertionError("Expected unified locations to show canonical office and custom hotel bases.")
    if "/admin/love-racing-refresh" not in admin_page.text or "Refresh Planning Calendar" not in admin_page.text:
        raise AssertionError("Expected admin page to render the planning refresh control.")

    save_love_racing_meetings(
        [
            {
                "date": "2026-07-04",
                "racecourse": "Te Rapa",
                "club_name": "Waikato",
                "source_url": "https://loveracing.example/calendar",
            },
            {
                "date": "2026-07-05",
                "racecourse": "Te Aroha",
                "club_name": "Waikato",
                "source_url": "https://loveracing.example/calendar",
            },
        ],
        "2026-06-30T09:00:00+12:00",
    )
    planning_admin = client.get("/admin")
    if "Locations" not in planning_admin.text or "Te Rapa" not in planning_admin.text:
        raise AssertionError("Expected admin page to list saved planning locations with travel defaults.")
    ignore_location = client.post(
        "/admin/planning-locations",
        data={"location_key": "te-rapa", "enabled": "0"},
        follow_redirects=False,
    )
    assert_redirect(ignore_location, "Planning+location+ignored")
    visible_planning = fetch_love_racing_meetings_between("2026-07-01", "2026-07-31")
    if [row["racecourse"] for row in visible_planning] != ["Te Aroha"]:
        raise AssertionError(f"Expected ignored Te Rapa planning rows to be hidden, got {visible_planning!r}")
    locations = {row["location_key"]: row for row in list_planning_locations()}
    if int(locations["terapa"]["is_enabled"] or 0) != 0:
        raise AssertionError(f"Expected Te Rapa preference to remain visible as ignored, got {locations!r}")
    planning_month = client.get("/month?year=2026&month=7")
    if '/day/2026-07-05#planning-' not in planning_month.text:
        raise AssertionError("Expected Love Racing marker to open the internal day details.")
    if "https://loveracing.example/calendar" in planning_month.text:
        raise AssertionError("Love Racing markers must not render outbound calendar links.")
    planning_day = client.get("/day/2026-07-05")
    if "Love Racing racing calendar" not in planning_day.text or "Te Aroha" not in planning_day.text:
        raise AssertionError("Expected day view to show saved Love Racing meeting facts.")
    if "https://loveracing.example/calendar" in planning_day.text:
        raise AssertionError("Love Racing day details must not render outbound calendar links.")

    people = schedule_people(
        [
            {
                "source_shift_id": 100,
                "captured_at": "2026-06-19T05:00:00+12:00",
                "area_id": 1182,
                "area_name": "Start",
                "area_location_id": 58,
                "area_roster_sort_order": 7,
                "employee_id": 109,
                "employee_name": "Campbell Stephens",
                "start_at": "2026-06-20T09:00:00+12:00",
                "end_at": "2026-06-20T19:00:00+12:00",
                "duration": 10,
                "is_open": 0,
                "is_published": 1,
                "changed_since_viewed": 1,
                "change_summary": "Person: Campbell Stephens -> Elliot",
            },
            {
                "source_shift_id": 101,
                "captured_at": "2026-06-20T08:49:00+12:00",
                "area_id": 1182,
                "area_name": "Start",
                "area_location_id": 58,
                "area_roster_sort_order": 7,
                "employee_id": 11,
                "employee_name": "Elliot",
                "start_at": "2026-06-20T09:00:00+12:00",
                "end_at": "2026-06-20T19:00:00+12:00",
                "duration": 10,
                "is_open": 0,
                "is_published": 1,
                "changed_since_viewed": 0,
                "change_summary": "",
            },
            {
                "source_shift_id": 102,
                "captured_at": "2026-06-20T08:49:00+12:00",
                "area_id": 1600,
                "area_name": "684",
                "area_location_id": 58,
                "area_roster_sort_order": 26,
                "employee_id": 109,
                "employee_name": "Campbell Stephens",
                "start_at": "2026-06-20T07:30:00+12:00",
                "end_at": "2026-06-20T09:00:00+12:00",
                "duration": 1.5,
                "is_open": 0,
                "is_published": 1,
                "changed_since_viewed": 0,
                "change_summary": "",
            },
        ],
        expected_areas=[
            {"area_id": 1178, "name": "Side 1", "location_id": 58, "roster_sort_order": 1},
            {"area_id": 1182, "name": "Start", "location_id": 58, "roster_sort_order": 7},
        ],
    )
    start_rows = [person for person in people if person["position_label"] == "Start"]
    if len(start_rows) != 1 or start_rows[0]["employee_name"] != "Elliot":
        raise AssertionError(f"Expected stale Start assignment to collapse to Elliot, got {start_rows!r}")
    if not start_rows[0]["changed"] or "Campbell Stephens -> Elliot" not in str(start_rows[0]["change_summary"]):
        raise AssertionError(f"Expected replacement change summary on Start row, got {start_rows[0]!r}")
    if any(person["employee_name"] == "Campbell Stephens" for person in people):
        raise AssertionError(f"Expected vehicle-only Campbell row to be hidden, got {people!r}")
    side_one_rows = [person for person in people if person["position_label"] == "Side 1"]
    if len(side_one_rows) != 1 or side_one_rows[0]["employee_name"] != "TBC":
        raise AssertionError(f"Expected missing Side 1 placeholder, got {side_one_rows!r}")

    stale_role_people = schedule_people(
        [
            {
                "source_shift_id": 200,
                "captured_at": "2026-06-30T05:00:00+12:00",
                "area_id": 1335,
                "area_name": "CCU2",
                "area_location_id": 129,
                "area_roster_sort_order": 12,
                "employee_id": 15,
                "employee_name": "Grant Woolston",
                "start_at": "2026-07-01T09:30:00+12:00",
                "end_at": "2026-07-01T19:00:00+12:00",
                "is_published": 1,
            },
            {
                "source_shift_id": 201,
                "captured_at": "2026-06-30T14:08:00+12:00",
                "area_id": 1467,
                "area_name": "SVT",
                "area_location_id": 129,
                "area_roster_sort_order": 28,
                "employee_id": 15,
                "employee_name": "Grant Woolston",
                "start_at": "2026-07-01T09:30:00+12:00",
                "end_at": "2026-07-01T19:00:00+12:00",
                "is_published": 1,
            },
        ]
    )
    if len(stale_role_people) != 1 or stale_role_people[0]["position_label"] != "Sound VT":
        raise AssertionError(f"Expected newer Sound VT assignment to suppress stale CCU2, got {stale_role_people!r}")

    same_capture_people = schedule_people(
        [
            {
                "source_shift_id": 202,
                "captured_at": "2026-06-30T14:08:00+12:00",
                "area_id": 1335,
                "area_name": "CCU2",
                "area_location_id": 129,
                "employee_id": 15,
                "employee_name": "Grant Woolston",
                "start_at": "2026-07-01T09:30:00+12:00",
                "end_at": "2026-07-01T19:00:00+12:00",
                "is_published": 1,
            },
            {
                "source_shift_id": 203,
                "captured_at": "2026-06-30T14:08:00+12:00",
                "area_id": 1467,
                "area_name": "SVT",
                "area_location_id": 129,
                "employee_id": 15,
                "employee_name": "Grant Woolston",
                "start_at": "2026-07-01T09:30:00+12:00",
                "end_at": "2026-07-01T19:00:00+12:00",
                "is_published": 1,
            },
        ]
    )
    if len(same_capture_people) != 1 or "CCU2" not in same_capture_people[0]["position_label"] or "Sound VT" not in same_capture_people[0]["position_label"]:
        raise AssertionError(f"Expected same-capture dual roles to remain visible, got {same_capture_people!r}")

    print("route smoke flows ok")


if __name__ == "__main__":
    main()
