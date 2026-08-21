from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, time, timedelta
from typing import Callable

from .config import Settings, get_settings
from .database import crew_identity_records, get_connection, get_settled_integrity_state, latest_relevant_sync_generation, list_open_workday_positions, resolve_workday_snapshot_assignments, save_settled_integrity_state
from .push_identity import ensure_push_identity
from .interpreted_workdays import interpret_deputy_workdays
from .workday_timing import effective_rostered_start
from .workday_builder import canonical_role_key


DEFAULT_PREFERENCES = {
    "enabled": 0,
    "changes_enabled": 1,
    "changes_within_24h": 1,
    "night_before": 1,
    "two_days_before": 0,
    "one_hour_before": 1,
    "admin_alerts": 0,
    "weekly_digest": 0,
    "open_positions_month": 0,
    "reminder_time": "19:00",
}
_runner_lock = threading.Lock()
INTEGRITY_SETTLE_SECONDS = 90


def notification_preferences(user_id: int) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM notification_preferences WHERE app_user_id=?", (user_id,)).fetchone()
    return {**DEFAULT_PREFERENCES, **(dict(row) if row else {}), "app_user_id": user_id}


def save_notification_preferences(user_id: int, values: dict[str, object]) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    reminder = str(values.get("reminder_time") or "19:00")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reminder):
        reminder = "19:00"
    fields = {key: 1 if values.get(key) else 0 for key in DEFAULT_PREFERENCES if key != "reminder_time"}
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO notification_preferences(
                app_user_id,enabled,changes_enabled,changes_within_24h,night_before,
                two_days_before,one_hour_before,admin_alerts,weekly_digest,open_positions_month,reminder_time,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(app_user_id) DO UPDATE SET enabled=excluded.enabled,
                changes_enabled=excluded.changes_enabled,changes_within_24h=excluded.changes_within_24h,
                night_before=excluded.night_before,two_days_before=excluded.two_days_before,
                one_hour_before=excluded.one_hour_before,admin_alerts=excluded.admin_alerts,
                weekly_digest=excluded.weekly_digest,open_positions_month=excluded.open_positions_month,
                reminder_time=excluded.reminder_time,updated_at=excluded.updated_at
            """,
            (user_id, fields["enabled"], fields["changes_enabled"], fields["changes_within_24h"],
             fields["night_before"], fields["two_days_before"], fields["one_hour_before"],
             fields["admin_alerts"], fields["weekly_digest"],
             fields["open_positions_month"], reminder, now),
        )


def register_push_subscription(
    user_id: int,
    subscription: dict[str, object],
    description: str = "",
    app_origin: str = "",
) -> int:
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not endpoint.startswith("https://") or not p256dh or not auth or not app_origin.startswith("https://"):
        raise ValueError("A valid Web Push subscription is required.")
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT app_user_id FROM push_subscriptions WHERE endpoint=?", (endpoint,)
        ).fetchone()
        if existing is not None and int(existing["app_user_id"]) != user_id:
            raise ValueError("This push subscription belongs to another account.")
        conn.execute(
            """
            INSERT INTO push_subscriptions(app_user_id,endpoint,p256dh,auth,app_origin,device_description,active,created_at,updated_at)
            VALUES (?,?,?,?,?,?,1,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh,
                auth=excluded.auth,app_origin=CASE
                    WHEN excluded.app_origin LIKE 'https://%' THEN excluded.app_origin
                    ELSE push_subscriptions.app_origin
                END,device_description=excluded.device_description,active=1,revoked_at=NULL,updated_at=excluded.updated_at
            """,
            (user_id, endpoint, p256dh, auth, app_origin, description[:240], now, now),
        )
        row = conn.execute("SELECT id FROM push_subscriptions WHERE endpoint=? AND app_user_id=?", (endpoint, user_id)).fetchone()
    if not row:
        raise ValueError("Subscription ownership could not be established.")
    return int(row["id"])


def revoke_push_subscription(user_id: int, subscription_id: int) -> bool:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE push_subscriptions SET active=0,revoked_at=?,updated_at=? WHERE id=? AND app_user_id=?",
            (now, now, subscription_id, user_id),
        )
    return bool(result.rowcount)


def list_user_push_subscriptions(user_id: int) -> list[dict[str, object]]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id,device_description,active,created_at,updated_at,last_success_at,last_failure_at,failure_count FROM push_subscriptions WHERE app_user_id=? ORDER BY id DESC",
            (user_id,),
        ).fetchall()]


def compact_push_payload(*, event_date: date | str | None, location: str, role: str,
                         start: str, transport: str = "", change: bool = False,
                         cancelled: bool = False, test: bool = False) -> tuple[str, str]:
    if test:
        return "Re-Deputy · Test", "Notifications are working."
    day = date.fromisoformat(str(event_date)) if not isinstance(event_date, date) else event_date
    title = _portable_title(day)
    parts = [location or "Work day", role or "Shift"]
    if cancelled:
        parts.append("Cancelled")
    if transport and not cancelled:
        parts.append(transport)
    if start and not cancelled:
        parts.append(start)
    if change:
        parts.append("Change")
    return title, " · ".join(parts)


def _portable_title(event_date: date) -> str:
    return f"Re-Deputy · {event_date.strftime('%A')} {event_date.day} {event_date.strftime('%B')}"


def _group_deputy_notification_workdays(
    shifts: list[dict[str, object]], user_id: int | None = None,
) -> list[dict[str, object]]:
    """Consume the same structured/note-aware final interpretation as the day page."""
    structured_rows: list[dict[str, object]] = []
    preceding_rows: list[dict[str, object]] = []
    identity: dict[str, object] = {}
    dates = sorted({str(row.get("date") or "") for row in shifts if row.get("date")})
    if user_id is not None and dates:
        previous_date = (date.fromisoformat(dates[0]) - timedelta(days=1)).isoformat()
        with get_connection() as conn:
            structured_rows = [dict(row) for row in conn.execute(
                "SELECT * FROM deputy_schedule_shifts WHERE date BETWEEN ? AND ? ORDER BY start_at,source_shift_id",
                (previous_date, dates[-1]),
            ).fetchall()]
            preceding_rows = [dict(row) for row in conn.execute(
                "SELECT * FROM shifts WHERE owner_user_id=? AND date=? AND deleted_from_source=0 ORDER BY start_at,id",
                (user_id, previous_date),
            ).fetchall()]
            person = conn.execute(
                "SELECT id,deputy_employee_id,canonical_display_name FROM crew_people WHERE app_user_id=? AND is_active=1",
                (user_id,),
            ).fetchone()
            if person:
                alias_rows = conn.execute(
                    "SELECT alias FROM crew_aliases WHERE person_id=?", (person["id"],),
                ).fetchall()
                identity = {
                    "deputy_employee_id": person["deputy_employee_id"],
                    "aliases": [person["canonical_display_name"], *(row["alias"] for row in alias_rows)],
                }
    result = []
    for workday in interpret_deputy_workdays(
        shifts, structured_rows=structured_rows, person_identity=identity,
        identity_records=crew_identity_records(), preceding_rows=preceding_rows,
        preceding_structured_rows=structured_rows,
    ):
        evidence = list(workday.get("structured_deputy_evidence") or [])
        item = dict(evidence[0]) if evidence else {}
        item.update({
            "date": workday["date"],
            "notification_location": workday["location"],
            "notification_role": workday["production_position"],
            "notification_transport": workday["vehicle"],
            "notification_rostered_start": workday["rostered_start"],
            "notification_workday_id": workday["logical_workday_id"],
            "changed_since_viewed": max((int(row.get("changed_since_viewed") or 0) for row in evidence), default=0),
            "deleted_from_source": int(bool(evidence) and all(int(row.get("deleted_from_source") or 0) for row in evidence)),
        })
        for field in ("first_seen_at", "last_changed_at", "last_synced_at"):
            item[field] = max((str(row.get(field) or "") for row in evidence), default="")
        result.append(item)
    return result


def queue_notification(*, user_id: int, event_type: str, workday_kind: str = "",
                       workday_id: str = "", event_date: str = "", title: str,
                       body: str, target_url: str, scheduled_at: datetime,
                       revision: str) -> bool:
    safe_url = target_url if target_url.startswith("/") and not target_url.startswith("//") else "/month"
    key_source = "|".join((str(user_id), event_type, workday_kind, workday_id, revision))
    dedupe = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            INSERT OR IGNORE INTO notification_events(
                app_user_id,event_type,workday_kind,workday_id,event_date,title,body,target_url,
                scheduled_at,status,dedupe_key,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,'queued',?,?)
            """,
            (user_id, event_type, workday_kind, workday_id, event_date, title, body,
             safe_url, scheduled_at.isoformat(timespec="seconds"), dedupe, now),
        )
    return bool(result.rowcount)


def queue_test_notification(user_id: int, scheduled_at: datetime | None = None) -> bool:
    settings = get_settings()
    due = (scheduled_at or datetime.now(settings.timezone)).replace(microsecond=0)
    return queue_notification(
        user_id=user_id, event_type="test", title="Re-Deputy · Test",
        body="Notifications are working.", target_url="/settings", scheduled_at=due,
        revision=f"test:{due.isoformat()}",
    )


def has_active_push_subscription(user_id: int) -> bool:
    with get_connection() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM push_subscriptions WHERE app_user_id=? AND active=1 LIMIT 1",
            (user_id,),
        ).fetchone())


