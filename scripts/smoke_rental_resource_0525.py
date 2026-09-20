from __future__ import annotations

"""Regression coverage for Deputy's Rental vehicle/resource Area."""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def interpreted_row(
    source_shift_id: str,
    area_name: str,
    start: str,
    end: str,
    *,
    employee_id: int = 17,
    employee_name: str = "Jayden-lee",
) -> dict[str, object]:
    return {
        "source_shift_id": source_shift_id,
        "date": "2026-09-18",
        "title": f"[T-Te Aroha] {area_name}",
        "location_name": "T-Te Aroha",
        "role_label": area_name,
        "employee_id": employee_id,
        "employee_name": employee_name,
        "start_at": f"2026-09-18T{start}:00+12:00",
        "end_at": f"2026-09-18T{end}:00+12:00",
        "source_payload": "{}",
    }


def schedule_row(
    source_shift_id: str,
    area_name: str,
    start: str,
    end: str,
    *,
    employee_id: int | None = 17,
    employee_name: str = "Jayden-lee",
    is_open: bool = False,
) -> dict[str, object]:
    return {
        "source_shift_id": source_shift_id,
        "date": "2026-09-18",
        "schedule_location_id": 64,
        "area_location_id": 64,
        "area_name": area_name,
        "area_roster_sort_order": 1,
        "employee_id": employee_id,
        "employee_name": employee_name,
        "is_open": int(is_open),
        "is_published": 1,
        "changed_since_viewed": 0,
        "assignment_changed": 0,
        "start_at": f"2026-09-18T{start}:00+12:00",
        "end_at": f"2026-09-18T{end}:00+12:00",
    }


def main() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="redeputy-0525-rental-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "rental.sqlite3"),
        APP_SECRET_KEY="rental-resource-smoke",
        TZ="Pacific/Auckland",
        DEPUTY_WRITE_MODE="off",
    )
    sys.path.insert(0, str(ROOT))
    from app.database import init_db
    from app.interpreted_workdays import interpret_deputy_workdays
    from app.main import display_schedule_area, role_is_vehicleish, schedule_people
    from app.roster_note_interpretation import canonical_vehicle_label, note_vehicle_allocations_from_text

    init_db()

    for label in ("684", "685", "Rav91", "Tender", "Transit", "OB", "RP1", "Rental"):
        if not role_is_vehicleish(label):
            raise AssertionError(f"{label} was not classified as a vehicle/resource Area.")
    if canonical_vehicle_label("rental") != "Rental":
        raise AssertionError("Rental was not recognized by vehicle canonicalisation.")
    if note_vehicle_allocations_from_text("Rental Jayden-lee") != [{
        "vehicle": "Rental", "people": ["Jayden-lee"], "raw": "Rental Jayden-lee",
    }]:
        raise AssertionError("Rental was not recognized as a precise roster-note vehicle token.")

    rental = interpreted_row("1770", "Rental", "10:00", "11:00")
    director = interpreted_row("1771", "DIR", "11:00", "18:45")
    workdays = interpret_deputy_workdays(
        [rental, director],
        person_identity={"id": 1, "deputy_employee_id": 17, "canonical_display_name": "Jayden-lee", "aliases": ["Jayden"]},
    )
    if len(workdays) != 1:
        raise AssertionError(f"Rental lead-in created multiple workdays: {workdays!r}")
    workday = workdays[0]
    if (
        workday["rostered_start"] != "10:00"
        or workday["rostered_finish"] != "18:45"
        or datetime.strptime("18:45", "%H:%M") - datetime.strptime("10:00", "%H:%M") != timedelta(hours=8, minutes=45)
        or workday["vehicle"] != "Rental"
        or display_schedule_area(str(workday["production_position"])) != "Director"
    ):
        raise AssertionError(f"Rental/Director workday was not preserved as vehicle plus production: {workday!r}")

    rendered = schedule_people(
        [schedule_row("1770", "Rental", "10:00", "11:00"), schedule_row("1771", "DIR", "11:00", "18:45")],
        include_placeholders=False,
    )
    if [(item["position_label"], item["employee_name"], item["vehicle_label"]) for item in rendered] != [
        ("Director", "Jayden-lee", "Rental"),
    ]:
        raise AssertionError(f"Rental rendered as a crew position: {rendered!r}")

    for role, employee_name in (("RTS", "James"), ("FM", "Sharne Connolly")):
        people = schedule_people(
            [
                schedule_row(f"{role}-rental", "Rental", "10:00", "11:00", employee_id=99, employee_name=employee_name),
                schedule_row(f"{role}-production", role, "11:00", "18:00", employee_id=99, employee_name=employee_name),
            ],
            include_placeholders=False,
        )
        if [(item["position_label"], item["vehicle_label"]) for item in people] != [(role, "Rental")]:
            raise AssertionError(f"{role} + Rental did not retain separate position/resource semantics: {people!r}")

    vehicle_only = schedule_people([schedule_row("rental-only", "Rental", "10:00", "11:00")], include_placeholders=False)
    vehicle_only_explicit = schedule_people(
        [schedule_row("rental-only", "Rental", "10:00", "11:00")],
        include_vehicle_only=True,
        include_placeholders=False,
    )
    if vehicle_only or [(item["position_label"], item["vehicle_label"]) for item in vehicle_only_explicit] != [("Travel", "Rental")]:
        raise AssertionError(f"Rental-only behavior did not follow existing vehicle-only semantics: {vehicle_only!r}, {vehicle_only_explicit!r}")

    open_rental = schedule_people(
        [schedule_row("rental-open", "Rental", "10:00", "11:00", employee_id=None, employee_name="", is_open=True)],
        include_placeholders=False,
    )
    if open_rental:
        raise AssertionError(f"Open Rental became a human crew vacancy: {open_rental!r}")

    print("Rental vehicle/resource regression smoke passed")


if __name__ == "__main__":
    main()
