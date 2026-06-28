from __future__ import annotations

import os
import re
import hashlib
import sqlite3
from datetime import datetime, timedelta
from typing import Iterable

from .config import Settings, get_settings


DEFAULT_CREW_POOL_NAME = "Northern Crew"


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
                owner_user_id INTEGER,
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
                display_theme TEXT DEFAULT 'jade',
                pin_hash TEXT,
                deputy_web_url TEXT,
                is_admin INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                last_seen_at TEXT,
                deactivated_at TEXT
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
                encrypted_ical_url TEXT,
                encrypted_session_json TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_sync_state (
                user_id INTEGER PRIMARY KEY,
                last_sync_at TEXT,
                next_sync_after TEXT,
                last_status TEXT,
                last_message TEXT,
                sync_in_progress INTEGER DEFAULT 0,
                last_planned_reason TEXT,
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

            CREATE TABLE IF NOT EXISTS error_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                user_id INTEGER,
                report_text TEXT,
                page_url TEXT,
                user_agent TEXT,
                diagnostics TEXT,
                status TEXT DEFAULT 'new',
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS deputy_web_captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER,
                captured_at TEXT,
                status TEXT,
                message TEXT,
                payload TEXT,
                created_at TEXT,
                FOREIGN KEY (owner_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS crew_pools (
                name TEXT PRIMARY KEY,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS user_crew_memberships (
                user_id INTEGER,
                crew_name TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, crew_name),
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (crew_name) REFERENCES crew_pools(name) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS crew_known_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crew_name TEXT,
                location_key TEXT,
                display_name TEXT,
                source_code TEXT,
                deputy_location_id INTEGER,
                first_seen_at TEXT,
                last_seen_at TEXT,
                source_user_id INTEGER,
                UNIQUE(crew_name, location_key),
                FOREIGN KEY (crew_name) REFERENCES crew_pools(name) ON DELETE CASCADE,
                FOREIGN KEY (source_user_id) REFERENCES app_users(id) ON DELETE SET NULL
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

            CREATE TABLE IF NOT EXISTS travel_time_defaults (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_key TEXT,
                track_label TEXT,
                base_label TEXT DEFAULT 'Clow Place',
                travel_minutes INTEGER,
                source TEXT DEFAULT 'manual',
                sample_count INTEGER DEFAULT 0,
                first_seen_at TEXT,
                last_seen_at TEXT,
                updated_at TEXT,
                note TEXT,
                UNIQUE(track_key, base_label)
            );

            CREATE TABLE IF NOT EXISTS deputy_schedule_areas (
                area_id INTEGER PRIMARY KEY,
                name TEXT,
                location_id INTEGER,
                roster_sort_order INTEGER,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS deputy_schedule_locations (
                location_id INTEGER PRIMARY KEY,
                name TEXT,
                address TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS deputy_schedule_shifts (
                source_shift_id INTEGER PRIMARY KEY,
                captured_at TEXT,
                area_id INTEGER,
                area_name TEXT,
                area_location_id INTEGER,
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

            CREATE TABLE IF NOT EXISTS love_racing_meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_date TEXT,
                racecourse_key TEXT,
                racecourse TEXT,
                club_name TEXT,
                source_url TEXT,
                source_hash TEXT UNIQUE,
                raw_text TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                last_synced_at TEXT,
                is_active INTEGER DEFAULT 1
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
            CREATE INDEX IF NOT EXISTS idx_error_reports_created ON error_reports(created_at);
            CREATE INDEX IF NOT EXISTS idx_deputy_web_captures_user ON deputy_web_captures(owner_user_id, captured_at DESC);
            CREATE INDEX IF NOT EXISTS idx_crew_known_locations_name ON crew_known_locations(crew_name, display_name);
            CREATE INDEX IF NOT EXISTS idx_capture_coverage_date ON capture_coverage(date);
            CREATE INDEX IF NOT EXISTS idx_user_sync_state_next ON user_sync_state(next_sync_after, sync_in_progress);
            CREATE INDEX IF NOT EXISTS idx_travel_time_defaults_track ON travel_time_defaults(track_key, base_label);
            CREATE INDEX IF NOT EXISTS idx_love_racing_meetings_date ON love_racing_meetings(meeting_date, racecourse_key);
            """
        )
        _ensure_default_crew_pool(conn)
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT OR IGNORE INTO user_crew_memberships (user_id, crew_name, created_at, updated_at)
            SELECT id, ?, ?, ?
            FROM app_users
            WHERE is_active = 1
            """,
            (DEFAULT_CREW_POOL_NAME, now, now),
        )
        _ensure_column(conn, "shifts", "source_link", "TEXT")
        _ensure_column(conn, "shifts", "source_status", "TEXT")
        _ensure_column(conn, "shifts", "owner_user_id", "INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shifts_owner ON shifts(owner_user_id)")
        _ensure_column(conn, "shift_marks", "timing_adjustment_time", "TEXT")
        _ensure_column(conn, "shift_marks", "timing_adjustment_last_race", "INTEGER DEFAULT 0")
        _ensure_column(conn, "shift_marks", "timing_adjustment_day_finished", "INTEGER DEFAULT 0")
        _ensure_column(conn, "deputy_schedule_shifts", "area_name", "TEXT")
        _ensure_column(conn, "deputy_schedule_shifts", "area_location_id", "INTEGER")
        _ensure_column(conn, "deputy_schedule_shifts", "area_roster_sort_order", "INTEGER")
        _ensure_column(conn, "deputy_schedule_shifts", "changed_since_viewed", "INTEGER DEFAULT 0")
        _ensure_column(conn, "deputy_schedule_shifts", "last_changed_at", "TEXT")
        _ensure_column(conn, "deputy_schedule_shifts", "change_summary", "TEXT")
        _ensure_column(conn, "app_users", "deputy_web_url", "TEXT")
        _ensure_column(conn, "app_users", "display_theme", "TEXT DEFAULT 'jade'")
        _ensure_column(conn, "deputy_user_secrets", "encrypted_ical_url", "TEXT")
        _ensure_column(conn, "app_users", "deactivated_at", "TEXT")
        _ensure_column(conn, "love_racing_meetings", "is_active", "INTEGER DEFAULT 1")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deputy_schedule_shifts_location ON deputy_schedule_shifts(date, area_location_id)"
        )
        conn.execute(
            """
            UPDATE deputy_schedule_shifts
            SET area_location_id = (
                SELECT location_id
                FROM deputy_schedule_areas a
                WHERE a.area_id = deputy_schedule_shifts.area_id
            )
            WHERE area_location_id IS NULL
            """
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_default_crew_pool(conn: sqlite3.Connection) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO crew_pools (name, created_at, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            updated_at = crew_pools.updated_at
        """,
        (DEFAULT_CREW_POOL_NAME, now, now),
    )


def _ensure_user_default_crew(conn: sqlite3.Connection, user_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    _ensure_default_crew_pool(conn)
    conn.execute(
        """
        INSERT INTO user_crew_memberships (user_id, crew_name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, crew_name) DO UPDATE SET
            updated_at = excluded.updated_at
        """,
        (user_id, DEFAULT_CREW_POOL_NAME, now, now),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def calendar_location_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


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
                s.next_sync_after,
                s.last_sync_at,
                s.last_status,
                s.last_message,
                s.sync_in_progress,
                s.last_planned_reason,
                secret.updated_at AS credentials_updated_at,
                CASE
                    WHEN TRIM(COALESCE(secret.encrypted_email, '')) != ''
                     AND TRIM(COALESCE(secret.encrypted_password, '')) != ''
                    THEN 1
                    ELSE 0
                END AS has_deputy_credentials,
                (
                    SELECT COUNT(*)
                    FROM trusted_devices d
                    WHERE d.user_id = u.id
                      AND d.revoked_at IS NULL
                      AND d.expires_at > ?
                ) AS active_devices
            FROM app_users u
            LEFT JOIN user_sync_state s ON s.user_id = u.id
            LEFT JOIN deputy_user_secrets secret ON secret.user_id = u.id
            ORDER BY u.is_admin DESC, LOWER(u.display_name), LOWER(u.deputy_email)
            """,
            (datetime.now(get_settings().timezone).isoformat(timespec="seconds"),),
        ).fetchall()
    return rows


def count_active_admins(excluding_user_id: int | None = None) -> int:
    with get_connection() as conn:
        params: list[object] = []
        exclude_sql = ""
        if excluding_user_id is not None:
            exclude_sql = "AND id != ?"
            params.append(excluding_user_id)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM app_users
            WHERE is_active = 1
              AND is_admin = 1
              {exclude_sql}
            """,
            params,
        ).fetchone()
    return int(row["count"] or 0) if row else 0


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


def get_deputy_user_secret(user_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT s.*, u.deputy_web_url, u.display_name, u.deputy_email, u.is_active
            FROM deputy_user_secrets s
            JOIN app_users u ON u.id = s.user_id
            WHERE s.user_id = ?
              AND u.is_active = 1
            """,
            (user_id,),
        ).fetchone()


def user_has_deputy_credentials(user_id: int) -> bool:
    row = get_deputy_user_secret(user_id)
    return bool(row and row["encrypted_email"] and row["encrypted_password"])


def user_has_ical_url(user_id: int) -> bool:
    row = get_deputy_user_secret(user_id)
    return bool(row and row["encrypted_ical_url"])


def update_deputy_user_ical_url(user_id: int, encrypted_ical_url: str) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE deputy_user_secrets
            SET encrypted_ical_url = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (encrypted_ical_url, now, user_id),
        )


def update_deputy_user_credentials(
    *,
    user_id: int,
    deputy_email: str,
    deputy_web_url: str,
    encrypted_email: str,
    encrypted_password: str,
) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    deputy_email = deputy_email.strip().lower()
    deputy_web_url = deputy_web_url.strip()
    with get_connection() as conn:
        result = conn.execute(
            """
                UPDATE app_users
                SET deputy_email = ?,
                    deputy_web_url = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (deputy_email, deputy_web_url, now, user_id),
            )
        if result.rowcount:
            conn.execute(
                """
                INSERT INTO deputy_user_secrets (
                    user_id, encrypted_email, encrypted_password, encrypted_ical_url, encrypted_session_json, updated_at
                )
                VALUES (?, ?, ?, '', '', ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    encrypted_email = excluded.encrypted_email,
                    encrypted_password = excluded.encrypted_password,
                    updated_at = excluded.updated_at
                """,
                (user_id, encrypted_email, encrypted_password, now),
            )
            conn.execute(
                """
                INSERT INTO user_sync_state (
                    user_id, last_sync_at, next_sync_after, last_status, last_message,
                    sync_in_progress, last_planned_reason, updated_at
                )
                VALUES (?, '', '', 'new', 'Deputy login updated. Run sync to test it.', 0, 'credentials_updated', ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    next_sync_after = '',
                    last_status = 'new',
                    last_message = 'Deputy login updated. Run sync to test it.',
                    sync_in_progress = 0,
                    last_planned_reason = 'credentials_updated',
                    updated_at = excluded.updated_at
                """,
                (user_id, now),
            )
    return result.rowcount


def list_syncable_app_users() -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.*,
                s.last_sync_at,
                s.next_sync_after,
                s.last_status,
                s.last_message,
                s.sync_in_progress,
                s.last_planned_reason
            FROM app_users u
            JOIN deputy_user_secrets secret ON secret.user_id = u.id
            LEFT JOIN user_sync_state s ON s.user_id = u.id
            WHERE u.is_active = 1
              AND TRIM(COALESCE(secret.encrypted_email, '')) != ''
              AND TRIM(COALESCE(secret.encrypted_password, '')) != ''
            ORDER BY u.id ASC
            """
        ).fetchall()
    return rows


def ensure_user_sync_state(user_id: int) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_sync_state (
                user_id, last_sync_at, next_sync_after, last_status, last_message,
                sync_in_progress, last_planned_reason, updated_at
            )
            VALUES (?, '', '', 'new', 'Waiting for first sync.', 0, '', ?)
            """,
            (user_id, now),
        )


def set_user_next_sync(user_id: int, next_sync_after: str, reason: str) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_sync_state (
                user_id, last_sync_at, next_sync_after, last_status, last_message,
                sync_in_progress, last_planned_reason, updated_at
            )
            VALUES (?, '', ?, 'planned', ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                next_sync_after = excluded.next_sync_after,
                last_status = CASE
                    WHEN user_sync_state.sync_in_progress = 1 THEN user_sync_state.last_status
                    ELSE excluded.last_status
                END,
                last_message = CASE
                    WHEN user_sync_state.sync_in_progress = 1 THEN user_sync_state.last_message
                    ELSE excluded.last_message
                END,
                last_planned_reason = excluded.last_planned_reason,
                updated_at = excluded.updated_at
            """,
            (user_id, next_sync_after, f"Planned {reason} sync.", reason, now),
        )


def get_due_user_syncs(now_iso: str, limit: int = 1) -> list[sqlite3.Row]:
    safe_limit = max(1, int(limit or 1))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                u.*,
                s.next_sync_after,
                s.last_planned_reason
            FROM user_sync_state s
            JOIN app_users u ON u.id = s.user_id
            JOIN deputy_user_secrets secret ON secret.user_id = u.id
            WHERE u.is_active = 1
              AND s.sync_in_progress = 0
              AND TRIM(COALESCE(s.next_sync_after, '')) != ''
              AND s.next_sync_after <= ?
              AND TRIM(COALESCE(secret.encrypted_email, '')) != ''
              AND TRIM(COALESCE(secret.encrypted_password, '')) != ''
            ORDER BY s.next_sync_after ASC, u.id ASC
            LIMIT ?
            """,
            (now_iso, safe_limit),
        ).fetchall()
    return rows


def mark_user_sync_started(user_id: int, started_at: str) -> bool:
    stale_cutoff = (
        datetime.now(get_settings().timezone) - timedelta(minutes=45)
    ).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE user_sync_state
            SET sync_in_progress = 1,
                last_status = 'running',
                last_message = 'Sync running.',
                updated_at = ?
            WHERE user_id = ?
              AND (
                    sync_in_progress = 0
                    OR COALESCE(updated_at, '') < ?
                  )
            """,
            (started_at, user_id, stale_cutoff),
        )
    return result.rowcount > 0


def mark_user_sync_finished(
    user_id: int,
    *,
    finished_at: str,
    status: str,
    message: str,
    next_sync_after: str = "",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_sync_state (
                user_id, last_sync_at, next_sync_after, last_status, last_message,
                sync_in_progress, last_planned_reason, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 0, '', ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_sync_at = excluded.last_sync_at,
                next_sync_after = excluded.next_sync_after,
                last_status = excluded.last_status,
                last_message = excluded.last_message,
                sync_in_progress = 0,
                updated_at = excluded.updated_at
            """,
            (user_id, finished_at, next_sync_after, status, message[:500], finished_at),
        )


def get_user_sync_state(user_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM user_sync_state
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return row


def reset_incomplete_user_syncs(message: str = "Previous sync stopped during app restart.") -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE user_sync_state
            SET sync_in_progress = 0,
                last_status = 'error',
                last_message = ?,
                updated_at = ?
            WHERE sync_in_progress = 1
            """,
            (message[:500], now),
        )
    return result.rowcount


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
                deputy_email, display_name, display_theme, pin_hash, deputy_web_url, is_admin,
                is_active, created_at, updated_at
            )
            VALUES (?, ?, 'jade', ?, ?, ?, 1, ?, ?)
            """,
            (deputy_email.strip(), display_name.strip(), pin_hash, deputy_web_url.strip(), is_admin, now, now),
        )
        user_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO deputy_user_secrets (
                user_id, encrypted_email, encrypted_password, encrypted_ical_url, encrypted_session_json, updated_at
            )
            VALUES (?, ?, ?, '', '', ?)
            """,
            (user_id, encrypted_email, encrypted_password, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO user_sync_state (
                user_id, last_sync_at, next_sync_after, last_status, last_message,
                sync_in_progress, last_planned_reason, updated_at
            )
            VALUES (?, '', '', 'new', 'Waiting for first sync.', 0, 'signup', ?)
            """,
            (user_id, now),
        )
        _ensure_user_default_crew(conn, user_id)
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
                u.display_theme,
                u.is_admin,
                u.is_active,
                s.last_sync_at
            FROM trusted_devices d
            JOIN app_users u ON u.id = d.user_id
            LEFT JOIN user_sync_state s ON s.user_id = u.id
            WHERE d.token_hash = ?
              AND d.revoked_at IS NULL
              AND d.expires_at > ?
              AND u.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()


def update_trusted_device_seen(device_id: int, expires_at: str | None = None) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE trusted_devices
            SET last_seen_at = ?,
                expires_at = COALESCE(?, expires_at)
            WHERE id = ?
            """,
            (now, expires_at, device_id),
        )


def list_trusted_devices_for_user(user_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM trusted_devices
            WHERE user_id = ?
            ORDER BY revoked_at IS NOT NULL, last_seen_at DESC, created_at DESC
            """,
            (user_id,),
        ).fetchall()


def revoke_trusted_device(device_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE trusted_devices SET revoked_at = ? WHERE id = ?",
            (now, device_id),
        )


def revoke_trusted_device_for_user(user_id: int, device_id: int) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE trusted_devices
            SET revoked_at = ?
            WHERE id = ?
              AND user_id = ?
              AND revoked_at IS NULL
            """,
            (now, device_id, user_id),
        )
    return result.rowcount


def update_user_pin_hash(user_id: int, pin_hash: str) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE app_users
            SET pin_hash = ?,
                updated_at = ?
            WHERE id = ?
              AND is_active = 1
            """,
            (pin_hash, now, user_id),
        )
    return result.rowcount


def update_user_display_theme(user_id: int, display_theme: str) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE app_users
            SET display_theme = ?,
                updated_at = ?
            WHERE id = ?
              AND is_active = 1
            """,
            (display_theme, now, user_id),
        )
    return result.rowcount


def set_app_user_active(user_id: int, is_active: bool) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE app_users
            SET is_active = ?,
                updated_at = ?,
                deactivated_at = CASE WHEN ? THEN NULL ELSE COALESCE(deactivated_at, ?) END
            WHERE id = ?
            """,
            (1 if is_active else 0, now, 1 if is_active else 0, now, user_id),
        )
        if result.rowcount and not is_active:
            conn.execute(
                """
                UPDATE trusted_devices
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE user_id = ?
                """,
                (now, user_id),
            )
            conn.execute(
                """
                UPDATE user_sync_state
                SET next_sync_after = '',
                    last_status = 'disabled',
                    last_message = 'User deactivated by admin.',
                    sync_in_progress = 0,
                    last_planned_reason = 'disabled',
                    updated_at = ?
                WHERE user_id = ?
                """,
                (now, user_id),
            )
        elif result.rowcount:
            conn.execute(
                """
                INSERT INTO user_sync_state (
                    user_id, last_sync_at, next_sync_after, last_status, last_message,
                    sync_in_progress, last_planned_reason, updated_at
                )
                VALUES (?, '', '', 'new', 'User reactivated. Run sync to refresh roster.', 0, 'reactivated', ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_status = 'new',
                    last_message = 'User reactivated. Run sync to refresh roster.',
                    sync_in_progress = 0,
                    last_planned_reason = 'reactivated',
                    updated_at = excluded.updated_at
                """,
                (user_id, now),
            )
    return result.rowcount


def purge_app_user(user_id: int) -> dict[str, int]:
    with get_connection() as conn:
        user = conn.execute(
            "SELECT * FROM app_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if user is None:
            return {"users": 0, "devices": 0, "shifts": 0, "marks": 0, "changes": 0}
        if int(user["is_active"] or 0):
            return {"users": 0, "devices": 0, "shifts": 0, "marks": 0, "changes": 0}

        deleted_marks = conn.execute(
            "DELETE FROM shift_marks WHERE shift_id IN (SELECT id FROM shifts WHERE owner_user_id = ?)",
            (user_id,),
        ).rowcount
        deleted_changes = conn.execute(
            "DELETE FROM shift_changes WHERE shift_id IN (SELECT id FROM shifts WHERE owner_user_id = ?)",
            (user_id,),
        ).rowcount
        deleted_shifts = conn.execute(
            "DELETE FROM shifts WHERE owner_user_id = ?",
            (user_id,),
        ).rowcount
        conn.execute("DELETE FROM deputy_user_secrets WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_sync_state WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_crew_memberships WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE error_reports SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE deputy_web_captures SET owner_user_id = NULL WHERE owner_user_id = ?", (user_id,))
        conn.execute("UPDATE crew_known_locations SET source_user_id = NULL WHERE source_user_id = ?", (user_id,))
        conn.execute("UPDATE capture_coverage SET source_user_id = NULL WHERE source_user_id = ?", (user_id,))
        deleted_devices = conn.execute("DELETE FROM trusted_devices WHERE user_id = ?", (user_id,)).rowcount
        deleted_user = conn.execute("DELETE FROM app_users WHERE id = ?", (user_id,)).rowcount
    return {
        "users": deleted_user,
        "devices": deleted_devices,
        "shifts": deleted_shifts,
        "marks": deleted_marks,
        "changes": deleted_changes,
    }


def purge_old_inactive_records(days: int = 30) -> dict[str, int]:
    cutoff = (datetime.now(get_settings().timezone) - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")
    purged_users = 0
    with get_connection() as conn:
        revoked_devices = conn.execute(
            """
            DELETE FROM trusted_devices
            WHERE revoked_at IS NOT NULL
              AND revoked_at < ?
            """,
            (cutoff,),
        ).rowcount
        inactive_users = conn.execute(
            """
            SELECT id
            FROM app_users
            WHERE is_active = 0
              AND COALESCE(deactivated_at, updated_at, created_at) < ?
            """,
            (cutoff,),
        ).fetchall()
    for user in inactive_users:
        result = purge_app_user(int(user["id"]))
        purged_users += int(result.get("users", 0))
    return {"users": purged_users, "devices": revoked_devices}


def reset_user_roster_data(user_id: int) -> dict[str, int]:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        deleted_marks = conn.execute(
            "DELETE FROM shift_marks WHERE shift_id IN (SELECT id FROM shifts WHERE owner_user_id = ?)",
            (user_id,),
        ).rowcount
        deleted_changes = conn.execute(
            "DELETE FROM shift_changes WHERE shift_id IN (SELECT id FROM shifts WHERE owner_user_id = ?)",
            (user_id,),
        ).rowcount
        deleted_shifts = conn.execute(
            "DELETE FROM shifts WHERE owner_user_id = ?",
            (user_id,),
        ).rowcount
        conn.execute(
            """
            INSERT INTO user_sync_state (
                user_id, last_sync_at, next_sync_after, last_status, last_message,
                sync_in_progress, last_planned_reason, updated_at
            )
            VALUES (?, '', '', 'new', 'Roster data reset. Run sync to rebuild it.', 0, 'roster_reset', ?)
            ON CONFLICT(user_id) DO UPDATE SET
                next_sync_after = '',
                last_status = 'new',
                last_message = 'Roster data reset. Run sync to rebuild it.',
                sync_in_progress = 0,
                last_planned_reason = 'roster_reset',
                updated_at = excluded.updated_at
            """,
            (user_id, now),
        )
    return {
        "shifts": deleted_shifts,
        "marks": deleted_marks,
        "changes": deleted_changes,
    }


def create_error_report(
    *,
    user_id: int | None,
    report_text: str,
    page_url: str,
    user_agent: str,
    diagnostics: str,
) -> sqlite3.Row:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO error_reports (
                created_at, user_id, report_text, page_url, user_agent, diagnostics, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'new')
            """,
            (now, user_id, report_text[:4000], page_url[:1000], user_agent[:500], diagnostics),
        )
        return conn.execute(
            """
            SELECT r.*, u.display_name, u.deputy_email
            FROM error_reports r
            LEFT JOIN app_users u ON u.id = r.user_id
            WHERE r.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()


def list_error_reports(limit: int = 12) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT r.*, u.display_name, u.deputy_email
            FROM error_reports r
            LEFT JOIN app_users u ON u.id = r.user_id
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (max(1, int(limit or 12)),),
        ).fetchall()


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


def list_travel_time_defaults() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM travel_time_defaults
            ORDER BY
                CASE source WHEN 'manual' THEN 0 ELSE 1 END,
                LOWER(track_label),
                LOWER(base_label)
            """
        ).fetchall()


def list_known_racecourse_names() -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        text = str(value or "").strip()
        if not text:
            return
        text = re.sub(r"^[THG]-", "", text, flags=re.IGNORECASE).strip()
        if not text or text.lower() in {"web", "shift", "vehicles", "travel"}:
            return
        key = calendar_location_key(text)
        if not key or key in seen:
            return
        seen.add(key)
        names.append(text)

    with get_connection() as conn:
        for row in conn.execute(
            """
            SELECT display_name AS name
            FROM crew_known_locations
            WHERE TRIM(COALESCE(display_name, '')) != ''
            UNION
            SELECT name
            FROM deputy_schedule_locations
            WHERE TRIM(COALESCE(name, '')) != ''
            UNION
            SELECT track_label AS name
            FROM travel_time_defaults
            WHERE TRIM(COALESCE(track_label, '')) != ''
            UNION
            SELECT location AS name
            FROM shifts
            WHERE TRIM(COALESCE(location, '')) != ''
            """
        ).fetchall():
            add(row["name"])

        for row in conn.execute(
            """
            SELECT title
            FROM shifts
            WHERE TRIM(COALESCE(title, '')) != ''
            """
        ).fetchall():
            match = re.match(r"^\[([^\]]+)\]", str(row["title"] or ""))
            if match:
                add(match.group(1))

    if not names:
        for fallback in (
            "Cambridge",
            "Cambridge Synthetic",
            "Ellerslie",
            "Matamata",
            "Pukekohe",
            "Rotorua",
            "Ruakaka",
            "Tauranga",
            "Te Aroha",
            "Te Rapa",
        ):
            add(fallback)
    return names


def save_love_racing_meetings(meetings: list[dict[str, object]], synced_at: str | None = None) -> int:
    synced_at = synced_at or datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    saved = 0
    with get_connection() as conn:
        for meeting in meetings:
            meeting_date = str(meeting.get("date") or meeting.get("meeting_date") or "").strip()
            racecourse = str(meeting.get("racecourse") or "").strip()
            if not meeting_date or not racecourse:
                continue
            racecourse_key = str(meeting.get("racecourse_key") or calendar_location_key(racecourse)).strip()
            club_name = str(meeting.get("club_name") or "").strip()
            source_url = str(meeting.get("source_url") or "").strip()
            raw_text = str(meeting.get("raw_text") or "").strip()
            source_hash = str(meeting.get("source_hash") or "").strip()
            if not source_hash:
                source_hash = hashlib.sha256(
                    "|".join([meeting_date, racecourse_key, club_name, raw_text]).encode("utf-8")
                ).hexdigest()
            conn.execute(
                """
                INSERT INTO love_racing_meetings (
                    meeting_date, racecourse_key, racecourse, club_name,
                    source_url, source_hash, raw_text, first_seen_at,
                    last_seen_at, last_synced_at, is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_hash) DO UPDATE SET
                    meeting_date = excluded.meeting_date,
                    racecourse_key = excluded.racecourse_key,
                    racecourse = excluded.racecourse,
                    club_name = excluded.club_name,
                    source_url = excluded.source_url,
                    raw_text = excluded.raw_text,
                    last_seen_at = excluded.last_seen_at,
                    last_synced_at = excluded.last_synced_at,
                    is_active = 1
                """,
                (
                    meeting_date,
                    racecourse_key,
                    racecourse,
                    club_name,
                    source_url,
                    source_hash,
                    raw_text,
                    synced_at,
                    synced_at,
                    synced_at,
                ),
            )
            saved += 1
    return saved


def fetch_love_racing_meetings_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM love_racing_meetings
            WHERE is_active = 1
              AND meeting_date BETWEEN ? AND ?
            ORDER BY meeting_date ASC, racecourse ASC, id ASC
            """,
            (start_date, end_date),
        ).fetchall()


def get_love_racing_snapshot(today: str | None = None) -> dict[str, object]:
    today = today or datetime.now(get_settings().timezone).date().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN meeting_date >= ? THEN 1 ELSE 0 END) AS upcoming_rows,
                COUNT(DISTINCT racecourse_key) AS location_count,
                MIN(meeting_date) AS first_date,
                MAX(meeting_date) AS last_date,
                MAX(last_synced_at) AS last_synced_at
            FROM love_racing_meetings
            WHERE is_active = 1
            """,
            (today,),
        ).fetchone()
        upcoming = conn.execute(
            """
            SELECT *
            FROM love_racing_meetings
            WHERE is_active = 1
              AND meeting_date >= ?
            ORDER BY meeting_date ASC, racecourse ASC
            LIMIT 8
            """,
            (today,),
        ).fetchall()
    return {
        "total_rows": int(row["total_rows"] or 0) if row else 0,
        "upcoming_rows": int(row["upcoming_rows"] or 0) if row else 0,
        "location_count": int(row["location_count"] or 0) if row else 0,
        "first_date": row["first_date"] or "" if row else "",
        "last_date": row["last_date"] or "" if row else "",
        "last_synced_at": row["last_synced_at"] or "" if row else "",
        "upcoming": [dict(item) for item in upcoming],
    }


def get_travel_time_default(track_keys: list[str], base_label: str = "Clow Place") -> sqlite3.Row | None:
    keys = [key.strip().lower() for key in track_keys if str(key or "").strip()]
    if not keys:
        return None
    placeholders = ",".join("?" for _ in keys)
    params: list[object] = [*keys, base_label.strip() or "Clow Place"]
    with get_connection() as conn:
        return conn.execute(
            f"""
            SELECT *
            FROM travel_time_defaults
            WHERE track_key IN ({placeholders})
              AND LOWER(base_label) = LOWER(?)
            ORDER BY CASE source WHEN 'manual' THEN 0 ELSE 1 END, sample_count DESC, updated_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()


def upsert_travel_time_default(
    *,
    track_key: str,
    track_label: str,
    base_label: str,
    travel_minutes: int,
    source: str,
    sample_count: int = 0,
    first_seen_at: str = "",
    last_seen_at: str = "",
    note: str = "",
) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    clean_source = "manual" if source != "learned" else "learned"
    clean_base = base_label.strip() or "Clow Place"
    clean_key = track_key.strip().lower()
    if not clean_key or not track_label.strip() or travel_minutes <= 0:
        return
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO travel_time_defaults (
                track_key, track_label, base_label, travel_minutes, source,
                sample_count, first_seen_at, last_seen_at, updated_at, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_key, base_label) DO UPDATE SET
                track_label = excluded.track_label,
                travel_minutes = CASE
                    WHEN travel_time_defaults.source = 'manual' AND excluded.source = 'learned'
                    THEN travel_time_defaults.travel_minutes
                    ELSE excluded.travel_minutes
                END,
                source = CASE
                    WHEN travel_time_defaults.source = 'manual' AND excluded.source = 'learned'
                    THEN travel_time_defaults.source
                    ELSE excluded.source
                END,
                sample_count = CASE
                    WHEN travel_time_defaults.source = 'manual' AND excluded.source = 'learned'
                    THEN travel_time_defaults.sample_count
                    ELSE excluded.sample_count
                END,
                first_seen_at = COALESCE(NULLIF(travel_time_defaults.first_seen_at, ''), excluded.first_seen_at),
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at,
                note = CASE
                    WHEN travel_time_defaults.source = 'manual' AND excluded.source = 'learned'
                    THEN travel_time_defaults.note
                    ELSE excluded.note
                END
            """,
            (
                clean_key,
                track_label.strip(),
                clean_base,
                max(1, int(travel_minutes)),
                clean_source,
                max(0, int(sample_count or 0)),
                first_seen_at,
                last_seen_at,
                now,
                note.strip(),
            ),
        )


def delete_travel_time_default(default_id: int) -> int:
    with get_connection() as conn:
        result = conn.execute(
            "DELETE FROM travel_time_defaults WHERE id = ?",
            (default_id,),
        )
    return result.rowcount


def update_travel_time_default(
    default_id: int,
    *,
    track_key: str,
    track_label: str,
    base_label: str,
    travel_minutes: int,
    note: str = "",
) -> int:
    clean_key = track_key.strip().lower()
    clean_label = track_label.strip()
    clean_base = base_label.strip() or "Clow Place"
    if not clean_key or not clean_label or travel_minutes <= 0:
        return 0
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE travel_time_defaults
            SET track_key = ?,
                track_label = ?,
                base_label = ?,
                travel_minutes = ?,
                source = 'manual',
                updated_at = ?,
                note = ?
            WHERE id = ?
            """,
            (
                clean_key,
                clean_label,
                clean_base,
                max(1, int(travel_minutes)),
                now,
                note.strip(),
                default_id,
            ),
        )
    return result.rowcount


def fetch_shifts_for_travel_learning(limit: int = 800) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT id, title, description, location, start_at, end_at, date, source_payload
            FROM shifts
            WHERE deleted_from_source = 0
              AND TRIM(COALESCE(description, '')) != ''
            ORDER BY date DESC, start_at DESC
            LIMIT ?
            """,
            (max(1, int(limit or 800)),),
        ).fetchall()