def _parse_deputy_title(raw: str) -> tuple[str, str]:
    match = re.match(r"^\[([^]]+)]\s*(.*)$", raw.strip())
    if not match:
        return raw.strip() or "Work day", "Shift"
    code, role = match.groups()
    location = re.sub(r"^[THG]-", "", code, flags=re.I).replace("CAMBRIDGE", "Cambridge").title()
    role_labels = {"DIR": "Director", "SVT": "Sound/VT"}
    return location, role_labels.get(role.upper(), role or "Shift")


def _transport_from_source(payload: str) -> str:
    try:
        value = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return ""
    return str(value.get("vehicle_label") or value.get("vehicle") or "").strip()


def _local_datetime(value: object, settings: Settings) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=settings.timezone)
    return parsed.astimezone(settings.timezone)


def _manual_snapshot(value: object) -> dict[str, object]:
    try:
        snapshot = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return {}
    return snapshot if isinstance(snapshot, dict) else {}


def _manual_user_assignment(snapshot: dict[str, object], user_id: int) -> dict[str, object] | None:
    assignments = resolve_workday_snapshot_assignments(list(snapshot.get("assignments") or []))
    return next(
        (
            item for item in assignments
            if int(item.get("user_id") or 0) == user_id
            and str(item.get("assignment_state") or "assigned") == "assigned"
        ),
        None,
    )


