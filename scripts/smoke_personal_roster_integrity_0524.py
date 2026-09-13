from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temp_dir = Path(tempfile.mkdtemp(prefix="redeputy-0524-personal-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "personal-integrity.sqlite3"),
        APP_SECRET_KEY="personal-integrity-smoke",
        TZ="Pacific/Auckland",
        DEPUTY_WRITE_MODE="off",
    )

    from app.config import get_settings
    from app.database import (
        fetch_deputy_schedule_for_date,
        fetch_shifts_between,
        get_roster_integrity_diagnostics,
        get_upcoming_shifts,
        init_db,
        reconcile_effective_personal_rosters,
        save_deputy_web_schedule,
        workday_assignment_conflicts,
    )
    from app.auth import _add_sync_notice
    from app.main import (
        apply_event_changes_to_schedule_people,
        build_roster_insights,
        build_timesheet_summary,
        inferred_tbc_schedule,
        schedule_people,
    )
    from app.deputy_integration import load_config as load_deputy_write_config
    from app.notifications import generate_due_notifications, save_notification_preferences

    init_db()
    settings = get_settings()
    now = datetime.now(settings.timezone).replace(microsecond=0)
    dates = [(now.date() + timedelta(days=offset)).isoformat() for offset in (1, 3, 5, 7, 9, 12)]
    personal_dates = [(now.date() - timedelta(days=4)).isoformat(), (now.date() - timedelta(days=2)).isoformat(), now.date().isoformat()]
    captured_at = (now - timedelta(hours=2)).isoformat()
    db_path = settings.db_path
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO app_users
               (id,deputy_email,display_name,pin_hash,deputy_web_url,is_admin,is_active,created_at,updated_at)
               VALUES (1,'linked@example.test','Linked Crew','x','https://example.test',1,1,?,?)""",
            (captured_at, captured_at),
        )
        conn.execute(
            """INSERT INTO app_users
               (id,deputy_email,display_name,pin_hash,deputy_web_url,is_admin,is_active,created_at,updated_at)
               VALUES (2,'unresolved@example.test','Unresolved Crew','x','https://example.test',0,1,?,?)""",
            (captured_at, captured_at),
        )
        person_id = conn.execute(
            """INSERT INTO crew_people
               (deputy_employee_id,canonical_display_name,current_deputy_name,app_user_id,is_active,created_at,updated_at)
               VALUES (19,'Linked Crew','Linked Crew',1,1,?,?)""",
            (captured_at, captured_at),
        ).lastrowid
        conn.execute(
            """INSERT INTO app_user_deputy_identity
               (app_user_id,deputy_employee_id,canonical_person_id,first_confirmed_at,last_confirmed_at,
                confidence_source,status,updated_at)
               VALUES (1,19,?,?,?,'authenticated_personal_capture','confirmed',?)""",
            (person_id, captured_at, captured_at, captured_at),
        )

    areas = [
        {"id": 101, "name": "Director", "locationId": 64, "rosterSortOrder": 1},
        {"id": 102, "name": "Sound", "locationId": 64, "rosterSortOrder": 2},
        {"id": 103, "name": "VT", "locationId": 64, "rosterSortOrder": 3},
        {"id": 104, "name": "Side 1", "locationId": 64, "rosterSortOrder": 4},
        {"id": 105, "name": "Gimbal", "locationId": 64, "rosterSortOrder": 5},
        {"id": 106, "name": "Steadi", "locationId": 64, "rosterSortOrder": 6},
        {"id": 107, "name": "LDHO", "locationId": 64, "rosterSortOrder": 7},
    ]

    def row(shift_id: int, date_text: str, employee: int, name: str, area: int = 101, *, is_open: bool = False) -> dict[str, object]:
        area_row = next(item for item in areas if item["id"] == area)
        return {
            "id": shift_id, "area": area, "areaName": area_row["name"],
            "areaLocationId": 64, "location": 64, "locationName": "T-Test Track",
            "employee": employee, "employeeName": name,
            "start": f"{date_text}T09:00:00+12:00", "end": f"{date_text}T17:00:00+12:00",
            "duration": 28800, "isPublished": not is_open, "isOpen": is_open,
        }

    personal_rows = [row(8000 + index, day, 19, "Linked Crew") for index, day in enumerate(personal_dates)]
    shared_rows = [row(9000 + index, day, 19, "Linked Crew") for index, day in enumerate(dates)]
    shared_ids = [int(item["id"]) for item in shared_rows]
    result = save_deputy_web_schedule({
        "captured_at": captured_at,
        "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": personal_rows,
        "extracted_schedule_shifts": shared_rows,
        "native_schedule_shift_ids": shared_ids,
        "direct_schedule_shift_ids": [],
        "schedule_coverage": [],
        "own_roster_coverage": [{
            "start_date": personal_dates[0], "end_date": personal_dates[-1], "status": "partial",
            "records_returned": len(personal_rows), "pagination_complete": False,
            "known_shift_ids_checked": False,
        }],
    }, owner_user_id=1)
    if result["effective_roster_filled"] != len(shared_rows):
        raise AssertionError(result)

    effective = [dict(item) for item in fetch_shifts_between(personal_dates[0], dates[-1], owner_user_id=1)]
    effective_dates = {str(item["date"]) for item in effective if not int(item["deleted_from_source"] or 0)}
    if not set(dates).issubset(effective_dates):
        raise AssertionError(f"Shared future workdays missing from effective Month source: {effective_dates!r}")
    if len({item["source_uid"] for item in effective}) != len(effective):
        raise AssertionError("Effective personal roster contains duplicate Deputy shift identities.")
    upcoming = [dict(item) for item in get_upcoming_shifts(now.isoformat(), limit=20, owner_user_id=1)]
    if not set(dates).issubset({str(item["date"]) for item in upcoming}):
        raise AssertionError("Next Up did not consume the effective personal roster.")
    insights = build_roster_insights(1, now.date())
    if int(insights["upcoming_count"]) < len(shared_rows):
        raise AssertionError(f"Roster insights omitted effective shared work: {insights!r}")
    timesheet = build_timesheet_summary(date.fromisoformat(dates[0]), 1)
    timesheet_dates = {
        str(day["iso"])
        for day in timesheet["days"]
        if any(str(shift.get("source_uid") or "").endswith(":9000") for shift in day["shifts"])
    }
    if dates[0] not in timesheet_dates:
        raise AssertionError("Timesheet summary omitted an effective shared-positive workday.")

    linked_notice = {
        "id": 1, "has_deputy_credentials": True, "sync_in_progress": 0,
        "last_sync_status": "ok", "last_sync_at": now.isoformat(),
    }
    _add_sync_notice(linked_notice)
    if "shared roster evidence was used" not in str(linked_notice.get("sync_notice_text") or ""):
        raise AssertionError(f"Supplemented coverage notice was not truthful: {linked_notice!r}")
    unresolved_notice = {
        "id": 2, "has_deputy_credentials": True, "sync_in_progress": 0,
        "last_sync_status": "ok", "last_sync_at": now.isoformat(),
    }
    _add_sync_notice(unresolved_notice)
    if "Some workdays may be missing" not in str(unresolved_notice.get("sync_notice_text") or ""):
        raise AssertionError(f"Unresolved identity did not expose coverage risk: {unresolved_notice!r}")

    first_shared = next(item for item in effective if str(item["source_uid"]).endswith(":9000"))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO shift_marks (shift_id,private_note,personal_start_time,updated_at)
               VALUES (?, 'keep me', '08:45', ?)""",
            (first_shared["id"], captured_at),
        )
    save_deputy_web_schedule({
        "captured_at": (now - timedelta(hours=1)).isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": [shared_rows[0]], "extracted_schedule_shifts": [shared_rows[0]],
        "native_schedule_shift_ids": [9000], "direct_schedule_shift_ids": [9000],
        "schedule_coverage": [], "own_roster_coverage": [],
    }, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        caught_up = conn.execute("SELECT * FROM shifts WHERE owner_user_id=1 AND source_uid LIKE '%:9000'").fetchall()
        mark = conn.execute("SELECT * FROM shift_marks WHERE shift_id=?", (first_shared["id"],)).fetchone()
    if len(caught_up) != 1 or caught_up[0]["source_url_hash"] != "deputy-web:1":
        raise AssertionError("Authenticated personal catch-up did not upgrade the stable shared-derived row in place.")
    if mark is None or mark["private_note"] != "keep me" or mark["personal_start_time"] != "08:45":
        raise AssertionError("Personal marks/overrides were lost when authenticated evidence caught up.")
    save_deputy_web_schedule({
        "captured_at": (now - timedelta(minutes=30)).isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": [shared_rows[0]], "extracted_schedule_shifts": [shared_rows[0]],
        "native_schedule_shift_ids": [9000], "direct_schedule_shift_ids": [9000],
        "schedule_coverage": [], "own_roster_coverage": [],
    }, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        repeated = conn.execute(
            "SELECT * FROM shifts WHERE owner_user_id=1 AND source_uid LIKE '%:9000'"
        ).fetchall()
    repeated_payload = json.loads(str(repeated[0]["source_payload"] or "{}")) if repeated else {}
    if len(repeated) != 1 or not bool((repeated_payload.get("effective_roster") or {}).get("notification_baseline")):
        raise AssertionError("Repeated unchanged personal capture lost stable deduplication or notification baseline.")

    unresolved = row(9999, dates[0], 999, "Unresolved Employee")
    save_deputy_web_schedule({
        "captured_at": now.isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": [], "extracted_schedule_shifts": [unresolved],
        "native_schedule_shift_ids": [9999], "direct_schedule_shift_ids": [],
        "schedule_coverage": [], "own_roster_coverage": [],
    })
    if fetch_shifts_between(dates[0], dates[0], owner_user_id=2):
        raise AssertionError("Unresolved identity received a guessed personal workday.")

    # Partial direct absence cannot erase native-positive work or its personal fill.
    save_deputy_web_schedule({
        "captured_at": (now + timedelta(minutes=5)).isoformat(), "areas": areas,
        "locations": [], "extracted_shifts": [], "extracted_schedule_shifts": [],
        "native_schedule_shift_ids": [], "direct_schedule_shift_ids": [],
        "schedule_coverage": [], "own_roster_coverage": [],
    }, owner_user_id=1)
    if not any(str(item["source_uid"]).endswith(":9001") for item in fetch_shifts_between(dates[1], dates[1], 1)):
        raise AssertionError("Partial coverage deleted a shared-positive personal assignment.")

    replacement_date = (now.date() + timedelta(days=15)).isoformat()
    original_assignment = row(9200, replacement_date, 19, "Linked Crew")
    replacement_assignment = row(9201, replacement_date, 77, "Replacement Crew")
    exact_coverage = [{
        "start_date": replacement_date, "end_date": replacement_date,
        "mode": "selected", "location_ids": [64],
    }]
    save_deputy_web_schedule({
        "captured_at": captured_at, "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": [], "extracted_schedule_shifts": [original_assignment],
        "native_schedule_shift_ids": [], "direct_schedule_shift_ids": [9200],
        "schedule_coverage": exact_coverage, "own_roster_coverage": [],
    }, owner_user_id=1)
    save_deputy_web_schedule({
        "captured_at": (now - timedelta(hours=1)).isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": [], "extracted_schedule_shifts": [replacement_assignment],
        "native_schedule_shift_ids": [], "direct_schedule_shift_ids": [9201],
        "schedule_coverage": exact_coverage, "own_roster_coverage": [],
    }, owner_user_id=1)
    replaced_rows = [dict(item) for item in fetch_shifts_between(replacement_date, replacement_date, 1)]
    if not replaced_rows or not all(int(item["deleted_from_source"] or 0) for item in replaced_rows):
        raise AssertionError(f"Authoritative positive replacement left the old linked user current: {replaced_rows!r}")
    with sqlite3.connect(db_path) as conn:
        history_count = conn.execute(
            "SELECT COUNT(*) FROM deputy_schedule_event_changes WHERE date=?",
            (replacement_date,),
        ).fetchone()[0]
    if history_count < 1:
        raise AssertionError("Authoritative replacement did not retain assignment history.")

    # Reminders see a shared-derived shift, but initial reconciliation is not a fake change.
    save_notification_preferences(1, {
        "enabled": True, "night_before": True, "changes_enabled": True,
        "reminder_time": "19:00",
    })
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE deputy_schedule_shifts SET changed_since_viewed=1,last_changed_at=? WHERE source_shift_id='9000'",
            (now.isoformat(),),
        )
    reminder_now = datetime.combine(now.date(), time(19, 5), settings.timezone)
    generated = generate_due_notifications(reminder_now)
    if generated["reminders"] < 1 or generated["changes"] != 0:
        raise AssertionError(f"Expected normal reminder without reconciliation change storm, got {generated!r}")

    changed_shared = dict(shared_rows[1])
    changed_shared.update({
        "start": f"{dates[1]}T10:00:00+12:00",
        "end": f"{dates[1]}T18:00:00+12:00",
    })
    save_deputy_web_schedule({
        "captured_at": (now + timedelta(minutes=10)).isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": [], "extracted_schedule_shifts": [changed_shared],
        "native_schedule_shift_ids": [int(changed_shared["id"])],
        "direct_schedule_shift_ids": [], "schedule_coverage": [], "own_roster_coverage": [],
    }, owner_user_id=1)
    changed_notifications = generate_due_notifications(now + timedelta(minutes=11))
    if changed_notifications["changes"] < 1:
        raise AssertionError(
            f"A genuine post-reconciliation Deputy change did not resume normal notifications: {changed_notifications!r}"
        )
    changed_effective = next(
        dict(item) for item in fetch_shifts_between(dates[1], dates[1], 1)
        if str(item["source_uid"]).endswith(f":{changed_shared['id']}")
    )
    changed_payload = json.loads(str(changed_effective["source_payload"] or "{}"))
    if bool((changed_payload.get("effective_roster") or {}).get("notification_baseline")):
        raise AssertionError("A genuine Deputy change failed to clear the reconciliation notification baseline.")

    with sqlite3.connect(db_path) as conn:
        roster_day_id = conn.execute(
            """INSERT INTO roster_days
               (roster_date,track_key,track_label,day_type,office_start,end_time,status,created_at,updated_at)
               VALUES (?, 'test','Test Track','race_day','09:30','16:00','draft',?,?)""",
            (dates[0], now.isoformat(), now.isoformat()),
        ).lastrowid
    conflicts = workday_assignment_conflicts(crew_person_id=int(person_id), roster_day_id=int(roster_day_id))
    if not any(item.get("message") == "Not available - already rostered." for item in conflicts):
        raise AssertionError(f"Availability missed effective shared-positive work: {conflicts!r}")

    open_row = row(9100, dates[0], 0, "", area=104, is_open=True)
    event_rows = [row(9101, dates[0], 17, "Director Crew", area=101), row(9102, dates[0], 13, "Sound Crew", area=102), row(9103, dates[0], 20, "VT Crew", area=103), open_row]
    save_deputy_web_schedule({
        "captured_at": now.isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Test Track", "address": ""}],
        "extracted_shifts": [], "extracted_schedule_shifts": event_rows,
        "native_schedule_shift_ids": [9100, 9101, 9102, 9103], "direct_schedule_shift_ids": [],
        "schedule_coverage": [], "own_roster_coverage": [],
    })
    people = schedule_people(
        fetch_deputy_schedule_for_date(dates[0], [64]),
        expected_areas=areas,
        include_placeholders=False,
    )
    labels = {str(item["position_label"]) for item in people}
    if labels & {"Gimbal", "Steadi", "LDHO"}:
        raise AssertionError(f"Venue Area catalogue manufactured TBC positions: {labels!r}")
    if not any(item["employee_name"] == "Open shift" and item["position_label"] == "Side 1" for item in people):
        raise AssertionError("Genuine Deputy open shift was hidden with synthetic placeholders.")
    inferred_missing = inferred_tbc_schedule(
        date.fromisoformat(dates[0]), date.fromisoformat(dates[0])
    )
    if any(item["position_label"] in {"Gimbal", "Steadi", "LDHO"} for item in inferred_missing):
        raise AssertionError(f"Admin integrity inferred missing roles from venue Areas: {inferred_missing!r}")

    changes = [
        {"changed_at": "2030-09-12T05:06:00+12:00", "changed_since_viewed": True,
         "inline_changes": [{"position_key": "director", "summary": "C → D"}]},
        {"changed_at": "2030-09-11T14:00:00+12:00", "changed_since_viewed": True,
         "inline_changes": [{"position_key": "director", "summary": "B → C"}]},
        {"changed_at": "2030-09-10T09:00:00+12:00", "changed_since_viewed": True,
         "inline_changes": [{"position_key": "director", "summary": "A → B"}]},
    ]
    people_for_change = [{"employee_name": "D", "position_label": "Director", "last_changed_at": ""}]
    apply_event_changes_to_schedule_people(people_for_change, changes)
    if people_for_change[0]["change_time_label"] != "12 Sep 05:06" or people_for_change[0]["change_summary"] != "C → D":
        raise AssertionError(f"Older assignment change overwrote newest badge: {people_for_change!r}")
    equal_time = [
        {"changed_at": "2030-09-12T05:06:00+12:00", "changed_since_viewed": True,
         "inline_changes": [{"position_key": "director", "summary": "higher id/newest ordering"}]},
        {"changed_at": "2030-09-12T05:06:00+12:00", "changed_since_viewed": True,
         "inline_changes": [{"position_key": "director", "summary": "lower id"}]},
    ]
    tied = [{"employee_name": "D", "position_label": "Director", "last_changed_at": ""}]
    apply_event_changes_to_schedule_people(tied, equal_time)
    if tied[0]["change_summary"] != "higher id/newest ordering":
        raise AssertionError("Equal-time Changed badge did not preserve deterministic input/id-desc ordering.")

    diagnostic = get_roster_integrity_diagnostics()
    if diagnostic["shared_personal_fill_count"] < len(shared_rows) - 1:
        raise AssertionError(f"Admin shared-personal-fill diagnostic missing: {diagnostic!r}")
    if load_deputy_write_config().get("write_mode") != "off":
        raise AssertionError("Deputy write mode must remain off.")

    print(json.dumps({
        "effective_dates": sorted(effective_dates),
        "upcoming": len(upcoming),
        "reminders": generated,
        "post_reconciliation_changes": changed_notifications["changes"],
        "admin_shared_fills": diagnostic["shared_personal_fill_count"],
        "write_mode": load_deputy_write_config().get("write_mode"),
    }, sort_keys=True))
    print("0.5.24 personal roster integrity smoke passed")


if __name__ == "__main__":
    main()
