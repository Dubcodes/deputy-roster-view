from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-note-smoke-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "note-smoke.sqlite3"),
        APP_SECRET_KEY="note-smoke",
    )
    sys.path.insert(0, str(ROOT))

    from app.config import get_settings
    from app.database import init_db, upsert_travel_route
    from app.main import (
        apply_event_changes_to_schedule_people,
        apply_timing_math,
        build_race_day_summary,
        build_shift_change_summary,
        canonical_crew_name,
        decorate_change,
        extract_roster_time_token,
        group_event_changes,
        parse_roster_summary,
        parse_roster_time_token,
    )

    init_db()

    expected_times = {
        "8.15am": "08:15",
        "8:15am": "08:15",
        "8.15 am": "08:15",
        "8:15 am": "08:15",
        "815am": "08:15",
        "0815": "08:15",
        "8 15 am": "08:15",
        "8.45am": "08:45",
        "10.45am": "10:45",
        "11.00am": "11:00",
        "1.05pm": "13:05",
        "12.00pm": "12:00",
        "12.00am": "00:00",
        "0930": "09:30",
        "1615": "16:15",
        "16:15": "16:15",
        "1635": "16:35",
    }
    for raw_value, expected in expected_times.items():
        actual = parse_roster_time_token(raw_value)
        if actual != expected:
            raise AssertionError(f"{raw_value!r} parsed as {actual!r}, expected {expected!r}")
    for invalid in ("28.75am", "25:90", "8 Races", "1 Taylor Street", ".15am"):
        if extract_roster_time_token(invalid):
            raise AssertionError(f"Invalid/non-time value was parsed: {invalid!r}")

    raw_note = "\n".join(
        (
            "Clow Pl 8.15am",
            "On Track 8.45am",
            "Records 10.45am",
            "On Air 11.00am",
            "8 Races",
        )
    )
    original_note = raw_note
    lines = raw_note.splitlines()
    summary = parse_roster_summary(lines)
    timing_values = {
        str(item["label"]): str(item["time"])
        for item in summary["timings"]
    }
    expected_note_times = {
        "Clow Place": "08:15",
        "On track": "08:45",
        "Records": "10:45",
        "On air": "11:00",
    }
    if timing_values != expected_note_times:
        raise AssertionError(f"Te Rapa note parsed incorrectly: {timing_values!r}")
    if summary["production_notes"] != ["8 races"]:
        raise AssertionError(f"Te Rapa race count parsed incorrectly: {summary!r}")
    race_summary = build_race_day_summary(
        {"description_lines": lines, "roster_summary": summary},
        {},
    )
    if race_summary["rows"] != [
        {"label": "Clow Place", "value": "08:15"},
        {"label": "On track", "value": "08:45"},
        {"label": "Records", "value": "10:45"},
        {"label": "On air", "value": "11:00"},
        {"label": "8 races", "value": ""},
    ]:
        raise AssertionError(f"Te Rapa race-day rows were wrong: {race_summary!r}")
    rendered_values = repr(race_summary)
    if any(fragment in rendered_values for fragment in ("15:00", "45am")):
        raise AssertionError(f"Truncated dotted time leaked into output: {race_summary!r}")
    if raw_note != original_note:
        raise AssertionError("Parsing changed the original raw roster note.")
    with sqlite3.connect(get_settings().db_path) as conn:
        visible_changes = conn.execute("SELECT COUNT(*) FROM shift_changes").fetchone()[0]
    if visible_changes:
        raise AssertionError("Parser reinterpretation created a user-visible roster change.")

    identities = [
        {
            "canonical_display_name": "Campbell Stephens",
            "current_deputy_name": "Campbell Stephens",
            "deputy_employee_id": 44,
            "aliases": ["Cambo"],
        }
    ]
    if canonical_crew_name("Cambo", None, identities) != "Campbell Stephens":
        raise AssertionError("Crew alias did not resolve to its canonical identity.")
    base_change = {
        "group_id": "te-aroha-change",
        "changed_at": "2026-07-25T20:45:00+12:00",
        "changed_since_viewed": 1,
    }
    audit_rows = [
        base_change
        | {
            "change_type": "move",
            "old_positions": ["CCU2"],
            "new_positions": ["Head On"],
            "old_employee_name": "Nate",
            "new_employee_name": "Nate",
        },
        base_change
        | {
            "change_type": "replacement",
            "old_positions": ["Head On"],
            "new_positions": ["Head On"],
            "old_employee_name": canonical_crew_name("Cambo", None, identities),
            "new_employee_name": "Nate",
        },
        base_change
        | {
            "change_type": "opened",
            "old_positions": ["CCU2"],
            "new_positions": ["CCU2"],
            "old_employee_name": "Nate",
            "new_employee_name": "TBC",
        },
    ]
    groups = group_event_changes(audit_rows)
    expected_group_lines = [
        "Nate moved CCU2 → Head On, replacing Campbell Stephens",
        "CCU2 is now TBC",
    ]
    if len(audit_rows) != 3 or groups[0]["lines"] != expected_group_lines:
        raise AssertionError(f"Related audit rows were not grouped coherently: {groups!r}")
    people = [
        {"position_label": "Head On", "employee_name": "Nate"},
        {"position_label": "CCU2", "employee_name": "TBC", "placeholder": True},
    ]
    apply_event_changes_to_schedule_people(people, groups)
    if people[0].get("change_summary") != "Nate moved from CCU2, replacing Campbell Stephens":
        raise AssertionError(f"Head On inline summary was wrong: {people!r}")
    if people[1].get("change_summary") != "Nate moved to Head On; position now TBC":
        raise AssertionError(f"CCU2 inline summary was wrong: {people!r}")
    simple_groups = group_event_changes([
        {
            **base_change,
            "group_id": "side-two-change",
            "change_type": "filled",
            "old_positions": ["Side 2"],
            "new_positions": ["Side 2"],
            "old_employee_name": "TBC",
            "new_employee_name": "Dylan Holden",
            "inline_summary": "TBC → Dylan Holden",
        }
    ])
    simple_people = [{"position_label": "Side 2", "employee_name": "Dylan Holden"}]
    apply_event_changes_to_schedule_people(simple_people, simple_groups)
    if simple_groups[0]["lines"] != ["Side 2 — TBC → Dylan Holden"]:
        raise AssertionError(f"Simple crew replacement was over-grouped: {simple_groups!r}")
    if simple_people[0].get("change_summary") != "TBC → Dylan Holden":
        raise AssertionError(f"Simple crew row lost its change summary: {simple_people!r}")

    changes = [
        decorate_change(
            {
                "field_name": "end_at",
                "old_value": "2026-07-26T18:00:00+12:00",
                "new_value": "2026-07-26T18:30:00+12:00",
            }
        ),
        decorate_change(
            {
                "field_name": "start_at",
                "old_value": "2026-07-26T09:00:00+12:00",
                "new_value": "2026-07-26T09:30:00+12:00",
            }
        ),
    ]
    if build_shift_change_summary(changes) != "Start 09:00 → 09:30 · Finish 18:00 → 18:30":
        raise AssertionError(f"Personal change summary was not compact: {changes!r}")
    if [(change["field_label"], change["old_display"], change["new_display"]) for change in changes] != [
        ("Finish time", "18:00", "18:30"),
        ("Start time", "09:00", "09:30"),
    ]:
        raise AssertionError(f"Expanded personal changes were not compact: {changes!r}")

    upsert_travel_route(
        origin_label="Office / Clow Place",
        destination_label="Te Aroha",
        travel_minutes=60,
        also_reverse=True,
    )
    calculated_shift = {
        "date": "2026-07-26",
        "track_label": "Te Aroha",
        "source_code": "T-Te Aroha",
        "start_at": "2026-07-26T09:30:00+12:00",
        "end_at": "2026-07-26T18:30:00+12:00",
        "start_label": "09:30",
        "end_label": "18:30",
        "time_range": "09:30-18:30",
        "raw_hours": 10.0,
        "raw_label": "10h",
        "paid_hours": 10.0,
        "paid_label": "10h",
        "break_minutes": 0,
        "role_segments": [],
        "description_lines": lines,
        "roster_summary": parse_roster_summary(
            ["Office 8.30am", "On track 9.30am", "8 races 12.10pm | 4.35pm"]
        ),
    }
    apply_timing_math(calculated_shift)
    if calculated_shift["display_window"] != {
        "source": "calculated",
        "start_label": "08:30",
        "end_label": "18:45",
        "hours": 10.25,
        "hours_label": "10h 15m",
        "time_range": "08:30–18:45",
    }:
        raise AssertionError(f"Calculated display window mixed sources: {calculated_shift!r}")

    roster_shift = {
        **calculated_shift,
        "start_label": "09:30",
        "end_label": "18:30",
        "time_range": "09:30-18:30",
        "raw_hours": 9.0,
        "raw_label": "9h",
        "paid_hours": 9.0,
        "paid_label": "9h",
        "roster_summary": parse_roster_summary(["Office 9.30am"]),
    }
    apply_timing_math(roster_shift)
    if roster_shift["display_window"] != {
        "source": "roster",
        "start_label": "09:30",
        "end_label": "18:30",
        "hours": 9.0,
        "hours_label": "9h",
        "time_range": "09:30–18:30",
    }:
        raise AssertionError(f"Roster-only display window mixed sources: {roster_shift!r}")

    print("note interpretation smoke ok")


if __name__ == "__main__":
    main()
