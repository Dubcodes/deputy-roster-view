from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def configure() -> Path:
    sys.path.insert(0, str(ROOT))
    temp_dir = ROOT / ".codex_tmp_override_smoke"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True)
    os.environ["DATA_DIR"] = str(temp_dir)
    os.environ["DB_PATH"] = str(temp_dir / "override.sqlite3")
    os.environ["APP_SECRET_KEY"] = "override-smoke-secret"
    return temp_dir


def seed_legacy_override(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE admin_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                created_by_user_id INTEGER,
                target_date TEXT,
                target_track TEXT,
                override_type TEXT,
                label TEXT,
                value TEXT,
                note TEXT,
                active INTEGER DEFAULT 1
            );
            INSERT INTO admin_overrides (
                created_at, created_by_user_id, target_date, target_track,
                override_type, label, value, note, active
            ) VALUES (
                '2026-07-26T08:00:00+12:00', NULL, '2026-07-25', 'Te Rapa',
                'timing', 'last race', '4:38 pm', 'Existing correction', 1
            );
            """
        )


def seed_shift_and_love_racing() -> None:
    from app.database import (
        get_connection,
        merge_love_racing_programme,
        upsert_love_racing_meeting_detail_identity,
        upsert_travel_route,
    )
    from app.love_racing_details import parse_meeting_programme

    upsert_travel_route(
        origin_label="Office / Clow Place",
        destination_label="Te Rapa",
        travel_minutes=30,
        source="manual",
    )
    upsert_travel_route(
        origin_label="Te Rapa",
        destination_label="Office / Clow Place",
        travel_minutes=30,
        source="manual",
    )
    upsert_love_racing_meeting_detail_identity(
        meeting_id="55032",
        meeting_url="https://loveracing.nz/raceinfo/55032/meeting-overview.aspx",
        meeting_date="2026-07-25",
        canonical_venue_key="terapa",
        canonical_venue_label="Te Rapa",
        club="Waikato Thoroughbred Racing",
        discovered_at="2026-07-20T04:30:00+12:00",
    )
    programme = parse_meeting_programme(
        (ROOT / "scripts" / "fixtures" / "love_racing" / "meeting_55032_complete.html").read_text(
            encoding="utf-8"
        )
    )
    merge_love_racing_programme(
        "55032",
        {
            "lifecycle_status": programme.lifecycle_status,
            "race_count": programme.race_count,
            "first_race_time": programme.first_race_time,
            "last_race_time": programme.last_race_time,
            "races": programme.races,
            "diagnostics": programme.diagnostics,
            "content_hash": programme.content_hash,
        },
        "2026-07-25T08:00:00+12:00",
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO shifts (
                source_uid, title, description, location, start_at, end_at, date,
                raw_hours, break_minutes, paid_hours, first_seen_at, last_synced_at,
                changed_since_viewed, deleted_from_source, source_status,
                source_payload, historical_locked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0, 0, 'published', '{}', ?)
            """,
            (
                "override-te-rapa",
                "[T-Te Rapa] DIR",
                "Office 0815\nOn track 0845\n8 races",
                "Te Rapa",
                "2026-07-25T08:15:00+12:00",
                "2026-07-25T17:00:00+12:00",
                "2026-07-25",
                8.75,
                8.75,
                "2026-07-20T05:00:00+12:00",
                "2026-07-25T05:00:00+12:00",
                "2026-07-26T04:00:00+12:00",
            ),
        )


def load_te_rapa(date_text: str = "2026-07-25") -> dict[str, object]:
    from app.database import fetch_shifts_for_date
    from app.main import decorate_shift, enrich_shifts_with_love_racing

    rows = fetch_shifts_for_date(date_text)
    assert rows
    shifts = [decorate_shift(row) for row in rows]
    enrich_shifts_with_love_racing(shifts, date_text, date_text)
    return shifts[0]