def ensure_shift_mark(conn: sqlite3.Connection, shift_id: int) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT OR IGNORE INTO shift_marks (shift_id, updated_at)
        VALUES (?, ?)
        """,
        (shift_id, now),
    )


def fetch_shifts_between(
    start_date: str,
    end_date: str,
    owner_user_id: int | None = None,
) -> list[sqlite3.Row]:
    owner_sql = ""
    params: list[object] = [start_date, end_date]
    if owner_user_id is not None:
        owner_sql = "AND s.owner_user_id = ?"
        params.append(owner_user_id)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT s.*, m.checked, m.confirmed, m.important, m.question,
                   m.early_start, m.gear_needed, m.travel_needed, m.pay_check,
                   m.private_note, m.custom_colour, m.timing_adjustment_time,
                   m.timing_adjustment_last_race, m.timing_adjustment_day_finished,
                   m.updated_at AS marks_updated_at
            FROM shifts s
            LEFT JOIN shift_marks m ON m.shift_id = s.id
            WHERE s.date BETWEEN ? AND ?
              {owner_sql}
            ORDER BY s.start_at, s.id
            """,
            params,
        ).fetchall()
    return rows


def fetch_shifts_for_date(date_text: str, owner_user_id: int | None = None) -> list[sqlite3.Row]:
    return fetch_shifts_between(date_text, date_text, owner_user_id=owner_user_id)