def _manual_location(snapshot: dict[str, object]) -> str:
    return str(
        snapshot.get("custom_location")
        or snapshot.get("track_label")
        or snapshot.get("title")
        or "Work day"
    ).strip()


def _manual_transport(assignment: dict[str, object], own_way: bool) -> str:
    if own_way or str(assignment.get("transport_mode") or "") == "self_travel":
        return "Own way"
    mode = str(assignment.get("transport_mode") or "unassigned")
    if mode == "vehicle":
        return str(assignment.get("vehicle_label") or "").strip()
    if mode == "custom":
        return str(assignment.get("custom_transport_text") or "").strip()
    return ""


def _manual_effective_values(
    snapshot: dict[str, object], assignment: dict[str, object], own_way: bool,
) -> tuple[str, str, str, str]:
    location = _manual_location(snapshot)
    role = str(assignment.get("role_label") or assignment.get("position_label") or "Shift").strip()
    start = effective_rostered_start(snapshot, assignment)
    return location, role, start, _manual_transport(assignment, own_way)


def _self_travel_event_ids(user_id: int, start_date: str, end_date: str) -> set[str]:
    with get_connection() as conn:
        return {
            str(row["event_id"])
            for row in conn.execute(
                """SELECT event_id FROM user_event_transport_preferences
                   WHERE user_id=? AND event_kind='manual_workday' AND self_travel=1
                     AND event_date BETWEEN ? AND ?""",
                (user_id, start_date, end_date),
            ).fetchall()
        }


