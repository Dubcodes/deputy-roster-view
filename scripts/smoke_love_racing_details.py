from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "scripts" / "fixtures" / "love_racing"
NZ = ZoneInfo("Pacific/Auckland")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def assert_programme_parser() -> None:
    from app.love_racing_details import (
        parse_calendar_meeting_identities,
        parse_meeting_page_metadata,
        parse_meeting_programme,
    )

    te_aroha = parse_meeting_programme(fixture("meeting_55034_complete.html"))
    te_aroha_metadata = parse_meeting_page_metadata(
        fixture("meeting_55034_complete.html")
    )
    assert te_aroha.lifecycle_status == "complete"
    assert (te_aroha.race_count, te_aroha.first_race_time, te_aroha.last_race_time) == (
        9,
        "11:34",
        "16:35",
    )
    assert len(te_aroha.races) == 9
    assert (
        te_aroha_metadata.meeting_date,
        te_aroha_metadata.raw_course_label,
    ) == ("2026-07-26", "Te Aroha")

    te_rapa = parse_meeting_programme(fixture("meeting_55032_complete.html"))
    te_rapa_metadata = parse_meeting_page_metadata(
        fixture("meeting_55032_complete.html")
    )
    assert (te_rapa.race_count, te_rapa.first_race_time, te_rapa.last_race_time) == (
        8,
        "12:25",
        "16:38",
    )
    assert (
        te_rapa_metadata.meeting_date,
        te_rapa_metadata.raw_course_label,
    ) == ("2026-07-25", "Te Rapa")

    awaiting = parse_meeting_programme(fixture("awaiting_schedule.html"))
    assert awaiting.lifecycle_status == "awaiting_schedule"
    assert awaiting.race_count == 2
    assert not awaiting.first_race_time and not awaiting.last_race_time

    partial = parse_meeting_programme(fixture("partial_programme.html"))
    assert partial.lifecycle_status == "partial"
    assert (partial.race_count, partial.first_race_time, partial.last_race_time) == (
        3,
        "12:25",
        "",
    )

    race_zero = parse_meeting_programme(fixture("race_zero_not_found.html"))
    assert race_zero.lifecycle_status == "awaiting_schedule"
    assert race_zero.race_count is None and race_zero.races == []
    assert race_zero.rejected_rows == ("Rejected Race 0.",)

    duplicate = parse_meeting_programme(fixture("duplicate_rows.html"))
    assert duplicate.lifecycle_status == "complete"
    assert duplicate.race_count == 2 and len(duplicate.races) == 2
    assert duplicate.duplicate_rows

    conflict = parse_meeting_programme(fixture("conflicting_duplicate.html"))
    assert conflict.lifecycle_status == "partial"
    assert not conflict.first_race_time
    assert any("conflicting" in item for item in conflict.diagnostics)

    results = parse_meeting_programme(fixture("results_layout_blank.html"))
    assert results.lifecycle_status == "awaiting_schedule"
    assert not results.first_race_time and not results.last_race_time

    identities = parse_calendar_meeting_identities(
        fixture("calendar_july_2026.html"),
        2026,
    )
    assert [
        (item["meeting_id"], item["DateISO"], item["Racecourse"])
        for item in identities
    ] == [
        ("55032", "2026-07-25", "Te Rapa"),
        ("55034", "2026-07-26", "Te Aroha"),
    ]


