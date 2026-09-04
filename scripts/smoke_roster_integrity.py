from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-integrity-smoke-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "integrity.sqlite3"),
        APP_SECRET_KEY="integrity-smoke",
        TZ="Pacific/Auckland",
    )

    from app.config import get_settings
    from app.deputy_web import _extract_schedule_shifts, _meaningful_management_schedule_shift
    from app.database import (
        fetch_deputy_schedule_for_date,
        fetch_personal_assignment_evidence_for_date,
        get_roster_integrity_diagnostics,
        get_shift_changes_for_date,
        init_db,
        lock_completed_events,
        recover_historical_schedule_from_captures,
        save_deputy_web_schedule,
    )
    from app.main import (
        effective_schedule_items, reconcile_personal_assignment_evidence,
        replacement_change_summary, schedule_people,
    )
    from app.scheduler import _combined_sync_status

    init_db()
    now = datetime.now(get_settings().timezone)
    future = (now + timedelta(days=14)).date().isoformat()
    past = (now - timedelta(days=14)).date().isoformat()
    db_path = get_settings().db_path
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO app_users
               (id, deputy_email, display_name, pin_hash, deputy_web_url, is_admin,
                is_active, created_at, updated_at)
               VALUES (1, 'crew@example.test', 'Jayden-lee', 'x', 'https://example.test', 1, 1, ?, ?)""",
            (now.isoformat(), now.isoformat()),
        )
        conn.execute(
            """INSERT INTO app_users
               (id, deputy_email, display_name, pin_hash, deputy_web_url, is_admin,
                is_active, created_at, updated_at)
               VALUES (2, 'observer@example.test', 'Second observer', 'x', 'https://example.test', 0, 1, ?, ?)""",
            (now.isoformat(), now.isoformat()),
        )
        conn.execute(
            """INSERT INTO crew_people
               (deputy_employee_id, canonical_display_name, current_deputy_name,
                app_user_id, is_active, created_at, updated_at)
               VALUES (17, 'Jayden-lee', 'Jayden-lee', 1, 1, ?, ?)""",
            (now.isoformat(), now.isoformat()),
        )

    areas = [
        {"id": 101, "name": "Side 1", "locationId": 64, "rosterSortOrder": 1},
        {"id": 102, "name": "Side 2", "locationId": 64, "rosterSortOrder": 2},
        {"id": 103, "name": "Head On", "locationId": 64, "rosterSortOrder": 3},
        {"id": 104, "name": "Back", "locationId": 64, "rosterSortOrder": 4},
        {"id": 105, "name": "Turn", "locationId": 64, "rosterSortOrder": 5},
        {"id": 106, "name": "RTS", "locationId": 64, "rosterSortOrder": 6},
        {"id": 107, "name": "Director", "locationId": 64, "rosterSortOrder": 7},
        {"id": 108, "name": "Sound/VT", "locationId": 64, "rosterSortOrder": 8},
        {"id": 109, "name": "ENG", "locationId": 64, "rosterSortOrder": 9},
        {"id": 110, "name": "CCU1", "locationId": 64, "rosterSortOrder": 10},
        {"id": 111, "name": "CCU2", "locationId": 64, "rosterSortOrder": 11},
        {"id": 112, "name": "684", "locationId": 64, "rosterSortOrder": 12},
    ]

    def shared_rows(date_text: str, *, include_ccu2: bool = False, employee: int = 88, name: str = "Other Crew") -> list[dict[str, object]]:
        rows = []
        for index, area in enumerate(areas[:9], start=1):
            rows.append({
                "id": int(date_text.replace("-", "")) * 100 + index,
                "area": area["id"], "areaName": area["name"], "areaLocationId": 64,
                "employee": 200 + index, "employeeName": f"Crew {index}",
                "start": f"{date_text}T09:30:00+12:00", "end": f"{date_text}T17:00:00+12:00",
                "duration": 27000, "isPublished": True,
            })
        if include_ccu2:
            rows.append({
                "id": int(date_text.replace("-", "")) * 100 + 20,
                "area": 111, "areaName": "CCU2", "areaLocationId": 64,
                "employee": employee, "employeeName": name,
                "start": f"{date_text}T09:30:00+12:00", "end": f"{date_text}T17:00:00+12:00",
                "duration": 27000, "isPublished": True,
            })
        return rows

    own_shift = {
        "id": 2200722, "area": 111, "areaName": "CCU2", "areaLocationId": 64,
        "location": 64, "locationName": "T-Cambridge", "employee": 17,
        "start": f"{future}T09:30:00+12:00", "end": f"{future}T17:00:00+12:00",
        "duration": 27000, "isPublished": True,
    }
    coverage = [{"start_date": future, "end_date": future, "mode": "all", "location_ids": []}]
    own_coverage = [{
        "start_date": future, "end_date": future, "status": "complete",
        "records_returned": 1, "pagination_complete": True, "known_shift_ids_checked": True,
    }]
    payload = {
        "captured_at": now.isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Cambridge", "address": ""}],
        "extracted_shifts": [own_shift], "extracted_schedule_shifts": shared_rows(future),
        "schedule_coverage": coverage, "own_roster_coverage": own_coverage,
        "event_retry_coverage": [{"date": future, "location_id": 64, "status": "partial"}],
    }

    # Sanitised production-shaped management Schedule fixture: 40 assigned
    # Ellerslie rows, including the positions the direct source omitted, plus
    # one genuine open Back row.  The direct subset intentionally has 30 rows.
    management_ids = [
        35839, 35897, 33576, 33618, 33620, 33624, 33628, 33632, 34294, 34380,
        *range(50001, 50031),
    ]
    recovered_roles = ["684", "685", "Head On", "IV / BP", "Director", "VT", "Slow Low", "VT", "Start", "RTS"]
    management_raw = []
    employee_metadata = []
    for index, shift_id in enumerate(management_ids):
        role = recovered_roles[index] if index < len(recovered_roles) else f"Crew {index + 1}"
        employee_id = 1000 + index
        management_raw.append({
            "id": shift_id, "employee": employee_id, "area": 101 + (index % 12),
            "areaName": role, "areaLocationId": 64, "location": 64,
            "start": f"{future}T09:00:00+12:00", "end": f"{future}T17:00:00+12:00",
            "duration": 28800, "isPublished": True, "isOpen": False,
            "note": "Sanitised management note" if shift_id == 34380 else "",
        })
        employee_metadata.append({"id": employee_id, "displayName": f"Management Crew {index + 1}"})
    management_raw.extend([
        {"id": 32586, "employee": 0, "area": 104, "areaName": "Back", "areaLocationId": 64,
         "location": 64, "start": f"{future}T09:00:00+12:00", "end": f"{future}T17:00:00+12:00",
         "duration": 28800, "isPublished": False, "isOpen": True, "note": ""},
        {"id": 59999, "employee": 0, "area": 104, "areaName": "Back", "areaLocationId": 64,
         "location": 64, "start": f"{future}T09:00:00+12:00", "end": f"{future}T17:00:00+12:00",
         "duration": 28800, "isPublished": False, "isOpen": False, "note": ""},
    ])
    parsed_management = _extract_schedule_shifts({
        "success": True, "data": {"shifts": management_raw},
        "metadata": {"employee": employee_metadata, "customFields": []},
    })
    meaningful_management = [row for row in parsed_management if _meaningful_management_schedule_shift(row)]
    assert len(meaningful_management) == 41
    assert next(row for row in meaningful_management if row["id"] == 34380)["employeeName"] == "Management Crew 10"
    assert next(row for row in meaningful_management if row["id"] == 34380)["note"] == "Sanitised management note"
    assert not _meaningful_management_schedule_shift(next(row for row in parsed_management if row["id"] == 59999))
    direct_subset = [row for row in meaningful_management if int(row["id"]) not in {32586, *management_ids[:10]}]
    management_payload = {
        **payload, "captured_at": (now + timedelta(seconds=1)).isoformat(),
        "extracted_schedule_shifts": meaningful_management,
        "native_schedule_shift_ids": [row["id"] for row in meaningful_management],
        "direct_schedule_shift_ids": [row["id"] for row in direct_subset],
        "schedule_coverage": coverage,
        "management_schedule_coverage": [{"start_date": future, "end_date": future, "status": "complete", "row_count": 41}],
    }
    save_deputy_web_schedule(management_payload, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM deputy_schedule_shifts WHERE source_shift_id IN ({})".format(",".join("?" * 41)), tuple([row["id"] for row in meaningful_management])).fetchone()[0] == 41
        open_row = conn.execute("SELECT employee_id,is_open,is_published,employee_name FROM deputy_schedule_shifts WHERE source_shift_id=32586").fetchone()
        assert open_row == (0, 1, 0, ""), open_row
        assert conn.execute("SELECT COUNT(*) FROM deputy_schedule_shifts WHERE source_shift_id=34380").fetchone()[0] == 1
    # A later complete direct omission cannot retire the independent positive
    # management/getRosters observation, while existing direct pruning remains unchanged below.
    save_deputy_web_schedule({**management_payload, "captured_at": (now + timedelta(seconds=2)).isoformat(), "extracted_schedule_shifts": meaningful_management, "direct_schedule_shift_ids": []}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT active FROM deputy_schedule_observations WHERE source_shift_id=34380 AND observer_key='user:1:native_get_rosters'").fetchone() == (1,)
        assert conn.execute("SELECT 1 FROM deputy_schedule_shifts WHERE source_shift_id=34380").fetchone() is not None
    health_payload = {"own_roster_coverage": [{"status": "complete"}], "direct_schedule_coverage": [{"status": "complete"}], "travel_schedule_coverage": [{"status": "complete"}]}
    assert _combined_sync_status({}, {"status": "ok", "payload": {**health_payload, "management_schedule_coverage": [{"status": "complete", "row_count": 41}]}}) == "ok"
    assert _combined_sync_status({}, {"status": "ok", "payload": {**health_payload, "management_schedule_coverage": [{"status": "partial", "row_count": 0}]}}) == "partial"

    first = save_deputy_web_schedule(payload, owner_user_id=1)
    second = save_deputy_web_schedule({**payload, "captured_at": (now + timedelta(minutes=1)).isoformat()}, owner_user_id=1)
    evidence = fetch_personal_assignment_evidence_for_date(future, [64])
    assert len(evidence) == 1, evidence
    assert first["partial_events"] == 1 and second["personal_evidence_saved"] == 1
    people = [
        {"position_label": "CCU1", "employee_name": "TBC", "placeholder": True, "sort_order": 10},
        {"position_label": "CCU2", "employee_name": "TBC", "placeholder": True, "sort_order": 11},
    ]
    reconcile_personal_assignment_evidence(people, evidence)
    ccu1 = next(item for item in people if item["position_label"] == "CCU1")
    ccu2 = next(item for item in people if item["position_label"] == "CCU2")
    assert ccu1["employee_name"] == "TBC"
    assert ccu2["employee_name"] == "Jayden-lee" and ccu2["personal_evidence"]
    assert ccu2["provenance_label"] == "Confirmed from personal roster"

    # A role merely existing in the venue Area catalogue is not evidence that
    # this particular event should contain it.
    catalogue_date = (now + timedelta(days=12)).date().isoformat()
    catalogue_payload = {
        **payload,
        "captured_at": (now + timedelta(seconds=30)).isoformat(),
        "extracted_shifts": [],
        "own_roster_coverage": [],
        "extracted_schedule_shifts": shared_rows(catalogue_date),
        "schedule_coverage": [{"start_date": catalogue_date, "end_date": catalogue_date, "mode": "all", "location_ids": []}],
        "event_retry_coverage": [],
    }
    catalogue_result = save_deputy_web_schedule(catalogue_payload, owner_user_id=1)
    assert catalogue_result["partial_events"] == 0, catalogue_result
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status, placeholder_positions FROM deputy_event_coverage WHERE date=? AND area_location_id=64",
            (catalogue_date,),
        ).fetchone() == ("complete", 0)

    # Shared rows retain per-account provenance. Account 1 not seeing a row
    # must not erase the copy still actively observed by account 2. Deputy can
    # also set isOpen=true on an assigned shift; that row is not a vacancy.
    observer_date = (now + timedelta(days=18)).date().isoformat()
    observer_area = {"id": 900, "name": "Office Day", "locationId": 900, "rosterSortOrder": 1}
    assigned_open = {
        "id": 33635, "area": 900, "areaName": "Office Day", "areaLocationId": 900,
        "employee": 685, "employeeName": "Alf", "isOpen": True, "isPublished": True,
        "start": f"{observer_date}T07:30:00+12:00", "end": f"{observer_date}T16:00:00+12:00",
    }
    observer_payload = {
        "captured_at": now.isoformat(), "areas": [observer_area],
        "locations": [{"id": 900, "name": "Taupo"}],
        "extracted_shifts": [], "own_roster_coverage": [],
        "extracted_schedule_shifts": [assigned_open],
        "schedule_coverage": [{"start_date": observer_date, "end_date": observer_date, "mode": "selected", "location_ids": [900]}],
    }
    save_deputy_web_schedule(observer_payload, owner_user_id=1)
    save_deputy_web_schedule({**observer_payload, "captured_at": (now + timedelta(seconds=1)).isoformat()}, owner_user_id=2)
    with sqlite3.connect(db_path) as conn:
        events_before_personalized_absence = conn.execute(
            "SELECT COUNT(*) FROM deputy_schedule_event_changes WHERE date=?", (observer_date,)
        ).fetchone()[0]
    account_one_absence = {**observer_payload, "captured_at": (now + timedelta(seconds=2)).isoformat(), "extracted_schedule_shifts": []}
    save_deputy_web_schedule(account_one_absence, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT employee_name FROM deputy_schedule_shifts WHERE source_shift_id=33635").fetchone() == ("Alf",)
        assert conn.execute("SELECT active FROM deputy_schedule_observations WHERE source_shift_id=33635 AND observer_key='user:1:direct_schedule'").fetchone() == (0,)
        assert conn.execute("SELECT active FROM deputy_schedule_observations WHERE source_shift_id=33635 AND observer_key='user:2:direct_schedule'").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM deputy_schedule_event_changes WHERE date=?", (observer_date,)).fetchone()[0] == events_before_personalized_absence
    from app.database import fetch_open_deputy_schedule_between
    assert all(int(row["source_shift_id"]) != 33635 for row in fetch_open_deputy_schedule_between(observer_date, observer_date))
    alf_refresh_at = (now + timedelta(seconds=3)).isoformat()
    save_deputy_web_schedule({**observer_payload, "captured_at": alf_refresh_at}, owner_user_id=2)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT last_seen_at FROM deputy_schedule_observations WHERE source_shift_id=33635 AND observer_key='user:2:direct_schedule'"
        ).fetchone() == (alf_refresh_at,)
    save_deputy_web_schedule({**account_one_absence, "captured_at": (now + timedelta(seconds=4)).isoformat()}, owner_user_id=2)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT 1 FROM deputy_schedule_shifts WHERE source_shift_id=33635").fetchone() is None

    # A complete direct search may retire only direct evidence: it cannot negate
    # an earlier native Schedule-grid observation from the same account.
    source_provenance_date = (now + timedelta(days=20)).date().isoformat()
    provenance_base = {**assigned_open, "start": f"{source_provenance_date}T07:30:00+12:00", "end": f"{source_provenance_date}T16:00:00+12:00"}
    native_row = {**provenance_base, "id": 9001, "isOpen": False}
    direct_row = {**provenance_base, "id": 9002, "area": 901, "areaName": "Different Direct Role", "areaLocationId": 902, "location": 902, "locationName": "Different Direct Location"}
    native_coverage = [{"start_date": source_provenance_date, "end_date": source_provenance_date, "mode": "selected", "location_ids": [900]}]
    direct_coverage = [{"start_date": source_provenance_date, "end_date": source_provenance_date, "mode": "selected", "location_ids": [902]}]
    native_payload = {**observer_payload, "captured_at": (now + timedelta(seconds=5)).isoformat(), "extracted_schedule_shifts": [native_row], "native_schedule_shift_ids": [9001], "direct_schedule_shift_ids": [], "schedule_coverage": native_coverage}
    save_deputy_web_schedule(native_payload, owner_user_id=1)
    save_deputy_web_schedule({**native_payload, "captured_at": (now + timedelta(seconds=6)).isoformat(), "extracted_schedule_shifts": [], "native_schedule_shift_ids": []}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT active FROM deputy_schedule_observations WHERE source_shift_id=9001 AND observer_key='user:1:native_get_rosters'").fetchone() == (1,)
        assert conn.execute("SELECT 1 FROM deputy_schedule_shifts WHERE source_shift_id=9001").fetchone() is not None
        conn.execute("INSERT INTO deputy_schedule_observations VALUES (9001,'user:1',1,'2026-01-01T00:00:00+12:00','2026-01-02T00:00:00+12:00',0,'2026-01-02T00:00:00+12:00')")
        conn.execute("INSERT INTO deputy_schedule_observations VALUES (9001,'user:1:direct_schedule',1,'2026-01-03T00:00:00+12:00','2026-01-04T00:00:00+12:00',1,NULL)")
        conn.commit()
    save_deputy_web_schedule({"captured_at": "2026-01-05T00:00:00+12:00"}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        direct_collision = conn.execute("SELECT observer_key,first_seen_at,last_seen_at,active,last_absent_at FROM deputy_schedule_observations WHERE source_shift_id=9001 AND observer_key='user:1:direct_schedule'").fetchone()
        assert direct_collision == ('user:1:direct_schedule','2026-01-01T00:00:00+12:00','2026-01-04T00:00:00+12:00',1,None), direct_collision
        assert conn.execute("SELECT 1 FROM deputy_schedule_observations WHERE source_shift_id=9001 AND observer_key='user:1'").fetchone() is None
        before_repeat = direct_collision
    save_deputy_web_schedule({"captured_at": "2026-01-06T00:00:00+12:00"}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT observer_key,first_seen_at,last_seen_at,active,last_absent_at FROM deputy_schedule_observations WHERE source_shift_id=9001 AND observer_key='user:1:direct_schedule'").fetchone() == before_repeat
        # Newer inactive legacy evidence wins over an older active direct row.
        conn.execute("INSERT INTO deputy_schedule_observations VALUES (9001,'user:1',1,'2026-02-01T00:00:00+12:00','2026-02-02T00:00:00+12:00',0,'2026-02-05T00:00:00+12:00')")
        conn.execute("UPDATE deputy_schedule_observations SET first_seen_at='2026-02-03T00:00:00+12:00',last_seen_at='2026-02-04T00:00:00+12:00',active=1,last_absent_at=NULL WHERE source_shift_id=9001 AND observer_key='user:1:direct_schedule'")
        conn.commit()
    save_deputy_web_schedule({"captured_at": "2026-02-05T00:00:00+12:00"}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        inactive_collision = conn.execute("SELECT first_seen_at,last_seen_at,active,last_absent_at FROM deputy_schedule_observations WHERE source_shift_id=9001 AND observer_key='user:1:direct_schedule'").fetchone()
        assert inactive_collision == ('2026-02-01T00:00:00+12:00','2026-02-04T00:00:00+12:00',0,'2026-02-05T00:00:00+12:00'), inactive_collision
        # A same-time positive sighting wins an absence tie.
        conn.execute("INSERT INTO deputy_schedule_observations VALUES (9001,'user:1',1,'2026-03-01T00:00:00+12:00','2026-03-04T00:00:00+12:00',0,'2026-03-04T00:00:00+12:00')")
        conn.execute("UPDATE deputy_schedule_observations SET first_seen_at='2026-03-02T00:00:00+12:00',last_seen_at='2026-03-04T00:00:00+12:00',active=1,last_absent_at=NULL WHERE source_shift_id=9001 AND observer_key='user:1:direct_schedule'")
        conn.commit()
    save_deputy_web_schedule({"captured_at": "2026-03-05T00:00:00+12:00"}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT first_seen_at,last_seen_at,active,last_absent_at FROM deputy_schedule_observations WHERE source_shift_id=9001 AND observer_key='user:1:direct_schedule'").fetchone() == ('2026-03-01T00:00:00+12:00','2026-03-04T00:00:00+12:00',1,None)
    direct_payload = {**observer_payload, "captured_at": (now + timedelta(seconds=7)).isoformat(), "extracted_schedule_shifts": [direct_row], "native_schedule_shift_ids": [], "direct_schedule_shift_ids": [9002], "schedule_coverage": direct_coverage}
    save_deputy_web_schedule(direct_payload, owner_user_id=1)
    save_deputy_web_schedule({**direct_payload, "captured_at": (now + timedelta(seconds=8)).isoformat(), "extracted_schedule_shifts": [], "direct_schedule_shift_ids": []}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        direct_observation = conn.execute("SELECT active FROM deputy_schedule_observations WHERE source_shift_id=9002 AND observer_key='user:1:direct_schedule'").fetchone()
        assert direct_observation is None, direct_observation
        assert conn.execute("SELECT 1 FROM deputy_schedule_shifts WHERE source_shift_id=9002").fetchone() is None

    # Upgrade legacy generic observations through the real save/pruning path.
    legacy_row = {**direct_row, "id": 9101}
    legacy_payload = {**direct_payload, "captured_at": (now + timedelta(seconds=9)).isoformat(), "extracted_schedule_shifts": [legacy_row], "direct_schedule_shift_ids": [9101]}
    save_deputy_web_schedule(legacy_payload, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE deputy_schedule_observations SET observer_key='user:1' WHERE source_shift_id=9101 AND observer_key='user:1:direct_schedule'")
        conn.commit()
    save_deputy_web_schedule({**legacy_payload, "captured_at": (now + timedelta(seconds=10)).isoformat(), "extracted_schedule_shifts": [], "direct_schedule_shift_ids": []}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT 1 FROM deputy_schedule_observations WHERE source_shift_id=9101 AND observer_key='user:1'").fetchone() is None
        assert conn.execute("SELECT 1 FROM deputy_schedule_shifts WHERE source_shift_id=9101").fetchone() is None
    # A native observation is independent of direct migration and absence.
    assert conn.execute("SELECT active FROM deputy_schedule_observations WHERE source_shift_id=9001 AND observer_key='user:1:native_get_rosters'").fetchone() == (1,)

    conflict_people = [{
        "position_label": "CCU2", "employee_name": "Other Crew", "employee_id": 88,
        "placeholder": False, "sort_order": 11,
    }]
    reconcile_personal_assignment_evidence(conflict_people, evidence)
    assert conflict_people[0]["employee_name"] == "Other Crew"
    assert "Jayden-lee" in conflict_people[0]["conflict_warning"]

    # 01 September Travel cohort: shared Matt plus authenticated personal Jayden.
    travel_people = [{
        "position_label": "Travel then Overnighter", "employee_name": "Matt Blackmore",
        "employee_id": 501, "placeholder": False, "sort_order": 1,
    }]
    travel_evidence = [{
        "position_label": "Travel then Overnighter", "employee_name": "Jayden-lee",
        "deputy_employee_id": 685, "canonical_person_id": 2,
        "start_at": "2026-09-01T08:00:00+12:00", "end_at": "2026-09-01T13:00:00+12:00",
        "status": "confirmed",
    }]
    reconcile_personal_assignment_evidence(
        travel_people, travel_evidence,
        event_start_at="2026-09-01T08:00:00+12:00",
        event_end_at="2026-09-01T13:00:00+12:00",
        travel_participant_union=True,
    )
    assert {row["employee_name"] for row in travel_people} == {"Matt Blackmore", "Jayden-lee"}
    unrelated = [{**travel_evidence[0], "employee_name": "Unrelated", "deputy_employee_id": 999,
                  "start_at": "2026-09-01T18:00:00+12:00", "end_at": "2026-09-01T19:00:00+12:00"}]
    reconcile_personal_assignment_evidence(
        travel_people, unrelated,
        event_start_at="2026-09-01T08:00:00+12:00",
        event_end_at="2026-09-01T13:00:00+12:00",
        travel_participant_union=True,
    )
    assert "Unrelated" not in {row["employee_name"] for row in travel_people}

    # One complete absence warns; partial coverage does not advance; the second complete absence retires.
    missing_payload = {
        **payload, "captured_at": (now + timedelta(minutes=2)).isoformat(),
        "extracted_shifts": [],
        "own_roster_coverage": [{**own_coverage[0], "records_returned": 0}],
    }
    save_deputy_web_schedule(missing_payload, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        status, count = conn.execute(
            "SELECT capture_status, missing_capture_count FROM shifts WHERE source_uid LIKE '%:2200722'"
        ).fetchone()
    assert (status, count) == ("possibly_missing", 1)
    partial_missing = {
        **missing_payload, "captured_at": (now + timedelta(minutes=3)).isoformat(),
        "own_roster_coverage": [{**own_coverage[0], "status": "partial", "pagination_complete": False}],
    }
    save_deputy_web_schedule(partial_missing, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT missing_capture_count FROM shifts WHERE source_uid LIKE '%:2200722'").fetchone()[0] == 1
    save_deputy_web_schedule({**missing_payload, "captured_at": (now + timedelta(minutes=4)).isoformat()}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT deleted_from_source FROM shifts WHERE source_uid LIKE '%:2200722'").fetchone()[0] == 1
    save_deputy_web_schedule({**payload, "captured_at": (now + timedelta(minutes=5)).isoformat()}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT deleted_from_source, capture_status, missing_capture_count FROM shifts WHERE source_uid LIKE '%:2200722'"
        ).fetchone() == (0, "confirmed", 0)

    cancelled = {**own_shift, "isCancelled": True}
    save_deputy_web_schedule({
        **payload, "captured_at": (now + timedelta(minutes=6)).isoformat(),
        "extracted_shifts": [cancelled],
    }, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT deleted_from_source, capture_status FROM shifts WHERE source_uid LIKE '%:2200722'"
        ).fetchone() == (1, "cancelled")

    # Completed events lock, cannot be pruned or overwritten, but blank notes may be filled.
    past_row = shared_rows(past, include_ccu2=True, employee=17, name="Jayden-lee")[-1]
    past_row["note"] = ""
    past_payload = {
        "captured_at": (now - timedelta(days=13)).isoformat(), "areas": areas,
        "locations": [{"id": 64, "name": "T-Cambridge"}],
        "extracted_shifts": [], "own_roster_coverage": [],
        "extracted_schedule_shifts": [past_row],
        "schedule_coverage": [{"start_date": past, "end_date": past, "mode": "all", "location_ids": []}],
    }
    save_deputy_web_schedule(past_payload, owner_user_id=1)
    lock_completed_events()
    omitted = {**past_payload, "captured_at": now.isoformat(), "extracted_schedule_shifts": []}
    save_deputy_web_schedule(omitted, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM deputy_schedule_shifts WHERE date = ?", (past,)).fetchone()[0] == 1
        conn.execute("UPDATE deputy_schedule_shifts SET changed_since_viewed = 1 WHERE date = ?", (past,))
    lock_completed_events()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT changed_since_viewed FROM deputy_schedule_shifts WHERE date = ?", (past,)).fetchone()[0] == 0
    conflicting = {**past_row, "employee": 99, "employeeName": "Replacement", "note": "Late useful note"}
    save_deputy_web_schedule({**past_payload, "captured_at": now.isoformat(), "extracted_schedule_shifts": [conflicting]}, owner_user_id=1)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT employee_name, note FROM deputy_schedule_shifts WHERE date = ?", (past,)).fetchone()
        assert row == ("Jayden-lee", "Late useful note"), row
        assert conn.execute("SELECT COUNT(*) FROM deputy_historical_discrepancies").fetchone()[0] >= 1

    # A pruned past row can be rebuilt once from the retained, successful archive.
    archive_date = (now - timedelta(days=21)).date().isoformat()
    archived_row = {**past_row, "id": 880001, "start": f"{archive_date}T09:30:00+12:00", "end": f"{archive_date}T17:00:00+12:00"}
    archive_payload = {**past_payload, "captured_at": (now - timedelta(days=20)).isoformat(), "extracted_schedule_shifts": [archived_row]}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO deputy_web_captures (owner_user_id, captured_at, status, message, payload, created_at) VALUES (1, ?, 'ok', 'fixture', ?, ?)",
            (archive_payload["captured_at"], json.dumps(archive_payload), now.isoformat()),
        )
    recovery = recover_historical_schedule_from_captures(force=True)
    assert recovery["rows_restored"] >= 1, recovery
    recover_historical_schedule_from_captures(force=True)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM deputy_schedule_shifts WHERE source_shift_id = 880001").fetchone()[0] == 1

    # Enrichment and normalization remain technical; operational changes remain visible.
    clean_date = (now + timedelta(days=28)).date().isoformat()
    base = {**own_shift, "id": 3300001, "start": f"{clean_date}T09:30:00+12:00", "end": f"{clean_date}T17:00:00+12:00", "locationName": "WEB", "note": ""}
    clean_payload = {**payload, "captured_at": now.isoformat(), "extracted_shifts": [base], "own_roster_coverage": []}
    save_deputy_web_schedule(clean_payload, owner_user_id=1)
    enriched = {**base, "locationName": "T-Cambridge", "note": "Initial complete note"}
    save_deputy_web_schedule({**clean_payload, "captured_at": (now + timedelta(minutes=7)).isoformat(), "extracted_shifts": [enriched]}, owner_user_id=1)
    assert get_shift_changes_for_date(clean_date) == []
    changed = {**enriched, "area": 107, "areaName": "Director", "start": f"{clean_date}T09:15:00+12:00", "end": f"{clean_date}T17:30:00+12:00", "note": "Materially different instruction"}
    save_deputy_web_schedule({**clean_payload, "captured_at": (now + timedelta(minutes=8)).isoformat(), "extracted_shifts": [changed]}, owner_user_id=1)
    visible_fields = {row["field_name"] for row in get_shift_changes_for_date(clean_date)}
    assert {"role", "start_at", "end_at", "description"}.issubset(visible_fields), visible_fields

    # Same-role occupants are retained only when a native Schedule capture saw
    # each distinct Deputy row together.  Presentation numbering is source-ID
    # ordered and never changes the canonical role used by history/dedupe.
    vt_date = (now + timedelta(days=35)).date().isoformat()
    vt_capture_at = (now + timedelta(minutes=20)).isoformat()
    vt_area = {"id": 776, "name": "VT", "locationId": 64, "rosterSortOrder": 9}
    vt_rows = [
        {"id": 33624, "area": 776, "areaName": "VT", "areaLocationId": 64, "employee": 59,
         "employeeName": "Darryl Cribb", "start": f"{vt_date}T09:00:00+12:00", "end": f"{vt_date}T19:30:00+12:00", "duration": 37800, "isPublished": True},
        {"id": 33632, "area": 776, "areaName": "VT", "areaLocationId": 64, "employee": 77,
         "employeeName": "James Topping", "start": f"{vt_date}T09:00:00+12:00", "end": f"{vt_date}T19:30:00+12:00", "duration": 37800, "isPublished": True},
    ]
    save_deputy_web_schedule({
        "captured_at": vt_capture_at, "areas": [*areas, vt_area],
        "locations": [{"id": 64, "name": "T-Cambridge"}], "extracted_shifts": [],
        "extracted_schedule_shifts": vt_rows, "native_schedule_shift_ids": [33624, 33632],
        "direct_schedule_shift_ids": [], "schedule_coverage": [], "own_roster_coverage": [],
    }, owner_user_id=1)
    native_vt_rows = [row for row in fetch_deputy_schedule_for_date(vt_date) if int(row["source_shift_id"]) in {33624, 33632}]
    vt_people = schedule_people(native_vt_rows, include_placeholders=False)
    if [(row["position_label"], row["employee_name"]) for row in vt_people] != [
        ("VT 1", "Darryl Cribb"), ("VT 2", "James Topping"),
    ]:
        raise AssertionError(f"Native co-observed VT pair was not retained and source-ordered: {vt_people!r}")
    vt_items, _contexts = effective_schedule_items(native_vt_rows)
    if any(item["area_display"] != "VT" or item.get("display_area_label") not in {"VT 1", "VT 2"} for item in vt_items):
        raise AssertionError(f"Display suffix leaked into canonical VT identity: {vt_items!r}")
    if replacement_change_summary(
        {"area_display": vt_items[0]["area_display"], "employee_name": "Darryl Cribb"},
        {"area_display": vt_items[1]["area_display"], "employee_name": "James Topping"},
    ) != "VT: Darryl Cribb → James Topping":
        raise AssertionError("Concurrent display suffix contaminated semantic change-history labels.")

    def concurrent_row(source_id: int, employee_id: int, *, context: str = "user:1:native_get_rosters", captured_at: str = "2026-09-01T10:00:00+12:00") -> dict[str, object]:
        return {
            "source_shift_id": source_id, "employee_id": employee_id, "employee_name": f"Crew {employee_id}",
            "area_id": 776, "area_name": "VT", "schedule_location_id": 64, "date": "2026-09-01",
            "start_at": "2026-09-01T09:00:00+12:00", "end_at": "2026-09-01T19:30:00+12:00",
            "captured_at": captured_at, "native_observation_contexts": f"{context}\x1f{captured_at}",
        }

    one_vt, _contexts = effective_schedule_items([concurrent_row(40001, 501)])
    if len(one_vt) != 1 or one_vt[0].get("display_area_label") or one_vt[0]["area_display"] != "VT":
        raise AssertionError(f"A singleton VT was numbered: {one_vt!r}")
    three_vt, _contexts = effective_schedule_items([concurrent_row(40001, 501), concurrent_row(40002, 502), concurrent_row(40003, 503)])
    if [row.get("display_area_label") for row in three_vt] != ["VT 1", "VT 2", "VT 3"]:
        raise AssertionError(f"Three native co-observed VTs were not numbered deterministically: {three_vt!r}")
    replacement_rows, _contexts = effective_schedule_items([
        concurrent_row(41001, 601, context="", captured_at="2026-09-01T09:00:00+12:00"),
        concurrent_row(41002, 602, context="", captured_at="2026-09-01T10:00:00+12:00"),
    ])
    if [row["employee_id"] for row in replacement_rows] != [602]:
        raise AssertionError(f"Ordinary VT replacement was incorrectly treated as concurrent: {replacement_rows!r}")
    disagreement_rows, _contexts = effective_schedule_items([
        concurrent_row(42001, 701, context="user:1:native_get_rosters"),
        concurrent_row(42002, 702, context="user:2:native_get_rosters"),
    ])
    if [row["employee_id"] for row in disagreement_rows] != [702]:
        raise AssertionError(f"Different native capture contexts fabricated concurrency: {disagreement_rows!r}")
    four_vt, _contexts = effective_schedule_items([concurrent_row(43000 + index, 800 + index) for index in range(1, 5)])
    if len(four_vt) != 4 or any(row.get("display_area_label") for row in four_vt) or not all(row.get("concurrent_assignment_warning") for row in four_vt):
        raise AssertionError(f"Over-cap concurrent VT evidence was dropped, numbered, or not flagged: {four_vt!r}")

    diagnostics = get_roster_integrity_diagnostics()
    assert diagnostics["partial_upcoming"] >= 1
    assert diagnostics["locked_events"] >= 1
    print("roster integrity smoke ok")


if __name__ == "__main__":
    main()