def fetch_shift(shift_id: int, owner_user_id: int | None = None) -> sqlite3.Row | None:
    owner_sql = ""
    params: list[object] = [shift_id]
    if owner_user_id is not None:
        owner_sql = "AND s.owner_user_id = ?"
        params.append(owner_user_id)
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT s.*, m.checked, m.confirmed, m.important, m.question,
                   m.early_start, m.gear_needed, m.travel_needed, m.pay_check,
                   m.private_note, m.custom_colour, m.timing_adjustment_time,
                   m.timing_adjustment_last_race, m.timing_adjustment_day_finished,
                   m.updated_at AS marks_updated_at
            FROM shifts s
            LEFT JOIN shift_marks m ON m.shift_id = s.id
            WHERE s.id = ?
              {owner_sql}
            """,
            params,
        ).fetchone()
    return row


def update_shift_marks(shift_id: int, values: dict[str, object], owner_user_id: int | None = None) -> bool:
    owner_sql = ""
    params: list[object] = [shift_id]
    if owner_user_id is not None:
        owner_sql = "AND owner_user_id = ?"
        params.append(owner_user_id)
    with get_connection() as conn:
        shift = conn.execute(f"SELECT id FROM shifts WHERE id = ? {owner_sql}", params).fetchone()
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


def clear_changed_for_date(date_text: str, owner_user_id: int | None = None, include_schedule: bool = False) -> int:
    owner_sql = ""
    params: list[object] = [date_text]
    if owner_user_id is not None:
        owner_sql = "AND owner_user_id = ?"
        params.append(owner_user_id)
    with get_connection() as conn:
        shift_result = conn.execute(
            f"UPDATE shifts SET changed_since_viewed = 0 WHERE date = ? {owner_sql}",
            params,
        )
        schedule_result_count = 0
        if include_schedule:
            schedule_result = conn.execute(
                """
                UPDATE deputy_schedule_shifts
                SET changed_since_viewed = 0,
                    change_summary = ''
                WHERE date = ?
                """,
                (date_text,),
            )
            schedule_result_count = schedule_result.rowcount
        return shift_result.rowcount + schedule_result_count


def clear_changed_for_shift(shift_id: int, owner_user_id: int | None = None) -> int:
    owner_sql = ""
    params: list[object] = [shift_id]
    if owner_user_id is not None:
        owner_sql = "AND owner_user_id = ?"
        params.append(owner_user_id)
    with get_connection() as conn:
        result = conn.execute(
            f"UPDATE shifts SET changed_since_viewed = 0 WHERE id = ? {owner_sql}",
            params,
        )
        return result.rowcount


def clear_changed_flags_for_user(owner_user_id: int) -> int:
    with get_connection() as conn:
        result = conn.execute(
            """
            UPDATE shifts
            SET changed_since_viewed = 0
            WHERE owner_user_id = ?
              AND changed_since_viewed = 1
            """,
            (owner_user_id,),
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


def _normalise_int_list(values: list[int] | tuple[int, ...] | set[int] | None) -> list[int]:
    if not values:
        return []
    normalised = []
    seen = set()
    for value in values:
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            continue
        if int_value in seen:
            continue
        seen.add(int_value)
        normalised.append(int_value)
    return normalised


def fetch_deputy_schedule_for_date(
    date_text: str,
    location_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> list[sqlite3.Row]:
    location_ids = _normalise_int_list(location_ids)
    location_sql = ""
    params: list[object] = [date_text]
    if location_ids:
        placeholders = ", ".join("?" for _ in location_ids)
        location_sql = f"AND COALESCE(s.area_location_id, a.location_id) IN ({placeholders})"
        params.extend(location_ids)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT s.*,
                   COALESCE(s.area_location_id, a.location_id) AS schedule_location_id
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a ON a.area_id = s.area_id
            WHERE s.date = ?
              {location_sql}
            ORDER BY
                COALESCE(s.area_roster_sort_order, 999999),
                s.area_name,
                s.start_at,
                s.employee_name
            """,
            params,
        ).fetchall()
    return rows