def _manual_rows(start_date: str, end_date: str) -> list[dict[str, object]]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT d.*,
                      (SELECT MAX(version_number) FROM roster_day_versions v WHERE v.roster_day_id=d.id) version_number,
                      (SELECT snapshot FROM roster_day_versions v WHERE v.roster_day_id=d.id
                       ORDER BY version_number DESC LIMIT 1 OFFSET 1) previous_snapshot
               FROM roster_days d
               WHERE d.roster_date BETWEEN ? AND ?
                 AND TRIM(COALESCE(d.published_snapshot,''))!=''
               ORDER BY d.roster_date,d.id""",
            (start_date, end_date),
        ).fetchall()]


def _queue_manual_notifications(
    *, pref: dict[str, object], now: datetime, reminder_at: time, created: dict[str, int],
) -> None:
    settings = get_settings()
    user_id = int(pref["app_user_id"])
    start_date = (now.date() - timedelta(days=1)).isoformat()
    end_date = (now.date() + timedelta(days=31)).isoformat()
    own_way_ids = _self_travel_event_ids(user_id, start_date, end_date)
    preferences_updated = _local_datetime(pref.get("updated_at"), settings)
    for row in _manual_rows(start_date, end_date):
        snapshot = _manual_snapshot(row.get("published_snapshot"))
        previous = _manual_snapshot(row.get("previous_snapshot"))
        assignment = _manual_user_assignment(snapshot, user_id)
        old_assignment = _manual_user_assignment(previous, user_id)
        if not assignment and not old_assignment:
            continue
        event_date = date.fromisoformat(str(snapshot.get("roster_date") or previous.get("roster_date") or row["roster_date"]))
        event_id = str(row["id"])
        own_way = event_id in own_way_ids
        active_snapshot = snapshot if assignment else previous
        active_assignment = assignment or old_assignment or {}
        location, role, start, transport = _manual_effective_values(active_snapshot, active_assignment, own_way)
        title, body = compact_push_payload(
            event_date=event_date, location=location, role=role, start=start,
            transport=transport, cancelled=assignment is None,
        )
        if assignment:
            for days, field, kind in ((1, "night_before", "night_before"), (2, "two_days_before", "two_days_before")):
                due = datetime.combine(event_date - timedelta(days=days), reminder_at, settings.timezone)
                if pref.get(field) and due <= now < due + timedelta(days=1):
                    created["reminders"] += int(queue_notification(
                        user_id=user_id, event_type=kind, workday_kind="manual", workday_id=event_id,
                        event_date=event_date.isoformat(), title=title, body=body,
                        target_url=f"/day/{event_date.isoformat()}", scheduled_at=due,
                        revision=f"{kind}:{event_date.isoformat()}",
                    ))
            if pref.get("one_hour_before") and start:
                due = datetime.combine(event_date, time.fromisoformat(start), settings.timezone) - timedelta(hours=1)
                if due <= now < due + timedelta(minutes=15):
                    created["reminders"] += int(queue_notification(
                        user_id=user_id, event_type="one_hour_before", workday_kind="manual", workday_id=event_id,
                        event_date=event_date.isoformat(), title=title, body=body,
                        target_url=f"/day/{event_date.isoformat()}", scheduled_at=due,
                        revision=f"one_hour_before:{event_date.isoformat()}:{start}",
                    ))
        published_at = _local_datetime(row.get("published_at"), settings)
        if not pref.get("changes_enabled") or not published_at or not preferences_updated or published_at < preferences_updated:
            continue
        old_values = _manual_effective_values(previous, old_assignment, own_way) if old_assignment else None
        new_values = _manual_effective_values(snapshot, assignment, own_way) if assignment else None
        if old_values == new_values:
            continue
        event_start = datetime.combine(event_date, time.fromisoformat(start or "00:00"), settings.timezone)
        within_24h = timedelta(0) <= event_start - now <= timedelta(hours=24)
        if within_24h and not pref.get("changes_within_24h"):
            continue
        change_title, change_body = compact_push_payload(
            event_date=event_date, location=location, role=role, start=start,
            transport=transport, change=True, cancelled=assignment is None,
        )
        revision_values = new_values if new_values is not None else (("cancelled", *old_values) if old_values else ("cancelled",))
        created["changes"] += int(queue_notification(
            user_id=user_id, event_type="change", workday_kind="manual", workday_id=event_id,
            event_date=event_date.isoformat(), title=change_title, body=change_body,
            target_url=f"/day/{event_date.isoformat()}", scheduled_at=now,
            revision=hashlib.sha256(f"{row.get('version_number')}|{revision_values}".encode()).hexdigest(),
        ))


def push_open_position_eligible(position: dict[str, object]) -> bool:
    if not position.get("can_apply") or not position.get("eligible") or position.get("conflicts"):
        return False
    if not position.get("eligible_all_teams") and not (
        position.get("eligible_team_id") or position.get("workday_team_id")
    ):
        return False
    return str(position.get("area_display") or "").strip().casefold() not in {"", "tbc"}


def _canonical_integrity_snapshot(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Canonical persisted integrity state: stable formatting, distinct episodes."""
    snapshots = []
    for row in rows:
        reason = str(row.get("reason") or "Conflicting roster evidence").casefold()
        roles: set[str] = set()
        clauses = []
        for raw_clause in reason.split(";"):
            condition_text, _, role_text = raw_clause.partition(":")
            clause_roles = {canonical_role_key(token) for token in re.split(r"\s*,\s*", role_text) if token.strip()}
            clause_roles.discard("")
            roles.update(clause_roles)
            condition = re.sub(r"[^a-z0-9]+", "_", condition_text).strip("_")
            clauses.append({"condition": condition or re.sub(r"[^a-z0-9]+", "_", raw_clause).strip("_"), "roles": sorted(clause_roles)})
        snapshots.append({
            "date": str(row.get("date") or ""),
            "location": str(row.get("area_location_id") or ""),
            "event_start": str(row.get("event_start_at") or ""),
            "clauses": sorted(clauses, key=lambda item: json.dumps(item, sort_keys=True)),
            "roles": sorted(roles),
            "conflict_count": int(row.get("conflict_count") or 0),
        })
    return sorted(snapshots, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))


def _integrity_delta(previous: list[dict[str, object]], current: list[dict[str, object]]) -> bool:
    def keyed(items: list[dict[str, object]]) -> dict[str, int]:
        result = {}
        for item in items:
            key = json.dumps({key: value for key, value in item.items() if key != "conflict_count"}, sort_keys=True, separators=(",", ":"))
            result[key] = max(result.get(key, 0), int(item.get("conflict_count") or 0))
        return result
    old, new = keyed(previous), keyed(current)
    return any(key not in old or value > old[key] for key, value in new.items())


