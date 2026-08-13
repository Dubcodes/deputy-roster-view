from __future__ import annotations

import os
import re
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
    from app.database import get_travel_route, init_db, save_crew_vehicle, upsert_travel_route
    import app.main as main_module
    from fastapi.testclient import TestClient
    from app.main import (
        apply_event_changes_to_schedule_people,
        apply_roster_note_vehicles,
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
    save_crew_vehicle(vehicle_id=None, display_label="684", aliases=[], active=True, sort_order=10, team_id=None, notes="", is_truck=False, actor_user_id=None)
    save_crew_vehicle(vehicle_id=None, display_label="Rav91", aliases=["Rav"], active=True, sort_order=20, team_id=None, notes="", is_truck=False, actor_user_id=None)

    travel_note = "Trucks Dylan and Esq\nGrant, Todd, Lans and Junior Rav91\nJosh Jayden Nate qua684"
    travel_summary = parse_roster_summary(travel_note.splitlines())
    allocations = list(travel_summary.get("crew_allocations") or [])
    allocation_684 = next((item for item in allocations if item.get("vehicle") == "684"), None)
    allocation_rav = next((item for item in allocations if item.get("vehicle") == "Rav91"), None)
    if not allocation_684 or "Josh Jayden Nate" not in str(allocation_684.get("people") or ""):
        raise AssertionError(f"Known vehicle suffix qua684 was not retained as conservative 684 evidence: {allocations!r}")
    if not allocation_rav or not all(name in str(allocation_rav.get("people") or "") for name in ("Grant", "Todd", "Lans", "Junior")):
        raise AssertionError(f"Trailing Rav91 crew group was not parsed: {allocations!r}")
    if "qua684" not in travel_note:
        raise AssertionError("Travel parsing modified the raw roster note.")

    taupo_people = [
        {"employee_name": "James", "vehicle_label": ""},
        {"employee_name": "Grant Woolston", "vehicle_label": ""},
        {"employee_name": "Lans McGall", "vehicle_label": ""},
        {"employee_name": "Alf", "vehicle_label": "Rav91"},
        {"employee_name": "Jayden-lee", "vehicle_label": "Rav91"},
        {"employee_name": "Joshua Druett", "vehicle_label": "Rav91"},
    ]
    taupo_note = "684 james grant lans\nRav Alf jayden and josh"
    apply_roster_note_vehicles(
        taupo_people,
        [{"roster_summary": parse_roster_summary(taupo_note.splitlines())}],
    )
    taupo_vehicles = {item["employee_name"]: item["vehicle_label"] for item in taupo_people}
    expected_taupo = {
        "James": "684", "Grant Woolston": "684", "Lans McGall": "684",
        "Alf": "Rav91", "Jayden-lee": "Rav91", "Joshua Druett": "Rav91",
    }
    if taupo_vehicles != expected_taupo or "Rav91, Rav" in repr(taupo_people):
        raise AssertionError(f"Vehicle aliases were not canonicalized before aggregation: {taupo_people!r}")

    main_module.queue_manual_sync = lambda *_args, **_kwargs: True
    client = TestClient(main_module.app)
    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://example.au.deputy.com/#/",
            "deputy_email": "travel@example.com",
            "deputy_password": "password",
            "pin": "1234",
            "pin_confirm": "1234",
            "next_url": "/settings",
        },
        follow_redirects=False,
    )
    if signup.status_code != 303:
        raise AssertionError(f"Travel render fixture signup failed: {signup.status_code}")
    with sqlite3.connect(get_settings().db_path) as conn:
        conn.row_factory = sqlite3.Row
        user_id = int(conn.execute("SELECT id FROM app_users WHERE deputy_email='travel@example.com'").fetchone()["id"])
        now = "2026-08-12T12:00:00+12:00"
        identities = {
            "Dylan Holden": ["Dylan"],
            "Esq": [],
            "Grant Woolston": ["Grant"],
            "Lans McGall": ["Lans"],
            "Joshua Druett": ["Josh"],
            "Jayden-lee": ["Jayden"],
            "Nate": [],
        }
        for employee_id, (canonical, aliases) in enumerate(identities.items(), start=100):
            cursor = conn.execute(
                "INSERT INTO crew_people(canonical_display_name,deputy_employee_id,current_deputy_name,is_active,created_at,updated_at) VALUES (?,?,?,1,?,?)",
                (canonical, employee_id, canonical, now, now),
            )
            for alias in aliases:
                conn.execute(
                    "INSERT INTO crew_aliases(person_id,alias,normalized_alias,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (cursor.lastrowid, alias, alias.casefold(), now, now),
                )
        conn.execute(
            "INSERT INTO shifts(source_uid,title,description,location,start_at,end_at,date,raw_hours,paid_hours,owner_user_id,last_synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("travel-2026-08-14", "[Travel] Travel then Overnighter", travel_note, "Travel", "2026-08-14T12:00:00+12:00", "2026-08-14T17:00:00+12:00", "2026-08-14", 5.0, 5.0, user_id, now),
        )
        conn.execute(
            "INSERT INTO deputy_schedule_shifts(source_shift_id,captured_at,area_name,employee_id,employee_name,start_at,end_at,date,duration,is_published,changed_since_viewed,change_summary) VALUES (?,?,?,?,?,?,?,?,?,1,1,?)",
            (9001, now, "Travel then Overnighter", 999, "Rob Watson", "2026-08-14T12:00:00+12:00", "2026-08-14T17:00:00+12:00", "2026-08-14", 18000, "Mark Strachan -> Rob Watson"),
        )
        conn.execute(
            "INSERT INTO deputy_schedule_event_changes(group_id,change_key,change_type,date,old_positions,new_positions,old_employee_name,new_employee_name,changed_at,display_summary,inline_summary) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("generic-travel", "replacement", "replacement", "2026-08-14", '["Travel then Overnighter"]', '["Travel then Overnighter"]', "Mark Strachan", "Rob Watson", now, "Travel then Overnighter: Mark Strachan -> Rob Watson", "Mark Strachan -> Rob Watson"),
        )
        conn.execute(
            "INSERT INTO deputy_schedule_assignment_history(source_shift_id,date,position_label,old_employee_name,new_employee_name,changed_at) VALUES (?,?,?,?,?,?)",
            (9001, "2026-08-14", "Travel then Overnighter", "Mark Strachan", "Rob Watson", now),
        )
        conn.commit()
    rendered = client.get("/day/2026-08-14")
    if rendered.status_code != 200:
        raise AssertionError(f"Travel day did not render: {rendered.status_code}")
    html = rendered.text
    crew_match = re.search(r'aria-label="Deputy schedule crew".*?</section>', html, re.S)
    crew_html = crew_match.group(0) if crew_match else ""
    expected_people = ("Dylan Holden", "Esq", "Grant Woolston", "Lans McGall", "Joshua Druett", "Jayden-lee", "Nate")
    if not crew_html or any(name not in crew_html for name in expected_people):
        raise AssertionError(f"Rendered Travel cohort omitted confidently resolved note people: {crew_html}")
    if "Rob Watson" in html or "Mark Strachan" in html:
        raise AssertionError("Rendered Travel day retained unrelated generic schedule crew/history.")
    if "Rav91" not in crew_html or "684" not in crew_html:
        raise AssertionError("Rendered Travel cohort lost canonical note vehicles.")
    if "Rav91, Rav" in crew_html:
        raise AssertionError("Final rendered Travel cohort leaked the Rav alias beside canonical Rav91.")
    if "Todd" in crew_html or "Junior" in crew_html:
        raise AssertionError("Unresolved short names were guessed into the visible Travel cohort.")
    for raw_line in travel_note.splitlines():
        if raw_line not in html:
            raise AssertionError(f"Raw Travel roster note line was not preserved: {raw_line!r}")

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

    upsert_travel_route(
        origin_label="Office / Clow Place",
        destination_label="Ruakaka",
        travel_minutes=300,
        also_reverse=True,
    )
    original_fetch = main_module.fetch_shifts_between
    main_module.fetch_shifts_between = lambda *_args, **_kwargs: [{
        "date": "2026-08-14",
        "title": "[Travel] Travel then Overnighter",
        "description": travel_note,
        "location": "Travel",
        "start_at": "2026-08-14T12:00:00+12:00",
        "end_at": "2026-08-14T17:00:00+12:00",
    }]
    try:
        overnight_shift = {
            **roster_shift,
            "owner_user_id": 1,
            "date": "2026-08-15",
            "track_label": "Ruakaka",
            "source_code": "T-Ruakaka",
            "start_at": "2026-08-15T09:00:00+12:00",
            "end_at": "2026-08-15T18:00:00+12:00",
            "start_label": "09:00",
            "end_label": "18:00",
            "time_range": "09:00-18:00",
            "raw_hours": 9.0,
            "raw_label": "9h",
            "paid_hours": 9.0,
            "paid_label": "9h",
            "description_lines": ["On track 09:30", "8 races 12:00 | 16:30"],
            "roster_summary": parse_roster_summary(["On track 09:30", "8 races 12:00 | 16:30"]),
        }
        apply_timing_math(overnight_shift)
    finally:
        main_module.fetch_shifts_between = original_fetch
    if overnight_shift["display_start_label"] == "04:30" or overnight_shift["display_hours"] != 9.0:
        raise AssertionError(f"Previous-day overnight travel still invented a five-hour office outbound leg: {overnight_shift!r}")
    race_day = overnight_shift["timing_math"]["race_day"]
    if race_day.get("available"):
        raise AssertionError(f"Unknown hotel departure should remain TBC instead of becoming a calculated office start: {race_day!r}")
    if get_travel_route("Ruakaka", "Office / Clow Place") is None:
        raise AssertionError("Suppressing the overnight outbound leg removed the valid return route.")

    print("note interpretation smoke ok")


if __name__ == "__main__":
    main()