def fetch_deputy_schedule_areas_for_locations(
    location_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> list[sqlite3.Row]:
    location_ids = _normalise_int_list(location_ids)
    if not location_ids:
        return []
    placeholders = ", ".join("?" for _ in location_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
                area_id,
                name,
                location_id,
                roster_sort_order
            FROM deputy_schedule_areas
            WHERE location_id IN ({placeholders})
            ORDER BY
                COALESCE(roster_sort_order, 999999),
                name
            """,
            location_ids,
        ).fetchall()
    return rows


def has_deputy_schedule_changes_for_date(
    date_text: str,
    location_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> bool:
    location_ids = _normalise_int_list(location_ids)
    location_sql = ""
    params: list[object] = [date_text]
    if location_ids:
        placeholders = ", ".join("?" for _ in location_ids)
        location_sql = f"AND COALESCE(s.area_location_id, a.location_id) IN ({placeholders})"
        params.extend(location_ids)

    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT 1
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a ON a.area_id = s.area_id
            WHERE s.date = ?
              {location_sql}
              AND s.changed_since_viewed = 1
              AND (
                s.change_summary LIKE '%Person:%'
                OR s.change_summary LIKE '%Position:%'
                OR s.change_summary LIKE '%Open shift:%'
              )
            LIMIT 1
            """,
            params,
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


def fetch_tbc_deputy_schedule_between(start_date: str, end_date: str, limit: int = 60) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*,
                   COALESCE(s.area_location_id, a.location_id) AS schedule_location_id,
                   l.name AS location_name
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a
              ON a.area_id = s.area_id
            LEFT JOIN deputy_schedule_locations l
              ON l.location_id = COALESCE(s.area_location_id, a.location_id)
            WHERE s.date BETWEEN ? AND ?
              AND (
                LOWER(TRIM(COALESCE(s.employee_name, ''))) = 'tbc'
                OR LOWER(TRIM(COALESCE(s.employee_name, ''))) LIKE 'tbc %'
                OR (
                  TRIM(COALESCE(s.employee_name, '')) = ''
                  AND COALESCE(s.is_open, 0) = 0
                )
              )
              AND LOWER(TRIM(COALESCE(s.area_name, ''))) NOT IN ('fm', 'floor manager')
            ORDER BY
                s.date ASC,
                s.start_at ASC,
                COALESCE(s.area_roster_sort_order, 999999),
                s.area_name ASC
            LIMIT ?
            """,
            (start_date, end_date, limit),
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


def save_deputy_web_capture_diagnostic(
    *,
    owner_user_id: int | None,
    captured_at: str,
    status: str,
    message: str,
    payload: str,
) -> None:
    created_at = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO deputy_web_captures (
                owner_user_id, captured_at, status, message, payload, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (owner_user_id, captured_at, status[:80], message[:500], payload, created_at),
        )
        if owner_user_id is None:
            conn.execute(
                """
                DELETE FROM deputy_web_captures
                WHERE owner_user_id IS NULL
                  AND id NOT IN (
                      SELECT id
                      FROM deputy_web_captures
                      WHERE owner_user_id IS NULL
                      ORDER BY captured_at DESC, id DESC
                      LIMIT 12
                  )
                """
            )
        else:
            conn.execute(
                """
                DELETE FROM deputy_web_captures
                WHERE owner_user_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM deputy_web_captures
                      WHERE owner_user_id = ?
                      ORDER BY captured_at DESC, id DESC
                      LIMIT 12
                  )
                """,
                (owner_user_id, owner_user_id),
            )


def get_latest_deputy_web_capture_for_user(owner_user_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM deputy_web_captures
            WHERE owner_user_id = ?
            ORDER BY captured_at DESC, id DESC
            LIMIT 1
            """,
            (owner_user_id,),
        ).fetchone()


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