def _queue_admin_operational_alerts(now: datetime) -> int:
    cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
    stale_cutoff = (now - timedelta(hours=36)).isoformat(timespec="seconds")
    with get_connection() as conn:
        admin_ids = [int(row["id"]) for row in conn.execute(
            """SELECT u.id FROM app_users u JOIN notification_preferences p ON p.app_user_id=u.id
               WHERE u.is_admin=1 AND u.is_active=1 AND p.enabled=1 AND p.admin_alerts=1"""
        ).fetchall()]
        travel_rows = [dict(row) for row in conn.execute(
            """SELECT a.*,u.display_name FROM user_event_transport_preference_audit a
               LEFT JOIN app_users u ON u.id=a.user_id WHERE a.changed_at>=? ORDER BY a.id DESC LIMIT 100""",
            (cutoff,),
        ).fetchall()]
        report_rows = [dict(row) for row in conn.execute(
            """SELECT r.id,r.created_at,r.report_text,COALESCE(u.display_name,'Unknown user') display_name
               FROM error_reports r LEFT JOIN app_users u ON u.id=r.user_id
               WHERE r.created_at>=? ORDER BY r.id DESC LIMIT 100""",
            (cutoff,),
        ).fetchall()]
        sync_rows = [dict(row) for row in conn.execute(
            """SELECT s.user_id,s.last_status,s.last_message,s.updated_at,u.display_name
               FROM user_sync_state s JOIN app_users u ON u.id=s.user_id
               WHERE u.is_active=1 AND s.last_status='error' AND s.updated_at>=?""",
            (cutoff,),
        ).fetchall()]
        stale_sync_rows = [dict(row) for row in conn.execute(
            """SELECT s.user_id,s.last_sync_at,s.updated_at,u.display_name
               FROM user_sync_state s
               JOIN app_users u ON u.id=s.user_id
               JOIN deputy_user_secrets secret ON secret.user_id=u.id
               WHERE u.is_active=1 AND COALESCE(u.account_type,'user')!='contractor'
                 AND TRIM(COALESCE(secret.encrypted_email,''))!=''
                 AND TRIM(COALESCE(secret.encrypted_password,''))!=''
                 AND COALESCE(s.sync_in_progress,0)=0
                 AND (TRIM(COALESCE(s.last_sync_at,''))='' OR s.last_sync_at<?)""",
            (stale_cutoff,),
        ).fetchall()]
        integrity_rows = [dict(row) for row in conn.execute(
            """SELECT date,area_location_id,event_start_at,reason,conflict_count,last_capture_at
               FROM deputy_event_coverage
               WHERE status='partial' AND (conflict_count>0 OR TRIM(COALESCE(reason,''))!='')
                 AND last_capture_at>=?""",
            (cutoff,),
        ).fetchall()]
        write_rows = [dict(row) for row in conn.execute(
            """SELECT operation_uuid,operation_type,stable_assignment_key,status,error_class,updated_at
               FROM deputy_write_operations WHERE status IN ('unknown','ambiguous') AND updated_at>=?""",
            (cutoff,),
        ).fetchall()]
    latest_generation = latest_relevant_sync_generation()
    latest_sync = str(latest_generation["completed_at"] or "") if latest_generation else ""
    sync_active = bool(latest_generation and str(latest_generation["status"] or "") == "pending")
    try:
        latest_sync_at = datetime.fromisoformat(latest_sync).astimezone(now.tzinfo) if latest_sync else None
    except ValueError:
        latest_sync_at = None
    integrity_ready = bool(
        integrity_rows and latest_generation is not None and str(latest_generation["status"] or "") == "complete" and not sync_active
        and (latest_sync_at is None or (now - latest_sync_at).total_seconds() >= INTEGRITY_SETTLE_SECONDS)
    )
    snapshot: list[dict[str, object]] = []
    should_alert = False
    dates: set[str] = set()
    if integrity_ready:
        dates = {str(row.get("date") or "") for row in integrity_rows}
        snapshot = _canonical_integrity_snapshot(integrity_rows)
        baseline = get_settled_integrity_state()
        try:
            previous = json.loads(str(baseline["findings_json"] or "[]")) if baseline else []
        except (TypeError, ValueError, json.JSONDecodeError):
            previous = []
        should_alert = _integrity_delta(previous, snapshot)
    created = 0
    for admin_id in admin_ids:
        for row in travel_rows:
            enabled = bool(row.get("new_self_travel"))
            action = "enabled" if enabled else "reversed"
            event_date = str(row.get("event_date") or "")
            created += int(queue_notification(
                user_id=admin_id, event_type="admin_alert", workday_kind="self_travel",
                workday_id=str(row["id"]), event_date=event_date,
                title="Re-Deputy · Admin alert",
                body=f"{row.get('display_name') or 'Crew member'} · Making My Own Way {action} · {row.get('location_key') or event_date}",
                target_url=f"/day/{event_date}" if event_date else "/admin", scheduled_at=now,
                revision=f"self-travel:{row['id']}:{int(enabled)}",
            ))
        for row in report_rows:
            created += int(queue_notification(
                user_id=admin_id, event_type="admin_alert", workday_kind="error_report",
                workday_id=str(row["id"]), event_date="", title="Re-Deputy · New error report",
                body=f"{row['display_name']} · {str(row.get('report_text') or '')[:120]}",
                target_url="/admin#error-reports", scheduled_at=now,
                revision=f"error-report:{row['id']}",
            ))
        for row in sync_rows:
            created += int(queue_notification(
                user_id=admin_id, event_type="admin_alert", workday_kind="sync_failure",
                workday_id=str(row["user_id"]), event_date="", title="Re-Deputy · Sync failed",
                body=f"{row.get('display_name') or 'Crew member'} · {str(row.get('last_message') or 'Primary roster sync failed')[:120]}",
                target_url="/admin#sync-status", scheduled_at=now,
                revision=f"sync-error:{row['user_id']}:{row.get('updated_at')}",
            ))
        for row in stale_sync_rows:
            created += int(queue_notification(
                user_id=admin_id, event_type="admin_alert", workday_kind="sync_stale",
                workday_id=str(row["user_id"]), event_date="", title="Re-Deputy · Sync seriously stale",
                body=f"{row.get('display_name') or 'Crew member'} · primary Deputy roster has not synced for over 36 hours",
                target_url="/admin#sync-status", scheduled_at=now,
                revision=f"sync-stale:{row['user_id']}:{row.get('last_sync_at') or row.get('updated_at')}",
            ))
        # Coverage warnings are snapshots of a multi-step sync.  Do not emit
        # provisional per-event pushes; after the persistent 90-second settle
        # period, send one deterministic aggregate for the committed state.
        if integrity_ready and should_alert:
            created += int(queue_notification(
                user_id=admin_id, event_type="admin_alert", workday_kind="roster_integrity",
                workday_id="settled-integrity", event_date="", title="Re-Deputy · Roster integrity",
                body=f"{len(integrity_rows)} items need review · {len(dates)} dates · inspect Roster integrity",
                target_url="/admin#roster-integrity", scheduled_at=now,
                revision=f"integrity-settled:generation:{latest_generation['id']}",
            ))
        for row in write_rows:
            created += int(queue_notification(
                user_id=admin_id, event_type="admin_alert", workday_kind="deputy_write_unknown",
                workday_id=str(row["operation_uuid"]), event_date="", title="Re-Deputy · Deputy write unresolved",
                body=f"{row.get('operation_type') or 'Write'} · {row.get('stable_assignment_key') or 'assignment'} · inspect before retry",
                target_url="/admin#deputy-write-audit", scheduled_at=now,
                revision=f"write-unresolved:{row['operation_uuid']}:{row.get('status')}:{row.get('error_class')}",
            ))
    # Persist only after every eligible recipient has been durably queued.  A
    # crash before this point is retried safely by queue_notification's key.
    if integrity_ready:
        save_settled_integrity_state(int(latest_generation["id"]), json.dumps(snapshot, sort_keys=True), now.isoformat())
    return created


