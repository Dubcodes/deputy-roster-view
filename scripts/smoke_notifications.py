from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="redeputy-notifications-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "notifications.sqlite3"),
        APP_SECRET_KEY="notification-smoke-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
        VAPID_PUBLIC_KEY="fixture-public-key",
        VAPID_PRIVATE_KEY="fixture-private-key",
        VAPID_SUBJECT="mailto:notifications@example.com",
    )

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.database import create_app_user, get_connection, init_db
    from app.main import app
    from app.notifications import (
        compact_push_payload,
        deliver_due_notifications,
        generate_due_notifications,
        notification_preferences,
        push_open_position_eligible,
        queue_notification,
        queue_test_notification,
        register_push_subscription,
        save_notification_preferences,
    )
    from app.security import encrypt_text, hash_pin
    from app.workday_timing import effective_personal_hours, effective_personal_start

    init_db()
    client = TestClient(app)
    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://example.deputy.com/#/",
            "deputy_email": "owner@example.com",
            "deputy_password": "password",
            "pin": "1234",
            "pin_confirm": "1234",
            "next_url": "/month",
        },
        follow_redirects=False,
    )
    if signup.status_code != 303:
        raise AssertionError("Notification smoke signup failed.")
    settings = get_settings()
    with get_connection() as conn:
        owner_id = int(conn.execute("SELECT id FROM app_users ORDER BY id LIMIT 1").fetchone()["id"])
    other = create_app_user(
        deputy_email="other@example.com",
        display_name="Other User",
        pin_hash=hash_pin("2345"),
        deputy_web_url="https://example.deputy.com/#/",
        encrypted_email=encrypt_text("other@example.com", settings),
        encrypted_password=encrypt_text("password", settings),
    )
    other_id = int(other["id"])

    workday = {"day_type": "race_day", "office_start": "08:30", "truck_start_offset_minutes": 15}
    tender = {"vehicle_is_truck": True, "vehicle_label": "Tender"}
    ordinary = {"vehicle_is_truck": False, "vehicle_label": "684"}
    if effective_personal_start(workday, tender) != "08:15" or effective_personal_start(workday, ordinary) != "08:30":
        raise AssertionError("Truck effective start did not distinguish Tender from 684.")
    if effective_personal_hours(workday, tender, 8.0) != 8.25:
        raise AssertionError("Truck early start did not add 15 minutes to personal hours.")
    if effective_personal_start({**workday, "truck_start_offset_minutes": 0}, tender) != "08:30":
        raise AssertionError("Disabled truck early start still changed personal time.")
    if effective_personal_start(workday, {"personal_start_time": "08:00", **tender}) != "08:00":
        raise AssertionError("Explicit personal start did not retain precedence.")

    endpoint = lambda suffix: {
        "endpoint": f"https://push.example/{suffix}",
        "keys": {"p256dh": f"p256-{suffix}", "auth": f"auth-{suffix}"},
    }
    first_id = register_push_subscription(owner_id, endpoint("owner-one"), "Phone")
    second_id = register_push_subscription(owner_id, endpoint("owner-two"), "Laptop")
    other_subscription_id = register_push_subscription(other_id, endpoint("other"), "Other phone")
    try:
        register_push_subscription(owner_id, endpoint("other"), "Stolen phone")
    except ValueError:
        pass
    else:
        raise AssertionError("A user could claim another account's existing push endpoint.")
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) n FROM push_subscriptions WHERE app_user_id=?", (owner_id,)).fetchone()["n"] != 2:
            raise AssertionError("One user could not retain multiple push devices.")
    denied = client.delete(f"/settings/notifications/subscriptions/{other_subscription_id}")
    allowed = client.delete(f"/settings/notifications/subscriptions/{first_id}")
    if denied.json().get("ok") or not allowed.json().get("ok"):
        raise AssertionError("Push subscription route did not enforce owner-only removal.")
    preferences = client.post(
        "/settings/notifications/preferences",
        data={"enabled": "1", "night_before": "1", "app_user_id": str(other_id), "reminder_time": "19:00"},
        follow_redirects=False,
    )
    if preferences.status_code != 303 or not notification_preferences(owner_id)["enabled"] or notification_preferences(other_id)["enabled"]:
        raise AssertionError("Notification preferences were not scoped to the authenticated user.")

    title, body = compact_push_payload(
        event_date="2026-08-15", location="Ruakaka", role="Head On", start="09:30", transport="685",
    )
    if title != "Re-Deputy · Saturday 15 August" or body != "Ruakaka · Head On · 09:30 · 685":
        raise AssertionError(f"Compact reminder payload changed: {title!r}, {body!r}")
    _, changed = compact_push_payload(
        event_date="2026-08-15", location="Ruakaka", role="Head On", start="08:15", transport="Own way", change=True,
    )
    _, cancelled = compact_push_payload(
        event_date="2026-08-15", location="Ruakaka", role="Head On", start="", change=True, cancelled=True,
    )
    if changed != "Ruakaka · Head On · 08:15 · Own way · Change" or cancelled != "Ruakaka · Head On · Cancelled · Change":
        raise AssertionError("Change, own-way, truck-time, or cancellation payload is not compact.")

    save_notification_preferences(owner_id, {
        "enabled": True, "changes_enabled": True, "changes_within_24h": True,
        "night_before": True, "two_days_before": True, "weekly_digest": True,
        "open_positions_month": False, "reminder_time": "19:00",
    })
    with get_connection() as conn:
        conn.execute("UPDATE notification_preferences SET updated_at='2026-08-12T10:00:00+12:00' WHERE app_user_id=?", (owner_id,))
        conn.execute(
            """INSERT INTO shifts(source_uid,title,start_at,end_at,date,changed_since_viewed,
                                  deleted_from_source,owner_user_id,last_changed_at,last_synced_at,source_payload)
               VALUES ('notify-deputy','[T-Te Rapa] DIR','2026-08-14T09:30:00+12:00',
                       '2026-08-14T18:00:00+12:00','2026-08-14',1,0,?,
                       '2026-08-12T18:00:00+12:00','2026-08-12T18:00:00+12:00','{}')""",
            (owner_id,),
        )
        conn.execute(
            """INSERT INTO shifts(source_uid,title,start_at,end_at,date,changed_since_viewed,
                                  deleted_from_source,owner_user_id,first_seen_at,last_synced_at,source_payload)
               VALUES ('notify-new','[T-Matamata] SVT','2026-08-15T09:00:00+12:00',
                       '2026-08-15T18:00:00+12:00','2026-08-15',0,0,?,
                       '2026-08-12T18:15:00+12:00','2026-08-12T18:15:00+12:00','{}')""",
            (owner_id,),
        )
        manual_snapshot = {
            "roster_date": "2026-08-13", "track_label": "Ruakaka", "day_type": "race_day",
            "office_start": "08:30", "truck_start_offset_minutes": 15,
            "assignments": [{
                "user_id": owner_id, "role_label": "Head On", "assignment_state": "assigned",
                "transport_mode": "vehicle", "vehicle_label": "Tender", "vehicle_is_truck": True,
            }],
        }
        snapshot_text = json.dumps(manual_snapshot)
        cursor = conn.execute(
            """INSERT INTO roster_days(roster_date,track_key,track_label,day_type,status,published_snapshot,
                                       published_at,created_at,updated_at)
               VALUES ('2026-08-13','ruakaka','Ruakaka','race_day','published',?,
                       '2026-08-12T18:00:00+12:00','2026-08-12T18:00:00+12:00','2026-08-12T18:00:00+12:00')""",
            (snapshot_text,),
        )
        manual_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO roster_day_versions(roster_day_id,version_number,snapshot,published_by_user_id,published_at) VALUES (?,?,?,?,?)",
            (manual_id, 1, snapshot_text, owner_id, "2026-08-12T18:00:00+12:00"),
        )
        cancelled_old = {**manual_snapshot, "roster_date": "2026-08-13", "track_label": "Te Aroha"}
        cancelled_new = {**cancelled_old, "assignments": []}
        cursor = conn.execute(
            """INSERT INTO roster_days(roster_date,track_key,track_label,day_type,status,published_snapshot,
                                       published_at,created_at,updated_at)
               VALUES ('2026-08-13','te-aroha','Te Aroha','race_day','published',?,
                       '2026-08-12T18:30:00+12:00','2026-08-12T17:00:00+12:00','2026-08-12T18:30:00+12:00')""",
            (json.dumps(cancelled_new),),
        )
        cancelled_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO roster_day_versions(roster_day_id,version_number,snapshot,published_by_user_id,published_at) VALUES (?,?,?,?,?)",
            (cancelled_id, 1, json.dumps(cancelled_old), owner_id, "2026-08-12T17:00:00+12:00"),
        )
        conn.execute(
            "INSERT INTO roster_day_versions(roster_day_id,version_number,snapshot,published_by_user_id,published_at) VALUES (?,?,?,?,?)",
            (cancelled_id, 2, json.dumps(cancelled_new), owner_id, "2026-08-12T18:30:00+12:00"),
        )
        conn.execute(
            """INSERT INTO roster_days(roster_date,track_key,track_label,day_type,status,published_snapshot,created_at,updated_at)
               VALUES ('2026-08-13','draft','Draft','race_day','draft','', '', '')"""
        )

    now = datetime.fromisoformat("2026-08-12T19:01:00+12:00")
    first = generate_due_notifications(now)
    second = generate_due_notifications(now)
    if first["reminders"] < 2 or first["changes"] < 4 or any(second.values()):
        raise AssertionError(f"Notification generation or deduplication failed: {first!r}, {second!r}")
    with get_connection() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM notification_events ORDER BY id").fetchall()]
    bodies = [str(row["body"]) for row in rows]
    if not any("Ruakaka · Head On · 08:15 · Tender" in value for value in bodies):
        raise AssertionError("Manual truck assignment reminder did not use the effective 08:15 start.")
    if not any("Te Aroha · Head On · Cancelled · Change" in value for value in bodies):
        raise AssertionError("Published manual assignment removal did not generate a cancellation.")
    if any("Draft" in value for value in bodies):
        raise AssertionError("A draft manual workday generated a push event.")

    digest = generate_due_notifications(datetime.fromisoformat("2026-08-10T19:01:00+12:00"))
    if digest["digests"] != 1:
        raise AssertionError(f"Weekly digest was not generated once when changes existed: {digest!r}")
    if generate_due_notifications(datetime.fromisoformat("2026-08-10T19:06:00+12:00"))["digests"]:
        raise AssertionError("Weekly digest repeated during the same week.")

    base_position = {"can_apply": True, "eligible": True, "conflicts": [], "area_display": "Gimbal"}
    if not push_open_position_eligible({**base_position, "workday_team_id": 1}):
        raise AssertionError("A team-classified eligible position was excluded from push.")
    for rejected in (
        base_position,
        {**base_position, "workday_team_id": 2, "eligible": False},
        {**base_position, "workday_team_id": 1, "area_display": "TBC"},
        {**base_position, "workday_team_id": 1, "conflicts": [{"reason": "overlap"}]},
    ):
        if push_open_position_eligible(rejected):
            raise AssertionError(f"An unclassified, other-team, TBC, or conflicting position was pushed: {rejected!r}")

    due = now + timedelta(hours=1)
    if not queue_test_notification(owner_id, due):
        raise AssertionError("Scheduled test was not persisted.")
    delivered_payloads: list[str] = []
    sender = lambda _subscription, payload, _settings: delivered_payloads.append(payload)
    deliver_due_notifications(now, sender)
    if any("Notifications are working" in value for value in delivered_payloads):
        raise AssertionError("A future scheduled test was delivered early.")
    deliver_due_notifications(due + timedelta(minutes=1), sender)
    if not any("Notifications are working" in value for value in delivered_payloads):
        raise AssertionError("A persisted scheduled test was not delivered in the background pass.")

    duplicate_due = now
    first_queue = queue_notification(
        user_id=owner_id, event_type="fixture", title="Re-Deputy", body="Fixture", target_url="/day/2026-08-15",
        scheduled_at=duplicate_due, revision="same-revision",
    )
    second_queue = queue_notification(
        user_id=owner_id, event_type="fixture", title="Re-Deputy", body="Fixture", target_url="/day/2026-08-15",
        scheduled_at=duplicate_due, revision="same-revision",
    )
    changed_queue = queue_notification(
        user_id=owner_id, event_type="fixture", title="Re-Deputy", body="Changed", target_url="/day/2026-08-15",
        scheduled_at=duplicate_due, revision="changed-revision",
    )
    if (first_queue, second_queue, changed_queue) != (True, False, True):
        raise AssertionError("Stable revisions did not dedupe while changed revisions remained eligible.")

    service_worker = (ROOT_DIR / "app" / "static" / "service-worker.js").read_text(encoding="utf-8")
    if "value.startsWith('//')" not in service_worker or "parsed.origin === self.location.origin" not in service_worker:
        raise AssertionError("Service-worker notification links are not restricted to safe local URLs.")
    settings_template = (ROOT_DIR / "app" / "templates" / "settings.html").read_text(encoding="utf-8")
    if "Notification.requestPermission()" not in settings_template.split("data-enable-push", 2)[-1]:
        raise AssertionError("Notification permission is not tied to the explicit Enable action.")

    print("notifications smoke ok")


if __name__ == "__main__":
    main()
