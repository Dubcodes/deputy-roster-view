from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = Path(tempfile.mkdtemp(prefix="redeputy-closure-"))
os.environ.update(DATA_DIR=str(TEMP), DB_PATH=str(TEMP / "closure.sqlite3"), APP_SECRET_KEY="closure-fixture")

from app.database import get_connection, init_db, save_deputy_web_schedule
from app.deputy_web import _extract_management_shifts, _extract_schedule_shifts
from app.interpreted_workdays import interpret_deputy_workdays


def row(uid: str, start: str, end: str, role: str = "Director", **extra: object) -> dict[str, object]:
    return {
        "id": uid, "source_shift_id": uid, "date": start[:10],
        "title": f"[T-Test] {role}", "location_name": "Test Park",
        "role_label": role, "start_at": start, "end_at": end, **extra,
    }


# A: same date/location but two non-overlapping duties remain two workdays.
split = interpret_deputy_workdays([
    row("morning", "2026-08-25T07:00:00+12:00", "2026-08-25T10:00:00+12:00"),
    row("evening", "2026-08-25T15:00:00+12:00", "2026-08-25T18:00:00+12:00"),
])
assert len(split) == 2 and [item["rostered_start"] for item in split] == ["07:00", "15:00"]

# B: overlapping production and vehicle rows are one workday.
combined = interpret_deputy_workdays([
    row("production", "2026-08-26T09:00:00+12:00", "2026-08-26T17:00:00+12:00"),
    row("vehicle", "2026-08-26T08:30:00+12:00", "2026-08-26T17:30:00+12:00", "Rav91"),
])
assert len(combined) == 1 and combined[0]["production_position"] == "Director" and combined[0]["vehicle"] == "Rav91"

# C: an exact current-note allocation overrides structured Deputy for that person only.
note_override = interpret_deputy_workdays([
    row(
        "jayden", "2026-08-27T09:00:00+12:00", "2026-08-27T17:00:00+12:00",
        employee_name="Jayden Smith",
        description="Rav91 Jayden\n685 Josh",
    )
], structured_rows=[{
    "source_shift_id": 701, "employee_id": 7, "employee_name": "Jayden Smith", "area_name": "684",
    "start_at": "2026-08-27T08:30:00+12:00", "end_at": "2026-08-27T17:30:00+12:00",
}], person_identity={"deputy_employee_id": 7, "aliases": ["Jayden Smith", "Jayden"]})
assert note_override[0]["vehicle"] == "Rav91"
assert note_override[0]["field_provenance"]["vehicle"] == "current_roster_note"
unrelated = interpret_deputy_workdays([
    row(
        "jayden-2", "2026-08-28T09:00:00+12:00", "2026-08-28T17:00:00+12:00",
        employee_name="Jayden Smith", description="685 Josh",
    )
], structured_rows=[{
    "source_shift_id": 702, "employee_id": 7, "employee_name": "Jayden Smith", "area_name": "684",
    "start_at": "2026-08-28T08:30:00+12:00", "end_at": "2026-08-28T17:30:00+12:00",
}], person_identity={"deputy_employee_id": 7, "aliases": ["Jayden Smith", "Jayden"]})
assert unrelated[0]["vehicle"] == "684"

# D: personal timing presentation cannot alter roster-backed notification timing.
timing = interpret_deputy_workdays([
    row(
        "timing", "2026-08-29T09:30:00+12:00", "2026-08-29T17:00:00+12:00",
        personal_start_time="08:00", personal_finish_time="18:00",
    )
])
assert timing[0]["rostered_start"] == "09:30" and timing[0]["rostered_finish"] == "17:00"

# Rich management and schedule state survives extraction for raw-payload storage.
rich = {
    "id": 99, "employee": 7, "area": 3, "start": "2026-08-30T09:00:00+12:00",
    "end": "2026-08-30T17:00:00+12:00", "canEdit": False,
    "timesheet": {"id": 44, "status": "approved"}, "approvalRequired": True,
    "approvalStatus": "pending", "confirmationStatus": "confirmed",
    "confirmationNote": "fixture", "mealbreakDuration": 30,
    "mealbreakSlots": [{"start": "12:00", "end": "12:30"}],
    "warning": {"code": "overlap"}, "warningOverrideComment": "fixture only",
    "isOpen": False, "isPublished": True, "publicationStatus": "published",
}
management = _extract_management_shifts({"data": [rich]})[0]
schedule = _extract_schedule_shifts({"data": {"shifts": [rich]}, "metadata": {"employee": []}})[0]
required = {
    "canEdit", "timesheet", "approvalRequired", "approvalStatus", "confirmationStatus",
    "confirmationNote", "mealbreakDuration", "mealbreakSlots", "warning",
    "warningOverrideComment", "isOpen", "isPublished", "publicationStatus",
}
assert required <= management.keys() and required <= schedule.keys()
assert schedule["mealbreakSlots"][0]["start"] == "12:00" and management["timesheet"]["id"] == 44
init_db()
save_deputy_web_schedule({
    "captured_at": "2026-08-30T08:00:00+12:00",
    "locations": [{"id": 1, "name": "Test Park"}],
    "areas": [{"id": 3, "name": "Sound/VT", "locationId": 1}],
    "extracted_schedule_shifts": [schedule],
})
with get_connection() as conn:
    stored = json.loads(conn.execute(
        "SELECT raw_payload FROM deputy_schedule_shifts WHERE source_shift_id=99"
    ).fetchone()["raw_payload"])
assert stored["canEdit"] is False and stored["timesheet"]["id"] == 44
assert stored["mealbreakSlots"][0]["end"] == "12:30" and stored["warning"]["code"] == "overlap"

print("2026.08.20.2 closure fixtures passed")