def generate_due_notifications(now: datetime | None = None) -> dict[str, int]:
    settings = get_settings()
    now = (now or datetime.now(settings.timezone)).astimezone(settings.timezone).replace(microsecond=0)
    created = {"reminders": 0, "changes": 0, "digests": 0, "open_positions": 0, "admin_alerts": 0}
    created["admin_alerts"] = _queue_admin_operational_alerts(now)
    with get_connection() as conn:
        prefs = [dict(row) for row in conn.execute(
            "SELECT * FROM notification_preferences WHERE enabled=1"
        ).fetchall()]
    for pref in prefs:
        user_id = int(pref["app_user_id"])
        reminder_at = time.fromisoformat(str(pref.get("reminder_time") or "19:00"))
        _queue_manual_notifications(pref=pref, now=now, reminder_at=reminder_at, created=created)
        with get_connection() as conn:
            shifts = [dict(row) for row in conn.execute(
                """SELECT s.* FROM shifts s
                   WHERE s.owner_user_id=? AND s.date BETWEEN ? AND ?
                   AND (s.deleted_from_source=0 OR s.changed_since_viewed=1) ORDER BY s.start_at,s.id""",
                (user_id, (now.date() - timedelta(days=1)).isoformat(), (now.date() + timedelta(days=31)).isoformat()),
            ).fetchall()]
        for shift in _group_deputy_notification_workdays(shifts, user_id):
            event_date = date.fromisoformat(str(shift["date"]))
            location = str(shift.get("notification_location") or "Work day")
            role = str(shift.get("notification_role") or "Shift")
            start = str(shift.get("notification_rostered_start") or "") or str(shift.get("start_at") or "")[11:16]
            transport = str(shift.get("notification_transport") or "")
            title, body = compact_push_payload(
                event_date=event_date, location=location, role=role, start=start, transport=transport,
            )
            for days, field, kind in ((1, "night_before", "night_before"), (2, "two_days_before", "two_days_before")):
                due = datetime.combine(event_date - timedelta(days=days), reminder_at, settings.timezone)
                if pref.get(field) and due <= now < due + timedelta(days=1):
                    created["reminders"] += int(queue_notification(
                        user_id=user_id, event_type=kind, workday_kind="deputy", workday_id=str(shift["notification_workday_id"]),
                        event_date=event_date.isoformat(), title=title, body=body,
                        target_url=f"/day/{event_date.isoformat()}", scheduled_at=due,
                        revision=f"{kind}:{event_date.isoformat()}",
                    ))
            if pref.get("one_hour_before") and start:
                due = datetime.combine(event_date, time.fromisoformat(start), settings.timezone) - timedelta(hours=1)
                if due <= now < due + timedelta(minutes=15):
                    created["reminders"] += int(queue_notification(
                        user_id=user_id, event_type="one_hour_before", workday_kind="deputy",
                        workday_id=str(shift["notification_workday_id"]), event_date=event_date.isoformat(),
                        title=title, body=body, target_url=f"/day/{event_date.isoformat()}", scheduled_at=due,
                        revision=f"one_hour_before:{event_date.isoformat()}:{start}",
                    ))
            preferences_updated = _local_datetime(pref.get("updated_at"), settings)
            first_seen_at = _local_datetime(shift.get("first_seen_at"), settings)
            newly_assigned = bool(
                first_seen_at and preferences_updated and first_seen_at >= preferences_updated
                and not shift.get("deleted_from_source")
            )
            if pref.get("changes_enabled") and (int(shift.get("changed_since_viewed") or 0) or newly_assigned):
                event_at = datetime.combine(event_date, time.fromisoformat(start), settings.timezone) if start else _local_datetime(shift.get("start_at"), settings)
                changed_at_value = _local_datetime(
                    shift.get("last_changed_at") or shift.get("first_seen_at") or shift.get("last_synced_at"), settings
                )
                if event_at is None or changed_at_value is None or preferences_updated is None or changed_at_value < preferences_updated:
                    continue
                within_24h = timedelta(0) <= event_at - now <= timedelta(hours=24)
                if not within_24h or pref.get("changes_within_24h"):
                    changed_at = str(shift.get("last_changed_at") or shift.get("last_synced_at") or "")
                    _, changed_body = compact_push_payload(
                        event_date=event_date, location=location, role=role, start=start,
                        transport=transport, change=True, cancelled=bool(shift.get("deleted_from_source")),
                    )
                    created["changes"] += int(queue_notification(
                        user_id=user_id, event_type="change", workday_kind="deputy", workday_id=str(shift["notification_workday_id"]),
                        event_date=event_date.isoformat(), title=title, body=changed_body,
                        target_url=f"/day/{event_date.isoformat()}", scheduled_at=now,
                        revision=hashlib.sha256(f"{changed_at}|{location}|{role}|{start}|{transport}|{shift.get('deleted_from_source')}".encode()).hexdigest(),
                    ))
        if pref.get("weekly_digest") and now.weekday() == 0 and now.time() >= reminder_at:
            with get_connection() as conn:
                changed_count = int(conn.execute(
                    """SELECT COUNT(DISTINCT COALESCE(workday_kind,'') || ':' || COALESCE(workday_id,'')) n
                       FROM notification_events
                       WHERE app_user_id=? AND event_type='change' AND event_date BETWEEN ? AND ?""",
                    (user_id, now.date().isoformat(), (now.date() + timedelta(days=7)).isoformat()),
                ).fetchone()["n"] or 0)
            if changed_count:
                monday = now.date() - timedelta(days=now.weekday())
                created["digests"] += int(queue_notification(
                    user_id=user_id, event_type="weekly_digest", title=f"Re-Deputy · {changed_count} roster changes this week",
                    body="Tap to review your upcoming work", target_url="/month", scheduled_at=now,
                    revision=f"week:{monday.isoformat()}",
                ))
        if pref.get("open_positions_month"):
            for position in list_open_workday_positions(now.date().isoformat(), (now.date() + timedelta(days=31)).isoformat(), app_user_id=user_id):
                if not push_open_position_eligible(position):
                    continue
                day_text = str(position.get("date") or "")
                event_date = date.fromisoformat(day_text)
                title = _portable_title(event_date)
                body = " · ".join((str(position.get("location_label") or "Work day"), str(position.get("area_display") or "Open position"), str(position.get("office_start") or "TBC"), "Open"))
                created["open_positions"] += int(queue_notification(
                    user_id=user_id, event_type="open_position", workday_kind="manual", workday_id=f"{position['roster_day_id']}:{position['assignment_key']}",
                    event_date=day_text, title=title, body=body, target_url=f"/day/{day_text}", scheduled_at=now,
                    revision=f"open:{position['roster_day_id']}:{position['assignment_key']}",
                ))
    return created


