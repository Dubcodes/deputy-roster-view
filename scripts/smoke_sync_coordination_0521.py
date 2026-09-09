from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-sync-coordination-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "sync.sqlite3"),
        APP_SECRET_KEY="sync-coordination-smoke",
        TZ="Pacific/Auckland",
        DEPUTY_WEB_URL="https://example.test",
        DEPUTY_LOGIN_EMAIL="fixture@example.test",
        DEPUTY_LOGIN_PASSWORD="fixture-password",
    )

    from app.auth import _add_sync_notice
    from app.config import get_settings
    from app.database import (
        create_sync_generation,
        fetch_personal_assignment_evidence_for_date,
        get_connection,
        init_db,
        mark_sync_generation_member,
        save_deputy_web_capture_diagnostic,
        save_deputy_web_schedule,
    )
    from app.deputy_web import _personal_endpoint_forbidden
    import app.scheduler as scheduler

    init_db()
    settings = get_settings()
    now = datetime.now(settings.timezone).replace(microsecond=0)
    future = (now + timedelta(days=14)).date().isoformat()
    with get_connection() as conn:
        for user_id, name in ((1, "Ordinary"), (2, "Proven manager")):
            conn.execute(
                """INSERT INTO app_users
                   (id,deputy_email,display_name,pin_hash,deputy_web_url,is_admin,is_active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,1,?,?)""",
                (
                    user_id,
                    f"user{user_id}@example.test",
                    name,
                    "x",
                    "https://example.test",
                    1 if user_id == 2 else 0,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

    # A forbidden response is terminal for equivalent weekly personal queries.
    attempts = 0
    for status in [403] + ([200] * 13):
        attempts += 1
        if _personal_endpoint_forbidden(status):
            break
    assert attempts == 1
    assert _personal_endpoint_forbidden(401)
    assert not _personal_endpoint_forbidden(500)

    personal_row = {
        "id": 91001,
        "area": 101,
        "areaName": "Director",
        "areaLocationId": 64,
        "location": 64,
        "employee": 17,
        "employeeName": "Personal One",
        "start": f"{future}T09:00:00+12:00",
        "end": f"{future}T17:00:00+12:00",
        "duration": 28800,
        "isPublished": True,
    }
    positive = {
        "captured_at": now.isoformat(),
        "areas": [{"id": 101, "name": "Director", "locationId": 64}],
        "locations": [{"id": 64, "name": "T-Cambridge"}],
        "extracted_shifts": [personal_row],
        "own_roster_coverage": [{
            "start_date": future,
            "end_date": future,
            "status": "complete",
            "records_returned": 1,
            "pagination_complete": True,
            "known_shift_ids_checked": True,
        }],
        "extracted_schedule_shifts": [],
        "schedule_coverage": [],
    }
    save_deputy_web_schedule(positive, owner_user_id=1)
    partial = {
        **positive,
        "captured_at": (now + timedelta(minutes=1)).isoformat(),
        "extracted_shifts": [],
        "own_roster_coverage": [{
            "start_date": future,
            "end_date": future,
            "status": "failed",
            "records_returned": 0,
            "pagination_complete": False,
            "known_shift_ids_checked": False,
            "note": "HTTP 403",
        }],
    }
    save_deputy_web_schedule(partial, owner_user_id=1)
    evidence = fetch_personal_assignment_evidence_for_date(future)
    user_one = [row for row in evidence if int(row["owner_user_id"]) == 1]
    user_two = [row for row in evidence if int(row["owner_user_id"]) == 2]
    assert len(user_one) == 1 and user_one[0]["status"] == "confirmed"
    assert user_two == []

    useful_partial = {
        "status": "ok",
        "shared_capture_reused": True,
        "payload": {
            "capture_scope": "personal_only",
            "own_roster_coverage": [{"status": "failed", "note": "HTTP 403"}],
        },
    }
    assert scheduler._combined_sync_status({}, useful_partial) == "partial"
    assert scheduler._combined_sync_status({}, {"status": "login_failed", "payload": {}}) == "error"
    assert scheduler._combined_sync_status({}, {
        "status": "ok",
        "shared_capture_reused": False,
        "payload": {"capture_scope": "personal_only", "own_roster_coverage": []},
    }) == "error"

    notice_user = {
        "has_deputy_credentials": True,
        "last_sync_status": "partial",
        "last_sync_at": now.isoformat(),
        "sync_in_progress": 0,
    }
    _add_sync_notice(notice_user)
    assert notice_user["sync_notice_text"] == "Roster updated · some personal Deputy checks could not be refreshed"

    proof_payload = {
        "capture_scope": "shared_and_personal",
        "shared_capture_success": True,
        "native_schedule_shift_ids": list(range(100)),
        "management_schedule_coverage": [{"status": "complete", "row_count": 100}],
    }
    save_deputy_web_capture_diagnostic(
        owner_user_id=2,
        captured_at=now.isoformat(),
        status="ok",
        message="fixture",
        payload=json.dumps(proof_payload),
    )
    users = [{"id": 1}, {"id": 2}]
    assert [int(user["id"]) for user in scheduler._ordered_syncable_users(users)] == [2, 1]
    assert scheduler._shared_capture_after(users, (now - timedelta(minutes=1)).isoformat())

    # One generation performs one shared pass, but every member gets its own
    # authenticated personal refresh.
    original = {
        name: getattr(scheduler, name)
        for name in (
            "get_due_user_syncs",
            "active_sync_generation_for_user",
            "claim_sync_generation_member",
            "mark_user_sync_started",
            "sync_roster_sources",
            "mark_user_sync_finished",
            "mark_sync_generation_member",
            "list_syncable_app_users",
            "_shared_capture_after",
        )
    }
    calls: list[tuple[int, bool, bool]] = []
    shared_done = False
    fake_users = [{"id": 2}, {"id": 1}]
    try:
        scheduler.get_due_user_syncs = lambda _now, limit=1: fake_users[:limit]
        scheduler.active_sync_generation_for_user = lambda user_id: {
            "generation_id": 88,
            "created_at": now.isoformat(),
            "status": "pending",
        }
        scheduler.claim_sync_generation_member = lambda *_args: True
        scheduler.mark_user_sync_started = lambda *_args: True
        scheduler.mark_user_sync_finished = lambda *_args, **_kwargs: None
        scheduler.mark_sync_generation_member = lambda *_args, **_kwargs: True
        scheduler.list_syncable_app_users = lambda: fake_users

        def shared_after(_users: list[object], _since: str) -> bool:
            return shared_done

        def fake_sync(_settings: object, user_id: int | None = None, *, include_shared: bool | None = None, shared_reused: bool = False) -> dict[str, object]:
            nonlocal shared_done
            calls.append((int(user_id or 0), bool(include_shared), shared_reused))
            if include_shared:
                shared_done = True
            return {"status": "partial", "calendar": {}, "web": {}}

        scheduler._shared_capture_after = shared_after
        scheduler.sync_roster_sources = fake_sync
        fake_settings = SimpleNamespace(
            timezone=ZoneInfo("Pacific/Auckland"),
            user_sync_batch_size=2,
        )
        result = scheduler.run_due_user_syncs(fake_settings)
        assert result["count"] == 2
        assert calls == [(2, True, False), (1, False, True)]
        assert {user_id for user_id, _shared, _reused in calls} == {1, 2}

        # When the first account cannot prove shared acquisition, the next
        # member safely gets one full fallback rather than assuming coverage.
        calls.clear()
        shared_done = False
        scheduler._shared_capture_after = lambda _users, _since: False
        scheduler.run_due_user_syncs(fake_settings)
        assert calls == [(2, True, False), (1, True, False)]
    finally:
        for name, value in original.items():
            setattr(scheduler, name, value)

    # A manual user refresh reuses fresh shared evidence and stays personal-only.
    original_fresh = scheduler._shared_capture_is_fresh
    original_settings_for_user = scheduler.settings_for_user
    original_web = scheduler.sync_deputy_web_schedule
    original_log = scheduler.write_sync_log
    manual_calls: list[tuple[int | None, bool, bool]] = []
    try:
        scheduler._shared_capture_is_fresh = lambda _settings: True
        scheduler.settings_for_user = lambda _user_id, base: base
        scheduler.write_sync_log = lambda _entry: None

        def fake_web(_settings: object, owner_user_id: int | None = None, *, include_shared: bool = True, include_personal: bool = True) -> dict[str, object]:
            manual_calls.append((owner_user_id, include_shared, include_personal))
            return {
                "status": "ok",
                "message": "fixture",
                "saved_own_shift_rows": 1,
                "saved_schedule_rows": 0,
                "payload": {
                    "capture_scope": "personal_only",
                    "own_roster_coverage": [{"status": "partial"}],
                },
            }

        scheduler.sync_deputy_web_schedule = fake_web
        fake_settings = SimpleNamespace(
            timezone=ZoneInfo("Pacific/Auckland"),
            deputy_ical_url="",
        )
        manual = scheduler.sync_roster_sources(fake_settings, user_id=1)
        assert manual["status"] == "partial"
        assert manual_calls == [(1, False, True)]
    finally:
        scheduler._shared_capture_is_fresh = original_fresh
        scheduler.settings_for_user = original_settings_for_user
        scheduler.sync_deputy_web_schedule = original_web
        scheduler.write_sync_log = original_log

    generation_id = create_sync_generation("partial-fixture", [(1, now.isoformat())], now.isoformat())
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
            "UPDATE sync_generation_members SET status='running' WHERE generation_id=? AND user_id=1",
            (generation_id,),
        )
    assert mark_sync_generation_member(generation_id, 1, "partial", now.isoformat(), "limited")
    with get_connection() as conn:
        member = conn.execute(
            "SELECT status FROM sync_generation_members WHERE generation_id=? AND user_id=1",
            (generation_id,),
        ).fetchone()
        generation = conn.execute("SELECT status FROM sync_generations WHERE id=?", (generation_id,)).fetchone()
    assert member["status"] == "partial" and generation["status"] == "complete"

    print("0.5.21 sync coordination smoke passed")


if __name__ == "__main__":
    main()