def assert_admin_preview_and_backfill() -> None:
    from app import planning_calendar
    from app.database import (
        get_love_racing_meeting_detail,
        merge_love_racing_programme,
        upsert_travel_route,
    )
    from app.love_racing_details import parse_meeting_programme
    from app.main import (
        build_race_day_calculation,
        format_race_time_backfill,
        parse_roster_summary,
        resolve_race_timing_fields,
    )

    original_capture_pages = planning_calendar.capture_meeting_pages
    original_capture_calendar = planning_calendar.capture_calendar_months
    try:
        planning_calendar.capture_meeting_pages = lambda meetings: [
            {
                "meeting_id": str(meetings[0]["meeting_id"]),
                "html": fixture("meeting_55034_complete.html"),
                "error": "",
            }
        ]
        preview = planning_calendar.preview_love_racing_meeting(
            "https://loveracing.nz/raceinfo/55034/meeting-overview.aspx",
            expected_date="2026-07-26",
            expected_venue="Te Aroha",
        )
        assert preview["ok"] is True
        assert (
            preview["meeting_id"],
            preview["meeting_date"],
            preview["canonical_venue_label"],
            preview["race_count"],
            preview["first_race_time"],
            preview["last_race_time"],
        ) == ("55034", "2026-07-26", "Te Aroha", 9, "11:34", "16:35")
        with sqlite3.connect(os.environ["DB_PATH"]) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM love_racing_meeting_details"
            ).fetchone()[0] == 1

        planning_calendar.capture_meeting_pages = lambda meetings: [
            {
                "meeting_id": str(meetings[0]["meeting_id"]),
                "html": fixture("meeting_mismatch.html"),
                "error": "",
            }
        ]
        mismatch = planning_calendar.preview_love_racing_meeting(
            "55034",
            expected_date="2026-07-26",
            expected_venue="Te Aroha",
        )
        assert mismatch["ok"] is False
        assert mismatch["date_matches"] is False
        assert mismatch["venue_matches"] is False

        planning_calendar.capture_calendar_months = lambda months: [
            {
                "meeting_id": "55032",
                "meeting_url": "https://loveracing.nz/raceinfo/55032/meeting-overview.aspx",
                "DateISO": "2026-07-25",
                "Racecourse": "Te Rapa",
                "Club": "Waikato Thoroughbred Racing",
            }
        ]
        planning_calendar.capture_meeting_pages = lambda meetings: [
            {
                "meeting_id": "55032",
                "html": fixture("meeting_55032_complete.html"),
                "error": "",
            }
        ]
        rows = planning_calendar.refresh_unresolved_race_days(
            [
                {
                    "date": "2026-07-25",
                    "venue_key": "terapa",
                    "venue_label": "Te Rapa",
                    "deputy_race_count": 8,
                    "deputy_first_race": "",
                    "deputy_last_race": "",
                    "user_last_race": "",
                }
            ],
            checked_at=datetime(2026, 7, 26, 9, 0, tzinfo=NZ),
        )
        assert len(rows) == 1
        assert (
            rows[0]["meeting_id"],
            rows[0]["love_race_count"],
            rows[0]["love_first_race"],
            rows[0]["love_last_race"],
        ) == ("55032", 8, "12:25", "16:38")
        report = format_race_time_backfill(rows, checked=1, already_complete=0)
        assert report["enriched"] == 1
        assert rows[0]["fields_filled"] == "First race, Last race"
        assert rows[0]["fields_preserved"] == "Deputy race count"

        cached_te_rapa = dict(get_love_racing_meeting_detail("55032"))
        te_rapa_note = ["Office 0900", "On track 0930", "8 races"]
        te_rapa_shift = {
            "race_type_label": "Thoroughbred racing",
            "track_label": "Te Rapa",
            "description_lines": te_rapa_note,
            "roster_summary": parse_roster_summary(te_rapa_note),
            "timing_adjustment_time": "",
            "timing_adjustment_last_race": 0,
            "start_at": "2026-07-25T09:00:00+12:00",
            "end_at": "2026-07-25T17:45:00+12:00",
            "raw_hours": 8.5,
            "paid_hours": 8.5,
        }
        assert upsert_travel_route(
            origin_label="Office / Clow Place",
            destination_label="Te Rapa",
            travel_minutes=30,
            source="test",
            also_reverse=True,
        )
        te_rapa_effective = resolve_race_timing_fields(
            te_rapa_shift,
            cached_te_rapa,
        )
        assert te_rapa_effective["sources"] == {
            "race_count": "Deputy",
            "first_race_time": "Love Racing",
            "last_race_time": "Love Racing",
        }
        te_rapa_shift["effective_race_timing"] = te_rapa_effective
        calculation = build_race_day_calculation(te_rapa_shift)
        assert calculation["last_race_label"] == "16:38"
        assert "scheduled last race from Love Racing" in calculation["formula"]
        assert te_rapa_note == ["Office 0900", "On track 0930", "8 races"]

        insert_identity("55034", "2026-07-26", "Te Aroha")
        complete = parse_meeting_programme(fixture("meeting_55034_complete.html"))
        merge_love_racing_programme(
            "55034",
            {
                "lifecycle_status": complete.lifecycle_status,
                "races": complete.races,
                "race_count": complete.race_count,
                "first_race_time": complete.first_race_time,
                "last_race_time": complete.last_race_time,
                "diagnostics": complete.diagnostics,
                "content_hash": complete.content_hash,
            },
            "2026-07-26T09:00:00+12:00",
        )
        te_aroha_detail = dict(get_love_racing_meeting_detail("55034"))
        te_aroha_note = ["9 races", "First race 1140 | Last race 1635"]
        te_aroha_shift = {
            "race_type_label": "Thoroughbred racing",
            "description_lines": te_aroha_note,
            "roster_summary": parse_roster_summary(te_aroha_note),
            "timing_adjustment_time": "",
            "timing_adjustment_last_race": 0,
        }
        te_aroha_effective = resolve_race_timing_fields(
            te_aroha_shift,
            te_aroha_detail,
        )
        assert (
            te_aroha_effective["race_count"],
            te_aroha_effective["first_race_time"],
            te_aroha_effective["last_race_time"],
        ) == (9, "11:40", "16:35")
        assert te_aroha_effective["sources"] == {
            "race_count": "Deputy",
            "first_race_time": "Deputy",
            "last_race_time": "Deputy",
        }
        assert te_aroha_note == ["9 races", "First race 1140 | Last race 1635"]

        with sqlite3.connect(os.environ["DB_PATH"]) as conn:
            assert conn.execute("SELECT COUNT(*) FROM shift_changes").fetchone()[0] == 0
    finally:
        planning_calendar.capture_meeting_pages = original_capture_pages
        planning_calendar.capture_calendar_months = original_capture_calendar