def _webpush_send(subscription: dict[str, object], payload: str, settings: Settings) -> None:
    from pywebpush import webpush
    identity = ensure_push_identity()
    if not identity.ready:
        raise RuntimeError("Push identity unavailable")
    webpush(
        subscription_info={"endpoint": subscription["endpoint"], "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
        data=payload,
        vapid_private_key=identity.private_key_path,
        vapid_claims={"sub": subscription["app_origin"]},
        ttl=86400,
    )


def deliver_due_notifications(now: datetime | None = None,
                              sender: Callable[[dict[str, object], str, Settings], None] | None = None) -> dict[str, int]:
    settings = get_settings()
    now = (now or datetime.now(settings.timezone)).astimezone(settings.timezone).replace(microsecond=0)
    result = {"events": 0, "delivered": 0, "failed": 0}
    if not ensure_push_identity().ready:
        return result
    sender = sender or _webpush_send
    with get_connection() as conn:
        events = [dict(row) for row in conn.execute(
            "SELECT * FROM notification_events WHERE status='queued' AND scheduled_at<=? ORDER BY scheduled_at,id LIMIT 100",
            (now.isoformat(timespec="seconds"),),
        ).fetchall()]
    for event in events:
        result["events"] += 1
        with get_connection() as conn:
            subscriptions = [dict(row) for row in conn.execute(
                "SELECT * FROM push_subscriptions WHERE app_user_id=? AND active=1", (event["app_user_id"],)
            ).fetchall()]
        if not subscriptions:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE notification_events SET status='failed',sent_at=?,failure_summary=? WHERE id=?",
                    (now.isoformat(timespec="seconds"), "No active push devices.", event["id"]),
                )
            continue
        delivered = 0
        for subscription in subscriptions:
            payload = json.dumps({"title": event["title"], "body": event["body"], "url": event["target_url"]}, separators=(",", ":"))
            attempted = now.isoformat(timespec="seconds")
            try:
                sender(subscription, payload, settings)
                delivered += 1
                result["delivered"] += 1
                with get_connection() as conn:
                    conn.execute("UPDATE push_subscriptions SET last_success_at=?,failure_count=0,updated_at=? WHERE id=?", (attempted, attempted, subscription["id"]))
                    conn.execute("INSERT INTO notification_deliveries(notification_event_id,subscription_id,attempted_at,result) VALUES (?,?,?,'sent')", (event["id"], subscription["id"], attempted))
            except Exception as exc:
                summary = f"{type(exc).__name__}: {str(exc)[:180]}"
                permanent = any(code in summary for code in ("404", "410"))
                result["failed"] += 1
                with get_connection() as conn:
                    conn.execute("UPDATE push_subscriptions SET last_failure_at=?,failure_count=failure_count+1,active=CASE WHEN ? THEN 0 ELSE active END,updated_at=? WHERE id=?", (attempted, 1 if permanent else 0, attempted, subscription["id"]))
                    conn.execute("INSERT INTO notification_deliveries(notification_event_id,subscription_id,attempted_at,result,failure_summary) VALUES (?,?,?,'failed',?)", (event["id"], subscription["id"], attempted, summary))
        with get_connection() as conn:
            conn.execute("UPDATE notification_events SET status=?,sent_at=?,failure_summary=? WHERE id=?", (
                "sent" if delivered else "failed", attempted, "" if delivered else "No active device accepted this notification.", event["id"]
            ))
    return result


