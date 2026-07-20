from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-extended-smoke-"))
    os.environ.update(DATA_DIR=str(temp_dir), DB_PATH=str(temp_dir / "extended.sqlite3"), APP_SECRET_KEY="extended-smoke")

    from app.config import get_settings
    from app.database import (
        crew_identity_records, delete_travel_route, delete_travel_time_default,
        get_track_map, get_travel_route, init_db, list_crew_people, list_travel_routes,
        update_crew_person, update_travel_time_default, upsert_track_map,
        upsert_travel_route, upsert_travel_time_default,
    )
    from app.main import apply_roster_note_vehicles, build_race_day_calculation, parse_roster_summary
    from app.public_holidays import holiday_for_date
    import app.track_maps as track_maps

    init_db()
    upsert_travel_route(origin_label="Beachfront Motel", destination_label="Ruakaka", travel_minutes=30)
    upsert_travel_route(origin_label="Ruakaka", destination_label="Office / Clow Place", travel_minutes=240)
    summary = parse_roster_summary(["Accommodation Beachfront Motel", "On track 0900", "7 races 1234 | 1605"])
    shift = {
        "date": "2026-07-18", "track_label": "Ruakaka", "source_code": "T-Ruakaka",
        "start_at": "2026-07-18T09:15:00+12:00", "end_at": "2026-07-18T22:00:00+12:00",
        "description_lines": ["Accommodation Beachfront Motel", "On track 0900", "7 races 1234 | 1605"],
        "roster_summary": summary,
    }
    calculation = build_race_day_calculation(shift)
    if calculation.get("start_label") != "08:30" or calculation.get("end_label") != "21:15":
        raise AssertionError(f"Ruakaka overnight legs were not resolved independently: {calculation!r}")
    labels = [str(line.get("label")) for line in calculation.get("lines", [])]
    if "Outbound travel" not in labels or "Return travel" not in labels or "travel each way" in calculation.get("formula", "").lower():
        raise AssertionError(f"Travel leg labels are not explicit: {calculation!r}")

    return_route = get_travel_route("Ruakaka", "Office / Clow Place")
    assert return_route is not None
    delete_travel_route(int(return_route["id"]))
    incomplete = build_race_day_calculation(shift)
    if incomplete.get("complete") or incomplete.get("warning") != "Return travel not configured" or incomplete.get("end_label"):
        raise AssertionError(f"Missing return route should remain visibly incomplete: {incomplete!r}")

    upsert_travel_route(origin_label="Office / Clow Place", destination_label="Matamata", travel_minutes=60, also_reverse=True)
    ordinary = build_race_day_calculation({
        "date": "2026-07-19", "track_label": "Matamata", "start_at": "2026-07-19T08:00:00+12:00",
        "roster_summary": parse_roster_summary(["Office 0800", "On track 0900", "6 races 1200 | 1600"]),
    })
    if not ordinary.get("complete") or ordinary.get("outbound_travel_label") != "1h" or ordinary.get("return_travel_label") != "1h":
        raise AssertionError(f"Ordinary same-base race day regressed: {ordinary!r}")

    upsert_travel_time_default(
        track_key="legacy-track", track_label="Legacy Track", base_label="Office",
        travel_minutes=45, source="manual",
    )
    with sqlite3.connect(get_settings().db_path) as conn:
        legacy_id = int(conn.execute(
            "SELECT id FROM travel_time_defaults WHERE track_label = 'Legacy Track' AND base_label = 'Office / Clow Place'"
        ).fetchone()[0])
    upsert_travel_route(
        origin_label="Office", destination_label="Legacy Track",
        travel_minutes=50, also_reverse=False,
    )
    delete_travel_time_default(legacy_id)
    if get_travel_route("Office", "Legacy Track") is None or get_travel_route("Legacy Track", "Office") is None:
        raise AssertionError("Deleting a legacy default removed independently managed routes.")

    upsert_travel_time_default(
        track_key="old-track", track_label="Old Track", base_label="Office",
        travel_minutes=35, source="manual",
    )
    with sqlite3.connect(get_settings().db_path) as conn:
        renamed_id = int(conn.execute(
            "SELECT id FROM travel_time_defaults WHERE track_label = 'Old Track' AND base_label = 'Office / Clow Place'"
        ).fetchone()[0])
    update_travel_time_default(
        renamed_id, track_key="new-track", track_label="New Track",
        base_label="Hotel One", travel_minutes=25,
    )
    if get_travel_route("Office", "Old Track") is not None or get_travel_route("Old Track", "Office") is not None:
        raise AssertionError("Renaming a legacy default left obsolete shared routes behind.")
    if get_travel_route("Hotel One", "New Track") is None or get_travel_route("New Track", "Hotel One") is None:
        raise AssertionError("Renaming a legacy default did not create its new shared routes.")

    for origin, destination, minutes in (
        ("Office / Clow Place", "Track A", 120), ("Track A", "Hotel One", 20),
        ("Hotel One", "Track B", 35), ("Track B", "Office / Clow Place", 150),
    ):
        upsert_travel_route(origin_label=origin, destination_label=destination, travel_minutes=minutes)
    day_one = build_race_day_calculation({
        "date": "2026-08-01", "track_label": "Track A", "travel_start_origin": "Office / Clow Place",
        "travel_finish_destination": "Hotel One", "start_at": "2026-08-01T07:00:00+12:00",
        "roster_summary": parse_roster_summary(["Office 0700", "On track 0900", "6 races 1200 | 1600"]),
    })
    day_two = build_race_day_calculation({
        "date": "2026-08-02", "track_label": "Track B", "travel_start_origin": "Hotel One",
        "travel_finish_destination": "Office / Clow Place", "start_at": "2026-08-02T08:00:00+12:00",
        "roster_summary": parse_roster_summary(["On track 0835", "6 races 1200 | 1600"]),
    })
    if not day_one.get("complete") or not day_two.get("complete") or day_two.get("return_travel_label") != "2h 30m":
        raise AssertionError(f"Two-day directed journey failed: {day_one!r} / {day_two!r}")

    with sqlite3.connect(get_settings().db_path) as conn:
        conn.executemany(
            """INSERT INTO deputy_schedule_shifts
               (source_shift_id, captured_at, employee_id, employee_name, area_name, date)
               VALUES (?, '2026-07-20T10:00:00+12:00', ?, ?, 'Side 1', '2026-07-20')""",
            [(9001, 101, "Gary Brown"), (9002, 202, "Gary Smith"), (9003, 303, "No App Crew")],
        )
    people = list_crew_people()
    garys = [person for person in people if str(person.get("canonical_display_name", "")).startswith("Gary")]
    if len(garys) != 2 or len({person["deputy_employee_id"] for person in garys}) != 2:
        raise AssertionError(f"Two Gary identities were incorrectly merged: {garys!r}")
    selected, other = garys
    saved, message = update_crew_person(
        int(selected["id"]), canonical_display_name=str(selected["canonical_display_name"]),
        app_user_id=None, aliases=["Gaz", "Gazz"], is_active=True, admin_note="",
    )
    if not saved:
        raise AssertionError(message)
    saved, _message = update_crew_person(
        int(other["id"]), canonical_display_name=str(other["canonical_display_name"]),
        app_user_id=None, aliases=["Gaz"], is_active=True, admin_note="",
    )
    if saved:
        raise AssertionError("Ambiguous active alias should be rejected.")
    schedule_people = [
        {"employee_name": "Gary Brown", "employee_id": 101, "vehicle_label": ""},
        {"employee_name": "Gary Smith", "employee_id": 202, "vehicle_label": ""},
    ]
    apply_roster_note_vehicles(schedule_people, [{"roster_summary": {"crew_allocations": [{"vehicle": "684", "people": "Gaz"}]}}])
    selected_row = next(person for person in schedule_people if person["employee_id"] == selected["deputy_employee_id"])
    other_row = next(person for person in schedule_people if person is not selected_row)
    if selected_row["vehicle_label"] != "684" or other_row["vehicle_label"]:
        raise AssertionError(f"Selected alias did not fill only its linked vehicle: {schedule_people!r}")
    if not any(person["canonical_display_name"] == "No App Crew" for person in list_crew_people()):
        raise AssertionError("Deputy-only crew member was absent from the admin directory.")

    waitangi = holiday_for_date(date(2026, 2, 6))
    if waitangi["names"] != ["Waitangi Day"] or holiday_for_date(date(2026, 2, 7))["is_public_holiday"]:
        raise AssertionError("NZ holiday rules returned an incorrect result.")

    html = """<meta property='og:image' content='/OnHorseFiles/Racecourses/Tracks/Ruakaka-thumb.jpg'>
        <a href='/OnHorseFiles/Racecourses/Tracks/Ruakaka-original.png'><img alt='Track - 2D'
        src='/Common/Image.ashx?p=/OnHorseFiles/Racecourses/Tracks/Ruakaka-thumb.jpg&w=300'
        srcset='/OnHorseFiles/Racecourses/Tracks/Ruakaka-medium.jpg 600w, /OnHorseFiles/Racecourses/Tracks/Ruakaka-large.jpg 1600w'></a>"""
    candidates = track_maps.parse_track_map_image_candidates(html, "https://loveracing.nz/RaceInfo/Clubs-And-Courses/32/Racecourse.aspx")
    if not any("Ruakaka-large.jpg" in candidate for candidate in candidates) or any("Common/Image.ashx" in candidate for candidate in candidates):
        raise AssertionError(f"Official map candidates or direct proxy resolution failed: {candidates!r}")

    def png(width: int, height: int, size: int) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes(max(0, size - 24))

    map_dir = temp_dir / "track_maps"
    map_dir.mkdir(exist_ok=True)
    previous_bytes = png(300, 300, 900)
    (map_dir / "ruakaka.png").write_bytes(previous_bytes)
    upsert_track_map(
        track_key="ruakaka", track_label="Ruakaka", course_label="Ruakaka", course_url="course",
        image_url="old", file_name="ruakaka.png", content_type="image/png", image_hash="old",
        status="ok", checked_at="old", updated_at="old", image_width=300, image_height=300, byte_size=len(previous_bytes),
    )

    class Response:
        def __init__(self, *, text: str = "", content: bytes = b"", content_type: str = "text/html", status: int = 200):
            self.text, self.content, self.status_code = text, content, status
            self.headers = {"content-type": content_type}
        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise track_maps.requests.RequestException(f"HTTP {self.status_code}")

    high = png(1400, 900, 4000)
    low = png(300, 300, 1000)
    class Session:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def get(self, url, **_kwargs):
            if "Racecourse.aspx" in url: return Response(text=html)
            if "large.jpg" in url: return Response(content=high, content_type="image/png")
            if "medium.jpg" in url or "thumb.jpg" in url: return Response(content=low, content_type="image/png")
            if "original.png" in url: return Response(content=high, content_type="image/png")
            return Response(status=404)

    original_session = track_maps.requests.Session
    original_known = track_maps.list_known_racecourse_names
    track_maps.requests.Session = Session
    track_maps.list_known_racecourse_names = lambda **_kwargs: ["Ruakaka"]
    try:
        result = track_maps.refresh_track_maps(get_settings())
    finally:
        track_maps.requests.Session = original_session
        track_maps.list_known_racecourse_names = original_known
    cached = get_track_map("ruakaka")
    if result["upgraded"] != 1 or cached is None or cached["image_width"] != 1400 or cached["byte_size"] != len(high):
        raise AssertionError(f"Low-resolution cached map was not upgraded: {result!r}")
    saved_hash = cached["image_hash"]

    class FailingSession(Session):
        def get(self, url, **_kwargs):
            raise track_maps.requests.RequestException("offline")
    track_maps.requests.Session = FailingSession
    track_maps.list_known_racecourse_names = lambda **_kwargs: ["Ruakaka"]
    try:
        failed = track_maps.refresh_track_maps(get_settings())
    finally:
        track_maps.requests.Session = original_session
        track_maps.list_known_racecourse_names = original_known
    if failed["failed"] != 1 or get_track_map("ruakaka")["image_hash"] != saved_hash:
        raise AssertionError("Failed replacement did not preserve the previous working map.")

    print("extended travel/people/holiday/map smoke ok")


if __name__ == "__main__":
    main()
