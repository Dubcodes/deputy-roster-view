from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="redeputy-notifications-"))
    for name in ("PUBLIC_APP_URL", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        os.environ.pop(name, None)
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "notifications.sqlite3"),
        APP_SECRET_KEY="notification-smoke-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
    )

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.database import create_app_user, get_connection, init_db, set_user_event_self_travel
    from app.main import app
    from app.notifications import (
        compact_push_payload,
        deliver_due_notifications,
        generate_due_notifications,
        notification_status,
        notification_preferences,
        push_open_position_eligible,
        queue_notification,
        queue_test_notification,
        register_push_subscription,
        save_notification_preferences,
        _webpush_send,
    )
    from app.push_identity import PRIVATE_KEY_FILE, ensure_push_identity
    from app.security import encrypt_text, hash_pin
    from app.workday_timing import effective_personal_hours, effective_personal_start

    init_db()
    first_identity = ensure_push_identity()
    second_identity = ensure_push_identity()
    if not first_identity.ready or first_identity.public_key != second_identity.public_key:
        raise AssertionError("The generated push identity did not persist across initialization.")
    decoded_public_key = base64.urlsafe_b64decode(first_identity.public_key + "==")
    if len(decoded_public_key) != 65 or decoded_public_key[0] != 4:
        raise AssertionError("The generated VAPID public key is not an uncompressed P-256 key.")
    private_key_text = (temp_dir / PRIVATE_KEY_FILE).read_text(encoding="ascii")
    if "PRIVATE KEY" not in private_key_text:
        raise AssertionError("The generated VAPID private key was not persisted in the data directory.")
    init_db()
    if ensure_push_identity().public_key != first_identity.public_key:
        raise AssertionError("A repeat migration regenerated the VAPID identity.")

    client = TestClient(app, base_url="https://deputyreviewer.example")
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
        raise AssertionError("Notification smoke signup failed.")
    settings = get_settings()
    with get_connection() as conn:
        owner_id = int(conn.execute("SELECT id FROM app_users ORDER BY id LIMIT 1").fetchone()["id"])
    no_device_test = client.post("/settings/notifications/test", data={}, follow_redirects=False)
    if no_device_test.status_code != 303 or "Enable+notifications+on+this+device" not in no_device_test.headers.get("location", ""):
        raise AssertionError("Immediate Test without a device did not return the friendly Settings result.")
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) n FROM notification_events WHERE event_type='test'").fetchone()["n"]:
            raise AssertionError("Immediate Test without a device left an orphan queued event.")

    legacy_due = datetime.now(get_settings().timezone).replace(microsecond=0)
    queue_notification(
        user_id=owner_id, event_type="legacy_no_device", title="Legacy", body="Queued",
        target_url="/settings", scheduled_at=legacy_due, revision="legacy-no-device",
    )
    delivery = deliver_due_notifications(legacy_due, lambda *_args: None)
    if delivery["delivered"] or delivery["failed"]:
        raise AssertionError("A no-device legacy event attempted delivery.")
    with get_connection() as conn:
        legacy = conn.execute("SELECT status,failure_summary FROM notification_events WHERE event_type='legacy_no_device'").fetchone()
    if not legacy or legacy["status"] != "failed" or "No active push devices" not in legacy["failure_summary"]:
        raise AssertionError("The worker did not safely close a queued event with zero subscriptions.")
    other = create_app_user(
        deputy_email="other@example.com",
        display_name="Other User",
        pin_hash=hash_pin("2345"),
        deputy_web_url="https://example.au.deputy.com/#/",
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
    app_origin = "https://deputyreviewer.example"
    first_id = register_push_subscription(owner_id, endpoint("owner-one"), "Phone", app_origin)
    second_id = register_push_subscription(owner_id, endpoint("owner-two"), "Laptop", app_origin)
    other_subscription_id = register_push_subscription(other_id, endpoint("other"), "Other phone", app_origin)
    try:
        register_push_subscription(owner_id, endpoint("other"), "Stolen phone", app_origin)
    except ValueError:
        pass
    else:
        raise AssertionError("A user could claim another account's existing push endpoint.")
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) n FROM push_subscriptions WHERE app_user_id=?", (owner_id,)).fetchone()["n"] != 2:
            raise AssertionError("One user could not retain multiple push devices.")
    route_subscription = client.post(
        "/settings/notifications/subscriptions",
        json={"subscription": endpoint("secure-route"), "app_origin": "https://attacker.example"},
        headers={"Origin": app_origin},
    )
    if route_subscription.status_code != 200 or route_subscription.json().get("app_origin"):
        raise AssertionError("Authenticated HTTPS subscription registration failed or exposed its origin.")
    secure_route_id = int(route_subscription.json()["subscription_id"])
    with get_connection() as conn:
        stored_origin = conn.execute(
            "SELECT app_origin FROM push_subscriptions WHERE id=?", (secure_route_id,)
        ).fetchone()["app_origin"]
    if stored_origin != app_origin:
        raise AssertionError(f"The authenticated request origin was not stored: {stored_origin!r}")
    malicious = client.post(
        "/settings/notifications/subscriptions",
        json={"subscription": endpoint("malicious")},
        headers={"Origin": "https://attacker.example"},
    )
    if malicious.status_code != 403:
        raise AssertionError("A cross-origin push subscription was accepted.")
    http_client = TestClient(app, base_url="http://deputyreviewer.example")
    http_client.cookies.update(client.cookies)
    insecure = http_client.post(
        "/settings/notifications/subscriptions",
        json={"subscription": endpoint("secure-route")},
        headers={"Origin": "http://deputyreviewer.example"},
    )
    if insecure.status_code != 400:
        raise AssertionError("A plain HTTP connection registered a push subscription.")
    with get_connection() as conn:
        retained_origin = conn.execute(
            "SELECT app_origin FROM push_subscriptions WHERE id=?", (secure_route_id,)
        ).fetchone()["app_origin"]
    if retained_origin != app_origin:
        raise AssertionError("Plain HTTP replaced a subscription's HTTPS app origin.")
    origin_cases = {
        "https://DEPUTYREVIEWER.EXAMPLE:443": 303,
        "https://deputyreviewer.example:444": 403,
        "http://deputyreviewer.example": 403,
        "https://deputyreviewer.example:bad": 403,
        "https://attacker.example": 403,
    }
    for supplied_origin, expected in origin_cases.items():
        response = client.post(
            "/settings/notifications/test", headers={"Origin": supplied_origin}, follow_redirects=False,
        )
        if response.status_code != expected:
            raise AssertionError(f"Origin {supplied_origin!r} returned {response.status_code}, expected {expected}.")
    if client.post(
        "/settings/notifications/test",
        headers={"Origin": app_origin, "Sec-Fetch-Site": "cross-site"}, follow_redirects=False,
    ).status_code != 403:
        raise AssertionError("Cross-site Fetch Metadata was accepted despite a matching Origin header.")
    explicit_default_client = TestClient(app, base_url="https://deputyreviewer.example:443")
    for cookie_name, cookie_value in client.cookies.items():
        explicit_default_client.cookies.set(cookie_name, cookie_value)
    if explicit_default_client.post(
        "/settings/notifications/test", headers={"Origin": app_origin}, follow_redirects=False,
    ).status_code != 303:
        raise AssertionError("An omitted HTTPS default port did not match an explicit request port 443.")
    lan_client = TestClient(app, base_url="http://192.168.0.10")
    for cookie_name, cookie_value in client.cookies.items():
        lan_client.cookies.set(cookie_name, cookie_value)
    if lan_client.post(
        "/settings/notifications/test", headers={"Origin": "http://192.168.0.10"}, follow_redirects=False,
    ).status_code != 303:
        raise AssertionError("A same-origin LAN HTTP mutation was rejected.")
    captured_send: dict[str, object] = {}
    original_pywebpush = sys.modules.get("pywebpush")
    sys.modules["pywebpush"] = types.SimpleNamespace(
        webpush=lambda **kwargs: captured_send.update(kwargs)
    )
    try:
        _webpush_send(
            {**endpoint("sender"), "endpoint": endpoint("sender")["endpoint"],
             "p256dh": "p256-sender", "auth": "auth-sender", "app_origin": app_origin},
            "{}",
            settings,
        )
    finally:
        if original_pywebpush is None:
            sys.modules.pop("pywebpush", None)
        else:
            sys.modules["pywebpush"] = original_pywebpush
    if captured_send.get("vapid_claims") != {"sub": app_origin}:
        raise AssertionError("Delivery did not use the subscription's HTTPS app origin as its VAPID subject.")
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
    if title != "Re-Deputy · Saturday 15 August" or body != "Ruakaka · Head On · 685 · 09:30":
        raise AssertionError(f"Compact reminder payload changed: {title!r}, {body!r}")
    _, changed = compact_push_payload(
        event_date="2026-08-15", location="Ruakaka", role="Head On", start="08:15", transport="Own way", change=True,
    )
    _, cancelled = compact_push_payload(
        event_date="2026-08-15", location="Ruakaka", role="Head On", start="", change=True, cancelled=True,
    )
    if changed != "Ruakaka · Head On · Own way · 08:15 · Change" or cancelled != "Ruakaka · Head On · Cancelled · Change":
        raise AssertionError("Change, own-way, truck-time, or cancellation payload is not compact.")

    save_notification_preferences(owner_id, {
        "enabled": True, "changes_enabled": True, "changes_within_24h": True,
        "night_before": True, "two_days_before": True, "one_hour_before": True, "admin_alerts": True, "weekly_digest": True,
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
               VALUES ('notify-taupo-vehicle','[Taupo] Rav91','2026-08-16T07:30:00+12:00',
                       '2026-08-16T09:30:00+12:00','2026-08-16',0,0,?,
                       '2026-08-12T18:15:00+12:00','2026-08-12T18:15:00+12:00','{}')""",
            (owner_id,),
        )
        conn.execute(
            """INSERT INTO shifts(source_uid,title,start_at,end_at,date,changed_since_viewed,
                                  deleted_from_source,owner_user_id,first_seen_at,last_synced_at,source_payload)
               VALUES ('notify-taupo-production','[Taupo] Sound/VT','2026-08-16T09:30:00+12:00',
                       '2026-08-16T19:30:00+12:00','2026-08-16',0,0,?,
                       '2026-08-12T18:15:00+12:00','2026-08-12T18:15:00+12:00',?)""",
            (owner_id, json.dumps({"description": "684 james grant lans\nRav Alf owner and josh"})),
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
    if not any("Ruakaka · Head On · Tender · 08:15" in value for value in bodies):
        raise AssertionError("Manual truck assignment reminder did not use the effective 08:15 start.")
    if not any("Te Aroha · Head On · Cancelled · Change" in value for value in bodies):
        raise AssertionError("Published manual assignment removal did not generate a cancellation.")
    if any("Draft" in value for value in bodies):
        raise AssertionError("A draft manual workday generated a push event.")

    one_hour = generate_due_notifications(datetime.fromisoformat("2026-08-16T06:31:00+12:00"))
    if one_hour["reminders"] < 1:
        raise AssertionError(f"One-hour workday reminder was not generated: {one_hour!r}")
    with get_connection() as conn:
        exact = [tuple(row) for row in conn.execute(
            "SELECT title,body FROM notification_events WHERE event_type='one_hour_before' AND event_date='2026-08-16'"
        ).fetchall()]
    if exact != [("Re-Deputy · Sunday 16 August", "Taupo · Sound/VT · Rav91 · 07:30")]:
        raise AssertionError(f"One-hour payload did not use interpreted workday order: {exact!r}")
    if generate_due_notifications(datetime.fromisoformat("2026-08-16T06:36:00+12:00"))["reminders"]:
        raise AssertionError("The contiguous Taupo workday generated a duplicate one-hour reminder.")

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

    # Both Making My Own Way transitions are sourced from the append-only audit
    # and target only active Admins who opted into Admin Alerts.
    alert_now = datetime.now(get_settings().timezone).replace(microsecond=0)
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_sync_state SET last_sync_at=?,last_status='success',sync_in_progress=0,updated_at=? WHERE user_id=?",
            (alert_now.isoformat(), alert_now.isoformat(), owner_id),
        )
        existing_person = conn.execute(
            "SELECT id FROM crew_people WHERE app_user_id=?", (owner_id,),
        ).fetchone()
        person_id = int(existing_person["id"]) if existing_person else int(conn.execute(
            "INSERT INTO crew_people(canonical_display_name,app_user_id,is_active,created_at,updated_at) VALUES('Alert Owner',?,1,?,?)",
            (owner_id, alert_now.isoformat(), alert_now.isoformat()),
        ).lastrowid)
    for state in (True, False):
        if not set_user_event_self_travel(
            user_id=owner_id, canonical_person_id=person_id, event_kind="manual_workday",
            event_id="alert-workday", event_date=(alert_now.date() + timedelta(days=1)).isoformat(),
            location_key="taupo", self_travel=state,
        ):
            raise AssertionError("Self-travel audit transition was not accepted.")
    alerts = generate_due_notifications(alert_now)
    if alerts["admin_alerts"] != 2 or generate_due_notifications(alert_now)["admin_alerts"]:
        raise AssertionError(f"Admin self-travel transitions were not deduped: {alerts!r}")
    with get_connection() as conn:
        alert_rows = [dict(row) for row in conn.execute(
            "SELECT app_user_id,body FROM notification_events WHERE event_type='admin_alert' AND workday_kind='self_travel' ORDER BY id"
        ).fetchall()]
    if len(alert_rows) != 2 or {row["app_user_id"] for row in alert_rows} != {owner_id}:
        raise AssertionError(f"Admin Alerts leaked to a non-Admin: {alert_rows!r}")
    if not any("enabled" in row["body"] for row in alert_rows) or not any("reversed" in row["body"] for row in alert_rows):
        raise AssertionError(f"Both self-travel transitions were not represented: {alert_rows!r}")

    with get_connection() as conn:
        stamp = alert_now.isoformat()
        stale_stamp = (alert_now - timedelta(hours=40)).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO user_sync_state(user_id,last_sync_at,last_status,last_message,sync_in_progress,updated_at)
               VALUES(?,?,?,?,0,?)""",
            (owner_id, stale_stamp, "error", "Primary Deputy web coverage failed", stamp),
        )
        conn.execute(
            "INSERT INTO deputy_event_coverage(date,area_location_id,event_start_at,status,conflict_count,reason,last_capture_at) VALUES(?,?,?,?,?,?,?)",
            ((alert_now.date() + timedelta(days=2)).isoformat(), 58, "07:30", "partial", 1, "personal/shared assignment conflict", stamp),
        )
        conn.execute(
            """INSERT INTO deputy_write_operations(
                   operation_uuid,app_user_id,tenant_host,deputy_user_id,deputy_employee_id,
                   permission_hash,permission_snapshot,stable_assignment_key,operation_type,
                   desired_state,status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("unknown-fixture", owner_id, "fixture.au.deputy.com", 1, 2, "hash", "{}", "fixture-assignment", "create", "{}", "unknown", stamp, stamp),
        )
        conn.execute(
            """INSERT INTO deputy_write_operations(
                   operation_uuid,app_user_id,tenant_host,deputy_user_id,deputy_employee_id,
                   permission_hash,permission_snapshot,stable_assignment_key,operation_type,
                   desired_state,status,error_class,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("ambiguous-fixture", owner_id, "fixture.au.deputy.com", 1, 2, "hash", "{}", "fixture-ambiguous", "update", "{}", "ambiguous", "readback_ambiguous", stamp, stamp),
        )
    serious_alerts = generate_due_notifications(alert_now)
    if serious_alerts["admin_alerts"] != 5 or generate_due_notifications(alert_now)["admin_alerts"]:
        raise AssertionError(f"Serious Admin Alerts were not queued and deduped once: {serious_alerts!r}")
    with get_connection() as conn:
        serious_kinds = {
            row["workday_kind"] for row in conn.execute(
                "SELECT workday_kind FROM notification_events WHERE event_type='admin_alert'"
            ).fetchall()
        }
    if not {"sync_failure", "sync_stale", "roster_integrity", "deputy_write_unknown"}.issubset(serious_kinds):
        raise AssertionError(f"Serious Admin Alert sources were incomplete: {serious_kinds!r}")
    with get_connection() as conn:
        healthy_stamp = (alert_now + timedelta(hours=1)).isoformat()
        conn.execute(
            "UPDATE user_sync_state SET last_sync_at=?,last_status='success',last_message='',updated_at=? WHERE user_id=?",
            (healthy_stamp, healthy_stamp, owner_id),
        )
    future = alert_now + timedelta(hours=38)
    if generate_due_notifications(future)["admin_alerts"] != 1:
        raise AssertionError("A healthy sync did not reset the stale-sync alert episode.")

    service_worker = (ROOT_DIR / "app" / "static" / "service-worker.js").read_text(encoding="utf-8")
    if "value.startsWith('//')" not in service_worker or "parsed.origin === self.location.origin" not in service_worker:
        raise AssertionError("Service-worker notification links are not restricted to safe local URLs.")
    settings_template = (ROOT_DIR / "app" / "templates" / "settings.html").read_text(encoding="utf-8")
    if "Notification.requestPermission()" not in settings_template.split("data-enable-push", 2)[-1]:
        raise AssertionError("Notification permission is not tied to the explicit Enable action.")
    settings_html = client.get("/settings").text
    admin_html = client.get("/admin").text
    if first_identity.public_key not in settings_html:
        raise AssertionError("The authenticated Settings flow did not receive the generated public key.")
    for output in (settings_html, admin_html, route_subscription.text):
        if private_key_text in output or "BEGIN PRIVATE KEY" in output:
            raise AssertionError("Private VAPID material was exposed to a browser response.")
    if private_key_text in json.dumps(notification_status(owner_id), sort_keys=True):
        raise AssertionError("Private VAPID material was exposed by notification status.")
    deployment_text = "\n".join(
        (ROOT_DIR / name).read_text(encoding="utf-8")
        for name in ("docker-compose.yml", ".env.example")
    )
    if any(name in deployment_text for name in ("PUBLIC_APP_URL", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT")):
        raise AssertionError("Deployment files still require notification-specific environment variables.")

    old_public_key = ensure_push_identity().public_key
    (temp_dir / PRIVATE_KEY_FILE).unlink()
    replacement = ensure_push_identity()
    if not replacement.ready or replacement.public_key == old_public_key:
        raise AssertionError("A genuinely missing VAPID private key was not safely replaced.")
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) n FROM push_subscriptions WHERE active=1").fetchone()["n"]:
            raise AssertionError("Subscriptions remained active after their VAPID identity was lost.")
    if "enable notifications again" not in replacement.diagnostic.lower():
        raise AssertionError("Key loss did not provide a concise re-enable diagnostic.")

    print("notifications smoke ok")


if __name__ == "__main__":
    main()