def run_notification_pass(now: datetime | None = None) -> dict[str, object]:
    if not _runner_lock.acquire(blocking=False):
        return {"ran": False, "reason": "notification pass already active"}
    try:
        return {"ran": True, "generated": generate_due_notifications(now), "delivery": deliver_due_notifications(now)}
    finally:
        _runner_lock.release()


def notification_status(user_id: int) -> dict[str, object]:
    prefs = notification_preferences(user_id)
    devices = list_user_push_subscriptions(user_id)
    with get_connection() as conn:
        last_test = conn.execute(
            "SELECT scheduled_at,sent_at,status,failure_summary FROM notification_events WHERE app_user_id=? AND event_type='test' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return {"preferences": prefs, "devices": devices, "active_devices": sum(int(d.get("active") or 0) for d in devices), "last_test": dict(last_test) if last_test else None}


def notification_admin_summary() -> dict[str, object]:
    identity = ensure_push_identity()
    with get_connection() as conn:
        row = conn.execute("""
            SELECT (SELECT COUNT(*) FROM notification_preferences WHERE enabled=1) enabled_users,
                   (SELECT COUNT(*) FROM push_subscriptions WHERE active=1) devices,
                   (SELECT COUNT(*) FROM notification_events WHERE status='failed') failed
        """).fetchone()
    summary = dict(row) if row else {"enabled_users": 0, "devices": 0, "failed": 0}
    summary.update({
        "identity_ready": identity.ready,
        "identity_status": "Push identity ready" if identity.ready else "Push identity unavailable",
        "identity_diagnostic": identity.diagnostic,
    })
    return summary