def main() -> None:
    temp_dir = configure()
    try:
        db_path = Path(os.environ["DB_PATH"])
        seed_legacy_override(db_path)

        from app.admin_overrides import (
            canonical_override_venue,
            normalise_override_field,
            normalise_override_value,
            normalise_time,
        )
        from app.database import (
            create_admin_override,
            create_app_user,
            disable_admin_override,
            get_connection,
            get_love_racing_meeting_detail,
            init_db,
            list_active_admin_overrides_between,
            list_admin_overrides,
        )
        init_db()
        migrated = list_admin_overrides()[0]
        assert migrated["status"] == "active"
        assert migrated["target_track_key"] == "terapa"
        assert migrated["field_key"] == "last_race"
        assert migrated["normalized_value"] == "16:38"
        assert migrated["original_value"] == "4:38 pm"

        seed_shift_and_love_racing()
        original_note = "Office 0815\nOn track 0845\n8 races"
        cached_before = dict(get_love_racing_meeting_detail("55032"))
        shift = load_te_rapa()
        assert shift["description"] == original_note
        assert shift["effective_race_timing"]["race_count"] == 8
        assert shift["effective_race_timing"]["last_race_time"] == "16:38"
        assert shift["effective_race_timing"]["sources"]["last_race_time"] == "Admin override"
        assert shift["timing_math"]["race_day"]["last_race_label"] == "16:38"
        assert shift["display_window"]["end_label"] == "18:15"
        assert shift["display_window"]["hours_label"] == "10h"
        assert "comes from an Admin override" in shift["timing_math"]["race_day"]["formula"]
        assert dict(get_love_racing_meeting_detail("55032")) == cached_before
        from app.main import parse_roster_summary, resolve_race_timing_fields

        user_conflict = {
            "race_type_label": "Thoroughbred racing",
            "roster_summary": parse_roster_summary(["8 races"]),
            "timing_adjustment_time": "16:50",
            "timing_adjustment_last_race": 1,
        }
        conflict_effective = resolve_race_timing_fields(
            user_conflict,
            cached_before,
            {
                "last_race": {
                    "normalized_value": "16:38",
                }
            },
        )
        assert conflict_effective["last_race_time"] == "16:38"
        assert conflict_effective["sources"]["last_race_time"] == "Admin override"
        with get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM shift_changes").fetchone()[0] == 0
            assert conn.execute(
                "SELECT changed_since_viewed FROM shifts WHERE source_uid = 'override-te-rapa'"
            ).fetchone()[0] == 0

        admin = create_app_user(
            deputy_email="admin@example.com",
            display_name="Override Admin",
            pin_hash="test-hash",
            deputy_web_url="https://example.deputy.com/",
            encrypted_email="",
            encrypted_password="",
        )
        replacement = create_admin_override(
            created_by_user_id=int(admin["id"]),
            target_date="2026-07-25",
            target_track="Te Rapa",
            override_type="timing",
            label="Last-Race",
            value="16:45",
            note="Later correction",
        )
        assert replacement["normalized_value"] == "16:45"
        audit = [dict(row) for row in list_admin_overrides()]
        assert audit[0]["status"] == "active"
        assert audit[1]["status"] == "superseded"
        assert audit[1]["superseded_by_id"] == replacement["id"]

        replaced_shift = load_te_rapa()
        assert replaced_shift["effective_race_timing"]["last_race_time"] == "16:45"
        assert replaced_shift["effective_race_timing"]["sources"]["last_race_time"] == "Admin override"
        assert replaced_shift["display_window"]["end_label"] == "18:30"

        assert disable_admin_override(
            int(replacement["id"]),
            disabled_by_user_id=int(admin["id"]),
        )
        fallback_shift = load_te_rapa()
        assert fallback_shift["effective_race_timing"]["last_race_time"] == "16:38"
        assert fallback_shift["effective_race_timing"]["sources"]["last_race_time"] == "Love Racing"
        assert not list_active_admin_overrides_between("2026-07-26", "2026-07-26")

        assert [normalise_time(value) for value in ("4:38 pm", "4.38pm", "1638", "16:38")] == [
            "16:38",
            "16:38",
            "16:38",
            "16:38",
        ]
        assert not normalise_time("25:80")
        assert normalise_override_field("timing", "  Last__Race  ")[0] == "last_race"
        assert normalise_override_field("timing", "mystery time")[1]
        for invalid in ("0", "-1", "many"):
            assert normalise_override_value("race_count", invalid)[1]
        assert normalise_override_value("race_count", "8") == ("8", "")
        assert normalise_override_value("return_travel", "30m") == ("30", "")
        assert normalise_override_value("return_travel", "30")[1]
        assert canonical_override_venue("Trials Te Rapa")[0] == "terapa"
        assert canonical_override_venue("Cambridge Trials")[0] == "cambridgesynthetic"
        assert canonical_override_venue("Cambridge Harness")[0] == "cambridgeharness"
        assert canonical_override_venue("Travel") == ("", "")
        audit_count = len(list_admin_overrides())
        try:
            create_admin_override(
                created_by_user_id=int(admin["id"]),
                target_date="2026-07-25",
                target_track="Te Rapa",
                override_type="timing",
                label="mystery time",
                value="16:38",
                note="must not save",
            )
        except ValueError as exc:
            assert "not supported" in str(exc)
        else:
            raise AssertionError("Unsupported override label should be rejected.")
        assert len(list_admin_overrides()) == audit_count

        print("admin override smoke ok")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
