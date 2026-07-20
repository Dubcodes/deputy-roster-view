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
    from app.main import reconcile_personal_assignment_evidence

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

    conflict_people = [{
        "position_label": "CCU2", "employee_name": "Other Crew", "employee_id": 88,
        "placeholder": False, "sort_order": 11,
    }]
    reconcile_personal_assignment_evidence(conflict_people, evidence)
    assert conflict_people[0]["employee_name"] == "Other Crew"
    assert "Jayden-lee" in conflict_people[0]["conflict_warning"]

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

    diagnostics = get_roster_integrity_diagnostics()
    assert diagnostics["partial_upcoming"] >= 1
    assert diagnostics["locked_events"] >= 1
    print("roster integrity smoke ok")


if __name__ == "__main__":
    main()
