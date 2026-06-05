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

            CREATE INDEX IF NOT EXISTS idx_shifts_date ON shifts(date);
            CREATE INDEX IF NOT EXISTS idx_shifts_start_at ON shifts(start_at);
            CREATE INDEX IF NOT EXISTS idx_shifts_changed ON shifts(changed_since_viewed);
            CREATE INDEX IF NOT EXISTS idx_shift_changes_shift ON shift_changes(shift_id, changed_at);
            CREATE INDEX IF NOT EXISTS idx_sync_log_started_at ON sync_log(started_at);
            """
        )
        _ensure_column(conn, "shifts", "source_link", "TEXT")
        _ensure_column(conn, "shifts", "source_status", "TEXT")
        _ensure_column(conn, "shift_marks", "timing_adjustment_time", "TEXT")
        _ensure_column(conn, "shift_marks", "timing_adjustment_last_race", "INTEGER DEFAULT 0")
        _ensure_column(conn, "shift_marks", "timing_adjustment_day_finished", "INTEGER DEFAULT 0")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


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
        result = conn.execute(
            "UPDATE shifts SET changed_since_viewed = 0 WHERE date = ?",
            (date_text,),
        )
        return result.rowcount


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


def clear_all_changed_flags() -> int:
    with get_connection() as conn:
        result = conn.execute(
            "UPDATE shifts SET changed_since_viewed = 0 WHERE changed_since_viewed = 1"
        )
        return result.rowcount


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
