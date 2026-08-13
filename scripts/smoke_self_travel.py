from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="redeputy-self-travel-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "self-travel.sqlite3"),
        APP_SECRET_KEY="self-travel-smoke-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
    )

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.database import (
        create_app_user,
        get_connection,
        get_user_canonical_person_id,
        init_db,
    )
    from app.main import app, apply_shift_self_travel
    from app.security import encrypt_text, hash_pin

    init_db()
    client = TestClient(app)
    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://example.au.deputy.com/#/",
            "deputy_email": "owner@example.com",
            "deputy_password": "password",
            "pin": "1234",
            "pin_confirm": "1234",
            "next_url": "/month",
        },
        follow_redirects=False,
    )
    if signup.status_code != 303:
        raise AssertionError("Owner signup failed.")

    settings = get_settings()
    other = create_app_user(
        deputy_email="other@example.com",
        display_name="Other User",
        pin_hash=hash_pin("2345"),
        deputy_web_url="https://example.au.deputy.com/#/",
        encrypted_email=encrypt_text("other@example.com", settings),
        encrypted_password=encrypt_text("password", settings),
    )
    init_db()
    with get_connection() as conn:
        owner_id = int(conn.execute("SELECT id FROM app_users WHERE deputy_email = 'owner@example.com'").fetchone()["id"])
        event_date = (datetime.now(settings.timezone).date() + timedelta(days=7)).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO shifts (
                source_uid, title, location, start_at, end_at, date,
                raw_hours, paid_hours, owner_user_id, changed_since_viewed
            ) VALUES (?, ?, ?, ?, ?, ?, 8, 8, ?, 0)
            """,
            (
                "self-travel-fixture",
                "[T-Te Rapa] Camera",
                "Te Rapa",
                f"{event_date}T09:00:00+12:00",
                f"{event_date}T17:00:00+12:00",
                event_date,
                owner_id,
            ),
        )
        shift_id = int(cursor.lastrowid)
        past_date = (datetime.now(settings.timezone).date() - timedelta(days=7)).isoformat()
        past_cursor = conn.execute(
            """
            INSERT INTO shifts (
                source_uid, title, location, start_at, end_at, date,
                raw_hours, paid_hours, owner_user_id, changed_since_viewed
            ) VALUES (?, ?, ?, ?, ?, ?, 4, 4, ?, 0)
            """,
            (
                "past-self-travel-fixture",
                "[T-Travel] Travel then Overnighter",
                "Travel",
                f"{past_date}T13:00:00+12:00",
                f"{past_date}T17:00:00+12:00",
                past_date,
                owner_id,
            ),
        )
        past_shift_id = int(past_cursor.lastrowid)

    person_id = get_user_canonical_person_id(owner_id)
    if person_id is None:
        raise AssertionError("Owner was not linked to a canonical crew identity.")

    enable = client.post(
        f"/day/{event_date}/self-travel",
        data={"event_kind": "deputy_shift", "event_id": str(shift_id), "self_travel": "1"},
        follow_redirects=False,
    )
    if enable.status_code != 303 or "saved+locally" not in enable.headers.get("location", ""):
        raise AssertionError("The owner could not enable the local self-travel preference.")

    first_overlay = [{"id": shift_id, "header_vehicle_label": "685"}]
    apply_shift_self_travel(first_overlay, event_date, event_date, owner_id)
    if first_overlay[0]["header_vehicle_label"] != "Making own way" or first_overlay[0]["underlying_vehicle_label"] != "685":
        raise AssertionError(f"Self-travel did not overlay the current roster vehicle: {first_overlay!r}")
    updated_overlay = [{"id": shift_id, "header_vehicle_label": "684"}]
    apply_shift_self_travel(updated_overlay, event_date, event_date, owner_id)
    if updated_overlay[0]["underlying_vehicle_label"] != "684":
        raise AssertionError("A later Deputy vehicle update was not retained under the local overlay.")

    other_client = TestClient(app)
    login = other_client.post(
        "/login",
        data={"deputy_email": "other@example.com", "pin": "2345", "next_url": "/month"},
        follow_redirects=False,
    )
    if login.status_code != 303:
        raise AssertionError("Second-user login failed.")
    forbidden = other_client.post(
        f"/day/{event_date}/self-travel",
        data={"event_kind": "deputy_shift", "event_id": str(shift_id), "self_travel": "1"},
    )
    if forbidden.status_code != 403:
        raise AssertionError("Another user was able to alter the owner's travel preference.")

    historical = client.post(
        f"/day/{past_date}/self-travel",
        data={"event_kind": "deputy_shift", "event_id": str(past_shift_id), "self_travel": "1"},
        follow_redirects=False,
    )
    if historical.status_code != 303 or "Past+workday" not in historical.headers.get("location", ""):
        raise AssertionError("An ordinary user could alter a completed historical workday.")

    disable = client.post(
        f"/day/{event_date}/self-travel",
        data={"event_kind": "deputy_shift", "event_id": str(shift_id), "self_travel": "0"},
        follow_redirects=False,
    )
    if disable.status_code != 303:
        raise AssertionError("The owner could not restore the roster transport.")
    restored = [{"id": shift_id, "header_vehicle_label": "684"}]
    apply_shift_self_travel(restored, event_date, event_date, owner_id)
    if restored[0]["header_vehicle_label"] != "684" or restored[0]["self_travel"]:
        raise AssertionError("Disabling self-travel did not reveal the latest roster vehicle.")

    with get_connection() as conn:
        shift = conn.execute("SELECT changed_since_viewed, title, location FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        travel_shift = conn.execute("SELECT title, changed_since_viewed FROM shifts WHERE id = ?", (past_shift_id,)).fetchone()
        audit = conn.execute(
            "SELECT old_self_travel, new_self_travel FROM user_event_transport_preference_audit ORDER BY id"
        ).fetchall()
        preferences = conn.execute(
            "SELECT self_travel, source FROM user_event_transport_preferences WHERE event_id = ?",
            (str(shift_id),),
        ).fetchone()
    if dict(shift) != {"changed_since_viewed": 0, "title": "[T-Te Rapa] Camera", "location": "Te Rapa"}:
        raise AssertionError("Local self-travel altered Deputy shift evidence or change state.")
    if dict(travel_shift) != {"title": "[T-Travel] Travel then Overnighter", "changed_since_viewed": 0}:
        raise AssertionError("The local preference altered a separate Deputy travel record.")
    if [(row["old_self_travel"], row["new_self_travel"]) for row in audit] != [(0, 1), (1, 0)]:
        raise AssertionError(f"Self-travel audit was incomplete: {[dict(row) for row in audit]!r}")
    if preferences is None or int(preferences["self_travel"]) != 0 or preferences["source"] != "user":
        raise AssertionError("The reversible preference was not retained as a local user record.")
    if int(other["id"]) == owner_id:
        raise AssertionError("Fixture users were not distinct.")

    print("self travel smoke ok")


if __name__ == "__main__":
    main()