DEPUTY_LOCATION_CODES = {
    29: "G-Cambridge",
    62: "T-Pukekohe",
    63: "TRAP-T",
    64: "CAMS-T",
    66: "TAUR-T",
    68: "MATA-T",
    69: "TARO-T",
    105: "8PE",
    121: "H-Cambridge",
    129: "T-Rotorua",
}
DEPUTY_LOCATION_ADDRESSES = {
    29: "1 Taylor Street",
    62: "222 Manukau Road",
    63: "12 Sir Tristram Avenue",
    64: "40 Racecourse Road",
    66: "1383 Cameron Road",
    68: "State Highway 27",
    69: "Stanley Road South",
    105: "National",
    121: "1 Taylor Street",
    129: "274-278 Fenton Street, Glenholme, Rotorua 3010",
}

H_CAMBRIDGE_AREA_CONTEXT = {
    "source_code": "H-Cambridge",
    "location": "1 Taylor Street",
    "location_id": 121,
}
DEPUTY_AREA_OVERRIDES = {
    1192: {**H_CAMBRIDGE_AREA_CONTEXT, "role": "Side 1"},
    1193: {**H_CAMBRIDGE_AREA_CONTEXT, "role": "Side 2"},
    1194: {**H_CAMBRIDGE_AREA_CONTEXT, "role": "Head On"},
    1196: {**H_CAMBRIDGE_AREA_CONTEXT, "role": "DIR"},
    1550: {**H_CAMBRIDGE_AREA_CONTEXT, "role": "684"},
    1488: {"source_code": "VEH", "role": "Vehicles", "location": "6 Clow Place"},
}


