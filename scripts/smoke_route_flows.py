from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date
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
                ('legacyvenue', 'Legacy Venue', 'Clow Place', 75, 'manual', 0, '', '', '2026-05-03T05:00:00+12:00', 'manual wins'),
                ('gcambridge', 'G Cambridge', 'Office', 30, 'learned', 2, '2026-05-01', '2026-05-02', '2026-05-02T05:00:00+12:00', ''),
                ('cambridgegreyhound', 'Cambridge Greyhound', 'Clow Place', 30, 'learned', 2, '2026-06-01', '2026-06-02', '2026-06-02T05:00:00+12:00', '');
            """
        )

    from fastapi.testclient import TestClient

    from app.database import (
        _compare_event_assignments,
        create_app_user,
        fetch_deputy_assignment_history_for_date,
        fetch_deputy_event_changes_for_date,
        fetch_love_racing_meetings_between,
        get_connection,
        get_app_user_by_email,
        init_db,
        list_planning_locations,
        list_travel_time_defaults,
        list_travel_routes,
        save_love_racing_meetings,
        save_deputy_web_capture_diagnostic,
        save_deputy_web_schedule,
        upsert_travel_time_default,
        upsert_track_map,
    )
    from app.main import (
        apply_schedule_role_context,
        apply_vehicle_carryover_from_people,
        app,
        build_roster_insights,
        build_race_day_calculation,
        build_race_day_summary,
        merge_description_change_lines,
        parse_roster_summary,
        refresh_learned_travel_defaults,
        roster_builder_positions,
        schedule_people,
        schedule_rows_are_vehicle_travel_context,
    )
    from app.security import encrypt_text, hash_pin

    init_db()
    stale_schedule_payload = {
        "captured_at": "2030-07-05T05:00:00+12:00",
        "areas": [],
        "locations": [],
        "extracted_shifts": [],
        "extracted_schedule_shifts": [
            {
                "id": 9001,
                "area": 1500,
                "areaName": "VT",
                "areaLocationId": 69,
                "employee": 18,
                "employeeName": "Gary McClure",
                "start": "2030-07-05T10:00:00+12:00",
                "end": "2030-07-05T18:00:00+12:00",
                "duration": 8,
                "isPublished": True,
            },
            {
                "id": 9002,
                "area": 1501,
                "areaName": "Head On",
                "areaLocationId": 58,
                "employee": 20,
                "employeeName": "Other Location",
                "start": "2030-07-05T10:00:00+12:00",
                "end": "2030-07-05T18:00:00+12:00",
                "duration": 8,
                "isPublished": True,
            },
        ],
    }
    save_deputy_web_schedule(stale_schedule_payload)
    partial_result = save_deputy_web_schedule(
        {
            "captured_at": "2030-07-05T06:00:00+12:00",
            "areas": [],
            "locations": [],
            "extracted_shifts": [],
            "extracted_schedule_shifts": [],
        }
    )
    with get_connection() as conn:
        retained_stale = conn.execute(
            "SELECT COUNT(*) FROM deputy_schedule_shifts WHERE source_shift_id = 9001"
        ).fetchone()[0]
    if retained_stale != 1 or partial_result["schedule_removed"] != 0:
        raise AssertionError("A partial Deputy capture must not remove missing schedule rows.")
    selected_result = save_deputy_web_schedule(
        {
            "captured_at": "2030-07-05T07:00:00+12:00",
            "areas": [],
            "locations": [],
            "extracted_shifts": [],
            "extracted_schedule_shifts": [],
            "schedule_coverage": [
                {
                    "start_date": "2030-07-05",
                    "end_date": "2030-07-05",
                    "mode": "selected",
                    "location_ids": [69],
                }
            ],
        }
    )
    with get_connection() as conn:
        selected_remaining = conn.execute(
            "SELECT source_shift_id FROM deputy_schedule_shifts WHERE source_shift_id IN (9001, 9002)"
        ).fetchall()
    if [row[0] for row in selected_remaining] != [9002] or selected_result["schedule_removed"] != 1:
        raise AssertionError("An exact selected-location retry should confirm removal only in its location.")
    authoritative_result = save_deputy_web_schedule(
        {
            "captured_at": "2030-07-05T08:00:00+12:00",
            "areas": [],
            "locations": [],
            "extracted_shifts": [],
            "extracted_schedule_shifts": [],
            "schedule_coverage": [
                {
                    "start_date": "2030-07-01",
                    "end_date": "2030-07-07",
                    "mode": "all",
                    "location_ids": [],
                }
            ],
            "event_retry_coverage": [
                {"date": "2030-07-05", "location_id": 58, "status": "complete"}
            ],
        }
    )
    assignment_payload = {
        "captured_at": "2030-07-18T08:00:00+12:00",
        "areas": [],
        "locations": [{"id": 88, "name": "T-Ruakaka", "address": ""}],
        "extracted_shifts": [],
        "extracted_schedule_shifts": [{
            "id": 9901, "area": 501, "areaName": "Side 2", "areaLocationId": 88,
            "employee": 41, "employeeName": "Previous Operator",
            "start": "2030-07-18T09:00:00+12:00", "end": "2030-07-18T18:00:00+12:00",
            "duration": 9, "isPublished": True,
        }],
    }
    save_deputy_web_schedule(assignment_payload)
    assignment_payload["captured_at"] = "2030-07-18T09:00:00+12:00"
    assignment_payload["extracted_schedule_shifts"][0].update(
        employee=42, employeeName="Current Operator"
    )
    save_deputy_web_schedule(assignment_payload)
    assignment_history = fetch_deputy_assignment_history_for_date("2030-07-18", [88])
    if not assignment_history or assignment_history[0]["old_employee_name"] != "Previous Operator" or assignment_history[0]["new_employee_name"] != "Current Operator":
        raise AssertionError(f"Expected durable crew assignment history, got {assignment_history!r}")

    event_coverage = [{
        "start_date": "2030-07-18", "end_date": "2030-07-18",
        "mode": "selected", "location_ids": [188],
    }]
    before_event_rows = [
        (11001, "Side 2", 51, "Previous Person"),
        (11002, "SVT", 52, "Grant Woolston"),
        (11003, "VT", 53, "Gary McClure"),
        (11004, "CCU2", 54, "Laine Baldwin"),
    ]
    save_deputy_web_schedule({
        "captured_at": "2030-07-18T20:00:00+12:00", "areas": [],
        "locations": [{"id": 188, "name": "T-Ruakaka", "address": ""}],
        "extracted_shifts": [], "schedule_coverage": event_coverage,
        "extracted_schedule_shifts": [{
            "id": shift_id, "area": shift_id, "areaName": position,
            "areaLocationId": 188, "employee": employee_id, "employeeName": employee_name,
            "start": "2030-07-18T09:00:00+12:00", "end": "2030-07-18T18:00:00+12:00",
            "duration": 9, "isPublished": True,
        } for shift_id, position, employee_id, employee_name in before_event_rows],
    })
    after_event_rows = [
        (12001, "Side 2", 54, "Laine Baldwin"),
        (12002, "SVT", 52, "Grant Woolston"),
        (12003, "CCU2", 53, "Gary McClure"),
    ]
    event_result = save_deputy_web_schedule({
        "captured_at": "2030-07-18T21:26:00+12:00", "areas": [],
        "locations": [{"id": 188, "name": "T-Ruakaka", "address": ""}],
        "extracted_shifts": [], "schedule_coverage": event_coverage,
        "extracted_schedule_shifts": [{
            "id": shift_id, "area": shift_id, "areaName": position,
            "areaLocationId": 188, "employee": employee_id, "employeeName": employee_name,
            "start": "2030-07-18T09:00:00+12:00", "end": "2030-07-18T18:00:00+12:00",
            "duration": 9, "isPublished": True,
        } for shift_id, position, employee_id, employee_name in after_event_rows],
    })
    event_history = [dict(row) for row in fetch_deputy_event_changes_for_date("2030-07-18", [188])]
    event_summaries = {str(row["display_summary"]) for row in event_history}
    expected_event_summaries = {
        "Crew move: Laine Baldwin — CCU2 → Side 2",
        "Crew move: Gary McClure — VT → CCU2",
        "Crew roles combined: Sound Grant Woolston + VT Gary McClure → Sound/VT Grant Woolston",
        "Crew: Side 2 — Previous Person → Laine Baldwin",
    }
    if event_result["event_changes_saved"] != 4 or event_summaries != expected_event_summaries:
        raise AssertionError(f"Connected crew changes were not reconstructed: {event_history!r}")
    repeated_result = save_deputy_web_schedule({
        "captured_at": "2030-07-18T21:30:00+12:00", "areas": [],
        "locations": [{"id": 188, "name": "T-Ruakaka", "address": ""}],
        "extracted_shifts": [], "schedule_coverage": event_coverage,
        "extracted_schedule_shifts": [{
            "id": shift_id, "area": shift_id, "areaName": position,
            "areaLocationId": 188, "employee": employee_id, "employeeName": employee_name,
            "start": "2030-07-18T09:00:00+12:00", "end": "2030-07-18T18:00:00+12:00",
            "duration": 9, "isPublished": True,
        } for shift_id, position, employee_id, employee_name in after_event_rows],
    })
    if repeated_result["event_changes_saved"] != 0 or len(fetch_deputy_event_changes_for_date("2030-07-18", [188])) != 4:
        raise AssertionError("An unchanged repeated capture duplicated event-level crew history.")

    def event_item(position_key: str, position_label: str, identity: str, name: str) -> dict[str, object]:
        return {
            "position_key": position_key,
            "position_label": position_label,
            "identity": identity,
            "employee_id": None,
            "employee_name": name,
            "is_open": identity == "open",
            "start_at": "2026-07-18T09:00:00+12:00",
            "end_at": "2026-07-18T18:00:00+12:00",
        }

    transition_checks = {
        "replacement": _compare_event_assignments(
            [event_item("side1", "Side 1", "employee:1", "First Operator")],
            [event_item("side1", "Side 1", "employee:2", "Second Operator")],
        ),
        "opened": _compare_event_assignments(
            [event_item("headon", "Head On", "employee:3", "Camera Operator")],
            [],
        ),
        "filled": _compare_event_assignments(
            [],
            [event_item("turn", "Turn", "employee:4", "Turn Operator")],
        ),
        "split": _compare_event_assignments(
            [event_item("soundvt", "Sound/VT", "employee:5", "Sound Operator")],
            [
                event_item("sound", "Sound", "employee:5", "Sound Operator"),
                event_item("vt", "VT", "employee:6", "VT Operator"),
            ],
        ),
    }
    for expected_type, changes in transition_checks.items():
        if len(changes) != 1 or changes[0]["change_type"] != expected_type:
            raise AssertionError(f"Expected one {expected_type} event transition, got {changes!r}")
    with get_connection() as conn:
        remaining_stale = conn.execute(
            "SELECT COUNT(*) FROM deputy_schedule_shifts WHERE source_shift_id IN (9001, 9002)"
        ).fetchone()[0]
    if remaining_stale != 0 or authoritative_result["schedule_removed"] != 1:
        raise AssertionError("A complete selected retry should confirm a future row removal.")
    migrated_legacy = [
        dict(row) for row in list_travel_time_defaults() if row["track_key"] == "legacyvenue"
    ]
    if len(migrated_legacy) != 1:
        raise AssertionError(f"Expected legacy Office/Clow Place rows to merge, got {migrated_legacy!r}")
    if migrated_legacy[0]["base_label"] != "Office / Clow Place" or migrated_legacy[0]["travel_minutes"] != 75:
        raise AssertionError(f"Expected latest manual legacy value to win migration, got {migrated_legacy!r}")
    migrated_routes = [
        dict(row) for row in list_travel_routes()
        if "Legacy Venue" in {row["origin_label"], row["destination_label"]}
    ]
    if len(migrated_routes) != 2 or not all(int(row["reverse_is_shared"] or 0) for row in migrated_routes):
        raise AssertionError(f"Expected legacy default to migrate into a shared directed pair, got {migrated_routes!r}")
    migrated_greyhound = [
        dict(row) for row in list_travel_time_defaults() if row["track_key"] == "cambridgegreyhound"
    ]
    if len(migrated_greyhound) != 1 or migrated_greyhound[0]["track_label"] != "Cambridge Greyhound":
        raise AssertionError(f"Expected G Cambridge alias rows to merge, got {migrated_greyhound!r}")
    client = TestClient(app)
    track_map_dir = temp_dir / "track_maps"
    track_map_dir.mkdir(parents=True, exist_ok=True)
    (track_map_dir / "tearoha.jpg").write_bytes(b"track-map-smoke")
    upsert_track_map(
        track_key="tearoha",
        track_label="Te Aroha",
        course_label="Te Aroha",
        course_url="https://loveracing.nz/RaceInfo/Clubs-And-Courses/34/35/Club.aspx",
        image_url="https://loveracing.nz/OnHorseFiles/Racecourses/Tracks/Te-Aroha_new.jpg",
        file_name="tearoha.jpg",
        content_type="image/jpeg",
        image_hash="smoke",
        status="ok",
        checked_at="2026-07-05T08:00:00+12:00",
        updated_at="2026-07-05T08:00:00+12:00",
    )
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
    track_map_response = client.get("/track-map/tearoha")
    if track_map_response.status_code != 200 or track_map_response.content != b"track-map-smoke":
        raise AssertionError(
            "Expected the cached track-map route to serve the local image, got "
            f"{track_map_response.status_code} {track_map_response.content[:120]!r}."
        )
    manual_map = (
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
        + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
        + b"manual-track-map-smoke"
    )
    upload_map = client.post(
        "/admin/track-maps/tearoha/upload",
        data={"track_label": "Te Aroha"},
        files={"image": ("te-aroha-better.png", manual_map, "image/png")},
        follow_redirects=False,
    )
    assert_redirect(upload_map, "Manual+map+uploaded+for+Te+Aroha")
    manual_response = client.get("/track-map/tearoha")
    if manual_response.status_code != 200 or manual_response.content != manual_map:
        raise AssertionError("Expected a manual track-map upload to override the automatic image.")
    auto_download = client.get("/admin/track-maps/tearoha/auto")
    if auto_download.status_code != 200 or auto_download.content != b"track-map-smoke":
        raise AssertionError("Expected the original automatic map to remain downloadable after an override.")
    invalid_upload = client.post(
        "/admin/track-maps/tearoha/upload",
        data={"track_label": "Te Aroha"},
        files={"image": ("not-an-image.txt", b"not an image", "text/plain")},
        follow_redirects=False,
    )
    assert_redirect(invalid_upload, "Upload+a+valid+JPEG%2C+PNG%2C+or+WebP+image")
    if client.get("/track-map/tearoha").content != manual_map:
        raise AssertionError("An invalid upload must not replace the current manual map.")

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
    admin_user = get_app_user_by_email("admin@example.com")
    if admin_user is None:
        raise AssertionError("Expected signed-up admin user to remain queryable.")

    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO shifts (
                source_uid, title, start_at, end_at, date, raw_hours, paid_hours,
                deleted_from_source, owner_user_id, source_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, '{}')
            """,
            [
                ("stats:completed", "[T-Test Park] Director", "2026-05-01T08:00:00+12:00", "2026-05-01T16:00:00+12:00", "2026-05-01", 8, 8, int(admin_user["id"])),
                ("stats:today", "[T-Test Park] Director", "2026-07-04T08:00:00+12:00", "2026-07-04T16:00:00+12:00", "2026-07-04", 8, 8, int(admin_user["id"])),
            ],
        )
        worked_shift = conn.execute(
            "SELECT id FROM shifts WHERE source_uid = 'stats:today'"
        ).fetchone()
        conn.execute(
            "INSERT INTO shift_marks (shift_id, private_note, updated_at) VALUES (?, ?, ?)",
            (int(worked_shift["id"]), "Remember the cable return.", "2026-07-04T18:00:00+12:00"),
        )
    insights = build_roster_insights(int(admin_user["id"]), date(2026, 7, 4))
    friday = next(item for item in insights["weekday_chart"] if item["label"] == "Fri")
    saturday = next(item for item in insights["weekday_chart"] if item["label"] == "Sat")
    if insights["past_90_count"] != 1 or friday["hours"] != 8 or friday["shift_count"] != 1 or saturday["shift_count"] != 0:
        raise AssertionError(f"Expected completed-only labelled weekday insights, got {insights!r}")

    builder = client.get("/admin/roster-days/new")
    if builder.status_code != 200 or "Build a race day" not in builder.text or "Crew and vehicles" not in builder.text:
        raise AssertionError("Expected admin roster builder to render.")
    filtered_positions = roster_builder_positions(["FCR CCU 1", "FCR DIR", "H-Cambridge", "Travel then Overnighter", "Side 1"])
    if filtered_positions.count("Side 1") != 1 or any(value in filtered_positions for value in ("FCR CCU 1", "FCR DIR", "H-Cambridge", "Travel then Overnighter")):
        raise AssertionError(f"Expected builder context areas to be filtered, got {filtered_positions!r}")
    draft = client.post(
        "/admin/roster-days/save",
        data={
            "roster_date": "2026-07-12",
            "new_track_label": "Test Park",
            "race_type": "thoroughbred",
            "office_start": "08:00",
            "on_track_time": "09:15",
            "first_race_time": "11:00",
            "last_race_time": "16:30",
            "race_count": "9",
            "position_label": "Director",
            "assignee": str(admin_user["id"]),
            "vehicle_label": "684",
            "notes": "Use the north gate.",
        },
        follow_redirects=False,
    )
    assert_redirect(draft, "/admin/roster-days/")
    draft_location = draft.headers["location"]
    roster_day_id = int(draft_location.split("/admin/roster-days/", 1)[1].split("?", 1)[0])
    draft_page = client.get(f"/admin/roster-days/{roster_day_id}")
    if "Not published yet" not in draft_page.text or "Use the north gate." not in draft_page.text:
        raise AssertionError("Expected saved roster draft to render privately.")
    publish = client.post(f"/admin/roster-days/{roster_day_id}/publish", follow_redirects=False)
    assert_redirect(publish, "Roster+version+1+published")
    published_day = client.get("/day/2026-07-12")
    if "Published roster" not in published_day.text or "Test Park" not in published_day.text or "684" not in published_day.text:
        raise AssertionError("Expected published roster to appear on the assigned user's day view.")
    published_month = client.get("/month?year=2026&month=7")
    if "published-roster-marker" not in published_month.text or "Test Park" not in published_month.text:
        raise AssertionError("Expected published roster marker on the assigned user's month view.")
    global_month = client.get("/month?year=2030&month=7&scope=global")
    if global_month.status_code != 200 or "Shared crew schedule" not in global_month.text:
        raise AssertionError("Expected the global crew calendar to render.")
    if "aria-label=\"Personal roster\"" not in global_month.text:
        raise AssertionError("Expected a personal-roster return control in global view.")
    if "/day/2030-07-18?scope=global&amp;location_id=88" not in global_month.text:
        raise AssertionError("Expected global calendar markers to carry their exact Deputy location.")
    global_day = client.get("/day/2030-07-18?scope=global&location_id=88")
    if global_day.status_code != 200 or "Current Operator" not in global_day.text or "Ruakaka" not in global_day.text:
        raise AssertionError("Expected the global day to render the selected location's crew data.")
    global_back_url = "/month?year=2030&amp;month=7&amp;scope=global"
    if global_back_url not in global_day.text:
        raise AssertionError("Expected global day calendar controls to return to the global month.")
    timesheet_page = client.get("/timesheet/2026-07-04")
    if "Remember the cable return." not in timesheet_page.text or "Calculation" not in timesheet_page.text:
        raise AssertionError("Expected private notes and collapsible calculation details on the timesheet.")
    changed_draft = client.post(
        "/admin/roster-days/save",
        data={
            "roster_day_id": str(roster_day_id),
            "roster_date": "2026-07-12",
            "track_label": "Test Park",
            "race_type": "thoroughbred",
            "office_start": "08:15",
            "on_track_time": "09:30",
            "first_race_time": "11:00",
            "last_race_time": "16:30",
            "race_count": "9",
            "position_label": "Director",
            "assignee": str(admin_user["id"]),
            "vehicle_label": "684",
            "notes": "Use the north gate.",
        },
        follow_redirects=False,
    )
    assert_redirect(changed_draft, f"/admin/roster-days/{roster_day_id}")
    review_page = client.get(f"/admin/roster-days/{roster_day_id}")
    if "Review unpublished changes" not in review_page.text or "08:00" not in review_page.text or "08:15" not in review_page.text:
        raise AssertionError("Expected saved changes to be highlighted against the published roster.")
    travel_draft = client.post(
        "/admin/roster-days/save",
        data={
            "roster_date": "2026-07-13",
            "new_track_label": "Rotorua",
            "race_type": "thoroughbred",
            "is_travel_day": "1",
            "hotel_user_id": str(admin_user["id"]),
            "hotel_name": "Lake Hotel",
        },
        follow_redirects=False,
    )
    assert_redirect(travel_draft, "/admin/roster-days/")
    travel_day_id = int(travel_draft.headers["location"].split("/admin/roster-days/", 1)[1].split("?", 1)[0])
    travel_publish = client.post(f"/admin/roster-days/{travel_day_id}/publish", follow_redirects=False)
    assert_redirect(travel_publish, "Roster+version+1+published")
    travel_day = client.get("/day/2026-07-13")
    if "Travel day" not in travel_day.text or "Lake Hotel" not in travel_day.text:
        raise AssertionError("Expected hotel-only travel day to publish to its assigned traveller.")
    diagnostic_marker = "LAZY-DIAGNOSTIC-MARKER"
    save_deputy_web_capture_diagnostic(
        owner_user_id=int(admin_user["id"]),
        captured_at="2099-06-30T16:23:27+12:00",
        status="ok",
        message="route smoke capture",
        payload=(
            '{"captured_at":"2099-06-30T16:23:27+12:00","status":"ok",'
            '"events":["' + diagnostic_marker + '"],"responses":[],"extracted_shifts":[],'
            '"extracted_schedule_shifts":[],"areas":[],"locations":[],"page_texts":[]}'
        ),
    )

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
    upsert_travel_time_default(
        track_key="northernopscontractors",
        track_label="Northern Ops Contractors",
        base_label="Office",
        travel_minutes=30,
        source="learned",
        sample_count=2,
        note="generic context must be removed",
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

    beachfront_summary = parse_roster_summary([
        "Accommodation Beachfront",
        "On track 0900",
        "7 races 1234 | 1605",
    ])
    beachfront_calc = build_race_day_calculation(
        {
            "track_label": "Ruakaka",
            "source_code": "T-Ruakaka",
            "start_at": "2026-07-18T09:15:00+12:00",
            "end_at": "2026-07-18T22:00:00+12:00",
            "description_lines": ["Accommodation Beachfront", "On track 0900", "7 races 1234 | 1605"],
            "roster_summary": beachfront_summary,
        }
    )
    if beachfront_calc.get("start_label") != "08:30" or beachfront_calc.get("travel_label") != "0h 30m":
        raise AssertionError(f"Expected Beachfront hotel default to infer 08:30 start, got {beachfront_calc!r}")
    if not beachfront_calc.get("roster_start_conflict") or not any(
        line.get("label") == "Deputy roster start" and line.get("value") == "09:15"
        for line in beachfront_calc.get("lines") or []
    ):
        raise AssertionError(f"Expected the late Deputy roster start to remain visible, got {beachfront_calc!r}")
    unknown_hotel_summary = parse_roster_summary([
        "Accommodation Unknown Motel",
        "On track 0900",
        "7 races 1234 | 1605",
    ])
    unknown_hotel_calc = build_race_day_calculation(
        {
            "track_label": "Ruakaka",
            "source_code": "T-Ruakaka",
            "start_at": "2026-07-18T09:15:00+12:00",
            "end_at": "2026-07-18T22:00:00+12:00",
            "description_lines": ["Accommodation Unknown Motel", "On track 0900", "7 races 1234 | 1605"],
            "roster_summary": unknown_hotel_summary,
        }
    )
    if unknown_hotel_calc.get("available"):
        raise AssertionError(f"Unknown hotels must not fall back to office travel defaults, got {unknown_hotel_calc!r}")

    changed_note_shift = {
        "track_label": "Ruakaka",
        "source_code": "T-Ruakaka",
        "start_at": "2026-07-17T13:00:00+12:00",
        "end_at": "2026-07-17T17:00:00+12:00",
        "description_lines": ["Accommodation Beachfront Motel"],
        "roster_summary": parse_roster_summary(["Accommodation Beachfront Motel"]),
        "changes": [
            {
                "field_name": "description",
                "old_value": "1pm Gaz reckons...don't argue",
                "new_value": "Accommodation Beachfront Motel",
            }
        ],
    }
    merge_description_change_lines(changed_note_shift)
    if "1pm Gaz reckons...don't argue" not in changed_note_shift.get("description_lines", []):
        raise AssertionError(f"Expected overwritten roster note line to stay visible, got {changed_note_shift!r}")

    settings_page = client.get("/settings")
    if settings_page.status_code != 200 or "Your Roster" not in settings_page.text:
        raise AssertionError("Expected settings page to render roster insights.")
    if "compact-stats-panel" not in settings_page.text or "sync-next-shift" not in settings_page.text:
        raise AssertionError("Expected settings insights to be collapsed and next-shift details consolidated with sync.")
    if "Roster snapshot" in settings_page.text:
        raise AssertionError("Expected the duplicate roster snapshot panel to remain removed.")
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
        for source_suffix, owner_id in (("admin", int(admin_user["id"])),):
            conn.execute(
                """
                INSERT INTO shifts (
                    source_uid, owner_user_id, title, description, start_at, end_at,
                    date, paid_hours, deleted_from_source, source_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    f"route-smoke-travel-{source_suffix}",
                    owner_id,
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
                    f"route-smoke-ruakaka-{source_suffix}",
                    owner_id,
                    "[T-Ruakaka] Director",
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
    if not any(
        row["base_label"] == "Office / Clow Place"
        and int(row["travel_minutes"]) == 240
        and int(row["sample_count"]) == 1
        for row in ruakaka_rows
    ):
        raise AssertionError(f"Expected overnight travel shift to teach Ruakaka's office journey, got {ruakaka_rows!r}")
    if any(row["track_key"] == "northernopscontractors" for row in list_travel_time_defaults()):
        raise AssertionError("Generic contractor context must not remain as a learned travel destination.")

    admin_page = client.get("/admin")
    if admin_page.status_code != 200 or "<strong>Locations</strong>" not in admin_page.text or "/admin/travel-defaults/" not in admin_page.text:
        raise AssertionError("Expected admin page to render unified editable locations.")
    if "Add hotel/base" not in admin_page.text or "location-management-panel" not in admin_page.text:
        raise AssertionError("Expected collapsible location management to include per-track hotel travel entry.")
    if "Office / Clow Place" not in admin_page.text or "Beachfront Hotel" not in admin_page.text:
        raise AssertionError("Expected unified locations to show canonical office and custom hotel bases.")
    if "/admin/love-racing-refresh" not in admin_page.text or "Refresh Planning Calendar" not in admin_page.text:
        raise AssertionError("Expected admin page to render the planning refresh control.")
    if "/admin/track-maps-refresh" not in admin_page.text or "Refresh Track Maps" not in admin_page.text:
        raise AssertionError("Expected admin page to render the track-map refresh control.")
    if "Manual upload" not in admin_page.text or "/admin/track-maps/tearoha/reset" not in admin_page.text:
        raise AssertionError("Expected admin track-map controls to show the active manual override.")
    if "/admin/track-maps/tearoha/auto" not in admin_page.text or "Download auto" not in admin_page.text:
        raise AssertionError("Expected the automatic map download control to remain available.")
    if "Unclassified locations" not in admin_page.text or "Test Park" not in admin_page.text:
        raise AssertionError("Expected an uncertain crew location to require compact admin classification.")
    classify_test_park = client.post(
        "/admin/track-map-locations/testpark/classify",
        data={"location_label": "Test Park", "classification": "venue"},
        follow_redirects=False,
    )
    assert_redirect(classify_test_park, "Test+Park+is+now+a+racing+venue")
    admin_page = client.get("/admin")
    if "/admin/track-maps/testpark/upload" not in admin_page.text:
        raise AssertionError("Expected a classified racing venue to gain a manual map upload control.")
    reset_map = client.post("/admin/track-maps/tearoha/reset", follow_redirects=False)
    assert_redirect(reset_map, "Manual+map+removed")
    reset_response = client.get("/track-map/tearoha")
    if reset_response.status_code != 200 or reset_response.content != b"track-map-smoke":
        raise AssertionError("Expected reset to restore the untouched automatic track map.")
    help_page = client.get("/help")
    if help_page.status_code != 200 or "<dt>Your shifts</dt>" not in help_page.text or "<dt>Day heading</dt>" not in help_page.text:
        raise AssertionError("Expected help introductions to use the aligned label-and-detail layout.")
    if "A quick note before you start" in help_page.text or "Admin tools" in help_page.text:
        raise AssertionError("Expected unnecessary help sections to remain hidden.")
    if diagnostic_marker in admin_page.text or f'/admin/users/{int(admin_user["id"])}/diagnostics.txt' not in admin_page.text:
        raise AssertionError("Expected Admin diagnostics to be loaded on demand rather than embedded in the page.")
    diagnostic_response = client.get(f'/admin/users/{int(admin_user["id"])}/diagnostics.txt')
    if diagnostic_response.status_code != 200 or diagnostic_marker not in diagnostic_response.text:
        raise AssertionError(
            "Expected the admin-only diagnostics endpoint to return the saved capture, got "
            f"{diagnostic_response.status_code} {diagnostic_response.text[:300]!r}"
        )

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

    ruakaka_travel_rows = [
        {
            "source_shift_id": 201,
            "captured_at": "2026-07-17T11:18:00+12:00",
            "date": "2026-07-17",
            "area_id": 1600,
            "area_name": "684",
            "area_location_id": 59,
            "area_roster_sort_order": 30,
            "employee_id": 17,
            "employee_name": "Jayden-lee",
            "start_at": "2026-07-17T13:00:00+12:00",
            "end_at": "2026-07-17T17:00:00+12:00",
            "is_published": 1,
        },
        {
            "source_shift_id": 202,
            "captured_at": "2026-07-17T11:18:00+12:00",
            "date": "2026-07-17",
            "area_id": 1601,
            "area_name": "Rav91",
            "area_location_id": 59,
            "area_roster_sort_order": 31,
            "employee_id": 24,
            "employee_name": "Grant Woolston",
            "start_at": "2026-07-17T12:00:00+12:00",
            "end_at": "2026-07-17T17:00:00+12:00",
            "is_published": 1,
        },
        {
            "source_shift_id": 203,
            "captured_at": "2026-07-17T11:18:00+12:00",
            "date": "2026-07-17",
            "area_id": 1602,
            "area_name": "Tender",
            "area_location_id": 59,
            "area_roster_sort_order": 32,
            "employee_id": 18,
            "employee_name": "Dylan Holden",
            "start_at": "2026-07-17T12:00:00+12:00",
            "end_at": "2026-07-17T17:00:00+12:00",
            "is_published": 1,
        },
    ]
    if schedule_people(ruakaka_travel_rows):
        raise AssertionError("Vehicle-only rows must remain hidden on normal crew tables.")
    if not schedule_rows_are_vehicle_travel_context(ruakaka_travel_rows):
        raise AssertionError("Expected Ruakaka vehicle rows to be detected as travel context.")
    travel_people = schedule_people(
        ruakaka_travel_rows,
        include_vehicle_only=True,
        include_placeholders=False,
    )
    jayden_travel = [person for person in travel_people if person["employee_name"] == "Jayden-lee"]
    if len(jayden_travel) != 1 or jayden_travel[0]["position_label"] != "Travel" or jayden_travel[0]["vehicle_label"] != "684":
        raise AssertionError(f"Expected vehicle-only travel row for Jayden-lee, got {travel_people!r}")

    ruakaka_race_people = schedule_people(
        [
            {
                "source_shift_id": 211,
                "captured_at": "2026-07-18T11:18:00+12:00",
                "date": "2026-07-18",
                "area_id": 1,
                "area_name": "Director",
                "area_location_id": 59,
                "employee_id": 17,
                "employee_name": "Jayden-lee",
                "start_at": "2026-07-18T09:15:00+12:00",
                "end_at": "2026-07-18T22:00:00+12:00",
                "is_published": 1,
            },
            {
                "source_shift_id": 212,
                "captured_at": "2026-07-18T11:18:00+12:00",
                "date": "2026-07-18",
                "area_id": 2,
                "area_name": "Sound",
                "area_location_id": 59,
                "employee_id": 24,
                "employee_name": "Grant Woolston",
                "start_at": "2026-07-18T09:15:00+12:00",
                "end_at": "2026-07-18T22:00:00+12:00",
                "is_published": 1,
            },
            {
                "source_shift_id": 213,
                "captured_at": "2026-07-18T11:18:00+12:00",
                "date": "2026-07-18",
                "area_id": 3,
                "area_name": "Side 1",
                "area_location_id": 59,
                "employee_id": 18,
                "employee_name": "Dylan Holden",
                "start_at": "2026-07-18T09:15:00+12:00",
                "end_at": "2026-07-18T22:00:00+12:00",
                "is_published": 1,
            },
        ]
    )
    apply_vehicle_carryover_from_people(ruakaka_race_people, travel_people)
    carryover = {person["employee_name"]: person["vehicle_label"] for person in ruakaka_race_people}
    if carryover.get("Jayden-lee") != "684" or carryover.get("Grant Woolston") != "Rav91" or carryover.get("Dylan Holden") != "Tender":
        raise AssertionError(f"Expected previous-day Ruakaka vehicles to carry onto race crew, got {carryover!r}")

    optional_people = schedule_people(
        [
            {
                "source_shift_id": 109,
                "captured_at": "2026-07-05T08:00:00+12:00",
                "date": "2026-07-05",
                "area_id": 1,
                "area_name": "Side 1",
                "area_location_id": 69,
                "employee_id": 19,
                "employee_name": "Joshua Druett",
                "start_at": "2026-07-05T10:00:00+12:00",
                "end_at": "2026-07-05T18:00:00+12:00",
                "is_published": 1,
            },
            {
                "source_shift_id": 108,
                "captured_at": "2026-07-05T08:00:00+12:00",
                "date": "2026-07-05",
                "area_id": 2,
                "area_name": "RTS",
                "area_location_id": 69,
                "employee_id": None,
                "employee_name": "",
                "start_at": "2026-07-05T10:00:00+12:00",
                "end_at": "2026-07-05T18:00:00+12:00",
                "is_open": 1,
                "is_published": 1,
            },
            {
                "source_shift_id": 107,
                "captured_at": "2026-07-05T08:00:00+12:00",
                "date": "2026-07-05",
                "area_id": 3,
                "area_name": "FM",
                "area_location_id": 69,
                "employee_id": None,
                "employee_name": "",
                "start_at": "2026-07-05T10:00:00+12:00",
                "end_at": "2026-07-05T18:00:00+12:00",
                "is_open": 1,
                "is_published": 1,
            },
        ],
        expected_areas=[
            {"area_id": 1, "name": "Side 1", "location_id": 69, "roster_sort_order": 1},
            {"area_id": 2, "name": "RTS", "location_id": 69, "roster_sort_order": 8},
            {"area_id": 3, "name": "FM", "location_id": 69, "roster_sort_order": 14},
        ],
    )
    if any(person["position_label"] in {"RTS", "FM"} for person in optional_people):
        raise AssertionError(f"Unassigned optional RTS/FM rows should stay hidden, got {optional_people!r}")

    moved_people = schedule_people(
        [
            {
                "source_shift_id": 110,
                "captured_at": "2026-07-03T12:45:00+12:00",
                "area_id": 1,
                "area_name": "Side 1",
                "area_location_id": 58,
                "employee_id": 10,
                "employee_name": "Leger",
                "start_at": "2026-07-04T09:30:00+12:00",
                "end_at": "2026-07-04T17:30:00+12:00",
                "changed_since_viewed": 1,
                "change_summary": "Position: Head On -> Side 1",
            },
            {
                "source_shift_id": 111,
                "captured_at": "2026-07-03T12:45:00+12:00",
                "area_id": 2,
                "area_name": "Side 2",
                "area_location_id": 58,
                "employee_id": 11,
                "employee_name": "Nate",
                "start_at": "2026-07-04T09:30:00+12:00",
                "end_at": "2026-07-04T17:30:00+12:00",
                "changed_since_viewed": 1,
                "change_summary": "Position: Side 1 -> Side 2",
            },
            {
                "source_shift_id": 112,
                "captured_at": "2026-07-03T12:45:00+12:00",
                "area_id": 3,
                "area_name": "Vehicles",
                "area_location_id": 58,
                "employee_id": 11,
                "employee_name": "Nate",
                "start_at": "2026-07-04T09:15:00+12:00",
                "end_at": "2026-07-04T17:30:00+12:00",
            },
            {
                "source_shift_id": 113,
                "captured_at": "2026-07-03T12:45:00+12:00",
                "area_id": 4,
                "area_name": "685",
                "area_location_id": 58,
                "employee_id": 11,
                "employee_name": "Nate",
                "start_at": "2026-07-04T09:15:00+12:00",
                "end_at": "2026-07-04T17:30:00+12:00",
            },
        ]
    )
    moved_side_one = next((person for person in moved_people if person["position_label"] == "Side 1"), None)
    moved_side_two = next((person for person in moved_people if person["position_label"] == "Side 2"), None)
    if moved_side_one is None or moved_side_two is None:
        raise AssertionError(f"Expected both moved production rows, got {moved_people!r}")
    if moved_side_one["change_summary"] != "Side 1: Nate -> Leger":
        raise AssertionError(f"Expected person-focused Side 1 change, got {moved_side_one!r}")
    if moved_side_two["vehicle_label"] != "685":
        raise AssertionError(f"Expected generic Vehicles label to be removed, got {moved_side_two!r}")

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
    if len(stale_role_people) != 1 or stale_role_people[0]["position_label"] != "Sound/VT":
        raise AssertionError(f"Expected newer Sound/VT assignment to suppress stale CCU2, got {stale_role_people!r}")

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
    if len(same_capture_people) != 1 or "CCU2" not in same_capture_people[0]["position_label"] or "Sound/VT" not in same_capture_people[0]["position_label"]:
        raise AssertionError(f"Expected same-capture dual roles to remain visible, got {same_capture_people!r}")

    split_sound_vt_rows = [
        {
            "source_shift_id": 204,
            "captured_at": "2026-07-05T08:13:00+12:00",
            "date": "2026-07-05",
            "area_id": 1491,
            "area_name": "SVT",
            "area_location_id": 69,
            "employee_id": 17,
            "employee_name": "Jayden-lee",
            "start_at": "2026-07-05T10:00:00+12:00",
            "end_at": "2026-07-05T18:00:00+12:00",
            "is_published": 1,
        },
        {
            "source_shift_id": 205,
            "captured_at": "2026-07-05T08:13:00+12:00",
            "date": "2026-07-05",
            "area_id": 1500,
            "area_name": "VT",
            "area_location_id": 69,
            "employee_id": 18,
            "employee_name": "Gary McClure",
            "start_at": "2026-07-05T10:00:00+12:00",
            "end_at": "2026-07-05T18:00:00+12:00",
            "is_published": 1,
        },
    ]
    split_sound_vt_people = schedule_people(split_sound_vt_rows)
    split_positions = {
        str(person["employee_name"]): str(person["position_label"])
        for person in split_sound_vt_people
    }
    if split_positions != {"Jayden-lee": "Sound", "Gary McClure": "VT"}:
        raise AssertionError(f"Expected separate Sound and VT positions, got {split_sound_vt_people!r}")

    own_sound_shift = {
        "date": "2026-07-05",
        "track_label": "Te Aroha",
        "schedule_location_id": 69,
        "schedule_location_ids": [69],
        "role_label": "SVT",
        "role_full_label": "Sound/VT",
        "display_title": "SVT at Te Aroha",
        "role_segments": [
            {"role": "Sound/VT", "role_short": "SVT", "kind": "role"},
        ],
        "role_chain_label": "Sound/VT",
    }
    apply_schedule_role_context([own_sound_shift], split_sound_vt_rows)
    if own_sound_shift["role_full_label"] != "Sound" or own_sound_shift["role_chain_label"] != "Sound":
        raise AssertionError(f"Expected the user's SVT shift to display as Sound, got {own_sound_shift!r}")

    combined_sound_people = schedule_people(split_sound_vt_rows[:1])
    if len(combined_sound_people) != 1 or combined_sound_people[0]["position_label"] != "Sound/VT":
        raise AssertionError(f"Expected SVT to stay combined without a separate VT assignment, got {combined_sound_people!r}")

    same_employee_split_rows = [dict(row) for row in split_sound_vt_rows]
    same_employee_split_rows[1]["employee_id"] = 17
    same_employee_split_rows[1]["employee_name"] = "Jayden-lee"
    same_employee_people = schedule_people(same_employee_split_rows)
    if len(same_employee_people) != 1 or same_employee_people[0]["position_label"] != "Sound/VT, VT":
        raise AssertionError(f"Expected one person's explicit dual assignment to remain visible, got {same_employee_people!r}")

    unknown_location_rows = [dict(row) for row in split_sound_vt_rows]
    for row in unknown_location_rows:
        row["area_location_id"] = None
    unknown_location_people = schedule_people(unknown_location_rows)
    unknown_positions = {str(person["position_label"]) for person in unknown_location_people}
    if unknown_positions != {"Sound/VT", "VT"}:
        raise AssertionError(f"Expected unknown-location rows not to infer a split, got {unknown_location_people!r}")

    print("route smoke flows ok")


if __name__ == "__main__":
    main()
