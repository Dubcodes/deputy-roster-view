from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Iterable

from .config import Settings, get_settings


def get_connection(settings: Settings | None = None) -> sqlite3.Connection:
    settings = settings or get_settings()
    os.makedirs(settings.data_dir, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(settings: Settings | None = None) -> None:
    with get_connection(settings) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_uid TEXT UNIQUE,
                source_url_hash TEXT,
                title TEXT,
                description TEXT,
                location TEXT,
                start_at TEXT,
                end_at TEXT,
                date TEXT,
                raw_hours REAL,
                break_minutes INTEGER,
                paid_hours REAL,
                last_synced_at TEXT,
                first_seen_at TEXT,
                last_changed_at TEXT,
                changed_since_viewed INTEGER DEFAULT 0,
                deleted_from_source INTEGER DEFAULT 0,
                source_link TEXT,
                source_status TEXT,
                source_payload TEXT
            );

            CREATE TABLE IF NOT EXISTS shift_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER UNIQUE,
                checked INTEGER DEFAULT 0,
                confirmed INTEGER DEFAULT 0,
                important INTEGER DEFAULT 0,
                question INTEGER DEFAULT 0,
                early_start INTEGER DEFAULT 0,
                gear_needed INTEGER DEFAULT 0,
                travel_needed INTEGER DEFAULT 0,
                pay_check INTEGER DEFAULT 0,
                private_note TEXT,
                custom_colour TEXT,
                timing_adjustment_time TEXT,
                timing_adjustment_last_race INTEGER DEFAULT 0,
                timing_adjustment_day_finished INTEGER DEFAULT 0,
                updated_at TEXT,
                FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS shift_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER,
                changed_at TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                finished_at TEXT,
                status TEXT,
                message TEXT,
                events_seen INTEGER,
                events_created INTEGER,
                events_updated INTEGER,
                events_marked_deleted INTEGER
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deputy_email TEXT UNIQUE,
                display_name TEXT,
                pin_hash TEXT,
                deputy_web_url TEXT,
                is_admin INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                last_seen_at TEXT
            );

            CREATE TABLE IF NOT EXISTS trusted_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                token_hash TEXT UNIQUE,
                label TEXT,
                user_agent TEXT,
                created_at TEXT,
                last_seen_at TEXT,
                expires_at TEXT,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deputy_user_secrets (
                user_id INTEGER PRIMARY KEY,
                encrypted_email TEXT,
                encrypted_password TEXT,
                encrypted_session_json TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS admin_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                created_by_user_id INTEGER,
                target_date TEXT,
                target_track TEXT,
                override_type TEXT,
                label TEXT,
                value TEXT,
                note TEXT,
                active INTEGER DEFAULT 1,
                FOREIGN KEY (created_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS capture_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                track_label TEXT,
                source_user_id INTEGER,
                captured_at TEXT,
                crew_rows INTEGER DEFAULT 0,
                open_shift_rows INTEGER DEFAULT 0,
                warning_rows INTEGER DEFAULT 0,
                unavailable_rows INTEGER DEFAULT 0,
                status TEXT,
                note TEXT,
                UNIQUE(date, track_label),
                FOREIGN KEY (source_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS deputy_schedule_areas (
                area_id INTEGER PRIMARY KEY,
                name TEXT,
                location_id INTEGER,
                roster_sort_order INTEGER,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS deputy_schedule_shifts (
                source_shift_id INTEGER PRIMARY KEY,
                captured_at TEXT,
                area_id INTEGER,
                area_name TEXT,
                area_roster_sort_order INTEGER,
                employee_id INTEGER,
                employee_name TEXT,
                start_at TEXT,
                end_at TEXT,
                date TEXT,
                duration REAL,
                is_open INTEGER DEFAULT 0,
                is_published INTEGER DEFAULT 0,
                changed_since_viewed INTEGER DEFAULT 0,
                last_changed_at TEXT,
                change_summary TEXT,
                note TEXT,
                raw_payload TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(date);
            CREATE INDEX IF NOT EXISTS idx_shifts_start_at ON shifts(start_at);
            CREATE INDEX IF NOT EXISTS idx_shifts_changed ON shifts(changed_since_viewed);
            CREATE INDEX IF NOT EXISTS idx_shift_changes_shift ON shift_changes(shift_id, changed_at);
            CREATE INDEX IF NOT EXISTS idx_sync_log_started_at ON sync_log(started_at);
            CREATE INDEX IF NOT EXISTS idx_deputy_schedule_shifts_date ON deputy_schedule_shifts(date);
            CREATE INDEX IF NOT EXISTS idx_deputy_schedule_shifts_start ON deputy_schedule_shifts(start_at);
            CREATE INDEX IF NOT EXISTS idx_trusted_devices_token ON trusted_devices(token_hash);
            CREATE INDEX IF NOT EXISTS idx_admin_overrides_date ON admin_overrides(target_date);
            CREATE INDEX IF NOT EXISTS idx_capture_coverage_date ON capture_coverage(date);
            """
        )
        _ensure_column(conn, "shifts", "source_link", "TEXT")
        _ensure_column(conn, "shifts", "source_status", "TEXT")
        _ensure_column(conn, "shift_marks", "timing_adjustment_time", "TEXT")
        _ensure_column(conn, "shift_marks", "timing_adjustment_last_race", "INTEGER DEFAULT 0")
        _ensure_column(conn, "shift_marks", "timing_adjustment_day_finished", "INTEGER DEFAULT 0")
        _ensure_column(conn, "deputy_schedule_shifts", "area_name", "TEXT")
        _ensure_column(conn, "deputy_schedule_shifts", "area_roster_sort_order", "INTEGER")
        _ensure_column(conn, "deputy_schedule_shifts", "changed_since_viewed", "INTEGER DEFAULT 0")
        _ensure_column(conn, "deputy_schedule_shifts", "last_changed_at", "TEXT")
        _ensure_column(conn, "deputy_schedule_shifts", "change_summary", "TEXT")
        _ensure_column(conn, "app_users", "deputy_web_url", "TEXT")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def count_app_users() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM app_users").fetchone()
    return int(row["count"] or 0) if row else 0


def list_app_users() -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.*,
                (
                    SELECT COUNT(*)
                    FROM trusted_devices d
                    WHERE d.user_id = u.id
                      AND d.revoked_at IS NULL
                      AND d.expires_at > ?
                ) AS active_devices
            FROM app_users u
            ORDER BY u.is_admin DESC, LOWER(u.display_name), LOWER(u.deputy_email)
            """,
            (datetime.now(get_settings().timezone).isoformat(timespec="seconds"),),
        ).fetchall()
    return rows


def get_app_user(user_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM app_users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()


def get_app_user_by_email(email: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM app_users WHERE LOWER(deputy_email) = LOWER(?)",
            (email.strip(),),
        ).fetchone()


def create_app_user(
    *,
    deputy_email: str,
    display_name: str,
    pin_hash: str,
    deputy_web_url: str,
    encrypted_email: str,
    encrypted_password: str,
) -> sqlite3.Row:
    now = datetime.now().isoformat(timespec="seconds")
    is_admin = 1 if count_app_users() == 0 else 0
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO app_users (
                deputy_email, display_name, pin_hash, deputy_web_url, is_admin,
                is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (deputy_email.strip(), display_name.strip(), pin_hash, deputy_web_url.strip(), is_admin, now, now),
        )
        user_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO deputy_user_secrets (
                user_id, encrypted_email, encrypted_password, encrypted_session_json, updated_at
            )
            VALUES (?, ?, ?, '', ?)
            """,
            (user_id, encrypted_email, encrypted_password, now),
        )
        return conn.execute("SELECT * FROM app_users WHERE id = ?", (user_id,)).fetchone()


def update_app_user_seen(user_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE app_users SET last_seen_at = ? WHERE id = ?",
            (now, user_id),
        )


def create_trusted_device(
    *,
    user_id: int,
    token_hash: str,
    expires_at: str,
    label: str = "",
    user_agent: str = "",
) -> sqlite3.Row:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trusted_devices (
                user_id, token_hash, label, user_agent, created_at, last_seen_at, expires_at, revoked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (user_id, token_hash, label, user_agent[:500], now, now, expires_at),
        )
        return conn.execute("SELECT * FROM trusted_devices WHERE id = ?", (cursor.lastrowid,)).fetchone()


def get_trusted_device(token_hash: str, now: str | None = None) -> sqlite3.Row | None:
    now = now or datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                d.*,
                u.deputy_email,
                u.display_name,
                u.is_admin,
                u.is_active
            FROM trusted_devices d
            JOIN app_users u ON u.id = d.user_id
            WHERE d.token_hash = ?
              AND d.revoked_at IS NULL
              AND d.expires_at > ?
              AND u.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()


def update_trusted_device_seen(device_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE trusted_devices SET last_seen_at = ? WHERE id = ?",
            (now, device_id),
        )


def revoke_trusted_device(device_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE trusted_devices SET revoked_at = ? WHERE id = ?",
            (now, device_id),
        )


def create_admin_override(
    *,
    created_by_user_id: int,
    target_date: str,
    target_track: str,
    override_type: str,
    label: str,
    value: str,
    note: str,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO admin_overrides (
                created_at, created_by_user_id, target_date, target_track,
                override_type, label, value, note, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (now, created_by_user_id, target_date, target_track, override_type, label, value, note),
        )


def list_admin_overrides(limit: int = 40) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT o.*, u.display_name AS created_by_name
            FROM admin_overrides o
            LEFT JOIN app_users u ON u.id = o.created_by_user_id
            ORDER BY o.created_at DESC, o.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def ensure_shift_mark(conn: sqlite3.Connection, shift_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT OR IGNORE INTO shift_marks (shift_id, updated_at)
        VALUES (?, ?)
        """,
        (shift_id, now),
    )


def fetch_shifts_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, m.checked, m.confirmed, m.important, m.question,
                   m.early_start, m.gear_needed, m.travel_needed, m.pay_check,
                   m.private_note, m.custom_colour, m.timing_adjustment_time,
                   m.timing_adjustment_last_race, m.timing_adjustment_day_finished,
                   m.updated_at AS marks_updated_at
            FROM shifts s
            LEFT JOIN shift_marks m ON m.shift_id = s.id
            WHERE s.date BETWEEN ? AND ?
            ORDER BY s.start_at, s.id
            """,
            (start_date, end_date),
        ).fetchall()
    return rows


def fetch_shifts_for_date(date_text: str) -> list[sqlite3.Row]:
    return fetch_shifts_between(date_text, date_text)


def fetch_shift(shift_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.*, m.checked, m.confirmed, m.important, m.question,
                   m.early_start, m.gear_needed, m.travel_needed, m.pay_check,
                   m.private_note, m.custom_colour, m.timing_adjustment_time,
                   m.timing_adjustment_last_race, m.timing_adjustment_day_finished,
                   m.updated_at AS marks_updated_at
            FROM shifts s
            LEFT JOIN shift_marks m ON m.shift_id = s.id
            WHERE s.id = ?
            """,
            (shift_id,),
        ).fetchone()
    return row


def update_shift_marks(shift_id: int, values: dict[str, object]) -> bool:
    with get_connection() as conn:
        shift = conn.execute("SELECT id FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if shift is None:
            return False
        ensure_shift_mark(conn, shift_id)
        conn.execute(
            """
            UPDATE shift_marks
            SET checked = ?,
                confirmed = ?,
                important = ?,
                question = ?,
                early_start = ?,
                gear_needed = ?,
                travel_needed = ?,
                pay_check = ?,
                private_note = ?,
                custom_colour = ?,
                timing_adjustment_time = ?,
                timing_adjustment_last_race = ?,
                timing_adjustment_day_finished = ?,
                updated_at = ?
            WHERE shift_id = ?
            """,
            (
                values.get("checked", 0),
                values.get("confirmed", 0),
                values.get("important", 0),
                values.get("question", 0),
                values.get("early_start", 0),
                values.get("gear_needed", 0),
                values.get("travel_needed", 0),
                values.get("pay_check", 0),
                values.get("private_note", ""),
                values.get("custom_colour", ""),
                values.get("timing_adjustment_time", ""),
                values.get("timing_adjustment_last_race", 0),
                values.get("timing_adjustment_day_finished", 0),
                datetime.now().isoformat(timespec="seconds"),
                shift_id,
            ),
        )
    return True


def clear_changed_for_date(date_text: str) -> int:
    with get_connection() as conn:
        shift_result = conn.execute(
            "UPDATE shifts SET changed_since_viewed = 0 WHERE date = ?",
            (date_text,),
        )
        schedule_result = conn.execute(
            """
            UPDATE deputy_schedule_shifts
            SET changed_since_viewed = 0,
                change_summary = ''
            WHERE date = ?
            """,
            (date_text,),
        )
        return shift_result.rowcount + schedule_result.rowcount


def clear_changed_for_shift(shift_id: int) -> int:
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE shifts SET changed_since_viewed = 0 WHERE id = ?",
            (shift_id,),
        )
        return result.rowcount


def get_recent_sync_logs(limit: int = 20) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM sync_log
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def get_shift_changes_for_date(date_text: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*
            FROM shift_changes c
            JOIN shifts s ON s.id = c.shift_id
            WHERE s.date = ?
            ORDER BY c.changed_at DESC, c.id DESC
            """,
            (date_text,),
        ).fetchall()
    return rows


def fetch_deputy_schedule_for_date(date_text: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM deputy_schedule_shifts
            WHERE date = ?
            ORDER BY
                COALESCE(area_roster_sort_order, 999999),
                area_name,
                start_at,
                employee_name
            """,
            (date_text,),
        ).fetchall()
    return rows


def has_deputy_schedule_changes_for_date(date_text: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM deputy_schedule_shifts
            WHERE date = ?
              AND changed_since_viewed = 1
              AND (
                change_summary LIKE '%Person:%'
                OR change_summary LIKE '%Position:%'
                OR change_summary LIKE '%Open shift:%'
              )
            LIMIT 1
            """,
            (date_text,),
        ).fetchone()
    return row is not None


def get_deputy_schedule_snapshot() -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN is_published = 1 THEN 1 ELSE 0 END) AS published_rows,
                SUM(CASE WHEN is_open = 1 OR TRIM(COALESCE(employee_name, '')) = '' THEN 1 ELSE 0 END) AS open_rows,
                SUM(CASE WHEN is_published = 0 THEN 1 ELSE 0 END) AS unpublished_rows,
                SUM(CASE WHEN changed_since_viewed = 1 THEN 1 ELSE 0 END) AS changed_rows,
                MIN(date) AS first_date,
                MAX(date) AS last_date,
                MAX(captured_at) AS captured_at
            FROM deputy_schedule_shifts
            """
        ).fetchone()
    if row is None:
        return {}
    return {
        "total_rows": int(row["total_rows"] or 0),
        "published_rows": int(row["published_rows"] or 0),
        "open_rows": int(row["open_rows"] or 0),
        "unpublished_rows": int(row["unpublished_rows"] or 0),
        "changed_rows": int(row["changed_rows"] or 0),
        "first_date": row["first_date"] or "",
        "last_date": row["last_date"] or "",
        "captured_at": row["captured_at"] or "",
    }


def fetch_open_deputy_schedule_shifts(limit: int = 8) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM deputy_schedule_shifts
            WHERE is_open = 1
               OR TRIM(COALESCE(employee_name, '')) = ''
            ORDER BY
                date ASC,
                start_at ASC,
                COALESCE(area_roster_sort_order, 999999),
                area_name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def fetch_open_deputy_schedule_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM deputy_schedule_shifts
            WHERE date BETWEEN ? AND ?
              AND (
                is_open = 1
                OR TRIM(COALESCE(employee_name, '')) = ''
              )
            ORDER BY
                date ASC,
                start_at ASC,
                COALESCE(area_roster_sort_order, 999999),
                area_name ASC
            """,
            (start_date, end_date),
        ).fetchall()
    return rows


def get_recent_source_payloads(limit: int = 6) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM shifts
            WHERE source_payload IS NOT NULL
              AND source_payload != ''
            ORDER BY start_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def get_app_settings() -> dict[str, str]:
    defaults = {
        "show_source_data": "0",
        "deputy_ical_url": "",
    }
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    values = {row["key"]: row["value"] for row in rows}
    return {**defaults, **values}


def get_app_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return default
    return str(row["value"] or "")


def get_last_deputy_web_capture() -> str:
    return get_app_setting("last_deputy_web_capture", "")


def get_calendar_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    saved_url = get_app_setting("deputy_ical_url", "").strip()
    return saved_url or settings.deputy_ical_url.strip()


def get_calendar_url_source(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    saved_url = get_app_setting("deputy_ical_url", "").strip()
    if saved_url:
        return "Saved in Settings"
    if settings.deputy_ical_url.strip():
        return "Docker/env"
    return "Missing"


def update_app_settings(values: dict[str, str]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )


SCHEDULE_COMPARE_FIELDS = (
    ("area_name", "Position"),
    ("employee_name", "Person"),
    ("start_at", "Start"),
    ("end_at", "End"),
    ("duration", "Hours"),
    ("is_open", "Open shift"),
    ("is_published", "Published"),
)


def _schedule_values_equal(field_name: str, old_value: object, new_value: object) -> bool:
    if field_name == "duration":
        try:
            return round(float(old_value or 0), 2) == round(float(new_value or 0), 2)
        except (TypeError, ValueError):
            return False
    if field_name in {"is_open", "is_published"}:
        return int(old_value or 0) == int(new_value or 0)
    return str(old_value or "") == str(new_value or "")


def _schedule_change_summary(existing: sqlite3.Row | None, values: dict[str, object]) -> str:
    if existing is None:
        return ""
    changes = []
    for field_name, label in SCHEDULE_COMPARE_FIELDS:
        old_value = existing[field_name]
        new_value = values[field_name]
        if not _schedule_values_equal(field_name, old_value, new_value):
            changes.append(f"{label}: {_display_change_value(old_value)} -> {_display_change_value(new_value)}")
    return "; ".join(changes)


def _display_change_value(value: object) -> str:
    if value in (None, ""):
        return "blank"
    return str(value)


def save_deputy_web_schedule(payload: dict[str, object]) -> int:
    captured_at = str(payload.get("captured_at") or datetime.now().isoformat(timespec="seconds"))
    areas = payload.get("areas") if isinstance(payload.get("areas"), list) else []
    shifts = payload.get("extracted_schedule_shifts") if isinstance(payload.get("extracted_schedule_shifts"), list) else []
    area_lookup: dict[str, dict[str, object]] = {}

    with get_connection() as conn:
        for area in areas:
            if not isinstance(area, dict) or area.get("id") in (None, ""):
                continue
            area_id = int(area["id"])
            area_lookup[str(area_id)] = area
            conn.execute(
                """
                INSERT INTO deputy_schedule_areas (
                    area_id, name, location_id, roster_sort_order, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(area_id) DO UPDATE SET
                    name = excluded.name,
                    location_id = excluded.location_id,
                    roster_sort_order = excluded.roster_sort_order,
                    updated_at = excluded.updated_at
                """,
                (
                    area_id,
                    str(area.get("name") or area_id),
                    _optional_int(area.get("locationId")),
                    _optional_int(area.get("rosterSortOrder")),
                    captured_at,
                ),
            )

        saved = 0
        for shift in shifts:
            if not isinstance(shift, dict) or shift.get("id") in (None, ""):
                continue
            area_id = _optional_int(shift.get("area"))
            area = area_lookup.get(str(area_id)) if area_id is not None else None
            area_name = str(shift.get("areaName") or (area or {}).get("name") or area_id or "")
            area_sort = _optional_int(shift.get("areaRosterSortOrder"))
            if area_sort is None and area:
                area_sort = _optional_int(area.get("rosterSortOrder"))
            start_at = str(shift.get("start") or "")
            end_at = str(shift.get("end") or "")
            source_shift_id = int(shift["id"])
            values = {
                "area_id": area_id,
                "area_name": area_name,
                "area_roster_sort_order": area_sort,
                "employee_id": _optional_int(shift.get("employee")),
                "employee_name": str(shift.get("employeeName") or ""),
                "start_at": start_at,
                "end_at": end_at,
                "date": start_at[:10],
                "duration": _optional_float(shift.get("duration")),
                "is_open": 1 if shift.get("isOpen") else 0,
                "is_published": 1 if shift.get("isPublished") else 0,
                "note": str(shift.get("note") or ""),
                "raw_payload": json_dumps(shift),
            }
            existing = conn.execute(
                "SELECT * FROM deputy_schedule_shifts WHERE source_shift_id = ?",
                (source_shift_id,),
            ).fetchone()
            change_summary = _schedule_change_summary(existing, values)
            changed = bool(change_summary)
            conn.execute(
                """
                INSERT INTO deputy_schedule_shifts (
                    source_shift_id, captured_at, area_id, area_name,
                    area_roster_sort_order, employee_id, employee_name,
                    start_at, end_at, date, duration, is_open, is_published,
                    changed_since_viewed, last_changed_at, change_summary,
                    note, raw_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_shift_id) DO UPDATE SET
                    captured_at = excluded.captured_at,
                    area_id = excluded.area_id,
                    area_name = excluded.area_name,
                    area_roster_sort_order = excluded.area_roster_sort_order,
                    employee_id = excluded.employee_id,
                    employee_name = excluded.employee_name,
                    start_at = excluded.start_at,
                    end_at = excluded.end_at,
                    date = excluded.date,
                    duration = excluded.duration,
                    is_open = excluded.is_open,
                    is_published = excluded.is_published,
                    changed_since_viewed = CASE WHEN ? THEN 1 ELSE deputy_schedule_shifts.changed_since_viewed END,
                    last_changed_at = CASE WHEN ? THEN ? ELSE deputy_schedule_shifts.last_changed_at END,
                    change_summary = CASE WHEN ? THEN ? ELSE deputy_schedule_shifts.change_summary END,
                    note = excluded.note,
                    raw_payload = excluded.raw_payload
                """,
                (
                    source_shift_id,
                    captured_at,
                    values["area_id"],
                    values["area_name"],
                    values["area_roster_sort_order"],
                    values["employee_id"],
                    values["employee_name"],
                    values["start_at"],
                    values["end_at"],
                    values["date"],
                    values["duration"],
                    values["is_open"],
                    values["is_published"],
                    0,
                    None,
                    "",
                    values["note"],
                    values["raw_payload"],
                    1 if changed else 0,
                    1 if changed else 0,
                    captured_at,
                    1 if changed else 0,
                    change_summary,
                ),
            )
            saved += 1
    return saved


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def clear_all_changed_flags() -> int:
    with get_connection() as conn:
        shift_result = conn.execute(
            "UPDATE shifts SET changed_since_viewed = 0 WHERE changed_since_viewed = 1"
        )
        schedule_result = conn.execute(
            """
            UPDATE deputy_schedule_shifts
            SET changed_since_viewed = 0,
                change_summary = ''
            WHERE changed_since_viewed = 1
            """
        )
        return shift_result.rowcount + schedule_result.rowcount


def get_last_successful_sync() -> sqlite3.Row | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM sync_log
            WHERE status = 'ok'
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return row


def get_next_upcoming_shift(now_iso: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM shifts
            WHERE deleted_from_source = 0
              AND start_at >= ?
            ORDER BY start_at ASC
            LIMIT 1
            """,
            (now_iso,),
        ).fetchone()
    return row


def get_current_or_next_shift(now_iso: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM shifts
            WHERE deleted_from_source = 0
              AND end_at >= ?
            ORDER BY
              CASE WHEN start_at <= ? THEN 0 ELSE 1 END,
              start_at ASC,
              id ASC
            LIMIT 1
            """,
            (now_iso, now_iso),
        ).fetchone()
    return row


def get_upcoming_shifts(now_iso: str, limit: int = 5) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*, m.checked, m.confirmed, m.important, m.question,
                   m.early_start, m.gear_needed, m.travel_needed, m.pay_check,
                   m.private_note, m.custom_colour, m.timing_adjustment_time,
                   m.timing_adjustment_last_race, m.timing_adjustment_day_finished,
                   m.updated_at AS marks_updated_at
            FROM shifts s
            LEFT JOIN shift_marks m ON m.shift_id = s.id
            WHERE s.deleted_from_source = 0
              AND s.start_at >= ?
            ORDER BY s.start_at ASC
            LIMIT ?
            """,
            (now_iso, limit),
        ).fetchall()
    return rows


def write_sync_log(summary: dict[str, object]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sync_log (
                started_at, finished_at, status, message, events_seen,
                events_created, events_updated, events_marked_deleted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.get("started_at"),
                summary.get("finished_at"),
                summary.get("status"),
                summary.get("message"),
                summary.get("events_seen", 0),
                summary.get("events_created", 0),
                summary.get("events_updated", 0),
                summary.get("events_marked_deleted", 0),
            ),
        )


def write_shift_changes(conn: sqlite3.Connection, shift_id: int, changed_at: str, changes: dict[str, tuple[object, object]]) -> None:
    for field_name, (old_value, new_value) in changes.items():
        conn.execute(
            """
            INSERT INTO shift_changes (shift_id, changed_at, field_name, old_value, new_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                shift_id,
                changed_at,
                field_name,
                "" if old_value is None else str(old_value),
                "" if new_value is None else str(new_value),
            ),
        )


def mark_missing_future_shifts_deleted(
    conn: sqlite3.Connection,
    source_url_hash: str,
    seen_uids: Iterable[str],
    now_iso: str,
    changed_at: str,
) -> int:
    seen = list(seen_uids)
    where_sql = """
        WHERE source_url_hash = ?
          AND deleted_from_source = 0
          AND start_at >= ?
    """
    params: list[object] = [source_url_hash, now_iso]
    if seen:
        placeholders = ",".join("?" for _ in seen)
        where_sql += f" AND source_uid NOT IN ({placeholders})"
        params.extend(seen)

    rows = conn.execute(f"SELECT id FROM shifts {where_sql}", params).fetchall()
    for row in rows:
        write_shift_changes(
            conn,
            int(row["id"]),
            changed_at,
            {"deleted_from_source": (0, 1)},
        )

    result = conn.execute(
        f"""
        UPDATE shifts
        SET deleted_from_source = 1,
            changed_since_viewed = 1,
            last_changed_at = ?
        {where_sql}
        """,
        [changed_at, *params],
    )
    return result.rowcount
