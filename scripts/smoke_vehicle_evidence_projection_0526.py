from __future__ import annotations

"""Read-only regressions for native vehicle-resource evidence convergence."""

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    temporary = Path(tempfile.mkdtemp(prefix="redeputy-0526-vehicle-projection-"))
    os.environ.update(
        DATA_DIR=str(temporary),
        DB_PATH=str(temporary / "projection.sqlite3"),
        APP_SECRET_KEY="vehicle-projection-smoke",
        TZ="Pacific/Auckland",
        DEPUTY_WRITE_MODE="off",
    )
    sys.path.insert(0, str(ROOT))
    from app.database import (
        fetch_deputy_schedule_between,
        fetch_deputy_schedule_for_date,
        get_connection,
        init_db,
    )
    from app.main import event_change_display_line, schedule_people

    init_db()

    def seed(
        source_shift_id: int,
        label: str,
        employee_id: int | None,
        *,
        date: str = "2026-09-18",
        location_id: int = 69,
        observed_at: str = "2026-09-10T10:00:00+12:00",
        observer: str = "user:1:native_get_rosters",
    ) -> None:
        values = {
            "source_shift_id": source_shift_id,
            "area_name": label,
            "area_location_id": location_id,
            "employee_id": employee_id,
            "employee_name": "Joshua Druett" if employee_id == 19 else "Nate" if employee_id == 23 else "",
            "start_at": f"{date}T08:00:00+12:00",
            "end_at": f"{date}T18:00:00+12:00",
            "date": date,
            "area_id": source_shift_id,
        }
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO deputy_schedule_shifts(
                    source_shift_id,captured_at,area_id,area_name,area_location_id,
                    employee_id,employee_name,start_at,end_at,date,is_published
                ) VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    source_shift_id, observed_at, source_shift_id, label, location_id,
                    employee_id, values["employee_name"], values["start_at"], values["end_at"], date,
                ),
            )
            conn.execute(
                """INSERT INTO deputy_schedule_observations(
                    source_shift_id,observer_key,first_seen_at,last_seen_at,active,assignment_fingerprint
                ) VALUES(?,?,?,?,1,?)""",
                # Legacy native observations without a fingerprint are valid only
                # when their observed time is the row's capture time.
                (source_shift_id, observer, observed_at, observed_at, None),
            )

    # Exact production shape: later same-native evidence converges each person
    # from 685 to Rental, while all captured rows and observations remain intact.
    seed(1901, "685", 19)
    seed(2301, "685", 23)
    seed(1902, "Rental", 19, observed_at="2026-09-12T10:00:00+12:00")
    seed(2302, "Rental", 23, observed_at="2026-09-12T10:00:00+12:00")
    effective = fetch_deputy_schedule_for_date("2026-09-18", [69])
    if {(row["employee_id"], row["area_name"]) for row in effective} != {(19, "Rental"), (23, "Rental")}:
        raise AssertionError(f"later same-native Rental did not suppress stale 685: {effective!r}")
    if {(row["employee_id"], row["area_name"]) for row in fetch_deputy_schedule_between("2026-09-18", "2026-09-18")} != {(19, "Rental"), (23, "Rental")}:
        raise AssertionError("date-range schedule read disagreed with date schedule read")
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) FROM deputy_schedule_shifts").fetchone()[0] != 4:
            raise AssertionError("effective projection changed stored source rows")
        if conn.execute("SELECT COUNT(*) FROM deputy_schedule_observations WHERE active=1").fetchone()[0] != 4:
            raise AssertionError("effective projection changed stored observations")

    # Equal native observation time is genuine concurrent resource evidence.
    seed(1911, "685", 19, date="2026-09-19", observed_at="2026-09-12T12:00:00+12:00")
    seed(1912, "Rental", 19, date="2026-09-19", observed_at="2026-09-12T12:00:00+12:00")
    equal_state = fetch_deputy_schedule_for_date("2026-09-19", [69])
    if {row["area_name"] for row in equal_state} != {"685", "Rental"}:
        raise AssertionError("same-capture 685 + Rental was not retained as concurrent evidence")

    # IDs, location, date, and observer are all hard isolation boundaries.
    seed(1921, "685", 19, date="2026-09-20")
    seed(1922, "Rental", 23, date="2026-09-20", observed_at="2026-09-13T10:00:00+12:00")
    seed(1931, "685", 19, date="2026-09-21", location_id=69)
    seed(1932, "Rental", 19, date="2026-09-21", location_id=70, observed_at="2026-09-13T10:00:00+12:00")
    seed(1941, "685", 19, date="2026-09-22", observer="user:1:native_get_rosters")
    seed(1942, "Rental", 19, date="2026-09-22", observed_at="2026-09-13T10:00:00+12:00", observer="user:2:native_get_rosters")
    seed(1951, "685", None, date="2026-09-23")
    seed(1952, "Rental", None, date="2026-09-23", observed_at="2026-09-13T10:00:00+12:00")
    for date in ("2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"):
        if len(fetch_deputy_schedule_for_date(date)) != 2:
            raise AssertionError(f"vehicle evidence crossed an identity/provenance boundary for {date}")

    # Catalogue compatibility and history formatting are deliberately narrow.
    from app.database import _looks_like_crew_vehicle
    from app.deputy_evidence import is_vehicle_context
    if not all(_looks_like_crew_vehicle(label) for label in ("684", "685", "Rav91", "Tender", "OB", "Transit", "Rental")):
        raise AssertionError("known vehicle catalogue values regressed")
    if not is_vehicle_context("RP1"):
        raise AssertionError("existing RP vehicle evidence classification regressed")
    if any(_looks_like_crew_vehicle(label) for label in ("Vehicle", "Vehicles", "1234")):
        raise AssertionError("Rental classification broadened the vehicle catalogue")
    if event_change_display_line({"old_positions": ["685"], "new_positions": ["Rental"]}) != "Vehicle — 685 → Rental":
        raise AssertionError("vehicle history was rendered as a role/position change")
    if event_change_display_line({"old_positions": ["CCU2"], "new_positions": ["Director"], "change_type": "move", "new_employee_name": "Nate"}) != "Nate moved CCU2 → Director":
        raise AssertionError("production history wording regressed")
    if schedule_people([{"area_name": "Side 1", "employee_id": None, "employee_name": "", "is_open": 1, "date": "2026-09-18", "schedule_location_id": 69, "start_at": "2026-09-18T08:00:00+12:00", "end_at": "2026-09-18T18:00:00+12:00"}], include_placeholders=False) == []:
        raise AssertionError("genuine open production shift disappeared")
    print("Vehicle evidence projection 0.5.26 smoke passed")


if __name__ == "__main__":
    main()