def _static_location_lookup() -> dict[int, dict[str, object]]:
    locations = {}
    for location_id, source_code in DEPUTY_LOCATION_CODES.items():
        locations[location_id] = {
            "id": location_id,
            "name": source_code,
            "address": DEPUTY_LOCATION_ADDRESSES.get(location_id, ""),
        }
    return locations


def _location_source_code(location_id: int | None, location_lookup: dict[int, dict[str, object]]) -> str:
    if location_id is None:
        return ""
    location = location_lookup.get(location_id) or {}
    name = str(location.get("name") or "").strip()
    if not name:
        return DEPUTY_LOCATION_CODES.get(location_id, "")
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"^([A-Z])\s*-\s*", r"\1-", name, flags=re.IGNORECASE)
    return name


def _location_id_for_source_code(source_code: str | None) -> int | None:
    source_code = re.sub(r"\s+", "", str(source_code or "").strip().upper())
    if not source_code:
        return None
    for location_id, location_code in DEPUTY_LOCATION_CODES.items():
        if re.sub(r"\s+", "", location_code.upper()) == source_code:
            return location_id
    return None


def _location_address(location_id: int | None, location_lookup: dict[int, dict[str, object]]) -> str:
    if location_id is None:
        return ""
    location = location_lookup.get(location_id) or {}
    return str(location.get("address") or DEPUTY_LOCATION_ADDRESSES.get(location_id, "") or "").strip()


def _clean_role_name(value: object) -> str:
    role = str(value or "").strip()
    bracketed = re.match(r"^\[[^\]]+\]\s*(.+)$", role)
    return bracketed.group(1).strip() if bracketed else role


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