def assert_refresh_cadence() -> None:
    from app.love_racing_details import programme_refresh_due

    meeting_date = "2026-07-26"
    seven_days = datetime(2026, 7, 19, 9, 0, tzinfo=NZ)
    detail = {
        "meeting_date": meeting_date,
        "lifecycle_status": "discovered",
        "page_last_checked_at": (seven_days - timedelta(hours=1)).isoformat(),
    }
    assert programme_refresh_due(detail, seven_days)[0] is False

    inside_72 = datetime(2026, 7, 24, 9, 0, tzinfo=NZ)
    detail["page_last_checked_at"] = (inside_72 - timedelta(hours=7)).isoformat()
    assert programme_refresh_due(detail, inside_72)[0] is True

    inside_24 = datetime(2026, 7, 25, 9, 0, tzinfo=NZ)
    detail["page_last_checked_at"] = (inside_24 - timedelta(hours=3)).isoformat()
    assert programme_refresh_due(detail, inside_24)[0] is True

    complete = {
        **detail,
        "lifecycle_status": "complete",
        "race_morning_confirmed_at": "2026-07-26T07:00:00+12:00",
        "first_race_time": "11:34",
    }
    assert programme_refresh_due(complete, inside_24)[0] is False
    assert programme_refresh_due(complete, inside_24, manual=True)[0] is True

    backoff = {
        **detail,
        "next_retry_at": (inside_24 + timedelta(hours=1)).isoformat(),
    }
    assert programme_refresh_due(backoff, inside_24)[0] is False


def insert_identity(meeting_id: str, meeting_date: str, venue: str) -> None:
    from app.database import merge_love_racing_meeting_identities, save_love_racing_meetings

    checked_at = "2026-07-20T09:00:00+12:00"
    save_love_racing_meetings(
        [
            {
                "date": meeting_date,
                "racecourse": venue,
                "club_name": "Waikato Thoroughbred Racing",
                "source_hash": f"fixture-{meeting_id}",
                "source_url": "fixture",
            }
        ],
        checked_at,
    )
    result = merge_love_racing_meeting_identities(
        [
            {
                "meeting_id": meeting_id,
                "meeting_url": (
                    f"https://loveracing.nz/RaceInfo/{meeting_id}/Meeting-Overview.aspx"
                ),
                "date": meeting_date,
                "racecourse": venue,
                "club_name": "Waikato Thoroughbred Racing",
            }
        ],
        checked_at,
    )
    assert result == {"matched": 1, "ambiguous": 0}


