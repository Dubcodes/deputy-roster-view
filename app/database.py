from __future__ import annotations

import os
import re
import hashlib
import json
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
                change_category TEXT DEFAULT 'source_change',
                user_visible INTEGER DEFAULT 1,
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

            CREATE TABLE IF NOT EXISTS travel_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin_key TEXT NOT NULL,
                origin_label TEXT NOT NULL,
                destination_key TEXT NOT NULL,
                destination_label TEXT NOT NULL,
                travel_minutes INTEGER NOT NULL,
                note TEXT,
                source TEXT DEFAULT 'manual',
                sample_count INTEGER DEFAULT 0,
                first_seen_at TEXT,
                last_seen_at TEXT,
                updated_at TEXT,
                reverse_is_shared INTEGER DEFAULT 0,
                UNIQUE(origin_key, destination_key)
            );

            CREATE TABLE IF NOT EXISTS crew_people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_display_name TEXT NOT NULL,
                deputy_employee_id INTEGER UNIQUE,
                current_deputy_name TEXT,
                app_user_id INTEGER UNIQUE,
                is_active INTEGER DEFAULT 1,
                admin_note TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS crew_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(person_id, normalized_alias),
                FOREIGN KEY (person_id) REFERENCES crew_people(id) ON DELETE CASCADE
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

            CREATE TABLE IF NOT EXISTS deputy_schedule_assignment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_shift_id INTEGER,
                date TEXT,
                area_location_id INTEGER,
                position_label TEXT,
                old_employee_name TEXT,
                new_employee_name TEXT,
                changed_at TEXT,
                UNIQUE(source_shift_id, position_label, old_employee_name, new_employee_name, changed_at)
            );

            CREATE TABLE IF NOT EXISTS deputy_schedule_event_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                change_key TEXT NOT NULL,
                change_type TEXT NOT NULL,
                date TEXT NOT NULL,
                area_location_id INTEGER,
                event_start_at TEXT,
                event_end_at TEXT,
                old_positions TEXT DEFAULT '[]',
                new_positions TEXT DEFAULT '[]',
                old_employee_id INTEGER,
                old_employee_name TEXT,
                new_employee_id INTEGER,
                new_employee_name TEXT,
                changed_at TEXT NOT NULL,
                display_summary TEXT NOT NULL,
                inline_summary TEXT,
                before_hash TEXT,
                after_hash TEXT,
                changed_since_viewed INTEGER DEFAULT 1,
                UNIQUE(group_id, change_key)
            );

            CREATE TABLE IF NOT EXISTS deputy_personal_assignment_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                deputy_employee_id INTEGER,
                canonical_person_id INTEGER,
                source_shift_uid TEXT NOT NULL,
                source_shift_id TEXT,
                date TEXT NOT NULL,
                area_location_id INTEGER NOT NULL,
                position_key TEXT NOT NULL,
                position_label TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_confirmed_at TEXT NOT NULL,
                missing_capture_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'confirmed',
                provenance TEXT,
                UNIQUE(owner_user_id, source_shift_uid),
                FOREIGN KEY (owner_user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (canonical_person_id) REFERENCES crew_people(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS deputy_personal_capture_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                captured_at TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL,
                records_returned INTEGER DEFAULT 0,
                pagination_complete INTEGER DEFAULT 0,
                known_shift_ids_checked INTEGER DEFAULT 0,
                note TEXT,
                UNIQUE(owner_user_id, captured_at, start_date, end_date),
                FOREIGN KEY (owner_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deputy_event_coverage (
                date TEXT NOT NULL,
                area_location_id INTEGER NOT NULL,
                event_start_at TEXT DEFAULT '',
                event_end_at TEXT DEFAULT '',
                status TEXT NOT NULL,
                expected_positions INTEGER DEFAULT 0,
                named_positions INTEGER DEFAULT 0,
                open_positions INTEGER DEFAULT 0,
                placeholder_positions INTEGER DEFAULT 0,
                personal_evidence_fills INTEGER DEFAULT 0,
                conflict_count INTEGER DEFAULT 0,
                reason TEXT,
                last_capture_at TEXT,
                source_user_id INTEGER,
                PRIMARY KEY (date, area_location_id, event_start_at),
                FOREIGN KEY (source_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS deputy_event_locks (
                date TEXT NOT NULL,
                area_location_id INTEGER NOT NULL,
                event_start_at TEXT DEFAULT '',
                event_end_at TEXT DEFAULT '',
                locked_at TEXT NOT NULL,
                lock_reason TEXT NOT NULL,
                recovered_from_capture INTEGER DEFAULT 0,
                PRIMARY KEY (date, area_location_id, event_start_at)
            );

            CREATE TABLE IF NOT EXISTS deputy_historical_discrepancies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                area_location_id INTEGER NOT NULL,
                source_shift_id INTEGER,
                position_label TEXT,
                existing_value TEXT,
                incoming_value TEXT,
                discrepancy_type TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                details TEXT,
                UNIQUE(date, area_location_id, source_shift_id, discrepancy_type, captured_at)
            );

            CREATE TABLE IF NOT EXISTS historical_recovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                events_inspected INTEGER DEFAULT 0,
                events_restored INTEGER DEFAULT 0,
                rows_restored INTEGER DEFAULT 0,
                events_unrecoverable INTEGER DEFAULT 0,
                note TEXT
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

            CREATE TABLE IF NOT EXISTS track_maps (
                track_key TEXT PRIMARY KEY,
                track_label TEXT,
                course_label TEXT,
                course_url TEXT,
                image_url TEXT,
                file_name TEXT,
                content_type TEXT,
                image_hash TEXT,
                status TEXT,
                checked_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS planning_location_preferences (
                location_key TEXT PRIMARY KEY,
                display_name TEXT,
                is_enabled INTEGER DEFAULT 1,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS roster_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roster_date TEXT,
                track_key TEXT,
                track_label TEXT,
                race_type TEXT,
                day_type TEXT DEFAULT 'race_day',
                office_start TEXT,
                on_track_time TEXT,
                first_race_time TEXT,
                last_race_time TEXT,
                race_count INTEGER,
                notes TEXT,
                hotel_assignments TEXT DEFAULT '[]',
                status TEXT DEFAULT 'draft',
                published_snapshot TEXT,
                created_by_user_id INTEGER,
                updated_by_user_id INTEGER,
                published_by_user_id INTEGER,
                created_at TEXT,
                updated_at TEXT,
                published_at TEXT,
                UNIQUE(roster_date, track_key),
                FOREIGN KEY (created_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL,
                FOREIGN KEY (updated_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL,
                FOREIGN KEY (published_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS roster_day_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roster_day_id INTEGER,
                position_label TEXT,
                user_id INTEGER,
                assignee_label TEXT,
                vehicle_label TEXT,
                sort_order INTEGER DEFAULT 999999,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(roster_day_id, position_label),
                FOREIGN KEY (roster_day_id) REFERENCES roster_days(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS roster_day_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roster_day_id INTEGER,
                version_number INTEGER,
                snapshot TEXT,
                published_by_user_id INTEGER,
                published_at TEXT,
                UNIQUE(roster_day_id, version_number),
                FOREIGN KEY (roster_day_id) REFERENCES roster_days(id) ON DELETE CASCADE,
                FOREIGN KEY (published_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL
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
            CREATE INDEX IF NOT EXISTS idx_travel_routes_destination ON travel_routes(destination_key, origin_key);
            CREATE INDEX IF NOT EXISTS idx_crew_people_name ON crew_people(canonical_display_name);
            CREATE INDEX IF NOT EXISTS idx_crew_aliases_normalized ON crew_aliases(normalized_alias);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_crew_aliases_unique_normalized ON crew_aliases(normalized_alias);
            CREATE INDEX IF NOT EXISTS idx_love_racing_meetings_date ON love_racing_meetings(meeting_date, racecourse_key);
            CREATE INDEX IF NOT EXISTS idx_planning_location_preferences_enabled ON planning_location_preferences(is_enabled);
            CREATE INDEX IF NOT EXISTS idx_roster_days_date ON roster_days(roster_date, status);
            CREATE INDEX IF NOT EXISTS idx_roster_day_assignments_user ON roster_day_assignments(user_id, roster_day_id);
            CREATE INDEX IF NOT EXISTS idx_roster_day_versions_day ON roster_day_versions(roster_day_id, version_number DESC);
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
        _ensure_column(conn, "shifts", "missing_capture_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "shifts", "capture_status", "TEXT DEFAULT 'confirmed'")
        _ensure_column(conn, "shifts", "historical_locked_at", "TEXT")
        _ensure_column(conn, "shift_changes", "change_category", "TEXT DEFAULT 'source_change'")
        _ensure_column(conn, "shift_changes", "user_visible", "INTEGER DEFAULT 1")
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
        _ensure_column(conn, "roster_days", "day_type", "TEXT DEFAULT 'race_day'")
        _ensure_column(conn, "roster_days", "hotel_assignments", "TEXT DEFAULT '[]'")
        _ensure_column(conn, "roster_days", "start_origin", "TEXT")
        _ensure_column(conn, "roster_days", "finish_destination", "TEXT")
        _ensure_column(conn, "track_maps", "image_width", "INTEGER")
        _ensure_column(conn, "track_maps", "image_height", "INTEGER")
        _ensure_column(conn, "track_maps", "byte_size", "INTEGER")
        _ensure_column(conn, "track_maps", "selected_source_url", "TEXT")
        _ensure_column(conn, "track_maps", "candidate_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "track_maps", "refresh_result", "TEXT")
        _ensure_column(conn, "deputy_schedule_event_changes", "changed_since_viewed", "INTEGER DEFAULT 1")
        _ensure_column(conn, "deputy_schedule_event_changes", "change_category", "TEXT DEFAULT 'assignment_change'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deputy_schedule_shifts_location ON deputy_schedule_shifts(date, area_location_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedule_assignment_history_day ON deputy_schedule_assignment_history(date, area_location_id, changed_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_schedule_event_changes_day ON deputy_schedule_event_changes(date, area_location_id, changed_at DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_evidence_event ON deputy_personal_assignment_evidence(date, area_location_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_coverage_user ON deputy_personal_capture_coverage(owner_user_id, captured_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_coverage_status ON deputy_event_coverage(status, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_event_locks_date ON deputy_event_locks(date, area_location_id)")
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
        _merge_equivalent_travel_bases(conn)
        _migrate_travel_defaults_to_routes(conn)
        _sync_crew_directory(conn)
        _reclassify_legacy_shift_changes(conn)
    recover_historical_schedule_from_captures(settings=settings)
    with get_connection(settings) as maintenance_conn:
        lock_completed_events(maintenance_conn)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _reclassify_legacy_shift_changes(conn: sqlite3.Connection) -> None:
    marker = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'shift_change_classification_v1'"
    ).fetchone()
    if marker is not None:
        return
    rows = conn.execute("SELECT * FROM shift_changes ORDER BY id").fetchall()
    for row in rows:
        field_name = str(row["field_name"] or "")
        old_value = str(row["old_value"] or "")
        new_value = str(row["new_value"] or "")
        category = "source_change"
        visible = 1
        replacement_field = field_name
        replacement_old = old_value
        replacement_new = new_value
        if field_name in {"raw_hours", "paid_hours", "break_minutes"}:
            category, visible = "derived_change", 0
        elif field_name == "location":
            category, visible = "enrichment", 0
        elif field_name == "description" and (not old_value.strip() or not new_value.strip()):
            category, visible = "enrichment", 0
        elif field_name == "title":
            old_location, _old_source, old_role, old_role_label = _canonical_title_facts(old_value)
            new_location, _new_source, new_role, new_role_label = _canonical_title_facts(new_value)
            if old_role and new_role and old_role != new_role:
                replacement_field = "role"
                replacement_old, replacement_new = old_role_label, new_role_label
            elif old_location and new_location and old_location != new_location:
                replacement_field = "track"
                replacement_old, replacement_new = old_location, new_location
            else:
                category, visible = "normalization", 0
        if re.sub(r"[\s/]", "", old_value.lower()) == re.sub(r"[\s/]", "", new_value.lower()):
            category, visible = "normalization", 0
        conn.execute(
            """
            UPDATE shift_changes
            SET field_name = ?, old_value = ?, new_value = ?,
                change_category = ?, user_visible = ?
            WHERE id = ?
            """,
            (replacement_field, replacement_old, replacement_new, category, visible, int(row["id"])),
        )
    conn.execute(
        """
        UPDATE shift_changes
        SET change_category = 'derived_change', user_visible = 0
        WHERE field_name IN ('raw_hours', 'paid_hours')
          AND EXISTS (
              SELECT 1 FROM shift_changes other
              WHERE other.shift_id = shift_changes.shift_id
                AND other.changed_at = shift_changes.changed_at
                AND other.field_name IN ('start_at', 'end_at')
          )
        """
    )
    conn.execute(
        """
        UPDATE shifts
        SET changed_since_viewed = 0
        WHERE changed_since_viewed = 1
          AND NOT EXISTS (
              SELECT 1
              FROM shift_changes c
              WHERE c.shift_id = shifts.id
                AND c.user_visible = 1
          )
        """
    )
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES ('shift_change_classification_v1', 'done', ?) ON CONFLICT(key) DO UPDATE SET value = 'done', updated_at = excluded.updated_at",
        (now,),
    )


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


def normalise_person_identity(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def canonical_travel_base_label(value: object) -> str:
    label = re.sub(r"\s+", " ", str(value or "").strip())
    key = calendar_location_key(label)
    if not key or key in {"office", "clowplace", "officeclowplace", "clowplaceoffice"}:
        return "Office / Clow Place"
    return label


def canonical_travel_track(track_key: object, track_label: object = "") -> tuple[str, str]:
    label = re.sub(r"\s+", " ", str(track_label or track_key or "").strip())
    key = calendar_location_key(track_key or label)
    if key in {"gcambridge", "cambridgegreyhound"}:
        return "cambridgegreyhound", "Cambridge Greyhound"
    return key, label


def _merge_equivalent_travel_bases(conn: sqlite3.Connection) -> None:
    rows = [dict(row) for row in conn.execute("SELECT * FROM travel_time_defaults").fetchall()]
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        track_key, _track_label = canonical_travel_track(row.get("track_key"), row.get("track_label"))
        base_label = canonical_travel_base_label(row.get("base_label"))
        grouped.setdefault((track_key, base_label.lower()), []).append(row)

    for (canonical_track_key, _), matches in grouped.items():
        _track_key, canonical_track_label = canonical_travel_track(
            canonical_track_key,
            matches[0].get("track_label"),
        )
        canonical_base = canonical_travel_base_label(matches[0].get("base_label"))
        if (
            len(matches) == 1
            and str(matches[0].get("track_key") or "") == canonical_track_key
            and str(matches[0].get("track_label") or "") == canonical_track_label
            and str(matches[0].get("base_label") or "") == canonical_base
        ):
            continue
        matches.sort(
            key=lambda row: (
                1 if str(row.get("source") or "") == "manual" else 0,
                str(row.get("updated_at") or ""),
                int(row.get("sample_count") or 0),
            ),
            reverse=True,
        )
        winner = matches[0]
        loser_ids = [int(row["id"]) for row in matches[1:]]
        if loser_ids:
            placeholders = ",".join("?" for _ in loser_ids)
            conn.execute(f"DELETE FROM travel_time_defaults WHERE id IN ({placeholders})", loser_ids)
        first_seen = min(
            (str(row.get("first_seen_at") or "") for row in matches if str(row.get("first_seen_at") or "")),
            default="",
        )
        last_seen = max(
            (str(row.get("last_seen_at") or "") for row in matches if str(row.get("last_seen_at") or "")),
            default="",
        )
        conn.execute(
            """
            UPDATE travel_time_defaults
            SET track_key = ?, track_label = ?, base_label = ?,
                sample_count = ?, first_seen_at = ?, last_seen_at = ?
            WHERE id = ?
            """,
            (
                canonical_track_key,
                canonical_track_label,
                canonical_base,
                sum(int(row.get("sample_count") or 0) for row in matches),
                first_seen,
                last_seen,
                int(winner["id"]),
            ),
        )


def _upsert_travel_route_conn(
    conn: sqlite3.Connection,
    *,
    origin_label: object,
    destination_label: object,
    travel_minutes: int,
    source: str,
    note: str = "",
    sample_count: int = 0,
    first_seen_at: str = "",
    last_seen_at: str = "",
    reverse_is_shared: bool = False,
) -> None:
    origin = canonical_travel_base_label(origin_label)
    destination = canonical_travel_base_label(destination_label)
    origin_key = calendar_location_key(origin)
    destination_key = calendar_location_key(destination)
    if not origin_key or not destination_key or origin_key == destination_key or int(travel_minutes or 0) <= 0:
        return
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    clean_source = "learned" if source == "learned" else "manual"
    conn.execute(
        """
        INSERT INTO travel_routes (
            origin_key, origin_label, destination_key, destination_label,
            travel_minutes, note, source, sample_count, first_seen_at,
            last_seen_at, updated_at, reverse_is_shared
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(origin_key, destination_key) DO UPDATE SET
            origin_label = excluded.origin_label,
            destination_label = excluded.destination_label,
            travel_minutes = CASE
                WHEN travel_routes.source = 'manual' AND excluded.source = 'learned'
                THEN travel_routes.travel_minutes ELSE excluded.travel_minutes END,
            note = CASE
                WHEN travel_routes.source = 'manual' AND excluded.source = 'learned'
                THEN travel_routes.note ELSE excluded.note END,
            source = CASE
                WHEN travel_routes.source = 'manual' AND excluded.source = 'learned'
                THEN travel_routes.source ELSE excluded.source END,
            sample_count = CASE
                WHEN travel_routes.source = 'manual' AND excluded.source = 'learned'
                THEN travel_routes.sample_count ELSE excluded.sample_count END,
            first_seen_at = COALESCE(NULLIF(travel_routes.first_seen_at, ''), excluded.first_seen_at),
            last_seen_at = excluded.last_seen_at,
            updated_at = excluded.updated_at,
            reverse_is_shared = CASE
                WHEN travel_routes.source = 'manual' AND excluded.source = 'learned'
                THEN travel_routes.reverse_is_shared ELSE excluded.reverse_is_shared END
        """,
        (
            origin_key, origin, destination_key, destination, max(1, int(travel_minutes)),
            note.strip(), clean_source, max(0, int(sample_count or 0)), first_seen_at,
            last_seen_at, now, 1 if reverse_is_shared else 0,
        ),
    )


def _delete_shared_travel_route_pair_conn(
    conn: sqlite3.Connection,
    *,
    origin_label: object,
    destination_label: object,
) -> None:
    """Remove only the paired routes still owned by a legacy travel default."""
    origin_key = calendar_location_key(canonical_travel_base_label(origin_label))
    destination_key = calendar_location_key(canonical_travel_base_label(destination_label))
    if not origin_key or not destination_key or origin_key == destination_key:
        return
    conn.execute(
        """
        DELETE FROM travel_routes
        WHERE reverse_is_shared = 1
          AND (
            (origin_key = ? AND destination_key = ?)
            OR (origin_key = ? AND destination_key = ?)
          )
        """,
        (origin_key, destination_key, destination_key, origin_key),
    )


def _migrate_travel_defaults_to_routes(conn: sqlite3.Connection) -> None:
    """Preserve every legacy base-to-track default as a directed pair."""
    migrated = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'travel_routes_migrated_v1'"
    ).fetchone()
    if migrated and str(migrated["value"] or "") == "1":
        return
    for row in conn.execute("SELECT * FROM travel_time_defaults").fetchall():
        values = dict(row)
        base = canonical_travel_base_label(values.get("base_label"))
        _track_key, track = canonical_travel_track(values.get("track_key"), values.get("track_label"))
        common = {
            "travel_minutes": int(values.get("travel_minutes") or 0),
            "source": str(values.get("source") or "manual"),
            "note": str(values.get("note") or ""),
            "sample_count": int(values.get("sample_count") or 0),
            "first_seen_at": str(values.get("first_seen_at") or ""),
            "last_seen_at": str(values.get("last_seen_at") or ""),
            "reverse_is_shared": True,
        }
        _upsert_travel_route_conn(conn, origin_label=base, destination_label=track, **common)
        _upsert_travel_route_conn(conn, origin_label=track, destination_label=base, **common)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES ('travel_routes_migrated_v1', '1', ?)
        ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at
        """,
        (now,),
    )


def _crew_person_candidates(conn: sqlite3.Connection, name: object) -> list[sqlite3.Row]:
    key = normalise_person_identity(name)
    if not key:
        return []
    return [
        row for row in conn.execute("SELECT * FROM crew_people WHERE is_active = 1").fetchall()
        if key in {
            normalise_person_identity(row["canonical_display_name"]),
            normalise_person_identity(row["current_deputy_name"]),
        }
    ]


def _insert_observed_person(
    conn: sqlite3.Connection,
    name: object,
    *,
    employee_id: int | None = None,
    app_user_id: int | None = None,
) -> int | None:
    display_name = re.sub(r"\s+", " ", str(name or "").strip())
    if not display_name:
        return None
    now = datetime.now().isoformat(timespec="seconds")
    if employee_id is not None:
        existing = conn.execute(
            "SELECT id FROM crew_people WHERE deputy_employee_id = ?", (employee_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE crew_people SET current_deputy_name = ?, updated_at = ? WHERE id = ?",
                (display_name, now, int(existing["id"])),
            )
            return int(existing["id"])
        cursor = conn.execute(
            """
            INSERT INTO crew_people (
                canonical_display_name, deputy_employee_id, current_deputy_name,
                app_user_id, is_active, admin_note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, '', ?, ?)
            """,
            (display_name, employee_id, display_name, app_user_id, now, now),
        )
        return int(cursor.lastrowid)
    if app_user_id is not None:
        linked = conn.execute("SELECT id FROM crew_people WHERE app_user_id = ?", (app_user_id,)).fetchone()
        if linked:
            return int(linked["id"])
    matches = _crew_person_candidates(conn, display_name)
    if len(matches) == 1:
        person_id = int(matches[0]["id"])
        if app_user_id is not None and matches[0]["app_user_id"] is None:
            conn.execute(
                "UPDATE crew_people SET app_user_id = ?, updated_at = ? WHERE id = ?",
                (app_user_id, now, person_id),
            )
        return person_id
    cursor = conn.execute(
        """
        INSERT INTO crew_people (
            canonical_display_name, deputy_employee_id, current_deputy_name,
            app_user_id, is_active, admin_note, created_at, updated_at
        ) VALUES (?, NULL, ?, ?, 1, '', ?, ?)
        """,
        (display_name, display_name, app_user_id, now, now),
    )
    return int(cursor.lastrowid)


def _sync_crew_directory(conn: sqlite3.Connection) -> None:
    observed_ids: set[int] = set()
    for row in conn.execute(
        """
        SELECT employee_id, employee_name, MAX(captured_at) AS captured_at
        FROM deputy_schedule_shifts
        WHERE TRIM(COALESCE(employee_name, '')) != ''
        GROUP BY employee_id, employee_name
        ORDER BY captured_at
        """
    ).fetchall():
        employee_id = int(row["employee_id"]) if row["employee_id"] is not None else None
        person_id = _insert_observed_person(conn, row["employee_name"], employee_id=employee_id)
        if person_id:
            observed_ids.add(person_id)

    for row in conn.execute(
        "SELECT id, display_name, deputy_email FROM app_users ORDER BY id"
    ).fetchall():
        display_name = str(row["display_name"] or "").strip() or str(row["deputy_email"] or "").split("@", 1)[0]
        person_id = _insert_observed_person(conn, display_name, app_user_id=int(row["id"]))
        if person_id:
            observed_ids.add(person_id)

    manual_names: list[str] = [
        str(row["assignee_label"] or "").strip()
        for row in conn.execute("SELECT assignee_label FROM roster_day_assignments").fetchall()
        if str(row["assignee_label"] or "").strip() and str(row["assignee_label"] or "").strip().lower() != "tbc"
    ]
    for row in conn.execute("SELECT published_snapshot FROM roster_days").fetchall():
        try:
            snapshot = json.loads(str(row["published_snapshot"] or ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        for assignment in snapshot.get("assignments", []):
            if not isinstance(assignment, dict):
                continue
            label = str(assignment.get("assignee_label") or "").strip()
            if label and label.lower() != "tbc":
                manual_names.append(label)
    for name in manual_names:
        person_id = _insert_observed_person(conn, name)
        if person_id:
            observed_ids.add(person_id)

    # Seed only aliases that resolve to one full canonical person. Gary/Gaz is
    # deliberately omitted because two Garys must be linked by an admin.
    for alias, target_first_name in (("Cambo", "campbell"), ("Josh", "joshua")):
        alias_key = normalise_person_identity(alias)
        matches = [
            row for row in conn.execute("SELECT * FROM crew_people WHERE is_active = 1").fetchall()
            if normalise_person_identity(row["canonical_display_name"]).startswith(target_first_name)
        ]
        name_conflicts = [
            row for row in conn.execute("SELECT * FROM crew_people WHERE is_active = 1").fetchall()
            if int(row["id"]) != (int(matches[0]["id"]) if len(matches) == 1 else -1)
            and alias_key in {
                normalise_person_identity(row["canonical_display_name"]),
                normalise_person_identity(row["current_deputy_name"]),
            }
        ]
        if len(matches) != 1 or name_conflicts:
            continue
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT OR IGNORE INTO crew_aliases (
                person_id, alias, normalized_alias, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (int(matches[0]["id"]), alias, alias_key, now, now),
        )


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


def list_roster_days(limit: int = 40) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT d.*,
                   creator.display_name AS created_by_name,
                   publisher.display_name AS published_by_name,
                   (SELECT COUNT(*) FROM roster_day_assignments a WHERE a.roster_day_id = d.id) AS assignment_count,
                   (SELECT MAX(version_number) FROM roster_day_versions v WHERE v.roster_day_id = d.id) AS version_number
            FROM roster_days d
            LEFT JOIN app_users creator ON creator.id = d.created_by_user_id
            LEFT JOIN app_users publisher ON publisher.id = d.published_by_user_id
            ORDER BY d.roster_date DESC, LOWER(d.track_label)
            LIMIT ?
            """,
            (max(1, int(limit or 40)),),
        ).fetchall()


def get_roster_day(roster_day_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM roster_days WHERE id = ?",
            (roster_day_id,),
        ).fetchone()


def get_roster_day_assignments(roster_day_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT a.*, u.display_name, u.deputy_email
            FROM roster_day_assignments a
            LEFT JOIN app_users u ON u.id = a.user_id
            WHERE a.roster_day_id = ?
            ORDER BY a.sort_order, LOWER(a.position_label)
            """,
            (roster_day_id,),
        ).fetchall()


def save_roster_day(
    *,
    roster_day_id: int | None,
    roster_date: str,
    track_key: str,
    track_label: str,
    race_type: str,
    day_type: str,
    start_origin: str,
    finish_destination: str,
    office_start: str,
    on_track_time: str,
    first_race_time: str,
    last_race_time: str,
    race_count: int | None,
    notes: str,
    hotel_assignments: str,
    updated_by_user_id: int,
    assignments: list[dict[str, object]],
) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        existing = None
        if roster_day_id is not None:
            existing = conn.execute(
                "SELECT id, published_snapshot FROM roster_days WHERE id = ?",
                (roster_day_id,),
            ).fetchone()
        if existing is None:
            existing = conn.execute(
                "SELECT id, published_snapshot FROM roster_days WHERE roster_date = ? AND track_key = ?",
                (roster_date, track_key),
            ).fetchone()

        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO roster_days (
                    roster_date, track_key, track_label, race_type, day_type,
                    start_origin, finish_destination, office_start,
                    on_track_time, first_race_time, last_race_time, race_count,
                    notes, hotel_assignments, status, published_snapshot, created_by_user_id,
                    updated_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', '', ?, ?, ?, ?)
                """,
                (
                    roster_date, track_key, track_label, race_type, day_type,
                    start_origin, finish_destination, office_start,
                    on_track_time, first_race_time, last_race_time, race_count,
                    notes, hotel_assignments, updated_by_user_id, updated_by_user_id, now, now,
                ),
            )
            saved_id = int(cursor.lastrowid)
        else:
            saved_id = int(existing["id"])
            status = "changes_pending" if str(existing["published_snapshot"] or "").strip() else "draft"
            conn.execute(
                """
                UPDATE roster_days
                SET roster_date = ?, track_key = ?, track_label = ?, race_type = ?, day_type = ?,
                    start_origin = ?, finish_destination = ?, office_start = ?,
                    on_track_time = ?, first_race_time = ?,
                    last_race_time = ?, race_count = ?, notes = ?, hotel_assignments = ?, status = ?,
                    updated_by_user_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    roster_date, track_key, track_label, race_type, day_type,
                    start_origin, finish_destination, office_start,
                    on_track_time, first_race_time, last_race_time, race_count,
                    notes, hotel_assignments, status, updated_by_user_id, now, saved_id,
                ),
            )

        conn.execute("DELETE FROM roster_day_assignments WHERE roster_day_id = ?", (saved_id,))
        for assignment in assignments:
            conn.execute(
                """
                INSERT INTO roster_day_assignments (
                    roster_day_id, position_label, user_id, assignee_label,
                    vehicle_label, sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saved_id,
                    str(assignment.get("position_label") or "").strip(),
                    assignment.get("user_id"),
                    str(assignment.get("assignee_label") or "").strip(),
                    str(assignment.get("vehicle_label") or "").strip(),
                    int(assignment.get("sort_order") or 999999),
                    now,
                    now,
                ),
            )
    return saved_id


def publish_roster_day(roster_day_id: int, snapshot: str, published_by_user_id: int) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM roster_days WHERE id = ?", (roster_day_id,)).fetchone()
        if row is None:
            return 0
        version_row = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM roster_day_versions WHERE roster_day_id = ?",
            (roster_day_id,),
        ).fetchone()
        version_number = int(version_row["next_version"] or 1)
        conn.execute(
            """
            UPDATE roster_days
            SET status = 'published', published_snapshot = ?,
                published_by_user_id = ?, published_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (snapshot, published_by_user_id, now, now, roster_day_id),
        )
        conn.execute(
            """
            INSERT INTO roster_day_versions (
                roster_day_id, version_number, snapshot, published_by_user_id, published_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (roster_day_id, version_number, snapshot, published_by_user_id, now),
        )
    return version_number


def fetch_published_roster_days_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT d.*,
                   (SELECT MAX(version_number) FROM roster_day_versions v WHERE v.roster_day_id = d.id) AS version_number
            FROM roster_days d
            WHERE d.roster_date BETWEEN ? AND ?
              AND TRIM(COALESCE(d.published_snapshot, '')) != ''
            ORDER BY d.roster_date, LOWER(d.track_label)
            """,
            (start_date, end_date),
        ).fetchall()


def list_roster_builder_location_labels() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT label
            FROM (
                SELECT display_name AS label FROM crew_known_locations
                UNION
                SELECT track_label AS label FROM travel_time_defaults
                UNION
                SELECT racecourse AS label FROM love_racing_meetings
                UNION
                SELECT name AS label FROM deputy_schedule_locations
            )
            WHERE TRIM(COALESCE(label, '')) != ''
            ORDER BY LOWER(label)
            """
        ).fetchall()
    return [str(row["label"]).strip() for row in rows]


def list_roster_builder_area_names() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT name
            FROM deputy_schedule_areas
            WHERE TRIM(COALESCE(name, '')) != ''
            ORDER BY COALESCE(roster_sort_order, 999999), LOWER(name)
            """
        ).fetchall()
    return [str(row["name"]).strip() for row in rows]


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


def list_known_racecourse_names(*, include_fallback: bool = True) -> list[str]:
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

    if not names and include_fallback:
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
        conn.execute(
            "UPDATE love_racing_meetings SET is_active = 0, last_synced_at = ? WHERE is_active = 1",
            (synced_at,),
        )
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
        conn.execute("DELETE FROM love_racing_meetings WHERE is_active = 0")
    return saved


def fetch_love_racing_meetings_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM love_racing_meetings
            WHERE is_active = 1
              AND meeting_date BETWEEN ? AND ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM planning_location_preferences preference
                  WHERE preference.location_key = love_racing_meetings.racecourse_key
                    AND preference.is_enabled = 0
              )
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
                (SELECT MAX(last_synced_at) FROM love_racing_meetings) AS last_synced_at
            FROM love_racing_meetings
            WHERE is_active = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM planning_location_preferences preference
                  WHERE preference.location_key = love_racing_meetings.racecourse_key
                    AND preference.is_enabled = 0
              )
            """,
            (today,),
        ).fetchone()
        upcoming = conn.execute(
            """
            SELECT *
            FROM love_racing_meetings
            WHERE is_active = 1
              AND meeting_date >= ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM planning_location_preferences preference
                  WHERE preference.location_key = love_racing_meetings.racecourse_key
                    AND preference.is_enabled = 0
              )
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


def list_planning_locations() -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                meetings.racecourse_key AS location_key,
                MAX(meetings.racecourse) AS display_name,
                COUNT(*) AS meeting_count,
                MIN(meetings.meeting_date) AS first_date,
                MAX(meetings.meeting_date) AS last_date,
                GROUP_CONCAT(DISTINCT NULLIF(TRIM(meetings.club_name), '')) AS club_names,
                COALESCE(preference.is_enabled, 1) AS is_enabled
            FROM love_racing_meetings meetings
            LEFT JOIN planning_location_preferences preference
              ON preference.location_key = meetings.racecourse_key
            WHERE meetings.is_active = 1
            GROUP BY meetings.racecourse_key, preference.is_enabled
            ORDER BY LOWER(MAX(meetings.racecourse)), meetings.racecourse_key
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_travel_routes() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM travel_routes
            ORDER BY LOWER(origin_label), LOWER(destination_label)
            """
        ).fetchall()


def get_travel_route(origin_label: object, destination_label: object) -> sqlite3.Row | None:
    origin_key = calendar_location_key(canonical_travel_base_label(origin_label))
    destination_key = calendar_location_key(canonical_travel_base_label(destination_label))
    if not origin_key or not destination_key:
        return None
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM travel_routes
            WHERE origin_key = ? AND destination_key = ?
            LIMIT 1
            """,
            (origin_key, destination_key),
        ).fetchone()


def upsert_travel_route(
    *,
    origin_label: str,
    destination_label: str,
    travel_minutes: int,
    note: str = "",
    source: str = "manual",
    also_reverse: bool = False,
) -> bool:
    if int(travel_minutes or 0) <= 0:
        return False
    with get_connection() as conn:
        _upsert_travel_route_conn(
            conn,
            origin_label=origin_label,
            destination_label=destination_label,
            travel_minutes=travel_minutes,
            note=note,
            source=source,
            reverse_is_shared=also_reverse,
        )
        if also_reverse:
            _upsert_travel_route_conn(
                conn,
                origin_label=destination_label,
                destination_label=origin_label,
                travel_minutes=travel_minutes,
                note=note,
                source=source,
                reverse_is_shared=True,
            )
        else:
            conn.execute(
                """
                UPDATE travel_routes SET reverse_is_shared = 0
                WHERE origin_key = ? AND destination_key = ?
                """,
                (
                    calendar_location_key(canonical_travel_base_label(destination_label)),
                    calendar_location_key(canonical_travel_base_label(origin_label)),
                ),
            )
    return True


def delete_travel_route(route_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM travel_routes WHERE id = ?", (route_id,)).fetchone()
        if row is None:
            return 0
        result = conn.execute("DELETE FROM travel_routes WHERE id = ?", (route_id,))
        if int(row["reverse_is_shared"] or 0):
            conn.execute(
                """
                UPDATE travel_routes SET reverse_is_shared = 0
                WHERE origin_key = ? AND destination_key = ?
                """,
                (row["destination_key"], row["origin_key"]),
            )
    return result.rowcount


def list_known_place_labels() -> list[str]:
    values: dict[str, str] = {}
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT origin_label AS label FROM travel_routes
            UNION SELECT destination_label AS label FROM travel_routes
            UNION SELECT track_label AS label FROM travel_time_defaults
            UNION SELECT base_label AS label FROM travel_time_defaults
            UNION SELECT name AS label FROM deputy_schedule_locations
            UNION SELECT display_name AS label FROM crew_known_locations
            """
        ).fetchall()
    for row in rows:
        label = canonical_travel_base_label(row["label"])
        key = calendar_location_key(label)
        if key:
            values.setdefault(key, label)
    values.setdefault("officeclowplace", "Office / Clow Place")
    return sorted(values.values(), key=str.lower)


def refresh_crew_directory() -> None:
    with get_connection() as conn:
        _sync_crew_directory(conn)


def list_crew_people() -> list[dict[str, object]]:
    refresh_crew_directory()
    with get_connection() as conn:
        people = [dict(row) for row in conn.execute(
            """
            SELECT p.*, u.display_name AS app_user_name, u.deputy_email AS app_user_email
            FROM crew_people p
            LEFT JOIN app_users u ON u.id = p.app_user_id
            ORDER BY p.is_active DESC, LOWER(p.canonical_display_name), p.id
            """
        ).fetchall()]
        aliases_by_person: dict[int, list[str]] = {}
        for row in conn.execute(
            "SELECT person_id, alias FROM crew_aliases ORDER BY LOWER(alias)"
        ).fetchall():
            aliases_by_person.setdefault(int(row["person_id"]), []).append(str(row["alias"]))
        schedule_rows = conn.execute(
            """
            SELECT employee_id, employee_name, area_name
            FROM deputy_schedule_shifts
            WHERE TRIM(COALESCE(employee_name, '')) != ''
              AND TRIM(COALESCE(area_name, '')) != ''
            """
        ).fetchall()
        vehicles = conn.execute(
            """
            SELECT employee_id, employee_name, area_name
            FROM deputy_schedule_shifts
            WHERE TRIM(COALESCE(employee_name, '')) != ''
              AND (
                area_name GLOB '[0-9][0-9][0-9]'
                OR UPPER(area_name) LIKE 'RAV%'
                OR UPPER(area_name) IN ('OB', 'TENDER', 'TRANSIT')
              )
            """
        ).fetchall()
    for person in people:
        person_id = int(person["id"])
        person["aliases"] = aliases_by_person.get(person_id, [])
        person["aliases_text"] = ", ".join(person["aliases"])
        identity_id = person.get("deputy_employee_id")
        names = {
            normalise_person_identity(person.get("canonical_display_name")),
            normalise_person_identity(person.get("current_deputy_name")),
        }
        observed_positions = {
            str(row["area_name"] or "").strip()
            for row in schedule_rows
            if (
                identity_id is not None and row["employee_id"] == identity_id
            ) or (
                identity_id is None and normalise_person_identity(row["employee_name"]) in names
            )
        }
        observed_vehicles = {
            str(row["area_name"] or "").strip()
            for row in vehicles
            if (
                identity_id is not None and row["employee_id"] == identity_id
            ) or (
                identity_id is None and normalise_person_identity(row["employee_name"]) in names
            )
        }
        person["observed_positions"] = sorted(observed_positions - observed_vehicles, key=str.lower)
        person["observed_vehicles"] = sorted(observed_vehicles, key=str.lower)
    return people


def crew_identity_records() -> list[dict[str, object]]:
    with get_connection() as conn:
        _sync_crew_directory(conn)
        people = [dict(row) for row in conn.execute(
            "SELECT * FROM crew_people WHERE is_active = 1 ORDER BY id"
        ).fetchall()]
        aliases: dict[int, list[str]] = {}
        for row in conn.execute("SELECT person_id, alias FROM crew_aliases").fetchall():
            aliases.setdefault(int(row["person_id"]), []).append(str(row["alias"]))
    for person in people:
        person["aliases"] = aliases.get(int(person["id"]), [])
    return people


def update_crew_person(
    person_id: int,
    *,
    canonical_display_name: str,
    app_user_id: int | None,
    aliases: list[str],
    is_active: bool,
    admin_note: str,
) -> tuple[bool, str]:
    display_name = re.sub(r"\s+", " ", canonical_display_name.strip())
    if not display_name:
        return False, "Canonical display name is required."
    clean_aliases: dict[str, str] = {}
    for alias in aliases:
        label = re.sub(r"\s+", " ", str(alias or "").strip(" ,;\t\r\n"))
        key = normalise_person_identity(label)
        if key:
            clean_aliases.setdefault(key, label)
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        person = conn.execute("SELECT * FROM crew_people WHERE id = ?", (person_id,)).fetchone()
        if person is None:
            return False, "Crew member was not found."
        if app_user_id is not None:
            conflict = conn.execute(
                "SELECT id FROM crew_people WHERE app_user_id = ? AND id != ?",
                (app_user_id, person_id),
            ).fetchone()
            if conflict:
                return False, "That app user is already linked to another crew member."
        canonical_key = normalise_person_identity(display_name)
        canonical_alias_conflict = conn.execute(
            """
            SELECT p.canonical_display_name
            FROM crew_aliases a
            JOIN crew_people p ON p.id = a.person_id
            WHERE a.normalized_alias = ? AND a.person_id != ? AND p.is_active = 1
            LIMIT 1
            """,
            (canonical_key, person_id),
        ).fetchone()
        if canonical_alias_conflict and is_active:
            return False, (
                f"Display name {display_name!r} conflicts with an alias for "
                f"{canonical_alias_conflict['canonical_display_name']}."
            )
        for key, label in clean_aliases.items():
            alias_conflicts = conn.execute(
                """
                SELECT p.canonical_display_name
                FROM crew_aliases a
                JOIN crew_people p ON p.id = a.person_id
                WHERE a.normalized_alias = ? AND a.person_id != ?
                """,
                (key, person_id),
            ).fetchall()
            name_conflicts = [
                row for row in conn.execute(
                    "SELECT * FROM crew_people WHERE id != ? AND is_active = 1",
                    (person_id,),
                ).fetchall()
                if key in {
                    normalise_person_identity(row["canonical_display_name"]),
                    normalise_person_identity(row["current_deputy_name"]),
                }
            ]
            if alias_conflicts or name_conflicts:
                names = sorted({
                    str(row["canonical_display_name"])
                    for row in [*alias_conflicts, *name_conflicts]
                })
                return False, f"Alias {label!r} is already assigned to or used by {', '.join(names)}."
        conn.execute(
            """
            UPDATE crew_people SET canonical_display_name = ?, app_user_id = ?,
                is_active = ?, admin_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (display_name, app_user_id, 1 if is_active else 0, admin_note.strip(), now, person_id),
        )
        conn.execute("DELETE FROM crew_aliases WHERE person_id = ?", (person_id,))
        for key, label in clean_aliases.items():
            conn.execute(
                """
                INSERT INTO crew_aliases (
                    person_id, alias, normalized_alias, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (person_id, label, key, now, now),
            )
    return True, "Crew member saved."


def set_planning_location_enabled(location_key: str, enabled: bool) -> bool:
    key = calendar_location_key(location_key)
    if not key:
        return False
    updated_at = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT racecourse
            FROM love_racing_meetings
            WHERE racecourse_key = ?
              AND is_active = 1
            ORDER BY last_seen_at DESC, id DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            """
            INSERT INTO planning_location_preferences (
                location_key, display_name, is_enabled, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(location_key) DO UPDATE SET
                display_name = excluded.display_name,
                is_enabled = excluded.is_enabled,
                updated_at = excluded.updated_at
            """,
            (key, str(row["racecourse"] or key), 1 if enabled else 0, updated_at),
        )
    return True


def get_travel_time_default(track_keys: list[str], base_label: str = "Office / Clow Place") -> sqlite3.Row | None:
    keys = [key.strip().lower() for key in track_keys if str(key or "").strip()]
    if not keys:
        return None
    placeholders = ",".join("?" for _ in keys)
    params: list[object] = [*keys, canonical_travel_base_label(base_label)]
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
    clean_base = canonical_travel_base_label(base_label)
    clean_key, clean_label = canonical_travel_track(track_key, track_label)
    if not clean_key or not clean_label or travel_minutes <= 0:
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
                clean_label,
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
        route_values = dict(
            travel_minutes=max(1, int(travel_minutes)), source=clean_source,
            sample_count=max(0, int(sample_count or 0)), first_seen_at=first_seen_at,
            last_seen_at=last_seen_at, note=note, reverse_is_shared=True,
        )
        _upsert_travel_route_conn(conn, origin_label=clean_base, destination_label=clean_label, **route_values)
        _upsert_travel_route_conn(conn, origin_label=clean_label, destination_label=clean_base, **route_values)


def delete_travel_time_default(default_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM travel_time_defaults WHERE id = ?", (default_id,)).fetchone()
        result = conn.execute(
            "DELETE FROM travel_time_defaults WHERE id = ?",
            (default_id,),
        )
        if row is not None:
            _track_key, track_label = canonical_travel_track(row["track_key"], row["track_label"])
            _delete_shared_travel_route_pair_conn(
                conn,
                origin_label=row["base_label"],
                destination_label=track_label,
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
    clean_key, clean_label = canonical_travel_track(track_key, track_label)
    clean_base = canonical_travel_base_label(base_label)
    if not clean_key or not clean_label or travel_minutes <= 0:
        return 0
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        original = conn.execute(
            "SELECT * FROM travel_time_defaults WHERE id = ?",
            (default_id,),
        ).fetchone()
        if original is None:
            return 0
        conflict = conn.execute(
            """
            SELECT *
            FROM travel_time_defaults
            WHERE track_key = ? AND LOWER(base_label) = LOWER(?) AND id != ?
            LIMIT 1
            """,
            (clean_key, clean_base, default_id),
        ).fetchone()
        if conflict is not None:
            conn.execute("DELETE FROM travel_time_defaults WHERE id = ?", (int(conflict["id"]),))
            _conflict_key, conflict_track = canonical_travel_track(
                conflict["track_key"], conflict["track_label"]
            )
            _delete_shared_travel_route_pair_conn(
                conn,
                origin_label=conflict["base_label"],
                destination_label=conflict_track,
            )
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
        _original_key, original_track = canonical_travel_track(
            original["track_key"], original["track_label"]
        )
        original_pair = {
            calendar_location_key(canonical_travel_base_label(original["base_label"])),
            calendar_location_key(canonical_travel_base_label(original_track)),
        }
        updated_pair = {
            calendar_location_key(clean_base),
            calendar_location_key(clean_label),
        }
        if original_pair != updated_pair:
            _delete_shared_travel_route_pair_conn(
                conn,
                origin_label=original["base_label"],
                destination_label=original_track,
            )
        route_values = dict(
            travel_minutes=max(1, int(travel_minutes)), source="manual",
            note=note, reverse_is_shared=True,
        )
        _upsert_travel_route_conn(conn, origin_label=clean_base, destination_label=clean_label, **route_values)
        _upsert_travel_route_conn(conn, origin_label=clean_label, destination_label=clean_base, **route_values)
    return result.rowcount


def fetch_shifts_for_travel_learning(limit: int = 800) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                id, owner_user_id, title, description, location,
                start_at, end_at, date, paid_hours, source_payload
            FROM shifts
            WHERE deleted_from_source = 0
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
            event_result = conn.execute(
                "UPDATE deputy_schedule_event_changes SET changed_since_viewed = 0 WHERE date = ? AND changed_since_viewed = 1",
                (date_text,),
            )
            schedule_result_count += event_result.rowcount
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
            WHERE s.date = ? AND COALESCE(c.user_visible, 1) = 1
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


def fetch_deputy_assignment_history_for_date(
    date_text: str,
    location_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> list[sqlite3.Row]:
    location_ids = _normalise_int_list(location_ids)
    location_sql = ""
    params: list[object] = [date_text]
    if location_ids:
        placeholders = ", ".join("?" for _ in location_ids)
        location_sql = f"AND area_location_id IN ({placeholders})"
        params.extend(location_ids)
    with get_connection() as conn:
        return conn.execute(
            f"""
            SELECT *
            FROM deputy_schedule_assignment_history
            WHERE date = ?
              {location_sql}
            ORDER BY changed_at DESC, id DESC
            """,
            params,
        ).fetchall()


def fetch_deputy_event_changes_for_date(
    date_text: str,
    location_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> list[sqlite3.Row]:
    location_ids = _normalise_int_list(location_ids)
    location_sql = ""
    params: list[object] = [date_text]
    if location_ids:
        placeholders = ", ".join("?" for _ in location_ids)
        location_sql = f"AND area_location_id IN ({placeholders})"
        params.extend(location_ids)
    with get_connection() as conn:
        return conn.execute(
            f"""
            SELECT *
            FROM deputy_schedule_event_changes
            WHERE date = ?
              AND change_category = 'assignment_change'
              {location_sql}
            ORDER BY changed_at DESC, id DESC
            """,
            params,
        ).fetchall()


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


def fetch_deputy_schedule_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
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
            ORDER BY
                s.date ASC,
                COALESCE(s.area_location_id, a.location_id),
                s.start_at ASC,
                COALESCE(s.area_roster_sort_order, 999999),
                s.area_name ASC
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


def get_roster_integrity_diagnostics() -> dict[str, object]:
    today_text = datetime.now(get_settings().timezone).date().isoformat()
    with get_connection() as conn:
        totals = conn.execute(
            """
            SELECT
                SUM(CASE WHEN date >= ? AND status = 'partial' THEN 1 ELSE 0 END) AS partial_upcoming,
                SUM(CASE WHEN date >= ? THEN personal_evidence_fills ELSE 0 END) AS evidence_fills,
                SUM(CASE WHEN date >= ? THEN conflict_count ELSE 0 END) AS coverage_conflicts,
                MAX(last_capture_at) AS last_checked_at
            FROM deputy_event_coverage
            """,
            (today_text, today_text, today_text),
        ).fetchone()
        evidence = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'possibly_missing' AND date >= ? THEN 1 ELSE 0 END) AS possibly_missing,
                SUM(CASE WHEN status = 'confirmed' AND date >= ? THEN 1 ELSE 0 END) AS confirmed_upcoming,
                SUM(CASE WHEN status = 'historical_locked' THEN 1 ELSE 0 END) AS locked_personal
            FROM deputy_personal_assignment_evidence
            """,
            (today_text, today_text),
        ).fetchone()
        locks = conn.execute(
            """
            SELECT COUNT(*) AS lock_count,
                   SUM(CASE WHEN recovered_from_capture = 1 THEN 1 ELSE 0 END) AS recovered_locks,
                   MAX(locked_at) AS latest_lock_at
            FROM deputy_event_locks
            """
        ).fetchone()
        recovery = conn.execute(
            "SELECT * FROM historical_recovery_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        discrepancies = conn.execute(
            "SELECT COUNT(*) AS count, MAX(captured_at) AS latest_at FROM deputy_historical_discrepancies"
        ).fetchone()
        partial_rows = conn.execute(
            """
            SELECT c.*, COALESCE(l.name, c.area_location_id) AS location_name
            FROM deputy_event_coverage c
            LEFT JOIN deputy_schedule_locations l ON l.location_id = c.area_location_id
            WHERE c.date >= ? AND c.status = 'partial'
            ORDER BY c.date, location_name
            LIMIT 12
            """,
            (today_text,),
        ).fetchall()
    return {
        "partial_upcoming": int((totals["partial_upcoming"] if totals else 0) or 0),
        "evidence_fills": int((totals["evidence_fills"] if totals else 0) or 0),
        "coverage_conflicts": int((totals["coverage_conflicts"] if totals else 0) or 0),
        "possibly_missing": int((evidence["possibly_missing"] if evidence else 0) or 0),
        "confirmed_upcoming": int((evidence["confirmed_upcoming"] if evidence else 0) or 0),
        "locked_personal": int((evidence["locked_personal"] if evidence else 0) or 0),
        "locked_events": int((locks["lock_count"] if locks else 0) or 0),
        "recovered_locks": int((locks["recovered_locks"] if locks else 0) or 0),
        "historical_discrepancies": int((discrepancies["count"] if discrepancies else 0) or 0),
        "last_checked_at": str((totals["last_checked_at"] if totals else "") or ""),
        "latest_lock_at": str((locks["latest_lock_at"] if locks else "") or ""),
        "latest_discrepancy_at": str((discrepancies["latest_at"] if discrepancies else "") or ""),
        "recovery": dict(recovery) if recovery is not None else None,
        "partial_rows": [dict(row) for row in partial_rows],
    }


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


def _record_schedule_assignment_change(
    conn: sqlite3.Connection,
    source_shift_id: int,
    existing: sqlite3.Row | None,
    values: dict[str, object],
    changed_at: str,
) -> None:
    if existing is None:
        return
    old_person = str(existing["employee_name"] or "").strip()
    new_person = str(values.get("employee_name") or "").strip()
    old_position = str(existing["area_name"] or "").strip()
    new_position = str(values.get("area_name") or "").strip()
    if old_person == new_person and old_position == new_position:
        return
    position_label = new_position or old_position or "Position"
    if old_position != new_position and new_person:
        candidates = conn.execute(
            """
            SELECT area_name, employee_name
            FROM deputy_schedule_shifts
            WHERE date = ?
              AND COALESCE(area_location_id, -1) = COALESCE(?, -1)
              AND source_shift_id != ?
            """,
            (values.get("date"), values.get("area_location_id"), source_shift_id),
        ).fetchall()
        target_key = re.sub(r"[^a-z0-9]+", "", new_position.lower())
        for candidate in candidates:
            candidate_key = re.sub(r"[^a-z0-9]+", "", str(candidate["area_name"] or "").lower())
            if candidate_key == target_key:
                old_person = str(candidate["employee_name"] or "").strip()
                break
    if old_person == new_person:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO deputy_schedule_assignment_history (
            source_shift_id, date, area_location_id, position_label,
            old_employee_name, new_employee_name, changed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_shift_id,
            values.get("date"),
            values.get("area_location_id"),
            position_label,
            old_person,
            new_person,
            changed_at,
        ),
    )


def _display_change_value(value: object) -> str:
    if value in (None, ""):
        return "blank"
    return str(value)


EVENT_POSITION_ALIASES = {
    "side1": ("side1", "Side 1"),
    "sideone": ("side1", "Side 1"),
    "sideonecam": ("side1", "Side 1"),
    "side2": ("side2", "Side 2"),
    "sidetwo": ("side2", "Side 2"),
    "sidetwocam": ("side2", "Side 2"),
    "start": ("start", "Start"),
    "headon": ("headon", "Head On"),
    "back": ("back", "Back"),
    "back2": ("back2", "Back2"),
    "turn": ("turn", "Turn"),
    "rts": ("rts", "RTS"),
    "iv": ("iv", "IV"),
    "iv1": ("iv", "IV"),
    "steadi": ("steadi", "Steadi"),
    "steadiassist": ("steadiassist", "Steadi Assist"),
    "dir": ("director", "Director"),
    "director": ("director", "Director"),
    "sound": ("sound", "Sound"),
    "svt": ("soundvt", "Sound/VT"),
    "soundvt": ("soundvt", "Sound/VT"),
    "vt": ("vt", "VT"),
    "vt2": ("vt2", "VT 2"),
    "ccu1": ("ccu1", "CCU1"),
    "ccu2": ("ccu2", "CCU2"),
    "eng": ("eng", "ENG"),
    "engineer": ("eng", "ENG"),
    "fm": ("fm", "FM"),
    "floormanager": ("fm", "FM"),
    "gimbal": ("gimbal", "Gimbal"),
    "drone": ("drone", "Drone"),
    "editor": ("editor", "Editor"),
}

EVENT_NON_POSITION_KEYS = {
    "vehicle", "vehicles", "travel", "travelthenovernighter", "outofregion",
    "manager", "northern", "northernopscontractors", "accommodation", "web",
    "shift", "maintenance", "training", "mewptraining", "office", "clowplace",
    "rav91", "tender", "transit", "ob",
}


def _event_position(value: object) -> tuple[str, str] | None:
    raw = _clean_role_name(value)
    key = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if not key or key in EVENT_NON_POSITION_KEYS or key.isdigit():
        return None
    if key in EVENT_POSITION_ALIASES:
        return EVENT_POSITION_ALIASES[key]
    return key, raw or "Position"


def _event_rows_overlap(left: dict[str, object], right: dict[str, object]) -> bool:
    left_start = str(left.get("start_at") or "")
    left_end = str(left.get("end_at") or "")
    right_start = str(right.get("start_at") or "")
    right_end = str(right.get("end_at") or "")
    if not all((left_start, left_end, right_start, right_end)):
        return True
    return left_start < right_end and right_start < left_end


def _event_person_identity(
    conn: sqlite3.Connection,
    employee_id: object,
    employee_name: object,
) -> tuple[str, int | None, str]:
    numeric_id = _optional_int(employee_id)
    clean_name = str(employee_name or "").strip()
    if numeric_id is not None:
        person = conn.execute(
            "SELECT canonical_display_name FROM crew_people WHERE deputy_employee_id = ? LIMIT 1",
            (numeric_id,),
        ).fetchone()
        canonical_name = str(person["canonical_display_name"] or "").strip() if person is not None else ""
        return f"employee:{numeric_id}", numeric_id, canonical_name or clean_name
    name_key = normalise_person_identity(clean_name)
    if name_key:
        people = conn.execute(
            """
            SELECT DISTINCT p.id, p.canonical_display_name
            FROM crew_people p
            LEFT JOIN crew_aliases a ON a.crew_person_id = p.id AND a.is_active = 1
            WHERE p.is_active = 1
              AND (p.normalized_name = ? OR a.normalized_alias = ?)
            """,
            (name_key, name_key),
        ).fetchall()
        if len(people) == 1:
            return f"crew:{int(people[0]['id'])}", None, str(people[0]["canonical_display_name"] or clean_name)
    return (f"name:{name_key}" if name_key else "open"), None, clean_name


def _authoritative_schedule_coverage(payload: dict[str, object]) -> list[dict[str, object]]:
    result = []
    for coverage in payload.get("schedule_coverage") or []:
        if not isinstance(coverage, dict):
            continue
        start_date = str(coverage.get("start_date") or "")[:10]
        end_date = str(coverage.get("end_date") or "")[:10]
        mode = str(coverage.get("mode") or "").strip().lower()
        location_ids = {
            value
            for value in (_optional_int(item) for item in coverage.get("location_ids") or [])
            if value is not None
        }
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date)
            or mode not in {"all", "selected"}
            or (mode == "selected" and not location_ids)
        ):
            continue
        result.append({"start_date": start_date, "end_date": end_date, "mode": mode, "location_ids": location_ids})
    return result


def _authoritative_schedule_rows(
    conn: sqlite3.Connection,
    coverage_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows_by_id: dict[int, dict[str, object]] = {}
    for coverage in coverage_rows:
        params: list[object] = [coverage["start_date"], coverage["end_date"]]
        location_sql = ""
        location_ids = sorted(coverage["location_ids"])
        if coverage["mode"] == "selected":
            placeholders = ", ".join("?" for _ in location_ids)
            location_sql = f"AND COALESCE(s.area_location_id, a.location_id) IN ({placeholders})"
            params.extend(location_ids)
        for row in conn.execute(
            f"""
            SELECT s.*, COALESCE(s.area_location_id, a.location_id) AS schedule_location_id
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a ON a.area_id = s.area_id
            WHERE s.date BETWEEN ? AND ? {location_sql}
            """,
            params,
        ).fetchall():
            rows_by_id[int(row["source_shift_id"])] = dict(row)
    return list(rows_by_id.values())


def _effective_event_snapshots(
    conn: sqlite3.Connection,
    rows: list[dict[str, object]],
) -> dict[tuple[str, int], list[dict[str, object]]]:
    scopes: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in rows:
        location_id = _optional_int(row.get("schedule_location_id") or row.get("area_location_id"))
        date_text = str(row.get("date") or "")[:10]
        position = _event_position(row.get("area_name"))
        if location_id is None or not date_text or position is None:
            continue
        identity, employee_id, employee_name = _event_person_identity(
            conn, row.get("employee_id"), row.get("employee_name")
        )
        item = {
            "position_key": position[0],
            "position_label": position[1],
            "identity": identity,
            "employee_id": employee_id,
            "employee_name": employee_name,
            "is_open": bool(int(row.get("is_open") or 0)) or not employee_name,
            "start_at": str(row.get("start_at") or ""),
            "end_at": str(row.get("end_at") or ""),
            "captured_at": str(row.get("captured_at") or ""),
            "source_shift_id": int(row.get("source_shift_id") or 0),
        }
        scopes.setdefault((date_text, location_id), []).append(item)

    for scope, items in list(scopes.items()):
        sound_vt_items = [item for item in items if item["position_key"] == "soundvt"]
        vt_items = [item for item in items if item["position_key"] == "vt"]
        for sound_vt in sound_vt_items:
            if any(
                vt["identity"] != sound_vt["identity"]
                and vt["identity"] != "open"
                and sound_vt["identity"] != "open"
                and _event_rows_overlap(sound_vt, vt)
                for vt in vt_items
            ):
                sound_vt["position_key"] = "sound"
                sound_vt["position_label"] = "Sound"

        visible = []
        for item in items:
            if item["identity"] != "open" and any(
                other["identity"] == item["identity"]
                and other["position_key"] != item["position_key"]
                and other["captured_at"] > item["captured_at"]
                and _event_rows_overlap(item, other)
                for other in items
            ):
                continue
            visible.append(item)

        deduped: list[dict[str, object]] = []
        for item in sorted(visible, key=lambda value: (value["captured_at"], value["source_shift_id"])):
            match = next((
                existing for existing in deduped
                if existing["position_key"] == item["position_key"] and _event_rows_overlap(existing, item)
            ), None)
            if match is None:
                deduped.append(item)
            elif (item["captured_at"], item["source_shift_id"]) >= (match["captured_at"], match["source_shift_id"]):
                deduped[deduped.index(match)] = item
        scopes[scope] = sorted(deduped, key=lambda item: (item["position_key"], item["identity"]))
    return scopes


def _snapshot_hash(items: list[dict[str, object]]) -> str:
    values = [
        {
            "position": item["position_key"],
            "identity": item["identity"],
            "open": item["is_open"],
            "start": item["start_at"],
            "end": item["end_at"],
        }
        for item in items
    ]
    return hashlib.sha256(json_dumps(values).encode("utf-8")).hexdigest()


def _event_overlap_components(
    before: list[dict[str, object]],
    after: list[dict[str, object]],
) -> list[tuple[list[dict[str, object]], list[dict[str, object]]]]:
    tagged = [("before", item) for item in before] + [("after", item) for item in after]
    components: list[list[tuple[str, dict[str, object]]]] = []
    for tagged_item in tagged:
        matching_indexes = [
            index
            for index, component in enumerate(components)
            if any(_event_rows_overlap(tagged_item[1], existing[1]) for existing in component)
        ]
        if not matching_indexes:
            components.append([tagged_item])
            continue
        first_index = matching_indexes[0]
        components[first_index].append(tagged_item)
        for index in reversed(matching_indexes[1:]):
            components[first_index].extend(components.pop(index))
    return [
        (
            [item for origin, item in component if origin == "before"],
            [item for origin, item in component if origin == "after"],
        )
        for component in components
    ]


def _event_change_record(
    *,
    change_type: str,
    old_positions: list[str],
    new_positions: list[str],
    old_person: dict[str, object] | None,
    new_person: dict[str, object] | None,
    display_summary: str,
    inline_summary: str,
) -> dict[str, object]:
    return {
        "change_type": change_type,
        "old_positions": old_positions,
        "new_positions": new_positions,
        "old_employee_id": (old_person or {}).get("employee_id"),
        "old_employee_name": str((old_person or {}).get("employee_name") or ""),
        "new_employee_id": (new_person or {}).get("employee_id"),
        "new_employee_name": str((new_person or {}).get("employee_name") or ""),
        "display_summary": display_summary,
        "inline_summary": inline_summary,
    }


def _compare_event_assignments(
    before: list[dict[str, object]],
    after: list[dict[str, object]],
) -> list[dict[str, object]]:
    before_by_position = {str(item["position_key"]): item for item in before}
    after_by_position = {str(item["position_key"]): item for item in after}
    before_by_person: dict[str, list[dict[str, object]]] = {}
    after_by_person: dict[str, list[dict[str, object]]] = {}
    for item in before:
        if item["identity"] != "open":
            before_by_person.setdefault(str(item["identity"]), []).append(item)
    for item in after:
        if item["identity"] != "open":
            after_by_person.setdefault(str(item["identity"]), []).append(item)

    changes: list[dict[str, object]] = []
    before_sound = before_by_position.get("sound")
    before_vt = before_by_position.get("vt")
    after_combined = after_by_position.get("soundvt")
    before_combined = before_by_position.get("soundvt")
    after_sound = after_by_position.get("sound")
    after_vt = after_by_position.get("vt")
    roles_merged = bool(before_sound and before_vt and after_combined)
    roles_split = bool(before_combined and after_sound and after_vt)
    moved_identities: set[str] = set()
    for identity in sorted(set(before_by_person) & set(after_by_person)):
        old_positions = {str(item["position_key"]): item for item in before_by_person[identity]}
        new_positions = {str(item["position_key"]): item for item in after_by_person[identity]}
        old_only = [old_positions[key] for key in sorted(set(old_positions) - set(new_positions))]
        new_only = [new_positions[key] for key in sorted(set(new_positions) - set(old_positions))]
        for old_item, new_item in zip(old_only, new_only):
            if not _event_rows_overlap(old_item, new_item):
                continue
            if (roles_merged or roles_split) and {
                str(old_item["position_key"]), str(new_item["position_key"])
            } <= {"sound", "vt", "soundvt"}:
                continue
            moved_identities.add(identity)
            name = str(new_item["employee_name"] or old_item["employee_name"] or "Crew member")
            changes.append(_event_change_record(
                change_type="move",
                old_positions=[str(old_item["position_label"])],
                new_positions=[str(new_item["position_label"])],
                old_person=old_item,
                new_person=new_item,
                display_summary=f"Crew move: {name} — {old_item['position_label']} → {new_item['position_label']}",
                inline_summary=f"{name} moved from {old_item['position_label']}",
            ))

    merge_positions: set[str] = set()
    if roles_merged:
        merge_positions = {"sound", "vt", "soundvt"}
        changes.append(_event_change_record(
            change_type="merge",
            old_positions=["Sound", "VT"],
            new_positions=["Sound/VT"],
            old_person=before_vt,
            new_person=after_combined,
            display_summary=(
                f"Crew roles combined: Sound {before_sound['employee_name'] or 'TBC'} + "
                f"VT {before_vt['employee_name'] or 'TBC'} → Sound/VT {after_combined['employee_name'] or 'TBC'}"
            ),
            inline_summary="Sound and VT combined",
        ))

    if roles_split:
        merge_positions = {"sound", "vt", "soundvt"}
        changes.append(_event_change_record(
            change_type="split",
            old_positions=["Sound/VT"],
            new_positions=["Sound", "VT"],
            old_person=before_combined,
            new_person=after_vt,
            display_summary=(
                f"Crew roles split: Sound/VT {before_combined['employee_name'] or 'TBC'} → "
                f"Sound {after_sound['employee_name'] or 'TBC'} + VT {after_vt['employee_name'] or 'TBC'}"
            ),
            inline_summary="Sound/VT split into Sound and VT",
        ))

    for position_key in sorted(set(before_by_position) | set(after_by_position)):
        if position_key in merge_positions:
            continue
        old_item = before_by_position.get(position_key)
        new_item = after_by_position.get(position_key)
        old_identity = str((old_item or {}).get("identity") or "open")
        new_identity = str((new_item or {}).get("identity") or "open")
        if old_identity == new_identity:
            continue
        position_label = str((new_item or old_item or {}).get("position_label") or "Position")
        old_name = str((old_item or {}).get("employee_name") or "TBC")
        new_name = str((new_item or {}).get("employee_name") or "TBC")
        if old_identity != "open" and new_identity != "open":
            if old_identity in moved_identities and new_identity in moved_identities:
                continue
            change_type = "replacement"
        elif old_identity != "open":
            change_type = "opened"
        else:
            change_type = "filled"
        changes.append(_event_change_record(
            change_type=change_type,
            old_positions=[position_label],
            new_positions=[position_label],
            old_person=old_item,
            new_person=new_item,
            display_summary=f"Crew: {position_label} — {old_name} → {new_name}",
            inline_summary=f"{old_name} → {new_name}",
        ))
    return changes


def _record_authoritative_event_changes(
    conn: sqlite3.Connection,
    before_snapshots: dict[tuple[str, int], list[dict[str, object]]],
    after_snapshots: dict[tuple[str, int], list[dict[str, object]]],
    captured_at: str,
) -> int:
    saved = 0
    for scope in sorted(set(before_snapshots) | set(after_snapshots)):
        before = before_snapshots.get(scope, [])
        after = after_snapshots.get(scope, [])
        if not before:
            continue
        before_hash = _snapshot_hash(before)
        after_hash = _snapshot_hash(after)
        if before_hash == after_hash:
            continue
        date_text, location_id = scope
        for event_before, event_after in _event_overlap_components(before, after):
            event_before_hash = _snapshot_hash(event_before)
            event_after_hash = _snapshot_hash(event_after)
            if event_before_hash == event_after_hash:
                continue
            group_id = hashlib.sha256(
                f"{date_text}|{location_id}|{event_before_hash}|{event_after_hash}|{captured_at}".encode("utf-8")
            ).hexdigest()
            starts = [str(item["start_at"]) for item in event_before + event_after if item.get("start_at")]
            ends = [str(item["end_at"]) for item in event_before + event_after if item.get("end_at")]
            for change in _compare_event_assignments(event_before, event_after):
                change_key = hashlib.sha256(json_dumps(change).encode("utf-8")).hexdigest()
                result = conn.execute(
                    """
                    INSERT OR IGNORE INTO deputy_schedule_event_changes (
                        group_id, change_key, change_type, date, area_location_id,
                        event_start_at, event_end_at, old_positions, new_positions,
                        old_employee_id, old_employee_name, new_employee_id, new_employee_name,
                        changed_at, display_summary, inline_summary, before_hash, after_hash,
                        changed_since_viewed, change_category
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group_id, change_key, change["change_type"], date_text, location_id,
                        min(starts) if starts else "", max(ends) if ends else "",
                        json_dumps(change["old_positions"]), json_dumps(change["new_positions"]),
                        change["old_employee_id"], change["old_employee_name"],
                        change["new_employee_id"], change["new_employee_name"], captured_at,
                        change["display_summary"], change["inline_summary"], before_hash, after_hash,
                        0 if _event_lock_row(conn, date_text, location_id) is not None else 1,
                        "historical_discrepancy" if _event_lock_row(conn, date_text, location_id) is not None else "assignment_change",
                    ),
                )
                saved += max(0, int(result.rowcount or 0))
    return saved


def _coverage_contains_scope(coverage_rows: list[dict[str, object]], date_text: str, location_id: int) -> bool:
    for coverage in coverage_rows:
        if not (str(coverage["start_date"]) <= date_text <= str(coverage["end_date"])):
            continue
        if coverage["mode"] == "all" or location_id in coverage["location_ids"]:
            return True
    return False


def _evaluate_event_coverage(
    conn: sqlite3.Connection,
    payload: dict[str, object],
    coverage_rows: list[dict[str, object]],
    before_snapshots: dict[tuple[str, int], list[dict[str, object]]],
    captured_at: str,
    owner_user_id: int | None,
) -> set[tuple[str, int]]:
    settings = get_settings()
    today_text = datetime.now(settings.timezone).date().isoformat()
    incoming_scopes: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in payload.get("extracted_schedule_shifts") or []:
        if not isinstance(row, dict):
            continue
        location_id = _optional_int(row.get("areaLocationId") or row.get("location") or row.get("locationId"))
        date_text = str(row.get("start") or "")[:10]
        position = _event_position(row.get("areaName"))
        if location_id is None or not date_text or position is None:
            continue
        incoming_scopes.setdefault((date_text, location_id), []).append({
            "position_key": position[0],
            "position_label": position[1],
            "employee_id": _optional_int(row.get("employee")),
            "employee_name": str(row.get("employeeName") or "").strip(),
            "is_open": bool(row.get("isOpen")),
            "start_at": str(row.get("start") or ""),
            "end_at": str(row.get("end") or ""),
        })
    evidence_scopes: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for evidence in conn.execute(
        """
        SELECT e.*, COALESCE(p.canonical_display_name, u.display_name) AS employee_name
        FROM deputy_personal_assignment_evidence e
        JOIN app_users u ON u.id = e.owner_user_id AND u.is_active = 1
        LEFT JOIN crew_people p ON p.id = e.canonical_person_id
        WHERE e.status IN ('confirmed', 'possibly_missing') AND e.date >= ?
        """,
        (today_text,),
    ).fetchall():
        scope = (str(evidence["date"]), int(evidence["area_location_id"]))
        if _coverage_contains_scope(coverage_rows, *scope):
            evidence_scopes.setdefault(scope, []).append(evidence)

    scopes = {
        scope for scope in set(incoming_scopes) | set(before_snapshots) | set(evidence_scopes)
        if scope[0] >= today_text and _coverage_contains_scope(coverage_rows, *scope)
    }
    partial_scopes: set[tuple[str, int]] = set()
    retry_lookup = {
        (str(item.get("date") or ""), _optional_int(item.get("location_id"))): item
        for item in payload.get("event_retry_coverage") or [] if isinstance(item, dict)
    }
    for date_text, location_id in sorted(scopes):
        incoming = incoming_scopes.get((date_text, location_id), [])
        evidence_rows = evidence_scopes.get((date_text, location_id), [])
        captured_by_position = {
            str(item["position_key"]): item for item in incoming
            if str(item.get("employee_name") or "").strip() or item.get("is_open")
        }
        named_positions = {
            key for key, item in captured_by_position.items()
            if str(item.get("employee_name") or "").strip()
        }
        expected_positions = set()
        for area in conn.execute(
            "SELECT name FROM deputy_schedule_areas WHERE location_id = ?",
            (location_id,),
        ).fetchall():
            position = _event_position(area["name"])
            if position and position[0] in CORE_EVENT_POSITION_KEYS:
                expected_positions.add(position[0])
        personal_positions = {str(row["position_key"]) for row in evidence_rows}
        previous_named = {
            str(item["position_key"]) for item in before_snapshots.get((date_text, location_id), [])
            if item.get("identity") != "open"
        }
        missing_expected = expected_positions - set(captured_by_position)
        missing_personal = personal_positions - named_positions
        missing_previous = previous_named - named_positions
        retry = retry_lookup.get((date_text, location_id))
        exact_selected_complete = any(
            coverage["mode"] == "selected"
            and str(coverage["start_date"]) == date_text
            and str(coverage["end_date"]) == date_text
            and location_id in coverage["location_ids"]
            for coverage in coverage_rows
        )
        retry_complete = bool(retry and retry.get("status") == "complete") or exact_selected_complete
        reasons = []
        if missing_expected:
            reasons.append("expected positions absent: " + ", ".join(sorted(missing_expected)))
        if missing_personal:
            reasons.append("personal assignments absent: " + ", ".join(sorted(missing_personal)))
        if missing_previous and not retry_complete:
            reasons.append("previous named assignments absent: " + ", ".join(sorted(missing_previous)))
        conflicts = 0
        for evidence in evidence_rows:
            shared = captured_by_position.get(str(evidence["position_key"]))
            if shared is None or not str(shared.get("employee_name") or "").strip():
                continue
            same_employee = (
                evidence["deputy_employee_id"] is not None
                and _optional_int(shared.get("employee_id")) == _optional_int(evidence["deputy_employee_id"])
            ) or (
                evidence["canonical_person_id"] is not None
                and normalise_person_identity(str(shared.get("employee_name") or ""))
                == normalise_person_identity(str(evidence["employee_name"] or ""))
            )
            if not same_employee:
                conflicts += 1
        if retry and retry.get("status") != "complete":
            reasons.append("selected-location retry incomplete")
        status = "partial" if reasons else "complete"
        if status == "partial":
            partial_scopes.add((date_text, location_id))
        starts = [str(item.get("start_at") or "") for item in incoming if item.get("start_at")]
        ends = [str(item.get("end_at") or "") for item in incoming if item.get("end_at")]
        conn.execute(
            """
            INSERT INTO deputy_event_coverage (
                date, area_location_id, event_start_at, event_end_at, status,
                expected_positions, named_positions, open_positions,
                placeholder_positions, personal_evidence_fills, conflict_count,
                reason, last_capture_at, source_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, area_location_id, event_start_at) DO UPDATE SET
                event_end_at = excluded.event_end_at,
                status = excluded.status,
                expected_positions = excluded.expected_positions,
                named_positions = excluded.named_positions,
                open_positions = excluded.open_positions,
                placeholder_positions = excluded.placeholder_positions,
                personal_evidence_fills = excluded.personal_evidence_fills,
                conflict_count = excluded.conflict_count,
                reason = excluded.reason,
                last_capture_at = excluded.last_capture_at,
                source_user_id = excluded.source_user_id
            """,
            (
                date_text, location_id, min(starts) if starts else "", max(ends) if ends else "",
                status, len(expected_positions), len(named_positions),
                sum(1 for item in captured_by_position.values() if item.get("is_open")),
                len(missing_expected), len(missing_personal), conflicts,
                "; ".join(reasons), captured_at, owner_user_id,
            ),
        )
    return partial_scopes


def _prune_missing_deputy_schedule_rows(
    conn: sqlite3.Connection,
    payload: dict[str, object],
    captured_shift_ids: set[int],
    partial_scopes: set[tuple[str, int]] | None = None,
) -> int:
    coverage_rows = payload.get("schedule_coverage")
    if not isinstance(coverage_rows, list):
        return 0

    remove_ids: set[int] = set()
    partial_scopes = partial_scopes or set()
    for coverage in coverage_rows:
        if not isinstance(coverage, dict):
            continue
        start_date = str(coverage.get("start_date") or "")[:10]
        end_date = str(coverage.get("end_date") or "")[:10]
        mode = str(coverage.get("mode") or "").strip().lower()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", end_date
        ):
            continue
        location_ids = {
            value
            for value in (_optional_int(item) for item in coverage.get("location_ids") or [])
            if value is not None
        }
        if mode not in {"all", "selected"} or (mode == "selected" and not location_ids):
            continue
        existing_rows = conn.execute(
            """
            SELECT s.source_shift_id, s.date,
                   COALESCE(s.area_location_id, a.location_id) AS schedule_location_id
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a ON a.area_id = s.area_id
            WHERE s.date BETWEEN ? AND ?
            """,
            (start_date, end_date),
        ).fetchall()
        for row in existing_rows:
            source_shift_id = int(row["source_shift_id"])
            if source_shift_id in captured_shift_ids:
                continue
            scope = (str(row["date"] or ""), _optional_int(row["schedule_location_id"]))
            if scope in partial_scopes:
                continue
            if _event_lock_row(conn, scope[0], scope[1]) is not None or scope[0] < datetime.now(get_settings().timezone).date().isoformat():
                continue
            if mode == "selected" and _optional_int(row["schedule_location_id"]) not in location_ids:
                continue
            remove_ids.add(source_shift_id)

    if remove_ids:
        conn.executemany(
            "DELETE FROM deputy_schedule_shifts WHERE source_shift_id = ?",
            ((source_shift_id,) for source_shift_id in sorted(remove_ids)),
        )
    return len(remove_ids)


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
        lock_completed_events(conn)
        authoritative_coverage = _authoritative_schedule_coverage(payload)
        before_event_snapshots = _effective_event_snapshots(
            conn,
            _authoritative_schedule_rows(conn, authoritative_coverage),
        )
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
        personal_coverage_counts = _save_personal_capture_coverage(
            conn, payload, owner_user_id, captured_at
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
            event_lock = _event_lock_row(
                conn, str(values["date"]), _optional_int(values["area_location_id"]),
                str(values["start_at"]), str(values["end_at"]),
            )
            if event_lock is not None and existing is None:
                incoming_position = _event_position(values["area_name"])
                same_position = next(
                    (
                        row for row in conn.execute(
                            "SELECT * FROM deputy_schedule_shifts WHERE date = ? AND area_location_id = ? ORDER BY captured_at DESC",
                            (values["date"], values["area_location_id"]),
                        ).fetchall()
                        if incoming_position is not None
                        and _event_position(row["area_name"]) is not None
                        and _event_position(row["area_name"])[0] == incoming_position[0]
                    ),
                    None,
                )
                if same_position is not None and str(same_position["employee_name"] or "").strip():
                    incoming_name = str(values["employee_name"] or "").strip()
                    if incoming_name and normalise_person_identity(incoming_name) != normalise_person_identity(str(same_position["employee_name"] or "")):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO deputy_historical_discrepancies (
                                date, area_location_id, source_shift_id, position_label,
                                existing_value, incoming_value, discrepancy_type,
                                captured_at, details
                            ) VALUES (?, ?, ?, ?, ?, ?, 'locked_assignment_conflict', ?, ?)
                            """,
                            (
                                values["date"], values["area_location_id"], source_shift_id,
                                values["area_name"], same_position["employee_name"], incoming_name,
                                captured_at, "Late Deputy row did not replace the locked historical assignment.",
                            ),
                        )
                        continue
            if event_lock is not None and existing is not None:
                for field_name in ("area_name", "employee_name", "start_at", "end_at", "note"):
                    old_value = existing[field_name]
                    new_value = values[field_name]
                    if str(old_value or "").strip() and str(new_value or "").strip() and str(old_value) != str(new_value):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO deputy_historical_discrepancies (
                                date, area_location_id, source_shift_id, position_label,
                                existing_value, incoming_value, discrepancy_type,
                                captured_at, details
                            ) VALUES (?, ?, ?, ?, ?, ?, 'locked_field_conflict', ?, ?)
                            """,
                            (
                                values["date"], values["area_location_id"], source_shift_id,
                                values["area_name"], str(old_value), str(new_value), captured_at,
                                f"Late Deputy {field_name} did not overwrite locked history.",
                            ),
                        )
                    if str(old_value or "").strip():
                        values[field_name] = old_value
                for field_name in ("area_id", "area_location_id", "area_roster_sort_order", "employee_id", "duration"):
                    if existing[field_name] is not None:
                        values[field_name] = existing[field_name]
                values["is_open"] = existing["is_open"]
                values["is_published"] = existing["is_published"]
                values["raw_payload"] = existing["raw_payload"]
            change_summary = "" if event_lock is not None else _schedule_change_summary(existing, values)
            changed = bool(change_summary)
            if changed:
                _record_schedule_assignment_change(conn, source_shift_id, existing, values, captured_at)
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
        partial_scopes = _evaluate_event_coverage(
            conn, payload, authoritative_coverage, before_event_snapshots,
            captured_at, owner_user_id,
        )
        removed = _prune_missing_deputy_schedule_rows(
            conn,
            payload,
            {
                int(shift_id)
                for shift_id in schedule_shift_lookup
                if str(shift_id).isdigit()
            },
            partial_scopes,
        )
        event_changes_saved = _record_authoritative_event_changes(
            conn,
            before_event_snapshots,
            _effective_event_snapshots(
                conn,
                _authoritative_schedule_rows(conn, authoritative_coverage),
            ),
            captured_at,
        )
    return {
        "own_seen": own_counts["seen"],
        "own_created": own_counts["created"],
        "own_updated": own_counts["updated"],
        "schedule_saved": saved,
        "schedule_removed": removed,
        "event_changes_saved": event_changes_saved,
        "personal_evidence_saved": own_counts["seen"],
        "personal_possibly_missing": personal_coverage_counts["possibly_missing"],
        "personal_retired": personal_coverage_counts["retired"],
        "partial_events": len(partial_scopes),
    }


def upsert_track_map(
    *,
    track_key: str,
    track_label: str,
    course_label: str,
    course_url: str,
    image_url: str,
    file_name: str,
    content_type: str,
    image_hash: str,
    status: str,
    checked_at: str,
    updated_at: str,
    image_width: int = 0,
    image_height: int = 0,
    byte_size: int = 0,
    selected_source_url: str = "",
    candidate_count: int = 0,
    refresh_result: str = "",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO track_maps (
                track_key, track_label, course_label, course_url, image_url,
                file_name, content_type, image_hash, status, checked_at, updated_at,
                image_width, image_height, byte_size, selected_source_url,
                candidate_count, refresh_result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_key) DO UPDATE SET
                track_label = excluded.track_label,
                course_label = excluded.course_label,
                course_url = excluded.course_url,
                image_url = excluded.image_url,
                file_name = excluded.file_name,
                content_type = excluded.content_type,
                image_hash = excluded.image_hash,
                status = excluded.status,
                checked_at = excluded.checked_at,
                updated_at = excluded.updated_at,
                image_width = excluded.image_width,
                image_height = excluded.image_height,
                byte_size = excluded.byte_size,
                selected_source_url = excluded.selected_source_url,
                candidate_count = excluded.candidate_count,
                refresh_result = excluded.refresh_result
            """,
            (
                track_key,
                track_label,
                course_label,
                course_url,
                image_url,
                file_name,
                content_type,
                image_hash,
                status,
                checked_at,
                updated_at,
                max(0, int(image_width or 0)),
                max(0, int(image_height or 0)),
                max(0, int(byte_size or 0)),
                selected_source_url or image_url,
                max(0, int(candidate_count or 0)),
                refresh_result,
            ),
        )


def get_track_map(track_key: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM track_maps WHERE track_key = ? AND status = 'ok'",
            (track_key,),
        ).fetchone()


def list_track_maps() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM track_maps ORDER BY track_label, track_key"
        ).fetchall()


CORE_EVENT_POSITION_KEYS = {
    "side1", "side2", "headon", "back", "director", "sound", "soundvt",
    "vt", "ccu1", "ccu2", "eng",
}


def _event_completion_time(date_text: str, end_at: str, settings: Settings | None = None) -> datetime | None:
    settings = settings or get_settings()
    try:
        event_date = datetime.fromisoformat(date_text).date()
    except ValueError:
        return None
    if end_at:
        try:
            parsed_end = datetime.fromisoformat(end_at)
            if parsed_end.tzinfo is None:
                parsed_end = parsed_end.replace(tzinfo=settings.timezone)
            return parsed_end.astimezone(settings.timezone) + timedelta(hours=6)
        except ValueError:
            pass
    return datetime.combine(event_date + timedelta(days=1), datetime.min.time(), settings.timezone) + timedelta(hours=5)


def _event_is_completed(date_text: str, end_at: str, now: datetime | None = None) -> bool:
    settings = get_settings()
    now = now or datetime.now(settings.timezone)
    completion = _event_completion_time(date_text, end_at, settings)
    return completion is not None and completion <= now


def _event_lock_row(
    conn: sqlite3.Connection,
    date_text: str,
    location_id: int | None,
    start_at: str = "",
    end_at: str = "",
) -> sqlite3.Row | None:
    if location_id is None:
        return None
    rows = conn.execute(
        "SELECT * FROM deputy_event_locks WHERE date = ? AND area_location_id = ?",
        (date_text, location_id),
    ).fetchall()
    for row in rows:
        if not start_at or not row["event_start_at"]:
            return row
        if _event_rows_overlap(
            {"start_at": start_at, "end_at": end_at},
            {"start_at": row["event_start_at"], "end_at": row["event_end_at"]},
        ):
            return row
    return None


def lock_completed_events(conn: sqlite3.Connection | None = None, now: datetime | None = None) -> int:
    owns_connection = conn is None
    conn = conn or get_connection()
    settings = get_settings()
    now = now or datetime.now(settings.timezone)
    locked = 0
    try:
        events = conn.execute(
            """
            SELECT date, area_location_id, MIN(start_at) AS event_start_at, MAX(end_at) AS event_end_at
            FROM deputy_schedule_shifts
            WHERE area_location_id IS NOT NULL AND TRIM(date) != ''
            GROUP BY date, area_location_id
            """
        ).fetchall()
        for event in events:
            date_text = str(event["date"] or "")
            location_id = _optional_int(event["area_location_id"])
            start_at = str(event["event_start_at"] or "")
            end_at = str(event["event_end_at"] or "")
            if location_id is None or not _event_is_completed(date_text, end_at, now):
                continue
            result = conn.execute(
                """
                INSERT OR IGNORE INTO deputy_event_locks (
                    date, area_location_id, event_start_at, event_end_at,
                    locked_at, lock_reason, recovered_from_capture
                ) VALUES (?, ?, ?, ?, ?, 'completed_plus_6h', 0)
                """,
                (date_text, location_id, start_at, end_at, now.isoformat(timespec="seconds")),
            )
            locked += max(0, int(result.rowcount or 0))
            conn.execute(
                "UPDATE deputy_schedule_shifts SET changed_since_viewed = 0 WHERE date = ? AND area_location_id = ?",
                (date_text, location_id),
            )
            conn.execute(
                "UPDATE deputy_schedule_event_changes SET changed_since_viewed = 0 WHERE date = ? AND area_location_id = ?",
                (date_text, location_id),
            )
            conn.execute(
                "UPDATE deputy_event_coverage SET status = 'locked_historical' WHERE date = ? AND area_location_id = ?",
                (date_text, location_id),
            )
        personal_rows = conn.execute(
            "SELECT id, date, end_at FROM shifts WHERE deleted_from_source = 0 AND historical_locked_at IS NULL"
        ).fetchall()
        for row in personal_rows:
            if _event_is_completed(str(row["date"] or ""), str(row["end_at"] or ""), now):
                conn.execute(
                    "UPDATE shifts SET historical_locked_at = ?, changed_since_viewed = 0 WHERE id = ?",
                    (now.isoformat(timespec="seconds"), int(row["id"])),
                )
        conn.execute(
            """
            UPDATE deputy_personal_assignment_evidence
            SET status = 'historical_locked'
            WHERE status != 'historical_locked'
              AND EXISTS (
                  SELECT 1 FROM deputy_event_locks l
                  WHERE l.date = deputy_personal_assignment_evidence.date
                    AND l.area_location_id = deputy_personal_assignment_evidence.area_location_id
              )
            """
        )
        if owns_connection:
            conn.commit()
        return locked
    finally:
        if owns_connection:
            conn.close()


def recover_historical_schedule_from_captures(
    settings: Settings | None = None,
    *,
    force: bool = False,
) -> dict[str, object]:
    """Restore missing completed-event rows from retained successful captures once.

    Recovery is deliberately additive. It never overwrites a current row and never
    creates a row when the archived payload cannot identify the Deputy shift, event,
    position, and location.
    """
    settings = settings or get_settings()
    now = datetime.now(settings.timezone)
    marker_key = "historical_schedule_recovery_v1"
    with get_connection(settings) as conn:
        marker = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (marker_key,)
        ).fetchone()
        if marker is not None and not force:
            latest = conn.execute(
                "SELECT * FROM historical_recovery_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(latest) if latest is not None else {
                "events_inspected": 0,
                "events_restored": 0,
                "rows_restored": 0,
                "events_unrecoverable": 0,
                "note": "Historical recovery already completed.",
            }

        snapshots: dict[tuple[str, int], tuple[tuple[int, str], list[dict[str, object]]]] = {}
        captures = conn.execute(
            """
            SELECT captured_at, payload
            FROM deputy_web_captures
            WHERE status IN ('ok', 'success') AND TRIM(COALESCE(payload, '')) != ''
            ORDER BY captured_at, id
            """
        ).fetchall()
        for capture in captures:
            try:
                payload = json.loads(str(capture["payload"] or "{}"))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            area_lookup = {
                _optional_int(item.get("id")): item
                for item in payload.get("areas") or []
                if isinstance(item, dict) and _optional_int(item.get("id")) is not None
            }
            grouped: dict[tuple[str, int], list[dict[str, object]]] = {}
            for raw in payload.get("extracted_schedule_shifts") or []:
                if not isinstance(raw, dict) or _optional_int(raw.get("id")) is None:
                    continue
                area_id = _optional_int(raw.get("area"))
                area = area_lookup.get(area_id) or {}
                start_at = str(raw.get("start") or "")
                end_at = str(raw.get("end") or "")
                date_text = start_at[:10]
                location_id = _optional_int(
                    raw.get("areaLocationId")
                    or raw.get("location")
                    or raw.get("locationId")
                    or area.get("locationId")
                )
                area_name = str(raw.get("areaName") or area.get("name") or "").strip()
                if (
                    location_id is None
                    or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text)
                    or _event_position(area_name) is None
                    or not _event_is_completed(date_text, end_at, now)
                ):
                    continue
                grouped.setdefault((date_text, location_id), []).append({
                    "source_shift_id": int(raw["id"]),
                    "captured_at": str(capture["captured_at"] or ""),
                    "area_id": area_id,
                    "area_name": area_name,
                    "area_location_id": location_id,
                    "area_roster_sort_order": _optional_int(
                        raw.get("areaRosterSortOrder") or area.get("rosterSortOrder")
                    ),
                    "employee_id": _optional_int(raw.get("employee")),
                    "employee_name": str(raw.get("employeeName") or "").strip(),
                    "start_at": start_at,
                    "end_at": end_at,
                    "date": date_text,
                    "duration": _optional_float(raw.get("duration")),
                    "is_open": 1 if raw.get("isOpen") else 0,
                    "is_published": 1 if raw.get("isPublished") else 0,
                    "note": str(raw.get("note") or ""),
                    "raw_payload": json_dumps(raw),
                })
            capture_at = str(capture["captured_at"] or "")
            for scope, rows in grouped.items():
                latest_end = max((str(row["end_at"] or "") for row in rows), default="")
                completion = _event_completion_time(scope[0], latest_end, settings)
                try:
                    capture_dt = datetime.fromisoformat(capture_at)
                    if capture_dt.tzinfo is None:
                        capture_dt = capture_dt.replace(tzinfo=settings.timezone)
                except ValueError:
                    capture_dt = None
                preferred_window = int(
                    completion is not None
                    and capture_dt is not None
                    and capture_dt <= completion + timedelta(hours=24)
                )
                score = (preferred_window, capture_at)
                previous = snapshots.get(scope)
                if previous is None or score >= previous[0]:
                    snapshots[scope] = (score, rows)

        events_inspected = len(snapshots)
        events_restored = 0
        rows_restored = 0
        events_unrecoverable = 0
        for (date_text, location_id), (_score, archived_rows) in sorted(snapshots.items()):
            current_rows = conn.execute(
                "SELECT * FROM deputy_schedule_shifts WHERE date = ? AND area_location_id = ?",
                (date_text, location_id),
            ).fetchall()
            current_by_position: dict[str, list[sqlite3.Row]] = {}
            for current in current_rows:
                position = _event_position(current["area_name"])
                if position is not None:
                    current_by_position.setdefault(position[0], []).append(current)
            restored_this_event = 0
            conflict_this_event = False
            for values in archived_rows:
                if conn.execute(
                    "SELECT 1 FROM deputy_schedule_shifts WHERE source_shift_id = ?",
                    (values["source_shift_id"],),
                ).fetchone() is not None:
                    continue
                position = _event_position(values["area_name"])
                if position is None:
                    conflict_this_event = True
                    continue
                same_position = current_by_position.get(position[0], [])
                archived_name = str(values["employee_name"] or "").strip()
                matching_current = any(
                    (
                        values["employee_id"] is not None
                        and _optional_int(row["employee_id"]) == values["employee_id"]
                    )
                    or (
                        archived_name
                        and normalise_person_identity(row["employee_name"])
                        == normalise_person_identity(archived_name)
                    )
                    for row in same_position
                )
                if matching_current:
                    continue
                if same_position and archived_name and any(
                    str(row["employee_name"] or "").strip() for row in same_position
                ):
                    conflict_this_event = True
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO deputy_schedule_shifts (
                        source_shift_id, captured_at, area_id, area_name,
                        area_location_id, area_roster_sort_order, employee_id,
                        employee_name, start_at, end_at, date, duration, is_open,
                        is_published, changed_since_viewed, last_changed_at,
                        change_summary, note, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, '', ?, ?)
                    """,
                    (
                        values["source_shift_id"], values["captured_at"], values["area_id"],
                        values["area_name"], values["area_location_id"],
                        values["area_roster_sort_order"], values["employee_id"],
                        values["employee_name"], values["start_at"], values["end_at"],
                        values["date"], values["duration"], values["is_open"],
                        values["is_published"], values["note"], values["raw_payload"],
                    ),
                )
                restored_this_event += max(0, int(conn.execute("SELECT changes()").fetchone()[0]))
            if restored_this_event:
                events_restored += 1
                rows_restored += restored_this_event
                starts = [str(item["start_at"] or "") for item in archived_rows if item["start_at"]]
                ends = [str(item["end_at"] or "") for item in archived_rows if item["end_at"]]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO deputy_event_locks (
                        date, area_location_id, event_start_at, event_end_at,
                        locked_at, lock_reason, recovered_from_capture
                    ) VALUES (?, ?, ?, ?, ?, 'recovered_completed_event', 1)
                    """,
                    (
                        date_text, location_id, min(starts) if starts else "",
                        max(ends) if ends else "", now.isoformat(timespec="seconds"),
                    ),
                )
            if conflict_this_event:
                events_unrecoverable += 1

        note = (
            f"Replayed {len(captures)} retained successful captures; "
            "restored only archived rows with stable Deputy event identities."
        )
        conn.execute(
            """
            INSERT INTO historical_recovery_runs (
                ran_at, events_inspected, events_restored, rows_restored,
                events_unrecoverable, note
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                now.isoformat(timespec="seconds"), events_inspected, events_restored,
                rows_restored, events_unrecoverable, note,
            ),
        )
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, 'done', ?)
            ON CONFLICT(key) DO UPDATE SET value = 'done', updated_at = excluded.updated_at
            """,
            (marker_key, now.isoformat(timespec="seconds")),
        )
        return {
            "events_inspected": events_inspected,
            "events_restored": events_restored,
            "rows_restored": rows_restored,
            "events_unrecoverable": events_unrecoverable,
            "note": note,
        }


def _personal_evidence_identity(conn: sqlite3.Connection, owner_user_id: int, employee_id: int | None) -> tuple[int | None, str]:
    person = None
    if employee_id is not None:
        person = conn.execute(
            "SELECT id, canonical_display_name FROM crew_people WHERE deputy_employee_id = ? LIMIT 1",
            (employee_id,),
        ).fetchone()
    if person is None:
        person = conn.execute(
            "SELECT id, canonical_display_name FROM crew_people WHERE app_user_id = ? LIMIT 1",
            (owner_user_id,),
        ).fetchone()
    user = conn.execute("SELECT display_name FROM app_users WHERE id = ?", (owner_user_id,)).fetchone()
    display_name = str(
        (person["canonical_display_name"] if person is not None else "")
        or (user["display_name"] if user is not None else "")
        or "Crew member"
    )
    return (_optional_int(person["id"] if person is not None else None), display_name)


def _upsert_personal_assignment_evidence(
    conn: sqlite3.Connection,
    values: dict[str, object],
    owner_user_id: int | None,
    captured_at: str,
) -> bool:
    if owner_user_id is None:
        return False
    payload = _json_loads_dict(str(values.get("source_payload") or ""))
    normalised = payload.get("normalised") if isinstance(payload.get("normalised"), dict) else {}
    position = _event_position(normalised.get("role_label") or normalised.get("area_name"))
    location_id = _optional_int(normalised.get("area_location_id"))
    employee_id = _optional_int(normalised.get("employee_id"))
    if position is None or position[0] not in EVENT_POSITION_ALIASES and position[0] not in CORE_EVENT_POSITION_KEYS:
        return False
    if location_id is None or not values.get("date") or not values.get("start_at") or not values.get("end_at"):
        return False
    canonical_person_id, display_name = _personal_evidence_identity(conn, owner_user_id, employee_id)
    source_uid = str(values.get("source_uid") or "")
    source_shift_id = source_uid.rsplit(":", 1)[-1] if source_uid else ""
    evidence_status = "cancelled" if values.get("source_status") == "cancelled" else "confirmed"
    provenance = json_dumps({
        "source": "deputy_personal_roster",
        "display_name": display_name,
        "captured_at": captured_at,
    })
    conn.execute(
        """
        INSERT INTO deputy_personal_assignment_evidence (
            owner_user_id, deputy_employee_id, canonical_person_id,
            source_shift_uid, source_shift_id, date, area_location_id,
            position_key, position_label, start_at, end_at,
            first_seen_at, last_seen_at, last_confirmed_at,
            missing_capture_count, status, provenance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(owner_user_id, source_shift_uid) DO UPDATE SET
            deputy_employee_id = excluded.deputy_employee_id,
            canonical_person_id = excluded.canonical_person_id,
            source_shift_id = excluded.source_shift_id,
            date = excluded.date,
            area_location_id = excluded.area_location_id,
            position_key = excluded.position_key,
            position_label = excluded.position_label,
            start_at = excluded.start_at,
            end_at = excluded.end_at,
            last_seen_at = excluded.last_seen_at,
            last_confirmed_at = excluded.last_confirmed_at,
            missing_capture_count = 0,
            status = CASE WHEN status = 'historical_locked' THEN status ELSE excluded.status END,
            provenance = excluded.provenance
        """,
        (
            owner_user_id, employee_id, canonical_person_id, source_uid, source_shift_id,
            values["date"], location_id, position[0], position[1], values["start_at"],
            values["end_at"], captured_at, captured_at, captured_at, evidence_status, provenance,
        ),
    )
    return True


def _save_personal_capture_coverage(
    conn: sqlite3.Connection,
    payload: dict[str, object],
    owner_user_id: int | None,
    captured_at: str,
) -> dict[str, int]:
    counts = {"possibly_missing": 0, "retired": 0, "coverage_rows": 0}
    if owner_user_id is None:
        return counts
    seen_uids = {
        f"deputy-web:{owner_user_id}:{shift.get('id')}"
        for shift in payload.get("extracted_shifts") or []
        if isinstance(shift, dict) and shift.get("id") not in (None, "")
    }
    processed_missing_evidence: set[int] = set()
    for coverage in payload.get("own_roster_coverage") or []:
        if not isinstance(coverage, dict):
            continue
        start_date = str(coverage.get("start_date") or "")[:10]
        end_date = str(coverage.get("end_date") or "")[:10]
        status = str(coverage.get("status") or "failed")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date):
            continue
        complete = status == "complete" and bool(coverage.get("pagination_complete"))
        coverage_insert = conn.execute(
            """
            INSERT OR IGNORE INTO deputy_personal_capture_coverage (
                owner_user_id, captured_at, start_date, end_date, status,
                records_returned, pagination_complete, known_shift_ids_checked, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_user_id, captured_at, start_date, end_date, status,
                int(coverage.get("records_returned") or 0),
                1 if coverage.get("pagination_complete") else 0,
                1 if coverage.get("known_shift_ids_checked") else 0,
                str(coverage.get("note") or ""),
            ),
        )
        if int(coverage_insert.rowcount or 0) <= 0:
            continue
        counts["coverage_rows"] += 1
        if not complete:
            continue
        evidence_rows = conn.execute(
            """
            SELECT * FROM deputy_personal_assignment_evidence
            WHERE owner_user_id = ? AND date BETWEEN ? AND ?
              AND status IN ('confirmed', 'possibly_missing')
            """,
            (owner_user_id, start_date, end_date),
        ).fetchall()
        for evidence in evidence_rows:
            evidence_id = int(evidence["id"])
            if (
                evidence_id in processed_missing_evidence
                or evidence["source_shift_uid"] in seen_uids
                or evidence["status"] == "historical_locked"
            ):
                continue
            processed_missing_evidence.add(evidence_id)
            missing_count = int(evidence["missing_capture_count"] or 0) + 1
            new_status = "cancelled" if missing_count >= 2 else "possibly_missing"
            conn.execute(
                "UPDATE deputy_personal_assignment_evidence SET missing_capture_count = ?, status = ? WHERE id = ?",
                (missing_count, new_status, evidence_id),
            )
            shift = conn.execute(
                "SELECT id, deleted_from_source FROM shifts WHERE owner_user_id = ? AND source_uid = ?",
                (owner_user_id, evidence["source_shift_uid"]),
            ).fetchone()
            if shift is not None:
                conn.execute(
                    """
                    UPDATE shifts
                    SET missing_capture_count = ?, capture_status = ?,
                        deleted_from_source = CASE WHEN ? = 'cancelled' THEN 1 ELSE deleted_from_source END,
                        changed_since_viewed = CASE WHEN ? = 'cancelled' THEN 1 ELSE changed_since_viewed END,
                        last_changed_at = CASE WHEN ? = 'cancelled' THEN ? ELSE last_changed_at END
                    WHERE id = ?
                    """,
                    (missing_count, new_status, new_status, new_status, new_status, captured_at, int(shift["id"])),
                )
                if new_status == "cancelled":
                    write_shift_changes(
                        conn, int(shift["id"]), captured_at,
                        {"deleted_from_source": (0, 1)},
                        classifications={"deleted_from_source": ("source_change", True)},
                    )
            counts["retired" if new_status == "cancelled" else "possibly_missing"] += 1
    return counts


def fetch_personal_assignment_evidence_for_date(
    date_text: str,
    location_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> list[sqlite3.Row]:
    location_ids = _normalise_int_list(location_ids)
    location_sql = ""
    params: list[object] = [date_text]
    if location_ids:
        placeholders = ", ".join("?" for _ in location_ids)
        location_sql = f"AND e.area_location_id IN ({placeholders})"
        params.extend(location_ids)
    with get_connection() as conn:
        return conn.execute(
            f"""
            SELECT e.*, u.display_name,
                   COALESCE(p.canonical_display_name, u.display_name) AS employee_name
            FROM deputy_personal_assignment_evidence e
            JOIN app_users u ON u.id = e.owner_user_id AND u.is_active = 1
            LEFT JOIN crew_people p ON p.id = e.canonical_person_id
            WHERE e.date = ? {location_sql}
              AND e.status IN ('confirmed', 'possibly_missing', 'historical_locked')
            ORDER BY e.position_label, employee_name
            """,
            params,
        ).fetchall()


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
        _upsert_personal_assignment_evidence(conn, values, owner_user_id, captured_at)
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
                    1 if values["source_status"] == "cancelled" else 0,
                    owner_user_id,
                    values["source_link"],
                    values["source_status"],
                    values["source_payload"],
                ),
            )
            counts["created"] += 1
            continue

        changes, classifications = _deputy_web_shift_changes(existing, values)
        changed = any(visible for _category, visible in classifications.values())
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
                deleted_from_source = ?,
                missing_capture_count = 0,
                capture_status = CASE
                    WHEN historical_locked_at IS NOT NULL THEN 'historical_locked'
                    WHEN ? = 'cancelled' THEN 'cancelled'
                    ELSE 'confirmed'
                END,
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
                1 if values["source_status"] == "cancelled" else 0,
                values["source_status"],
                values["source_payload"],
                int(existing["id"]),
            ),
        )
        if changed:
            counts["updated"] += 1
        if changes:
            write_shift_changes(
                conn, int(existing["id"]), captured_at, changes,
                classifications=classifications,
            )
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


def _canonical_title_facts(title: object) -> tuple[str, str, str, str]:
    raw = str(title or "").strip()
    match = re.match(r"^\[([^]]+)\]\s*(.*)$", raw)
    source_code = str(match.group(1) if match else "").strip()
    role_raw = str(match.group(2) if match else raw).strip()
    position = _event_position(role_raw)
    role_key = position[0] if position else re.sub(r"[^a-z0-9]+", "", role_raw.lower())
    role_label = position[1] if position else role_raw
    location_key = re.sub(r"[^a-z0-9]+", "", re.sub(r"^[thg]-", "", source_code.lower()))
    if location_key in {"", "web", "shift", "national", "travel", "8pe"}:
        location_key = ""
    return location_key, source_code, role_key, role_label


def _deputy_web_shift_changes(
    existing: sqlite3.Row,
    values: dict[str, object],
) -> tuple[dict[str, tuple[object, object]], dict[str, tuple[str, bool]]]:
    technical_changes: dict[str, tuple[object, object]] = {}
    classifications: dict[str, tuple[str, bool]] = {}
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
        technical_changes[field_name] = (old_value, new_value)
        classifications[field_name] = ("normalization", False)

    old_location_key, _old_source, old_role_key, old_role_label = _canonical_title_facts(existing["title"])
    new_location_key, _new_source, new_role_key, new_role_label = _canonical_title_facts(values["title"])
    if old_location_key and new_location_key and old_location_key != new_location_key:
        technical_changes["track"] = (old_location_key, new_location_key)
        classifications["track"] = ("source_change", True)
    if old_role_key and new_role_key and old_role_key != new_role_key:
        technical_changes["role"] = (old_role_label, new_role_label)
        classifications["role"] = ("source_change", True)

    for field_name in ("start_at", "end_at"):
        if field_name in technical_changes:
            classifications[field_name] = ("source_change", True)
    if "description" in technical_changes:
        old_note, new_note = technical_changes["description"]
        classifications["description"] = (
            "source_change" if str(old_note or "").strip() and str(new_note or "").strip() else "enrichment",
            bool(str(old_note or "").strip() and str(new_note or "").strip()),
        )
    if "source_status" in technical_changes:
        old_status, new_status = technical_changes["source_status"]
        classifications["source_status"] = (
            "source_change" if "cancelled" in {str(old_status), str(new_status)} else "normalization",
            "cancelled" in {str(old_status), str(new_status)},
        )
    for field_name in ("raw_hours", "paid_hours", "break_minutes"):
        if field_name in technical_changes:
            classifications[field_name] = ("derived_change", False)
    if "location" in technical_changes:
        classifications["location"] = ("enrichment", False)
    if "title" in technical_changes:
        classifications["title"] = ("normalization", False)
    return technical_changes, classifications


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
    status_text = str(shift.get("status") or shift.get("Status") or "").strip().lower()
    is_cancelled = bool(
        shift.get("isDeleted")
        or shift.get("deleted")
        or shift.get("isCancelled")
        or shift.get("cancelled")
        or status_text in {"cancelled", "canceled", "deleted", "removed"}
    )
    source_status = "published" if shift.get("isPublished") else "unpublished"
    if is_cancelled:
        source_status = "cancelled"
    elif shift.get("isOpen"):
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
        event_result = conn.execute(
            "UPDATE deputy_schedule_event_changes SET changed_since_viewed = 0 WHERE changed_since_viewed = 1"
        )
        return shift_result.rowcount + schedule_result.rowcount + event_result.rowcount


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


def write_shift_changes(
    conn: sqlite3.Connection,
    shift_id: int,
    changed_at: str,
    changes: dict[str, tuple[object, object]],
    *,
    classifications: dict[str, tuple[str, bool]] | None = None,
) -> None:
    classifications = classifications or {}
    for field_name, (old_value, new_value) in changes.items():
        category, user_visible = classifications.get(field_name, ("source_change", True))
        conn.execute(
            """
            INSERT INTO shift_changes (
                shift_id, changed_at, field_name, old_value, new_value,
                change_category, user_visible
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shift_id,
                changed_at,
                field_name,
                "" if old_value is None else str(old_value),
                "" if new_value is None else str(new_value),
                category,
                1 if user_visible else 0,
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