def save_deputy_web_schedule(payload: dict[str, object], owner_user_id: int | None = None) -> dict[str, int]:
    captured_at = str(payload.get("captured_at") or datetime.now().isoformat(timespec="seconds"))
    areas = payload.get("areas") if isinstance(payload.get("areas"), list) else []
    locations = payload.get("locations") if isinstance(payload.get("locations"), list) else []
    own_shifts = payload.get("extracted_shifts") if isinstance(payload.get("extracted_shifts"), list) else []
    shifts = payload.get("extracted_schedule_shifts") if isinstance(payload.get("extracted_schedule_shifts"), list) else []
    area_lookup: dict[str, dict[str, object]] = {}
    location_lookup: dict[int, dict[str, object]] = _static_location_lookup()
    schedule_shift_lookup = {
        str(shift.get("id")): shift
        for shift in shifts
        if isinstance(shift, dict) and shift.get("id") not in (None, "")
    }

    with get_connection() as conn:
        for row in conn.execute("SELECT * FROM deputy_schedule_locations").fetchall():
            location_id = _optional_int(row["location_id"])
            if location_id is None:
                continue
            location_lookup[location_id] = {
                "id": location_id,
                "name": row["name"] or "",
                "address": row["address"] or "",
            }

        for location in locations:
            if not isinstance(location, dict) or location.get("id") in (None, ""):
                continue
            location_id = int(location["id"])
            name = str(location.get("name") or location_id).strip()
            address = str(location.get("address") or "").strip()
            location_lookup[location_id] = {
                "id": location_id,
                "name": name,
                "address": address,
            }
            conn.execute(
                """
                INSERT INTO deputy_schedule_locations (
                    location_id, name, address, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    name = excluded.name,
                    address = excluded.address,
                    updated_at = excluded.updated_at
                """,
                (location_id, name, address, captured_at),
            )

        for shift in list(own_shifts) + list(shifts):
            if not isinstance(shift, dict):
                continue
            location_id = _optional_int(shift.get("location") or shift.get("locationId") or shift.get("location_id"))
            name = str(shift.get("locationName") or shift.get("LocationName") or "").strip()
            if location_id is None or not name:
                continue
            address = DEPUTY_LOCATION_ADDRESSES.get(location_id, "")
            location_lookup[location_id] = {
                "id": location_id,
                "name": name,
                "address": address,
            }
            conn.execute(
                """
                INSERT INTO deputy_schedule_locations (
                    location_id, name, address, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    name = excluded.name,
                    address = CASE
                        WHEN TRIM(excluded.address) != '' THEN excluded.address
                        ELSE deputy_schedule_locations.address
                    END,
                    updated_at = excluded.updated_at
                """,
                (location_id, name, address, captured_at),
            )

        for row in conn.execute("SELECT * FROM deputy_schedule_areas").fetchall():
            area_id = _optional_int(row["area_id"])
            if area_id is None:
                continue
            area_lookup[str(area_id)] = {
                "id": area_id,
                "name": row["name"] or "",
                "locationId": row["location_id"],
                "rosterSortOrder": row["roster_sort_order"],
            }

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

        own_counts = _save_deputy_web_own_shifts(
            conn,
            own_shifts,
            schedule_shift_lookup,
            area_lookup,
            location_lookup,
            captured_at,
            owner_user_id,
        )
        saved = 0
        for shift in shifts:
            if not isinstance(shift, dict) or shift.get("id") in (None, ""):
                continue
            area_id = _optional_int(shift.get("area"))
            area_override = DEPUTY_AREA_OVERRIDES.get(area_id or -1, {})
            area = area_lookup.get(str(area_id)) if area_id is not None else None
            area_name = str(shift.get("areaName") or (area or {}).get("name") or area_override.get("role") or area_id or "")
            area_location_id = _optional_int(shift.get("location") or shift.get("locationId") or shift.get("location_id"))
            if area_location_id is None:
                area_location_id = _optional_int(shift.get("areaLocationId"))
            if area_location_id is None and area:
                area_location_id = _optional_int(area.get("locationId"))
            if area_location_id is None and area_override:
                area_location_id = _optional_int(area_override.get("location_id"))
            if area_location_id is None and area_override:
                area_location_id = _location_id_for_source_code(str(area_override.get("source_code") or ""))
            area_sort = _optional_int(shift.get("areaRosterSortOrder"))
            if area_sort is None and area:
                area_sort = _optional_int(area.get("rosterSortOrder"))
            start_at = str(shift.get("start") or "")
            end_at = str(shift.get("end") or "")
            source_shift_id = int(shift["id"])
            values = {
                "area_id": area_id,
                "area_name": area_name,
                "area_location_id": area_location_id,
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
                    area_location_id, area_roster_sort_order, employee_id, employee_name,
                    start_at, end_at, date, duration, is_open, is_published,
                    changed_since_viewed, last_changed_at, change_summary,
                    note, raw_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_shift_id) DO UPDATE SET
                    captured_at = excluded.captured_at,
                    area_id = excluded.area_id,
                    area_name = excluded.area_name,
                    area_location_id = excluded.area_location_id,
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
                    values["area_location_id"],
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
    return {
        "own_seen": own_counts["seen"],
        "own_created": own_counts["created"],
        "own_updated": own_counts["updated"],
        "schedule_saved": saved,
    }