def assert_cache_queue_and_precedence() -> None:
    from app.database import (
        claim_love_racing_detail_jobs,
        get_love_racing_meeting_detail,
        merge_love_racing_programme,
        queue_love_racing_detail_job,
    )
    from app.love_racing_details import parse_meeting_programme
    from app.main import build_race_day_summary, parse_roster_summary, resolve_race_timing_fields

    insert_identity("55032", "2026-07-25", "Te Rapa")
    complete = parse_meeting_programme(fixture("meeting_55032_complete.html"))
    programme = {
        "lifecycle_status": complete.lifecycle_status,
        "races": complete.races,
        "race_count": complete.race_count,
        "first_race_time": complete.first_race_time,
        "last_race_time": complete.last_race_time,
        "diagnostics": complete.diagnostics,
        "content_hash": complete.content_hash,
    }
    merged = merge_love_racing_programme(
        "55032",
        programme,
        "2026-07-24T08:00:00+12:00",
    )
    assert merged["material_change"] is True

    blank = parse_meeting_programme(fixture("results_layout_blank.html"))
    merge_love_racing_programme(
        "55032",
        {
            "lifecycle_status": blank.lifecycle_status,
            "races": blank.races,
            "race_count": blank.race_count,
            "first_race_time": blank.first_race_time,
            "last_race_time": blank.last_race_time,
            "diagnostics": blank.diagnostics,
            "content_hash": blank.content_hash,
        },
        "2026-07-26T18:00:00+12:00",
    )
    cached = dict(get_love_racing_meeting_detail("55032"))
    assert cached["lifecycle_status"] == "historical"
    assert (cached["race_count"], cached["first_race_time"], cached["last_race_time"]) == (
        8,
        "12:25",
        "16:38",
    )

    assert queue_love_racing_detail_job(
        "55032",
        reason="user one",
        priority=10,
        requested_at="2026-07-24T09:00:00+12:00",
    )
    assert not queue_love_racing_detail_job(
        "55032",
        reason="user two",
        priority=20,
        requested_at="2026-07-24T09:01:00+12:00",
    )
    claimed = claim_love_racing_detail_jobs("2026-07-24T09:02:00+12:00")
    assert len(claimed) == 1
    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        conn.execute(
            """
            UPDATE love_racing_detail_jobs
            SET started_at = '2026-07-24T08:00:00+12:00'
            WHERE id = ?
            """,
            (claimed[0]["id"],),
        )
    reclaimed = claim_love_racing_detail_jobs("2026-07-24T09:03:00+12:00")
    assert len(reclaimed) == 1 and reclaimed[0]["id"] == claimed[0]["id"]

    raw_note = ["8 races"]
    shift = {
        "race_type_label": "Thoroughbred racing",
        "description_lines": raw_note,
        "roster_summary": parse_roster_summary(raw_note),
        "timing_adjustment_time": "",
        "timing_adjustment_last_race": 0,
    }
    effective = resolve_race_timing_fields(shift, cached)
    assert effective["race_count"] == 8
    assert effective["first_race_time"] == "12:25"
    assert effective["last_race_time"] == "16:38"
    assert effective["sources"] == {
        "race_count": "Deputy",
        "first_race_time": "Love Racing",
        "last_race_time": "Love Racing",
    }
    shift["effective_race_timing"] = effective
    summary = build_race_day_summary(shift, {})
    assert {"label": "8 races", "value": "12:25 | 16:38"} in summary["rows"]
    assert summary["source_note"] == "Race count from Deputy · Race times from Love Racing"
    assert raw_note == ["8 races"]

    user_override = {
        **shift,
        "timing_adjustment_time": "16:45",
        "timing_adjustment_last_race": 1,
    }
    overridden = resolve_race_timing_fields(user_override, cached)
    assert overridden["last_race_time"] == "16:45"
    assert overridden["sources"]["last_race_time"] == "User"

    harness = {
        **shift,
        "race_type_label": "Harness racing",
    }
    no_love = resolve_race_timing_fields(harness, cached)
    assert "Love Racing" not in no_love["sources"].values()

    with sqlite3.connect(os.environ["DB_PATH"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shift_changes").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM love_racing_detail_jobs WHERE meeting_id = '55032'"
        ).fetchone()[0] == 1


def main() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix=".codex_tmp_love-racing-detail-", dir=ROOT))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "details.sqlite3"),
        APP_SECRET_KEY="love-racing-detail-smoke",
        TZ="Pacific/Auckland",
    )
    sys.path.insert(0, str(ROOT))

    from app.database import init_db

    try:
        init_db()
        assert_programme_parser()
        assert_refresh_cadence()
        assert_cache_queue_and_precedence()
        assert_admin_preview_and_backfill()
        print("Love Racing meeting-detail smoke ok")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
