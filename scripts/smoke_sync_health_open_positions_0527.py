from __future__ import annotations

"""0.5.27 Day open-position preference regression coverage."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    temporary = Path(tempfile.mkdtemp(prefix="redeputy-0527-open-positions-"))
    os.environ.update(DATA_DIR=str(temporary), DB_PATH=str(temporary / "app.sqlite3"), APP_SECRET_KEY="open-position-smoke", SIGNUP_ENABLED="true", COOKIE_SECURE="false")
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from app.database import get_connection, init_db, save_deputy_web_schedule
    from app.main import app
    init_db()
    with get_connection() as conn:
        column = next(row for row in conn.execute("PRAGMA table_info(app_users)") if row[1] == "show_open_positions_on_day")
        assert str(column[4]) == "0", tuple(column)
    client = TestClient(app, base_url="http://testserver")
    signup = client.post("/signup", data={"deputy_web_url": "https://fixture.au.deputy.com/#/", "deputy_email": "fixture@example.test", "display_name": "Fixture User", "deputy_password": "fixture-password", "pin": "1234", "pin_confirm": "1234", "next_url": "/month"}, follow_redirects=False)
    if signup.status_code != 303 or "signup" in signup.headers.get("location", ""):
        raise AssertionError(f"fixture signup failed: {signup.status_code} {signup.headers.get('location', '')}")
    with get_connection() as conn:
        user_id = int(conn.execute("SELECT id FROM app_users").fetchone()[0])
        assert int(conn.execute("SELECT show_open_positions_on_day FROM app_users WHERE id=?", (user_id,)).fetchone()[0]) == 0
    day = "2030-01-02"
    save_deputy_web_schedule({
        "captured_at": f"{day}T00:00:00+12:00",
        "areas": [{"id": 10, "name": "Director", "locationId": 69}, {"id": 11, "name": "Side 1", "locationId": 69}],
        "locations": [{"id": 69, "name": "T-Te Rapa"}],
        "extracted_shifts": [{"id": 101, "area": 10, "areaName": "Director", "areaLocationId": 69, "location": 69, "employee": 19, "employeeName": "Fixture User", "start": f"{day}T09:00:00+12:00", "end": f"{day}T17:00:00+12:00", "isPublished": True}],
        "extracted_schedule_shifts": [{"id": 201, "area": 11, "areaName": "Side 1", "areaLocationId": 69, "location": 69, "employee": 0, "employeeName": "", "start": f"{day}T09:00:00+12:00", "end": f"{day}T17:00:00+12:00", "isPublished": True, "isOpen": True}],
        "own_roster_coverage": [], "schedule_coverage": [],
    }, owner_user_id=user_id)
    hidden = client.get(f"/day/{day}")
    assert hidden.status_code == 200 and "Available / Open Shifts" not in hidden.text
    assert "Open shift" in hidden.text
    settings = client.get("/settings")
    assert "Show open positions at top of race days" in settings.text and "Side 1" in settings.text
    saved = client.post("/settings/day-view", data={"show_open_positions_on_day": "1"}, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert saved.status_code == 303
    with get_connection() as conn:
        saved_value = conn.execute("SELECT show_open_positions_on_day FROM app_users WHERE id=?", (user_id,)).fetchone()[0]
    visible = client.get(f"/day/{day}")
    assert "Available / Open Shifts" in visible.text and "Open shift" in visible.text, (saved.headers.get("location"), saved_value, visible.text[:500])
    legacy_path = temporary / "legacy.sqlite3"
    with sqlite3.connect(legacy_path) as legacy:
        legacy.execute("CREATE TABLE app_users (id INTEGER PRIMARY KEY, deputy_email TEXT, display_name TEXT, display_theme TEXT, pin_hash TEXT, deputy_web_url TEXT, is_admin INTEGER, is_active INTEGER, created_at TEXT, updated_at TEXT)")
        legacy.execute("INSERT INTO app_users VALUES (1,'legacy@example.test','Legacy','jade','x','https://example.test',0,1,'','')")
    os.environ["DB_PATH"] = str(legacy_path)
    from app.config import get_settings
    get_settings.cache_clear(); init_db()
    with get_connection() as conn:
        assert int(conn.execute("SELECT show_open_positions_on_day FROM app_users WHERE id=1").fetchone()[0]) == 0
    print("0.5.27 sync-health and open-position preference smoke passed")


if __name__ == "__main__":
    main()