def _save_deputy_web_own_shifts(
    conn: sqlite3.Connection,
    own_shifts: list[object],
    schedule_shift_lookup: dict[str, object],
    area_lookup: dict[str, dict[str, object]],
    location_lookup: dict[int, dict[str, object]],
    captured_at: str,
    owner_user_id: int | None,
) -> dict[str, int]:
    counts = {"seen": 0, "created": 0, "updated": 0}
    source_owner = owner_user_id if owner_user_id is not None else "env"
    source_url_hash = f"deputy-web:{source_owner}"
    for shift in own_shifts:
        if not isinstance(shift, dict) or shift.get("id") in (None, ""):
            continue
        shift_id = str(shift["id"])
        rich_shift = schedule_shift_lookup.get(shift_id)
        merged_shift = dict(shift)
        if isinstance(rich_shift, dict):
            merged_shift.update({key: value for key, value in rich_shift.items() if value not in (None, "")})
        values = _deputy_web_shift_values(
            merged_shift,
            area_lookup,
            location_lookup,
            captured_at,
            source_url_hash,
            owner_user_id,
        )
        if values is None:
            continue
        counts["seen"] += 1
        _record_known_location_from_shift(conn, values, owner_user_id, captured_at)
        existing = _find_existing_shift_for_web(conn, str(values["source_uid"]), shift_id, owner_user_id)
        if existing is None:
            conn.execute(
                """
                INSERT INTO shifts (
                    source_uid, source_url_hash, title, description, location,
                    start_at, end_at, date, raw_hours, break_minutes, paid_hours,
                    last_synced_at, first_seen_at, last_changed_at,
                    changed_since_viewed, deleted_from_source, owner_user_id,
                    source_link, source_status, source_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["source_uid"],
                    values["source_url_hash"],
                    values["title"],
                    values["description"],
                    values["location"],
                    values["start_at"],
                    values["end_at"],
                    values["date"],
                    values["raw_hours"],
                    values["break_minutes"],
                    values["paid_hours"],
                    captured_at,
                    captured_at,
                    None,
                    0,
                    0,
                    owner_user_id,
                    values["source_link"],
                    values["source_status"],
                    values["source_payload"],
                ),
            )
            counts["created"] += 1
            continue

        changes = _deputy_web_shift_changes(existing, values)
        changed = bool(changes)
        conn.execute(
            """
            UPDATE shifts
            SET source_uid = ?,
                source_url_hash = ?,
                title = ?,
                description = ?,
                location = ?,
                start_at = ?,
                end_at = ?,
                date = ?,
                raw_hours = ?,
                break_minutes = ?,
                paid_hours = ?,
                source_link = ?,
                source_status = ?,
                owner_user_id = ?,
                last_synced_at = ?,
                last_changed_at = CASE WHEN ? THEN ? ELSE last_changed_at END,
                changed_since_viewed = CASE WHEN ? THEN 1 ELSE changed_since_viewed END,
                deleted_from_source = 0,
                source_payload = ?
            WHERE id = ?
            """,
            (
                values["source_uid"],
                values["source_url_hash"],
                values["title"],
                values["description"],
                values["location"],
                values["start_at"],
                values["end_at"],
                values["date"],
                values["raw_hours"],
                values["break_minutes"],
                values["paid_hours"],
                values["source_link"],
                values["source_status"],
                owner_user_id,
                captured_at,
                1 if changed else 0,
                captured_at,
                1 if changed else 0,
                values["source_payload"],
                int(existing["id"]),
            ),
        )
        if changed:
            write_shift_changes(conn, int(existing["id"]), captured_at, changes)
            counts["updated"] += 1
    return counts


def _record_known_location_from_shift(
    conn: sqlite3.Connection,
    values: dict[str, object],
    owner_user_id: int | None,
    captured_at: str,
) -> None:
    if owner_user_id is not None:
        _ensure_user_default_crew(conn, owner_user_id)
    payload = _json_loads_dict(str(values.get("source_payload") or ""))
    normalised = payload.get("normalised") if isinstance(payload.get("normalised"), dict) else {}
    source_code = str(normalised.get("source_code") or "").strip()
    deputy_location_id = _optional_int(normalised.get("area_location_id"))
    display_name = str(normalised.get("location_name") or source_code or values.get("location") or "").strip()
    display_name = re.sub(r"^[THG]-", "", display_name, flags=re.IGNORECASE).strip() or source_code
    if not display_name or display_name.upper() in {"WEB", "SHIFT"}:
        return
    location_key = f"deputy:{deputy_location_id}" if deputy_location_id is not None else re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    if not location_key:
        return
    conn.execute(
        """
        INSERT INTO crew_known_locations (
            crew_name, location_key, display_name, source_code, deputy_location_id,
            first_seen_at, last_seen_at, source_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(crew_name, location_key) DO UPDATE SET
            display_name = excluded.display_name,
            source_code = excluded.source_code,
            deputy_location_id = excluded.deputy_location_id,
            last_seen_at = excluded.last_seen_at,
            source_user_id = excluded.source_user_id
        """,
        (
            DEFAULT_CREW_POOL_NAME,
            location_key,
            display_name,
            source_code,
            deputy_location_id,
            captured_at,
            captured_at,
            owner_user_id,
        ),
    )


def _json_loads_dict(value: str) -> dict[str, object]:
    import json

    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


WEB_SHIFT_COMPARE_FIELDS = (
    "title",
    "description",
    "location",
    "start_at",
    "end_at",
    "raw_hours",
    "break_minutes",
    "paid_hours",
    "source_status",
)


def _deputy_web_shift_changes(existing: sqlite3.Row, values: dict[str, object]) -> dict[str, tuple[object, object]]:
    changes = {}
    for field_name in WEB_SHIFT_COMPARE_FIELDS:
        old_value = existing[field_name]
        new_value = values[field_name]
        if field_name in {"raw_hours", "paid_hours"}:
            try:
                if round(float(old_value or 0), 2) == round(float(new_value or 0), 2):
                    continue
            except (TypeError, ValueError):
                pass
        elif str(old_value or "") == str(new_value or ""):
            continue
        changes[field_name] = (old_value, new_value)
    return changes


def _find_existing_shift_for_web(
    conn: sqlite3.Connection,
    source_uid: str,
    source_shift_id: str,
    owner_user_id: int | None,
) -> sqlite3.Row | None:
    existing = conn.execute(
        "SELECT * FROM shifts WHERE source_uid = ?",
        (source_uid,),
    ).fetchone()
    if existing is not None:
        return existing

    owner_sql = "owner_user_id IS NULL" if owner_user_id is None else "owner_user_id = ?"
    params: list[object] = []
    if owner_user_id is not None:
        params.append(owner_user_id)
    params.extend([f"%/shift/{source_shift_id}%", f"%/record/{source_shift_id}%"])
    return conn.execute(
        f"""
        SELECT *
        FROM shifts
        WHERE source_uid LIKE 'ical:%'
          AND {owner_sql}
          AND (
                source_link LIKE ?
                OR source_link LIKE ?
              )
        ORDER BY last_synced_at DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def _deputy_web_shift_values(
    shift: dict[str, object],
    area_lookup: dict[str, dict[str, object]],
    location_lookup: dict[int, dict[str, object]],
    captured_at: str,
    source_url_hash: str,
    owner_user_id: int | None,
) -> dict[str, object] | None:
    start_at = str(shift.get("start") or "")
    end_at = str(shift.get("end") or "")
    if not start_at or not end_at:
        return None
    try:
        start_dt = datetime.fromisoformat(start_at)
        end_dt = datetime.fromisoformat(end_at)
    except ValueError:
        return None
    if end_dt <= start_dt:
        return None

    area_id = _optional_int(shift.get("area"))
    area = area_lookup.get(str(area_id)) if area_id is not None else None
    area_name = str(shift.get("areaName") or (area or {}).get("name") or "").strip()
    raw_role_name = _clean_role_name(shift.get("roleName") or shift.get("role") or shift.get("title") or "")
    shift_location_name = str(shift.get("locationName") or shift.get("LocationName") or "").strip()
    location_id = _optional_int(shift.get("location") or shift.get("locationId") or shift.get("location_id"))
    if location_id is not None and shift_location_name:
        existing_location = location_lookup.get(location_id) or {}
        location_lookup[location_id] = {
            "id": location_id,
            "name": shift_location_name,
            "address": existing_location.get("address") or DEPUTY_LOCATION_ADDRESSES.get(location_id, ""),
        }
    if location_id is None:
        location_id = _optional_int(shift.get("areaLocationId"))
    if location_id is None and area:
        location_id = _optional_int(area.get("locationId"))
    area_override = DEPUTY_AREA_OVERRIDES.get(area_id or -1, {})
    if location_id is None and area_override:
        location_id = _optional_int(area_override.get("location_id"))
    if location_id is None and area_override:
        location_id = _location_id_for_source_code(str(area_override.get("source_code") or ""))
    role_label = str(area_override.get("role") or area_name or raw_role_name or "Shift").strip()
    source_code = str(shift_location_name or area_override.get("source_code") or _location_source_code(location_id, location_lookup) or "WEB").strip()
    title = f"[{source_code}] {role_label}".strip()
    location = str(_location_address(location_id, location_lookup) or area_override.get("location") or "").strip()
    raw_hours = round((end_dt - start_dt).total_seconds() / 3600, 2)
    break_minutes = 0
    paid_hours = raw_hours
    source_status = "published" if shift.get("isPublished") else "unpublished"
    if shift.get("isOpen"):
        source_status = "open"
    source_uid = f"deputy-web:{owner_user_id if owner_user_id is not None else 'env'}:{shift.get('id')}"
    normalised = {
        "uid": source_uid,
        "summary": title,
        "description": str(shift.get("note") or ""),
        "location": location,
        "dtstart": start_dt.isoformat(),
        "dtend": end_dt.isoformat(),
        "break_minutes": break_minutes,
        "source": "deputy_web",
        "source_code": source_code,
        "role_label": role_label,
        "area_id": area_id,
        "area_name": area_name,
        "area_location_id": location_id,
        "location_name": shift_location_name or str((location_lookup.get(location_id or -1) or {}).get("name") or ""),
        "employee_id": _optional_int(shift.get("employee")),
        "status": source_status,
        "captured_at": captured_at,
    }
    payload = {
        "normalised": normalised,
        "deputy_web": shift,
    }
    return {
        "source_uid": source_uid,
        "source_url_hash": source_url_hash,
        "title": title,
        "description": str(shift.get("note") or ""),
        "location": location,
        "start_at": start_dt.isoformat(),
        "end_at": end_dt.isoformat(),
        "date": start_dt.date().isoformat(),
        "raw_hours": raw_hours,
        "break_minutes": break_minutes,
        "paid_hours": paid_hours,
        "source_link": "",
        "source_status": source_status,
        "source_payload": json_dumps(payload),
    }


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


def get_next_upcoming_shift(now_iso: str, owner_user_id: int | None = None) -> sqlite3.Row | None:
    owner_sql = ""
    params: list[object] = [now_iso]
    if owner_user_id is not None:
        owner_sql = "AND owner_user_id = ?"
        params.append(owner_user_id)
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM shifts
            WHERE deleted_from_source = 0
              AND start_at >= ?
              {owner_sql}
            ORDER BY start_at ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return row


def get_current_or_next_shift(now_iso: str, owner_user_id: int | None = None) -> sqlite3.Row | None:
    owner_sql = ""
    params: list[object] = [now_iso]
    if owner_user_id is not None:
        owner_sql = "AND owner_user_id = ?"
        params.append(owner_user_id)
    params.append(now_iso)
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM shifts
            WHERE deleted_from_source = 0
              AND end_at >= ?
              {owner_sql}
            ORDER BY
              CASE WHEN start_at <= ? THEN 0 ELSE 1 END,
              start_at ASC,
              id ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return row


def get_upcoming_shifts(now_iso: str, limit: int = 5, owner_user_id: int | None = None) -> list[sqlite3.Row]:
    owner_sql = ""
    params: list[object] = [now_iso]
    if owner_user_id is not None:
        owner_sql = "AND s.owner_user_id = ?"
        params.append(owner_user_id)
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT s.*, m.checked, m.confirmed, m.important, m.question,
                   m.early_start, m.gear_needed, m.travel_needed, m.pay_check,
                   m.private_note, m.custom_colour, m.timing_adjustment_time,
                   m.timing_adjustment_last_race, m.timing_adjustment_day_finished,
                   m.updated_at AS marks_updated_at
            FROM shifts s
            LEFT JOIN shift_marks m ON m.shift_id = s.id
            WHERE s.deleted_from_source = 0
              AND s.start_at >= ?
              {owner_sql}
            ORDER BY s.start_at ASC
            LIMIT ?
            """,
            params,
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
    owner_user_id: int | None = None,
) -> int:
    seen = list(seen_uids)
    where_sql = """
        WHERE source_url_hash = ?
          AND deleted_from_source = 0
          AND start_at >= ?
    """
    params: list[object] = [source_url_hash, now_iso]
    if owner_user_id is not None:
        where_sql += " AND owner_user_id = ?"
        params.append(owner_user_id)
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
