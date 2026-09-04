from __future__ import annotations

import os
import re
import hashlib
import json
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Iterable

from .config import Settings, get_settings
from .admin_overrides import (
    canonical_override_venue,
    normalise_override_field,
    normalise_override_value,
    validate_override_date,
)
from .workday_builder import BUILT_IN_ROLES, canonical_role_key, legacy_transport_mode
from .interpreted_workdays import deputy_shift_is_available
from .deputy_evidence import classify_deputy_evidence
from .travel_cohorts import is_travel_participant_cohort


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
                personal_start_time TEXT,
                personal_finish_time TEXT,
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

            CREATE TABLE IF NOT EXISTS login_throttle (
                account_key TEXT PRIMARY KEY,
                failures INTEGER NOT NULL DEFAULT 0,
                first_failed_at TEXT NOT NULL,
                blocked_until TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_role_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                old_is_admin INTEGER NOT NULL,
                new_is_admin INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (actor_user_id) REFERENCES app_users(id),
                FOREIGN KEY (target_user_id) REFERENCES app_users(id)
            );

            CREATE TABLE IF NOT EXISTS contractor_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                crew_person_id INTEGER NOT NULL,
                created_by_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                revoked_at TEXT,
                activated_user_id INTEGER,
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id),
                FOREIGN KEY (created_by_user_id) REFERENCES app_users(id),
                FOREIGN KEY (activated_user_id) REFERENCES app_users(id)
            );

            CREATE TABLE IF NOT EXISTS account_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                account_email TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                created_by_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                revoked_at TEXT,
                activated_user_id INTEGER,
                FOREIGN KEY (created_by_user_id) REFERENCES app_users(id),
                FOREIGN KEY (activated_user_id) REFERENCES app_users(id)
            );

            CREATE TABLE IF NOT EXISTS deputy_oauth_config (
                id INTEGER PRIMARY KEY CHECK (id=1),
                client_id TEXT,
                encrypted_client_secret TEXT,
                authorize_path TEXT NOT NULL DEFAULT '/oauth/authorize',
                token_path TEXT NOT NULL DEFAULT '/oauth/access_token',
                write_mode TEXT NOT NULL DEFAULT 'off',
                allowed_trial_hosts TEXT NOT NULL DEFAULT '[]',
                updated_by_user_id INTEGER,
                updated_at TEXT,
                FOREIGN KEY (updated_by_user_id) REFERENCES app_users(id)
            );

            CREATE TABLE IF NOT EXISTS deputy_oauth_states (
                state_hash TEXT PRIMARY KEY,
                app_user_id INTEGER NOT NULL,
                tenant_host TEXT NOT NULL,
                redirect_origin TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deputy_oauth_connections (
                app_user_id INTEGER PRIMARY KEY,
                tenant_host TEXT NOT NULL,
                deputy_user_id INTEGER NOT NULL,
                deputy_employee_id INTEGER NOT NULL,
                display_label TEXT,
                encrypted_access_token TEXT NOT NULL,
                encrypted_refresh_token TEXT,
                token_expires_at TEXT,
                permissions_json TEXT NOT NULL DEFAULT '[]',
                permission_hash TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'connected',
                unavailable_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deputy_reference_employees (
                app_user_id INTEGER NOT NULL,
                tenant_host TEXT NOT NULL,
                deputy_employee_id INTEGER NOT NULL,
                deputy_user_id INTEGER,
                display_name TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_observed_at TEXT NOT NULL,
                PRIMARY KEY (app_user_id, tenant_host, deputy_employee_id),
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deputy_reference_units (
                app_user_id INTEGER NOT NULL,
                tenant_host TEXT NOT NULL,
                deputy_unit_id INTEGER NOT NULL,
                display_name TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                show_on_roster INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_observed_at TEXT NOT NULL,
                PRIMARY KEY (app_user_id, tenant_host, deputy_unit_id),
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deputy_person_mappings (
                tenant_host TEXT NOT NULL,
                crew_person_id INTEGER NOT NULL,
                deputy_employee_id INTEGER NOT NULL,
                updated_by_user_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_host, crew_person_id),
                UNIQUE (tenant_host, deputy_employee_id),
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id),
                FOREIGN KEY (updated_by_user_id) REFERENCES app_users(id)
            );

            CREATE TABLE IF NOT EXISTS deputy_unit_mappings (
                tenant_host TEXT NOT NULL,
                mapping_key TEXT NOT NULL,
                context_type TEXT NOT NULL,
                deputy_unit_id INTEGER NOT NULL,
                updated_by_user_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_host, mapping_key),
                FOREIGN KEY (updated_by_user_id) REFERENCES app_users(id)
            );

            CREATE TABLE IF NOT EXISTS deputy_roster_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_host TEXT NOT NULL,
                workday_id INTEGER NOT NULL,
                stable_assignment_key TEXT NOT NULL,
                crew_person_id INTEGER,
                deputy_employee_id INTEGER NOT NULL,
                deputy_unit_id INTEGER NOT NULL,
                deputy_roster_id INTEGER,
                context_type TEXT NOT NULL DEFAULT 'production',
                ownership TEXT NOT NULL DEFAULT 'observed',
                last_desired_hash TEXT,
                last_verified_hash TEXT,
                last_verified_state TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (tenant_host, stable_assignment_key),
                FOREIGN KEY (workday_id) REFERENCES roster_days(id),
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id)
            );

            CREATE TABLE IF NOT EXISTS deputy_write_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_uuid TEXT NOT NULL UNIQUE,
                app_user_id INTEGER NOT NULL,
                tenant_host TEXT NOT NULL,
                deputy_user_id INTEGER NOT NULL,
                deputy_employee_id INTEGER NOT NULL,
                permission_hash TEXT NOT NULL,
                permission_snapshot TEXT NOT NULL,
                workday_id INTEGER,
                stable_assignment_key TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                desired_state TEXT NOT NULL,
                before_state TEXT,
                deputy_roster_id INTEGER,
                attempt_number INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'prepared',
                error_class TEXT,
                sanitized_result TEXT,
                readback_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                sending_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id),
                FOREIGN KEY (workday_id) REFERENCES roster_days(id)
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
                target_track_key TEXT,
                field_key TEXT,
                normalized_value TEXT,
                original_value TEXT,
                status TEXT DEFAULT 'active',
                validation_error TEXT,
                superseded_by_id INTEGER,
                disabled_at TEXT,
                disabled_by_user_id INTEGER,
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
                person_type TEXT NOT NULL DEFAULT 'employee' CHECK (person_type IN ('employee', 'contractor')),
                company TEXT,
                identity_source TEXT NOT NULL DEFAULT 'observed',
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

            CREATE TABLE IF NOT EXISTS crew_identity_search_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crew_person_id INTEGER NOT NULL,
                search_term TEXT NOT NULL,
                normalized_term TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'observed',
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(crew_person_id, normalized_term, source),
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS crew_teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_key TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 999999,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crew_person_teams (
                crew_person_id INTEGER NOT NULL,
                team_id INTEGER NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (crew_person_id, team_id),
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id) ON DELETE CASCADE,
                FOREIGN KEY (team_id) REFERENCES crew_teams(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS crew_team_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                team_id INTEGER,
                crew_person_id INTEGER,
                actor_user_id INTEGER,
                details TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (team_id) REFERENCES crew_teams(id) ON DELETE SET NULL,
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id) ON DELETE SET NULL,
                FOREIGN KEY (actor_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS location_team_mappings (
                location_key TEXT PRIMARY KEY,
                location_label TEXT NOT NULL,
                primary_team_id INTEGER,
                updated_by_user_id INTEGER,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (primary_team_id) REFERENCES crew_teams(id) ON DELETE SET NULL,
                FOREIGN KEY (updated_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS app_user_deputy_identity (
                app_user_id INTEGER PRIMARY KEY,
                deputy_employee_id INTEGER,
                canonical_person_id INTEGER,
                first_confirmed_at TEXT,
                last_confirmed_at TEXT,
                evidence_capture_id INTEGER,
                confidence_source TEXT DEFAULT 'authenticated_personal_capture',
                status TEXT DEFAULT 'confirmed',
                conflict_details TEXT,
                updated_at TEXT,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (canonical_person_id) REFERENCES crew_people(id) ON DELETE SET NULL,
                FOREIGN KEY (evidence_capture_id) REFERENCES deputy_web_captures(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS crew_identity_merge_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_person_id INTEGER NOT NULL,
                target_person_id INTEGER NOT NULL,
                app_user_id INTEGER,
                merged_at TEXT NOT NULL,
                merged_by_user_id INTEGER,
                merge_reason TEXT,
                affected_counts TEXT DEFAULT '{}',
                FOREIGN KEY (source_person_id) REFERENCES crew_people(id) ON DELETE RESTRICT,
                FOREIGN KEY (target_person_id) REFERENCES crew_people(id) ON DELETE RESTRICT,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE SET NULL,
                FOREIGN KEY (merged_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS identity_reconciliation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                applied INTEGER DEFAULT 0,
                trigger_source TEXT,
                report TEXT DEFAULT '{}'
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

            CREATE TABLE IF NOT EXISTS sync_generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                superseded_by_generation_id INTEGER,
                FOREIGN KEY (superseded_by_generation_id) REFERENCES sync_generations(id)
            );
            CREATE TABLE IF NOT EXISTS sync_generation_members (
                generation_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                planned_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                message TEXT,
                PRIMARY KEY (generation_id, user_id),
                FOREIGN KEY (generation_id) REFERENCES sync_generations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sync_generation_members_user ON sync_generation_members(user_id,status,planned_at);
            CREATE TABLE IF NOT EXISTS settled_integrity_state (
                id INTEGER PRIMARY KEY CHECK (id=1),
                generation_id INTEGER,
                findings_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (generation_id) REFERENCES sync_generations(id)
            );

            CREATE TABLE IF NOT EXISTS deputy_schedule_observations (
                source_shift_id INTEGER NOT NULL,
                observer_key TEXT NOT NULL,
                observer_user_id INTEGER,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                last_absent_at TEXT,
                PRIMARY KEY (source_shift_id, observer_key),
                FOREIGN KEY (source_shift_id) REFERENCES deputy_schedule_shifts(source_shift_id) ON DELETE CASCADE,
                FOREIGN KEY (observer_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS deputy_employee_name_history (
                deputy_employee_id INTEGER NOT NULL,
                observed_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                observation_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (deputy_employee_id, normalized_name)
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
                raw_role_label TEXT NOT NULL DEFAULT '',
                evidence_type TEXT NOT NULL DEFAULT 'unknown',
                production_position INTEGER NOT NULL DEFAULT 0,
                participant_evidence INTEGER NOT NULL DEFAULT 0,
                cohort_type TEXT NOT NULL DEFAULT '',
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
                meeting_id TEXT,
                meeting_url TEXT,
                discovery_source TEXT,
                discovered_at TEXT,
                source_url TEXT,
                source_hash TEXT UNIQUE,
                raw_text TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                last_synced_at TEXT,
                is_active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS love_racing_meeting_details (
                meeting_id TEXT PRIMARY KEY,
                meeting_date TEXT NOT NULL,
                canonical_venue_key TEXT NOT NULL,
                canonical_venue_label TEXT NOT NULL,
                club TEXT,
                meeting_url TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL DEFAULT 'discovered',
                fetch_status TEXT NOT NULL DEFAULT 'ready',
                race_count INTEGER,
                race_count_last_confirmed_at TEXT,
                first_race_time TEXT,
                first_race_last_confirmed_at TEXT,
                last_race_time TEXT,
                last_race_last_confirmed_at TEXT,
                races_json TEXT NOT NULL DEFAULT '[]',
                parser_diagnostics TEXT NOT NULL DEFAULT '[]',
                page_last_checked_at TEXT,
                page_content_hash TEXT,
                last_material_change_at TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_failure_at TEXT,
                last_error_summary TEXT,
                next_retry_at TEXT,
                race_morning_confirmed_at TEXT,
                post_meeting_checked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS love_racing_detail_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id TEXT NOT NULL,
                requested_reason TEXT,
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                requested_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error TEXT,
                FOREIGN KEY (meeting_id) REFERENCES love_racing_meeting_details(meeting_id) ON DELETE CASCADE
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
                updated_at TEXT,
                manual_file_name TEXT,
                manual_content_type TEXT,
                manual_image_hash TEXT,
                manual_image_width INTEGER,
                manual_image_height INTEGER,
                manual_byte_size INTEGER,
                manual_updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS track_map_location_rules (
                location_key TEXT PRIMARY KEY,
                location_label TEXT NOT NULL,
                classification TEXT NOT NULL,
                canonical_venue_key TEXT,
                canonical_venue_label TEXT,
                source TEXT DEFAULT 'admin',
                note TEXT,
                updated_at TEXT NOT NULL,
                CHECK (classification IN ('venue', 'alias', 'excluded'))
            );

            CREATE TABLE IF NOT EXISTS track_map_migration_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias_key TEXT NOT NULL,
                alias_label TEXT,
                canonical_venue_key TEXT NOT NULL,
                canonical_venue_label TEXT,
                retained_file_name TEXT,
                image_hash TEXT,
                warning_type TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(alias_key, canonical_venue_key, image_hash, warning_type)
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

            CREATE TABLE IF NOT EXISTS workday_role_catalogue (
                role_key TEXT PRIMARY KEY,
                display_label TEXT NOT NULL,
                aliases TEXT DEFAULT '[]',
                display_order INTEGER DEFAULT 999999,
                is_active INTEGER DEFAULT 1,
                is_built_in INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS crew_vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_key TEXT NOT NULL UNIQUE,
                display_label TEXT NOT NULL,
                aliases TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 999999,
                team_id INTEGER,
                notes TEXT,
                source TEXT NOT NULL DEFAULT 'discovered',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_truck INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (team_id) REFERENCES crew_teams(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS crew_vehicle_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                vehicle_id INTEGER,
                actor_user_id INTEGER,
                details TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (vehicle_id) REFERENCES crew_vehicles(id) ON DELETE SET NULL,
                FOREIGN KEY (actor_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS workday_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roster_day_id INTEGER NOT NULL,
                person_id INTEGER,
                user_id INTEGER,
                assignee_label TEXT,
                role_key TEXT,
                role_label TEXT,
                assignment_state TEXT DEFAULT 'assigned',
                transport_mode TEXT DEFAULT 'unassigned',
                vehicle_key TEXT,
                vehicle_label TEXT,
                custom_transport_text TEXT,
                assignment_note TEXT,
                sort_order INTEGER DEFAULT 999999,
                legacy_assignment_id INTEGER UNIQUE,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (roster_day_id) REFERENCES roster_days(id) ON DELETE CASCADE,
                FOREIGN KEY (person_id) REFERENCES crew_people(id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS workday_open_position_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roster_day_id INTEGER NOT NULL,
                assignment_key TEXT NOT NULL,
                crew_person_id INTEGER NOT NULL,
                app_user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied_at TEXT NOT NULL,
                withdrawn_at TEXT,
                reviewed_at TEXT,
                reviewed_by_user_id INTEGER,
                admin_note TEXT,
                conflict_snapshot TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (roster_day_id) REFERENCES roster_days(id) ON DELETE CASCADE,
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id) ON DELETE CASCADE,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (reviewed_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS workday_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roster_day_id INTEGER NOT NULL,
                assignment_key TEXT,
                event_type TEXT NOT NULL,
                actor_user_id INTEGER,
                crew_person_id INTEGER,
                details TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (roster_day_id) REFERENCES roster_days(id) ON DELETE CASCADE,
                FOREIGN KEY (actor_user_id) REFERENCES app_users(id) ON DELETE SET NULL,
                FOREIGN KEY (crew_person_id) REFERENCES crew_people(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS workday_user_visibility (
                roster_day_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                canonical_person_id INTEGER,
                source TEXT DEFAULT 'canonical_assignment',
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (roster_day_id, user_id),
                FOREIGN KEY (roster_day_id) REFERENCES roster_days(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (canonical_person_id) REFERENCES crew_people(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS user_event_transport_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                canonical_person_id INTEGER NOT NULL,
                event_kind TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                location_key TEXT NOT NULL DEFAULT '',
                self_travel INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, event_kind, event_id),
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (canonical_person_id) REFERENCES crew_people(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_event_time_overrides (
                user_id INTEGER NOT NULL,
                canonical_person_id INTEGER NOT NULL,
                event_kind TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                personal_start_time TEXT,
                personal_finish_time TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id,event_kind,event_id),
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (canonical_person_id) REFERENCES crew_people(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_user_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                app_origin TEXT NOT NULL DEFAULT '',
                device_description TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_success_at TEXT,
                last_failure_at TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0,
                revoked_at TEXT,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_preferences (
                app_user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                changes_enabled INTEGER NOT NULL DEFAULT 1,
                changes_within_24h INTEGER NOT NULL DEFAULT 1,
                night_before INTEGER NOT NULL DEFAULT 1,
                two_days_before INTEGER NOT NULL DEFAULT 0,
                one_hour_before INTEGER NOT NULL DEFAULT 1,
                admin_alerts INTEGER NOT NULL DEFAULT 0,
                weekly_digest INTEGER NOT NULL DEFAULT 0,
                open_positions_month INTEGER NOT NULL DEFAULT 0,
                reminder_time TEXT NOT NULL DEFAULT '19:00',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                workday_kind TEXT,
                workday_id TEXT,
                event_date TEXT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                target_url TEXT NOT NULL DEFAULT '/month',
                scheduled_at TEXT NOT NULL,
                sent_at TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                dedupe_key TEXT NOT NULL UNIQUE,
                failure_summary TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (app_user_id) REFERENCES app_users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_event_id INTEGER NOT NULL,
                subscription_id INTEGER,
                attempted_at TEXT NOT NULL,
                result TEXT NOT NULL,
                failure_summary TEXT,
                FOREIGN KEY (notification_event_id) REFERENCES notification_events(id) ON DELETE CASCADE,
                FOREIGN KEY (subscription_id) REFERENCES push_subscriptions(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS user_event_transport_preference_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preference_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                canonical_person_id INTEGER NOT NULL,
                event_kind TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                location_key TEXT NOT NULL DEFAULT '',
                old_self_travel INTEGER NOT NULL DEFAULT 0,
                new_self_travel INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'user',
                changed_at TEXT NOT NULL,
                FOREIGN KEY (preference_id) REFERENCES user_event_transport_preferences(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                FOREIGN KEY (canonical_person_id) REFERENCES crew_people(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS backup_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_at TEXT NOT NULL,
                completed_at TEXT,
                reason TEXT NOT NULL,
                requested_by_user_id INTEGER,
                backup_id TEXT,
                status TEXT NOT NULL,
                backup_path TEXT,
                backup_size_bytes INTEGER,
                backup_sha256 TEXT,
                integrity_result TEXT,
                foreign_key_check_count INTEGER,
                failure_reason TEXT,
                FOREIGN KEY (requested_by_user_id) REFERENCES app_users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS admin_action_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_user_id INTEGER,
                actor_display_snapshot TEXT NOT NULL DEFAULT '',
                actor_account_snapshot TEXT NOT NULL DEFAULT '',
                action_key TEXT NOT NULL,
                action_category TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                target_label TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL,
                before_json TEXT NOT NULL DEFAULT '{}',
                after_json TEXT NOT NULL DEFAULT '{}',
                related_audit_type TEXT NOT NULL DEFAULT '',
                related_audit_id TEXT NOT NULL DEFAULT '',
                request_path TEXT NOT NULL DEFAULT '',
                safe_note TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (actor_user_id) REFERENCES app_users(id) ON DELETE SET NULL
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
            CREATE INDEX IF NOT EXISTS idx_crew_search_terms_normalized ON crew_identity_search_terms(normalized_term);
            CREATE INDEX IF NOT EXISTS idx_crew_person_teams_team ON crew_person_teams(team_id, active, crew_person_id);
            CREATE INDEX IF NOT EXISTS idx_crew_vehicles_team ON crew_vehicles(team_id, active, sort_order);
            CREATE INDEX IF NOT EXISTS idx_love_racing_meetings_date ON love_racing_meetings(meeting_date, racecourse_key);
            CREATE INDEX IF NOT EXISTS idx_love_racing_details_date ON love_racing_meeting_details(meeting_date, canonical_venue_key);
            CREATE INDEX IF NOT EXISTS idx_love_racing_jobs_due ON love_racing_detail_jobs(status, next_attempt_at, priority);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_love_racing_jobs_active
                ON love_racing_detail_jobs(meeting_id)
                WHERE status IN ('queued', 'fetching');
            CREATE INDEX IF NOT EXISTS idx_planning_location_preferences_enabled ON planning_location_preferences(is_enabled);
            CREATE INDEX IF NOT EXISTS idx_roster_days_date ON roster_days(roster_date, status);
            CREATE INDEX IF NOT EXISTS idx_roster_day_assignments_user ON roster_day_assignments(user_id, roster_day_id);
            CREATE INDEX IF NOT EXISTS idx_roster_day_versions_day ON roster_day_versions(roster_day_id, version_number DESC);
            CREATE INDEX IF NOT EXISTS idx_workday_assignments_day ON workday_assignments(roster_day_id, sort_order);
            CREATE INDEX IF NOT EXISTS idx_workday_assignments_user ON workday_assignments(user_id, roster_day_id);
            CREATE INDEX IF NOT EXISTS idx_workday_applications_position ON workday_open_position_applications(roster_day_id, assignment_key, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workday_application_active
                ON workday_open_position_applications(roster_day_id, assignment_key, crew_person_id)
                WHERE status IN ('pending', 'accepted');
            CREATE INDEX IF NOT EXISTS idx_user_transport_pref_day ON user_event_transport_preferences(event_date, location_key, canonical_person_id);
            CREATE INDEX IF NOT EXISTS idx_workday_visibility_user ON workday_user_visibility(user_id, roster_day_id);
            CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user_active ON push_subscriptions(app_user_id, active);
            CREATE INDEX IF NOT EXISTS idx_notification_events_due ON notification_events(status, scheduled_at);
            CREATE INDEX IF NOT EXISTS idx_notification_events_user_type ON notification_events(app_user_id, event_type, event_date);
            CREATE INDEX IF NOT EXISTS idx_identity_deputy_employee ON app_user_deputy_identity(deputy_employee_id);
            CREATE INDEX IF NOT EXISTS idx_backup_runs_completed ON backup_runs(status, completed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_admin_action_audit_recent ON admin_action_audit(created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_admin_action_audit_actor ON admin_action_audit(actor_user_id, created_at DESC);
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
        _ensure_column(conn, "shift_marks", "personal_start_time", "TEXT")
        _ensure_column(conn, "shift_marks", "personal_finish_time", "TEXT")
        _ensure_column(conn, "app_users", "account_type", "TEXT NOT NULL DEFAULT 'user'")
        _ensure_column(conn, "app_users", "contractor_person_id", "INTEGER")
        _ensure_column(conn, "app_users", "last_activity_at", "TEXT")
        _ensure_column(conn, "deputy_oauth_config", "callback_origin", "TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contractor_invites_person ON contractor_invites(crew_person_id, expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deputy_write_status ON deputy_write_operations(status, created_at DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deputy_active_assignment_operation ON deputy_write_operations(tenant_host, stable_assignment_key) WHERE status IN ('prepared','sending','unknown')")
        _ensure_column(conn, "admin_overrides", "target_track_key", "TEXT")
        _ensure_column(conn, "admin_overrides", "field_key", "TEXT")
        _ensure_column(conn, "admin_overrides", "normalized_value", "TEXT")
        _ensure_column(conn, "admin_overrides", "original_value", "TEXT")
        _ensure_column(conn, "admin_overrides", "status", "TEXT")
        _ensure_column(conn, "admin_overrides", "validation_error", "TEXT")
        _ensure_column(conn, "admin_overrides", "superseded_by_id", "INTEGER")
        _ensure_column(conn, "admin_overrides", "disabled_at", "TEXT")
        _ensure_column(conn, "admin_overrides", "disabled_by_user_id", "INTEGER")
        _ensure_column(conn, "deputy_schedule_shifts", "area_name", "TEXT")
        _ensure_column(conn, "deputy_schedule_shifts", "area_location_id", "INTEGER")
        _ensure_column(conn, "deputy_schedule_shifts", "area_roster_sort_order", "INTEGER")
        _ensure_column(conn, "deputy_schedule_shifts", "changed_since_viewed", "INTEGER DEFAULT 0")
        _ensure_column(conn, "deputy_schedule_shifts", "last_changed_at", "TEXT")
        _ensure_column(conn, "deputy_schedule_shifts", "change_summary", "TEXT")
        _ensure_column(conn, "notification_preferences", "one_hour_before", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "notification_preferences", "admin_alerts", "INTEGER NOT NULL DEFAULT 0")
        if conn.execute("SELECT value FROM app_settings WHERE key='admin_alert_defaults_v1'").fetchone() is None:
            conn.execute(
                """UPDATE notification_preferences SET admin_alerts=1
                   WHERE enabled=1 AND app_user_id IN (
                       SELECT id FROM app_users WHERE is_admin=1 AND is_active=1
                   )"""
            )
            conn.execute(
                "INSERT INTO app_settings(key,value,updated_at) VALUES('admin_alert_defaults_v1','1',?)",
                (datetime.now(get_settings().timezone).isoformat(timespec="seconds"),),
            )
        _ensure_column(conn, "app_users", "deputy_web_url", "TEXT")
        _ensure_column(conn, "app_users", "display_theme", "TEXT DEFAULT 'jade'")
        _ensure_column(conn, "deputy_user_secrets", "encrypted_ical_url", "TEXT")
        _ensure_column(conn, "app_users", "deactivated_at", "TEXT")
        _ensure_column(conn, "love_racing_meetings", "is_active", "INTEGER DEFAULT 1")
        _ensure_column(conn, "love_racing_meetings", "meeting_id", "TEXT")
        _ensure_column(conn, "love_racing_meetings", "meeting_url", "TEXT")
        _ensure_column(conn, "love_racing_meetings", "discovery_source", "TEXT")
        _ensure_column(conn, "love_racing_meetings", "discovered_at", "TEXT")
        _ensure_column(conn, "roster_days", "day_type", "TEXT DEFAULT 'race_day'")
        _ensure_column(conn, "roster_days", "hotel_assignments", "TEXT DEFAULT '[]'")
        _ensure_column(conn, "roster_days", "start_origin", "TEXT")
        _ensure_column(conn, "roster_days", "finish_destination", "TEXT")
        _ensure_column(conn, "roster_days", "title", "TEXT")
        _ensure_column(conn, "roster_days", "custom_location", "TEXT")
        _ensure_column(conn, "roster_days", "end_time", "TEXT")
        _ensure_column(conn, "roster_days", "break_minutes", "INTEGER DEFAULT 0")
        _ensure_column(conn, "roster_days", "source_reference", "TEXT")
        _ensure_column(conn, "roster_days", "provenance", "TEXT DEFAULT 'manual'")
        _ensure_column(conn, "roster_days", "linked_deputy_event_id", "TEXT")
        _ensure_column(conn, "roster_days", "duplicate_resolution", "TEXT DEFAULT 'keep_separate'")
        _ensure_column(conn, "roster_days", "canonical_location_key", "TEXT")
        _ensure_column(conn, "roster_days", "team_id", "INTEGER")
        _ensure_column(conn, "roster_days", "truck_start_offset_minutes", "INTEGER DEFAULT 0")
        _ensure_column(conn, "crew_vehicles", "is_truck", "INTEGER DEFAULT 0")
        _ensure_column(conn, "crew_people", "merged_into_person_id", "INTEGER")
        _ensure_column(conn, "crew_people", "merged_at", "TEXT")
        _ensure_column(conn, "crew_people", "merged_by_user_id", "INTEGER")
        _ensure_column(conn, "crew_people", "merge_reason", "TEXT")
        _ensure_column(conn, "crew_people", "person_type", "TEXT NOT NULL DEFAULT 'employee'")
        _ensure_column(conn, "crew_people", "company", "TEXT")
        _ensure_column(conn, "crew_people", "identity_source", "TEXT NOT NULL DEFAULT 'observed'")
        _ensure_column(conn, "account_invitations", "crew_person_id", "INTEGER")
        conn.execute(
            """UPDATE crew_people SET person_type='contractor'
               WHERE id IN (SELECT contractor_person_id FROM app_users WHERE account_type='contractor' AND contractor_person_id IS NOT NULL)
                  OR id IN (SELECT crew_person_id FROM contractor_invites)"""
        )
        _ensure_column(conn, "workday_assignments", "assignment_key", "TEXT")
        _ensure_column(conn, "workday_assignments", "vehicle_id", "INTEGER")
        _ensure_column(conn, "workday_assignments", "eligible_team_id", "INTEGER")
        _ensure_column(conn, "workday_assignments", "eligible_all_teams", "INTEGER DEFAULT 0")
        if not conn.execute("SELECT 1 FROM app_settings WHERE key='truck_vehicle_seed_v1'").fetchone():
            conn.execute("UPDATE crew_vehicles SET is_truck=1 WHERE LOWER(display_label) IN ('tender','ob')")
            conn.execute(
                "INSERT INTO app_settings(key,value,updated_at) VALUES ('truck_vehicle_seed_v1','1',?)",
                (datetime.now(get_settings().timezone).isoformat(timespec="seconds"),),
            )
        _backfill_workday_assignment_keys(conn)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_workday_assignment_key "
            "ON workday_assignments(roster_day_id, assignment_key)"
        )
        conn.execute(
            """
            UPDATE roster_days
            SET canonical_location_key = track_key
            WHERE TRIM(COALESCE(canonical_location_key, '')) = ''
            """
        )
        _ensure_column(conn, "track_maps", "image_width", "INTEGER")
        _ensure_column(conn, "track_maps", "image_height", "INTEGER")
        _ensure_column(conn, "track_maps", "byte_size", "INTEGER")
        _ensure_column(conn, "track_maps", "selected_source_url", "TEXT")
        _ensure_column(conn, "track_maps", "candidate_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "track_maps", "refresh_result", "TEXT")
        _ensure_column(conn, "track_maps", "manual_file_name", "TEXT")
        _ensure_column(conn, "track_maps", "manual_content_type", "TEXT")
        _ensure_column(conn, "track_maps", "manual_image_hash", "TEXT")
        _ensure_column(conn, "track_maps", "manual_image_width", "INTEGER")
        _ensure_column(conn, "track_maps", "manual_image_height", "INTEGER")
        _ensure_column(conn, "track_maps", "manual_byte_size", "INTEGER")
        _ensure_column(conn, "track_maps", "manual_updated_at", "TEXT")
        _ensure_column(conn, "deputy_schedule_event_changes", "changed_since_viewed", "INTEGER DEFAULT 1")
        _ensure_column(conn, "deputy_schedule_event_changes", "change_category", "TEXT DEFAULT 'assignment_change'")
        _ensure_column(conn, "deputy_personal_assignment_evidence", "raw_role_label", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "deputy_personal_assignment_evidence", "evidence_type", "TEXT NOT NULL DEFAULT 'unknown'")
        _ensure_column(conn, "deputy_personal_assignment_evidence", "production_position", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "deputy_personal_assignment_evidence", "participant_evidence", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "deputy_personal_assignment_evidence", "cohort_type", "TEXT NOT NULL DEFAULT ''")
        if conn.execute("SELECT value FROM app_settings WHERE key='personal_evidence_classification_v1'").fetchone() is None:
            for legacy_row in conn.execute(
                "SELECT id, raw_role_label, position_label FROM deputy_personal_assignment_evidence"
            ).fetchall():
                classification = classify_deputy_evidence(
                    legacy_row["raw_role_label"] or legacy_row["position_label"],
                    production_keys=CORE_EVENT_POSITION_KEYS,
                    production_aliases=EVENT_POSITION_ALIASES,
                )
                conn.execute(
                    """UPDATE deputy_personal_assignment_evidence
                       SET raw_role_label=?, position_key=?, position_label=?, evidence_type=?,
                           production_position=?, participant_evidence=?, cohort_type=? WHERE id=?""",
                    (
                        classification.raw_label, classification.role_key, classification.role_label,
                        classification.evidence_type, 1 if classification.production_position else 0,
                        1 if classification.participant_evidence else 0, classification.cohort_type,
                        int(legacy_row["id"]),
                    ),
                )
            conn.execute(
                "INSERT INTO app_settings(key,value,updated_at) VALUES('personal_evidence_classification_v1','1',?)",
                (datetime.now(get_settings().timezone).isoformat(timespec="seconds"),),
            )
        _ensure_column(conn, "push_subscriptions", "app_origin", "TEXT NOT NULL DEFAULT ''")
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_schedule_observations_active ON deputy_schedule_observations(observer_key, active, source_shift_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_employee_name_history_name ON deputy_employee_name_history(normalized_name, deputy_employee_id)")
        _backfill_guarded_identity_aliases(conn)
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
        _enforce_trusted_device_limit_conn(conn)
        _reclassify_legacy_shift_changes(conn)
        _migrate_admin_overrides(conn)
        _seed_workday_role_catalogue(conn)
        _seed_default_team(conn)
        _sync_crew_search_terms(conn)
        _sync_vehicle_catalogue(conn)
        _migrate_legacy_roster_assignments(conn)
        if conn.execute("SELECT 1 FROM app_settings WHERE key='legacy_tbc_assignment_state_v1'").fetchone() is None:
            conn.execute(
                "UPDATE workday_assignments SET assignment_state='tbc' "
                "WHERE legacy_assignment_id IS NOT NULL AND assignment_state='open' "
                "AND LOWER(TRIM(COALESCE(assignee_label,'')))='tbc'"
            )
            conn.execute(
                "INSERT INTO app_settings(key,value,updated_at) VALUES ('legacy_tbc_assignment_state_v1','done',?)",
                (datetime.now(get_settings().timezone).isoformat(timespec="seconds"),),
            )
        _backfill_workday_assignment_keys(conn)
        _reconcile_authenticated_identities_conn(conn, apply=True, trigger_source="startup")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_admin_overrides_effective
            ON admin_overrides(target_date, target_track_key, field_key, status)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_overrides_one_active
            ON admin_overrides(target_date, target_track_key, field_key)
            WHERE status = 'active' AND active = 1
            """
        )
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


def _backfill_guarded_identity_aliases(conn: sqlite3.Connection) -> None:
    """Add production-known aliases only when immutable Deputy ID and name agree."""
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    rules = {
        7: (("danny", "hunter"), ("Esq",)),
        13: (("gary", "mcclure"), ("Jr", "Jnr", "Junior")),
        14: (("gary", "russo"), ("Gaz", "Gazz")),
    }
    for employee_id, (required_parts, aliases) in rules.items():
        person = conn.execute(
            "SELECT id,canonical_display_name,current_deputy_name FROM crew_people WHERE deputy_employee_id=?",
            (employee_id,),
        ).fetchone()
        if person is None:
            continue
        combined = normalise_person_identity(
            f"{person['canonical_display_name'] or ''} {person['current_deputy_name'] or ''}"
        )
        if not all(part in combined for part in required_parts):
            continue
        for alias in aliases:
            normalized = normalise_person_identity(alias)
            conflict = conn.execute(
                "SELECT person_id FROM crew_aliases WHERE normalized_alias=?",
                (normalized,),
            ).fetchone()
            if conflict is not None and int(conflict["person_id"]) != int(person["id"]):
                continue
            conn.execute(
                """INSERT OR IGNORE INTO crew_aliases(person_id,alias,normalized_alias,created_at,updated_at)
                   VALUES(?,?,?,?,?)""",
                (int(person["id"]), alias, normalized, now, now),
            )


def _seed_workday_role_catalogue(conn: sqlite3.Connection) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    for order, (role_key, display_label, aliases) in enumerate(BUILT_IN_ROLES):
        conn.execute(
            """
            INSERT INTO workday_role_catalogue (
                role_key, display_label, aliases, display_order, is_active,
                is_built_in, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, 1, ?, ?)
            ON CONFLICT(role_key) DO UPDATE SET
                is_built_in = 1,
                aliases = CASE
                    WHEN TRIM(COALESCE(workday_role_catalogue.aliases, '')) IN ('', '[]')
                    THEN excluded.aliases ELSE workday_role_catalogue.aliases END,
                updated_at = CASE
                    WHEN workday_role_catalogue.updated_at IS NULL THEN excluded.updated_at
                    ELSE workday_role_catalogue.updated_at END
            """,
            (role_key, display_label, json.dumps(list(aliases)), order, now, now),
        )


def _backfill_workday_assignment_keys(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE workday_assignments
        SET assignment_key = 'assignment-' || id
        WHERE TRIM(COALESCE(assignment_key, '')) = ''
        """
    )


def _seed_default_team(conn: sqlite3.Connection) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO crew_teams(stable_key,display_name,active,sort_order,created_at,updated_at)
        VALUES ('northern-team','Northern Team',1,10,?,?)
        ON CONFLICT(stable_key) DO NOTHING
        """,
        (now, now),
    )


def _add_crew_search_term_conn(
    conn: sqlite3.Connection,
    person_id: int,
    value: object,
    source: str,
) -> None:
    label = re.sub(r"\s+", " ", str(value or "").strip())
    key = normalise_person_identity(label)
    if not key:
        return
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO crew_identity_search_terms(
            crew_person_id,search_term,normalized_term,source,created_at,updated_at
        ) VALUES (?,?,?,?,?,?)
        ON CONFLICT(crew_person_id,normalized_term,source) DO UPDATE SET
            search_term=excluded.search_term,updated_at=excluded.updated_at
        """,
        (person_id, label[:250], key, source[:40], now, now),
    )


def _sync_crew_search_terms(conn: sqlite3.Connection) -> None:
    for row in conn.execute(
        """
        SELECT p.id,p.canonical_display_name,p.current_deputy_name,p.deputy_employee_id,
               u.display_name app_user_name,u.deputy_email app_user_email
        FROM crew_people p
        LEFT JOIN app_users u ON u.id=p.app_user_id
        WHERE p.merged_into_person_id IS NULL
        """
    ).fetchall():
        person_id = int(row["id"])
        for value, source in (
            (row["canonical_display_name"], "canonical"),
            (row["current_deputy_name"], "deputy_observed"),
            (row["app_user_name"], "app_account"),
            (row["app_user_email"], "app_email"),
            (row["deputy_employee_id"], "deputy_employee_id"),
        ):
            _add_crew_search_term_conn(conn, person_id, value, source)
    for row in conn.execute("SELECT person_id,alias FROM crew_aliases").fetchall():
        _add_crew_search_term_conn(conn, int(row["person_id"]), row["alias"], "alias")
    for row in conn.execute(
        """
        SELECT DISTINCT p.id,s.employee_name
        FROM crew_people p
        JOIN deputy_schedule_shifts s ON s.employee_id=p.deputy_employee_id
        WHERE TRIM(COALESCE(s.employee_name,'')) != ''
        """
    ).fetchall():
        _add_crew_search_term_conn(conn, int(row["id"]), row["employee_name"], "deputy_history")


def _vehicle_catalogue_key(value: object) -> str:
    return normalise_person_identity(value)


def _looks_like_crew_vehicle(value: object) -> bool:
    label = str(value or "").strip()
    return bool(
        re.fullmatch(r"\d{3}", label)
        or re.fullmatch(r"Rav[0-9A-Za-z]+", label, re.IGNORECASE)
        or label.casefold() in {"ob", "tender", "transit"}
    )


def _sync_vehicle_catalogue(conn: sqlite3.Connection) -> None:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    observed = {
        str(row["area_name"] or "").strip()
        for row in conn.execute(
            "SELECT DISTINCT area_name FROM deputy_schedule_shifts WHERE TRIM(COALESCE(area_name,'')) != ''"
        ).fetchall()
        if _looks_like_crew_vehicle(row["area_name"])
    }
    observed.update(
        str(row["vehicle_label"] or "").strip()
        for row in conn.execute(
            "SELECT DISTINCT vehicle_label FROM workday_assignments WHERE TRIM(COALESCE(vehicle_label,'')) != ''"
        ).fetchall()
        if _looks_like_crew_vehicle(row["vehicle_label"])
    )
    common_order = {"684": 10, "685": 20, "rav91": 30, "transit": 40, "tender": 50, "ob": 60}
    for label in sorted(observed, key=lambda item: (common_order.get(item.casefold(), 999), item.casefold())):
        key = _vehicle_catalogue_key(label)
        if not key:
            continue
        conn.execute(
            """
            INSERT INTO crew_vehicles(
                stable_key,display_label,aliases,active,is_truck,sort_order,source,created_at,updated_at
            ) VALUES (?,?,'[]',1,?,?,'discovered',?,?)
            ON CONFLICT(stable_key) DO UPDATE SET
                display_label=CASE WHEN crew_vehicles.source='discovered' THEN excluded.display_label ELSE crew_vehicles.display_label END,
                updated_at=excluded.updated_at
            """,
            (key, label[:100], 1 if label.casefold() in {"tender", "ob"} else 0,
             common_order.get(label.casefold(), 999), now, now),
        )


def _migrate_legacy_roster_assignments(conn: sqlite3.Connection) -> None:
    migration_key = "workday_assignments_migrated_v1"
    migrated = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (migration_key,),
    ).fetchone()
    if migrated is not None and str(migrated["value"] or "") == "1":
        return
    rows = conn.execute(
        """
        SELECT a.*,
               (SELECT p.id FROM crew_people p WHERE p.app_user_id = a.user_id LIMIT 1) AS person_id
        FROM roster_day_assignments a
        ORDER BY a.roster_day_id, a.sort_order, a.id
        """
    ).fetchall()
    for row in rows:
        role_label = str(row["position_label"] or "").strip()
        assignee_label = str(row["assignee_label"] or "").strip()
        vehicle_label = str(row["vehicle_label"] or "").strip()
        assignment_state = "tbc" if assignee_label.casefold() == "tbc" else "assigned"
        conn.execute(
            """
            INSERT OR IGNORE INTO workday_assignments (
                roster_day_id, person_id, user_id, assignee_label,
                role_key, role_label, assignment_state, transport_mode,
                vehicle_key, vehicle_label, custom_transport_text,
                assignment_note, sort_order, legacy_assignment_id,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?)
            """,
            (
                int(row["roster_day_id"]),
                row["person_id"],
                row["user_id"],
                assignee_label,
                canonical_role_key(role_label),
                role_label,
                assignment_state,
                legacy_transport_mode(vehicle_label),
                re.sub(r"[^a-z0-9]+", "", vehicle_label.lower()),
                vehicle_label,
                int(row["sort_order"]) if row["sort_order"] is not None else 999999,
                int(row["id"]),
                str(row["created_at"] or ""),
                str(row["updated_at"] or ""),
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, '1')",
        (migration_key,),
    )


def _migrate_admin_overrides(conn: sqlite3.Connection) -> None:
    rows = [
        dict(row)
        for row in conn.execute("SELECT * FROM admin_overrides ORDER BY created_at, id").fetchall()
    ]
    active_by_identity: dict[tuple[str, str, str], list[int]] = {}
    for row in rows:
        current_status = str(row.get("status") or "").strip().lower()
        if current_status in {"disabled", "superseded"}:
            continue
        target_date, date_error = validate_override_date(row.get("target_date"))
        track_key, _track_label = canonical_override_venue(row.get("target_track"))
        field_key, field_error = normalise_override_field(row.get("override_type"), row.get("label"))
        normalised_value, value_error = normalise_override_value(field_key, row.get("value")) if field_key else ("", "")
        error = date_error or ("Track must identify a racecourse." if not track_key else "") or field_error or value_error
        status = "invalid" if error else ("active" if int(row.get("active") or 0) else "disabled")
        conn.execute(
            """
            UPDATE admin_overrides
            SET target_date = COALESCE(NULLIF(?, ''), target_date),
                target_track_key = ?, field_key = ?, normalized_value = ?,
                original_value = COALESCE(original_value, value),
                status = ?, validation_error = ?, active = ?
            WHERE id = ?
            """,
            (
                target_date,
                track_key,
                field_key,
                normalised_value,
                status,
                error,
                1 if status == "active" else 0,
                int(row["id"]),
            ),
        )
        if status == "active":
            active_by_identity.setdefault((target_date, track_key, field_key), []).append(int(row["id"]))

    for row_ids in active_by_identity.values():
        if len(row_ids) < 2:
            continue
        latest_id = row_ids[-1]
        for old_id in row_ids[:-1]:
            conn.execute(
                """
                UPDATE admin_overrides
                SET active = 0, status = 'superseded', superseded_by_id = ?
                WHERE id = ?
                """,
                (latest_id, old_id),
            )


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
    folded = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", folded.encode("ascii", "ignore").decode().lower())


def normalise_person_identity(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", folded.encode("ascii", "ignore").decode().lower())


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
        row for row in conn.execute("SELECT * FROM crew_people WHERE is_active = 1 AND merged_into_person_id IS NULL AND COALESCE(person_type,'employee')='employee'").fetchall()
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

    # App accounts are access identities, not evidence that a crew person exists.
    # Account links are created only by authenticated Deputy identity, an existing
    # conservative resolver, an explicit Admin link, or the contractor workflow.

    manual_names: list[str] = [
        str(row["assignee_label"] or "").strip()
        for row in conn.execute(
            """
            SELECT legacy.assignee_label
            FROM roster_day_assignments legacy
            LEFT JOIN workday_assignments current ON current.legacy_assignment_id=legacy.id
            WHERE current.id IS NULL
            """
        ).fetchall()
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
            if (
                _optional_int(assignment.get("person_id")) is not None
                or _optional_int(assignment.get("user_id")) is not None
            ):
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
            row for row in conn.execute("SELECT * FROM crew_people WHERE is_active = 1 AND merged_into_person_id IS NULL").fetchall()
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


def get_app_user_any_status(user_id: int) -> sqlite3.Row | None:
    """Return an account for retention/safety operations, including inactive users."""
    with get_connection() as conn:
        return conn.execute("SELECT * FROM app_users WHERE id = ?", (user_id,)).fetchone()


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


def persist_deputy_user_credentials(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    deputy_web_url: str,
    encrypted_email: str,
    encrypted_password: str,
    now: str,
) -> int:
    deputy_web_url = deputy_web_url.strip()
    result = conn.execute(
        """
            UPDATE app_users
            SET deputy_web_url = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (deputy_web_url, now, user_id),
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


def update_deputy_user_credentials(
    *,
    user_id: int,
    deputy_email: str,
    deputy_web_url: str,
    encrypted_email: str,
    encrypted_password: str,
) -> int:
    del deputy_email  # Re-Deputy account email and Deputy credential email are deliberately independent.
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        return persist_deputy_user_credentials(
            conn,
            user_id=user_id,
            deputy_web_url=deputy_web_url,
            encrypted_email=encrypted_email,
            encrypted_password=encrypted_password,
            now=now,
        )


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
            "UPDATE app_users SET last_seen_at = ?, last_activity_at = CASE WHEN account_type='contractor' THEN ? ELSE last_activity_at END WHERE id = ?",
            (now, now, user_id),
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
        # Keep the newly authenticated device within the configured total of
        # most-recently-active valid devices. Revocation is per-user and idempotent.
        _enforce_trusted_device_limit_conn(conn, user_id=user_id, now=now)
        return conn.execute("SELECT * FROM trusted_devices WHERE id = ?", (cursor.lastrowid,)).fetchone()


def _enforce_trusted_device_limit_conn(
    conn: sqlite3.Connection,
    *,
    user_id: int | None = None,
    now: str | None = None,
) -> int:
    settings = get_settings()
    now = now or datetime.now(settings.timezone).isoformat(timespec="seconds")
    device_limit = settings.trusted_device_limit
    user_rows = (
        [{"id": user_id}]
        if user_id is not None
        else conn.execute("SELECT DISTINCT user_id AS id FROM trusted_devices").fetchall()
    )
    revoked = 0
    for user in user_rows:
        target_user_id = int(user["id"])
        result = conn.execute(
            """UPDATE trusted_devices SET revoked_at=?
               WHERE user_id=? AND revoked_at IS NULL AND expires_at>?
                 AND id NOT IN (
                   SELECT id FROM trusted_devices
                   WHERE user_id=? AND revoked_at IS NULL AND expires_at>?
                   ORDER BY COALESCE(last_seen_at,created_at) DESC,id DESC LIMIT ?
                 )""",
            (now, target_user_id, now, target_user_id, now, device_limit),
        )
        revoked += int(result.rowcount or 0)
    return revoked


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
                u.account_type,
                u.contractor_person_id,
                s.last_sync_at,
                s.last_status,
                s.last_message,
                s.sync_in_progress,
                CASE WHEN TRIM(COALESCE(secret.encrypted_email,'')) != '' AND TRIM(COALESCE(secret.encrypted_password,'')) != '' THEN 1 ELSE 0 END AS has_deputy_credentials
            FROM trusted_devices d
            JOIN app_users u ON u.id = d.user_id
            LEFT JOIN user_sync_state s ON s.user_id = u.id
            LEFT JOIN deputy_user_secrets secret ON secret.user_id = u.id
            WHERE d.token_hash = ?
              AND d.revoked_at IS NULL
              AND d.expires_at > ?
              AND u.is_active = 1
            """,
            (token_hash, now),
        ).fetchone()


def create_sync_generation(reason: str, members: list[tuple[int, str]], created_at: str) -> int:
    """Persist a planned dispatch; completion is derived only from terminal members."""
    with get_connection() as conn:
        cursor = conn.execute("INSERT INTO sync_generations(reason,created_at) VALUES(?,?)", (reason, created_at))
        generation_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO sync_generation_members(generation_id,user_id,planned_at) VALUES(?,?,?)",
            [(generation_id, user_id, planned_at) for user_id, planned_at in members],
        )
    return generation_id


def claim_sync_generation_member(generation_id: int, user_id: int, started_at: str) -> bool:
    """Atomically claim one pending member. Only the winner may capture roster data."""
    with get_connection() as conn:
        result = conn.execute(
            """UPDATE sync_generation_members SET status='running',started_at=?
               WHERE generation_id=? AND user_id=? AND status='pending'""",
            (started_at, generation_id, user_id),
        )
        return result.rowcount == 1


def mark_sync_generation_member(generation_id: int, user_id: int, status: str, at: str, message: str = "") -> bool:
    """Finish a claimed member without permitting terminal states to regress."""
    if status not in {"success", "error", "skipped", "superseded"}:
        raise ValueError("Invalid sync generation member status")
    with get_connection() as conn:
        result = conn.execute(
            """UPDATE sync_generation_members SET status=?,finished_at=?,message=?
               WHERE generation_id=? AND user_id=? AND status='running'""",
            (status, at, message[:500], generation_id, user_id),
        )
        if result.rowcount != 1:
            return False
        remaining = conn.execute(
            "SELECT COUNT(*) FROM sync_generation_members WHERE generation_id=? AND status NOT IN ('success','error','skipped','superseded')",
            (generation_id,),
        ).fetchone()[0]
        if not remaining:
            errors = conn.execute("SELECT COUNT(*) FROM sync_generation_members WHERE generation_id=? AND status='error'", (generation_id,)).fetchone()[0]
            conn.execute("""UPDATE sync_generations SET status=?,completed_at=COALESCE(completed_at,?)
                            WHERE id=? AND status='pending'""", ("error" if errors else "complete", at, generation_id))
        return True


def recover_incomplete_sync_generations(at: str, message: str = "Previous sync stopped during app restart.") -> int:
    """Repair persisted generations after restart; future pending members remain runnable."""
    with get_connection() as conn:
        running = conn.execute(
            """UPDATE sync_generation_members SET status='error',finished_at=?,message=?
               WHERE status='running'""", (at, message[:500])
        ).rowcount
        generation_ids = [row[0] for row in conn.execute(
            """SELECT id FROM sync_generations WHERE status='pending'
               AND NOT EXISTS (SELECT 1 FROM sync_generation_members m WHERE m.generation_id=sync_generations.id
                               AND m.status NOT IN ('success','error','skipped','superseded'))"""
        ).fetchall()]
        for generation_id in generation_ids:
            errors = conn.execute("SELECT COUNT(*) FROM sync_generation_members WHERE generation_id=? AND status='error'", (generation_id,)).fetchone()[0]
            conn.execute("UPDATE sync_generations SET status=?,completed_at=COALESCE(completed_at,?) WHERE id=? AND status='pending'", ("error" if errors else "complete", at, generation_id))
        return running + len(generation_ids)


def latest_relevant_sync_generation() -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM sync_generations ORDER BY id DESC LIMIT 1").fetchone()


def active_scheduled_sync_generation() -> sqlite3.Row | None:
    """The one persisted scheduled batch that future dispatches must reuse."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM sync_generations WHERE status='pending' AND reason!='manual'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()


def integrity_generation_boundary() -> tuple[sqlite3.Row | None, bool]:
    """Return the final terminal mutation boundary, while any active work blocks it."""
    with get_connection() as conn:
        active = conn.execute(
            """SELECT 1 FROM sync_generation_members m JOIN sync_generations g ON g.id=m.generation_id
               WHERE m.status IN ('pending','running') AND g.status!='superseded' LIMIT 1"""
        ).fetchone() is not None
        latest = conn.execute(
            """SELECT * FROM sync_generations WHERE status IN ('complete','error')
               ORDER BY completed_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        return latest, active


def active_sync_generation_for_user(user_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """SELECT m.generation_id,m.status FROM sync_generation_members m JOIN sync_generations g ON g.id=m.generation_id
               WHERE m.user_id=? AND g.status='pending' AND m.status IN ('pending','running') ORDER BY m.generation_id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()


def promote_pending_sync_generation_member(user_id: int, planned_at: str) -> int | None:
    """Bring one existing pending member forward without creating another dispatch."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT m.generation_id FROM sync_generation_members m JOIN sync_generations g ON g.id=m.generation_id
               WHERE m.user_id=? AND g.status='pending' AND m.status='pending' ORDER BY m.generation_id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        generation_id = int(row["generation_id"])
        conn.execute("UPDATE sync_generation_members SET planned_at=? WHERE generation_id=? AND user_id=? AND status='pending'", (planned_at, generation_id, user_id))
        conn.execute("UPDATE user_sync_state SET next_sync_after=?,updated_at=? WHERE user_id=?", (planned_at, planned_at, user_id))
        return generation_id


def get_settled_integrity_state() -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM settled_integrity_state WHERE id=1").fetchone()


def save_settled_integrity_state(generation_id: int, findings_json: str, updated_at: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO settled_integrity_state(id,generation_id,findings_json,updated_at) VALUES(1,?,?,?)
               ON CONFLICT(id) DO UPDATE SET generation_id=excluded.generation_id,findings_json=excluded.findings_json,updated_at=excluded.updated_at""",
            (generation_id, findings_json, updated_at),
        )


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


def _purge_result(status: str, reason: str = "", **counts: int) -> dict[str, object]:
    return {
        "status": status,
        "purged": status == "purged",
        "reason": reason,
        "users": int(counts.get("users", 0)),
        "devices": int(counts.get("devices", 0)),
        "shifts": int(counts.get("shifts", 0)),
        "marks": int(counts.get("marks", 0)),
        "changes": int(counts.get("changes", 0)),
    }


def _retained_user_purge_blockers(conn: sqlite3.Connection, user_id: int) -> list[str]:
    checks = (
        ("Deputy write audit history", "SELECT 1 FROM deputy_write_operations WHERE app_user_id=? LIMIT 1", (user_id,)),
        ("Deputy OAuth configuration audit history", "SELECT 1 FROM deputy_oauth_config WHERE updated_by_user_id=? LIMIT 1", (user_id,)),
        ("Deputy person-mapping audit history", "SELECT 1 FROM deputy_person_mappings WHERE updated_by_user_id=? LIMIT 1", (user_id,)),
        ("Deputy unit-mapping audit history", "SELECT 1 FROM deputy_unit_mappings WHERE updated_by_user_id=? LIMIT 1", (user_id,)),
        (
            "Re-Deputy invitations created for other accounts",
            """SELECT 1 FROM account_invitations
               WHERE created_by_user_id=? AND COALESCE(activated_user_id,-1)<>? LIMIT 1""",
            (user_id, user_id),
        ),
        (
            "contractor invitations created for other accounts",
            "SELECT 1 FROM contractor_invites WHERE created_by_user_id=? LIMIT 1",
            (user_id,),
        ),
        (
            "Admin role changes performed on other accounts",
            "SELECT 1 FROM app_role_audit WHERE actor_user_id=? AND target_user_id<>? LIMIT 1",
            (user_id, user_id),
        ),
    )
    return [label for label, sql, params in checks if conn.execute(sql, params).fetchone()]


def purge_app_user(user_id: int) -> dict[str, object]:
    try:
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute(
                "SELECT * FROM app_users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if user is None:
                return _purge_result("not_found", "User not found.")
            if int(user["is_active"] or 0):
                return _purge_result("still_active", "Only inactive users can be purged.")

            blockers = _retained_user_purge_blockers(conn, user_id)
            if blockers:
                return _purge_result(
                    "blocked",
                    "User cannot be purged because retained audit history references this account: "
                    + "; ".join(blockers)
                    + ".",
                )

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
            conn.execute("DELETE FROM account_invitations WHERE activated_user_id=?", (user_id,))
            conn.execute("DELETE FROM app_role_audit WHERE target_user_id=?", (user_id,))
            conn.execute("UPDATE contractor_invites SET activated_user_id=NULL WHERE activated_user_id=?", (user_id,))
            conn.execute("DELETE FROM deputy_user_secrets WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_sync_state WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_crew_memberships WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE error_reports SET user_id = NULL WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE deputy_web_captures SET owner_user_id = NULL WHERE owner_user_id = ?", (user_id,))
            conn.execute("UPDATE crew_known_locations SET source_user_id = NULL WHERE source_user_id = ?", (user_id,))
            conn.execute("UPDATE capture_coverage SET source_user_id = NULL WHERE source_user_id = ?", (user_id,))
            conn.execute("UPDATE admin_overrides SET disabled_by_user_id=NULL WHERE disabled_by_user_id=?", (user_id,))
            conn.execute("UPDATE crew_people SET merged_by_user_id=NULL WHERE merged_by_user_id=?", (user_id,))
            deleted_devices = conn.execute("DELETE FROM trusted_devices WHERE user_id = ?", (user_id,)).rowcount
            synthetic_people = conn.execute(
                """
                SELECT id FROM crew_people
                WHERE app_user_id=? AND deputy_employee_id IS NULL
                  AND identity_source='account_synthetic'
                  AND COALESCE(person_type,'employee')='employee'
                  AND NOT EXISTS (SELECT 1 FROM workday_assignments a WHERE a.person_id=crew_people.id)
                  AND NOT EXISTS (SELECT 1 FROM contractor_invites i WHERE i.crew_person_id=crew_people.id)
                """,
                (user_id,),
            ).fetchall()
            conn.execute("UPDATE crew_people SET app_user_id=NULL WHERE app_user_id=?", (user_id,))
            deleted_user = conn.execute("DELETE FROM app_users WHERE id = ?", (user_id,)).rowcount
            for person in synthetic_people:
                conn.execute(
                    "DELETE FROM crew_people WHERE id=? AND deputy_employee_id IS NULL AND app_user_id IS NULL",
                    (int(person["id"]),),
                )
        return _purge_result(
            "purged",
            users=deleted_user,
            devices=deleted_devices,
            shifts=deleted_shifts,
            marks=deleted_marks,
            changes=deleted_changes,
        )
    except sqlite3.IntegrityError:
        return _purge_result(
            "blocked",
            "User cannot be purged because retained audit history still references this account.",
        )


def purge_old_inactive_records(days: int = 30) -> dict[str, int]:
    cutoff = (datetime.now(get_settings().timezone) - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")
    purged_users = 0
    blocked_users = 0
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
        blocked_users += 1 if result.get("status") == "blocked" else 0
    return {"users": purged_users, "devices": revoked_devices, "blocked": blocked_users}


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
) -> sqlite3.Row:
    now = datetime.now().isoformat(timespec="seconds")
    target_date, date_error = validate_override_date(target_date)
    target_track_key, canonical_track = canonical_override_venue(target_track)
    field_key, field_error = normalise_override_field(override_type, label)
    normalized_value, value_error = normalise_override_value(field_key, value) if field_key else ("", "")
    error = date_error or ("Track must identify a racecourse." if not target_track_key else "") or field_error or value_error
    if error:
        raise ValueError(error)
    with get_connection() as conn:
        previous_rows = conn.execute(
            """
            SELECT id
            FROM admin_overrides
            WHERE target_date = ? AND target_track_key = ? AND field_key = ?
              AND status = 'active' AND active = 1
            ORDER BY created_at DESC, id DESC
            """,
            (target_date, target_track_key, field_key),
        ).fetchall()
        for previous in previous_rows:
            conn.execute(
                """
                UPDATE admin_overrides
                SET active = 0, status = 'superseded_pending'
                WHERE id = ?
                """,
                (int(previous["id"]),),
            )
        cursor = conn.execute(
            """
            INSERT INTO admin_overrides (
                created_at, created_by_user_id, target_date, target_track,
                override_type, label, value, note, active,
                target_track_key, field_key, normalized_value, original_value,
                status, validation_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 'active', '')
            """,
            (
                now,
                created_by_user_id,
                target_date,
                canonical_track,
                "timing",
                label,
                normalized_value,
                note,
                target_track_key,
                field_key,
                normalized_value,
                value,
            ),
        )
        new_id = int(cursor.lastrowid)
        for previous in previous_rows:
            conn.execute(
                """
                UPDATE admin_overrides
                SET active = 0, status = 'superseded', superseded_by_id = ?
                WHERE id = ?
                """,
                (new_id, int(previous["id"])),
            )
        return conn.execute(
            """
            SELECT o.*, u.display_name AS created_by_name
            FROM admin_overrides o
            LEFT JOIN app_users u ON u.id = o.created_by_user_id
            WHERE o.id = ?
            """,
            (new_id,),
        ).fetchone()


def disable_admin_override(override_id: int, *, disabled_by_user_id: int) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE admin_overrides
            SET active = 0, status = 'disabled', disabled_at = ?,
                disabled_by_user_id = ?
            WHERE id = ? AND status = 'active' AND active = 1
            """,
            (now, disabled_by_user_id, int(override_id)),
        )
        return cursor.rowcount > 0


def list_active_admin_overrides_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT o.*, u.display_name AS created_by_name
            FROM admin_overrides o
            LEFT JOIN app_users u ON u.id = o.created_by_user_id
            WHERE o.target_date BETWEEN ? AND ?
              AND o.status = 'active' AND o.active = 1
              AND o.target_track_key <> '' AND o.field_key <> ''
              AND o.normalized_value <> ''
            ORDER BY o.target_date, o.target_track_key, o.field_key, o.id DESC
            """,
            (start_date, end_date),
        ).fetchall()


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
                   (SELECT COUNT(*) FROM workday_assignments a WHERE a.roster_day_id = d.id) AS assignment_count,
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


def _never_published_draft_blockers_conn(
    conn: sqlite3.Connection,
    roster_day_id: int,
    row: sqlite3.Row,
) -> list[str]:
    blockers: list[str] = []
    if (
        str(row["status"] or "") != "draft"
        or str(row["published_snapshot"] or "").strip()
        or str(row["published_at"] or "").strip()
        or row["published_by_user_id"] is not None
        or conn.execute("SELECT 1 FROM roster_day_versions WHERE roster_day_id=? LIMIT 1", (roster_day_id,)).fetchone()
    ):
        blockers.append("published workday history")
    if str(row["linked_deputy_event_id"] or "").strip() or str(row["duplicate_resolution"] or "") == "linked":
        blockers.append("linked Deputy event evidence")
    retained_checks = (
        ("Deputy roster link history", "SELECT 1 FROM deputy_roster_links WHERE workday_id=? LIMIT 1", (roster_day_id,)),
        ("Deputy write-operation history", "SELECT 1 FROM deputy_write_operations WHERE workday_id=? LIMIT 1", (roster_day_id,)),
        ("open-position application history", "SELECT 1 FROM workday_open_position_applications WHERE roster_day_id=? LIMIT 1", (roster_day_id,)),
        ("published user visibility", "SELECT 1 FROM workday_user_visibility WHERE roster_day_id=? LIMIT 1", (roster_day_id,)),
        (
            "personal travel preferences",
            "SELECT 1 FROM user_event_transport_preferences WHERE event_kind='manual_workday' AND event_id=? LIMIT 1",
            (str(roster_day_id),),
        ),
        (
            "personal travel audit history",
            "SELECT 1 FROM user_event_transport_preference_audit WHERE event_kind='manual_workday' AND event_id=? LIMIT 1",
            (str(roster_day_id),),
        ),
        (
            "personal time overrides",
            "SELECT 1 FROM user_event_time_overrides WHERE event_kind='manual_workday' AND event_id=? LIMIT 1",
            (str(roster_day_id),),
        ),
        (
            "notification history",
            """SELECT 1 FROM notification_events
               WHERE workday_kind='manual' AND (workday_id=? OR workday_id LIKE ?) LIMIT 1""",
            (str(roster_day_id), f"{roster_day_id}:%"),
        ),
    )
    blockers.extend(label for label, sql, params in retained_checks if conn.execute(sql, params).fetchone())
    return blockers


def never_published_draft_deletion_status(roster_day_id: int) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM roster_days WHERE id=?", (roster_day_id,)).fetchone()
        if row is None:
            return {"status": "not_found", "allowed": False, "reason": "Workday draft not found."}
        blockers = _never_published_draft_blockers_conn(conn, roster_day_id, row)
    return {
        "status": "eligible" if not blockers else "blocked",
        "allowed": not blockers,
        "reason": "" if not blockers else "This workday cannot be deleted because it has " + "; ".join(blockers) + ".",
    }


def delete_never_published_roster_day(roster_day_id: int) -> dict[str, object]:
    try:
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM roster_days WHERE id=?", (roster_day_id,)).fetchone()
            if row is None:
                return {"status": "not_found", "deleted": False, "reason": "Workday draft not found."}
            blockers = _never_published_draft_blockers_conn(conn, roster_day_id, row)
            if blockers:
                return {
                    "status": "blocked",
                    "deleted": False,
                    "reason": "This workday cannot be deleted because it has " + "; ".join(blockers) + ".",
                }
            assignment_count = int(conn.execute(
                "SELECT COUNT(*) FROM workday_assignments WHERE roster_day_id=?", (roster_day_id,)
            ).fetchone()[0])
            audit_count = int(conn.execute(
                "SELECT COUNT(*) FROM workday_audit_events WHERE roster_day_id=?", (roster_day_id,)
            ).fetchone()[0])
            deleted = conn.execute("DELETE FROM roster_days WHERE id=?", (roster_day_id,)).rowcount
        return {
            "status": "deleted",
            "deleted": bool(deleted),
            "reason": "",
            "assignments": assignment_count,
            "audit_events": audit_count,
        }
    except sqlite3.IntegrityError:
        return {
            "status": "blocked",
            "deleted": False,
            "reason": "This workday cannot be deleted because retained history still references it.",
        }


def get_roster_day_assignments(roster_day_id: int) -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = [dict(row) for row in conn.execute(
            """
            SELECT a.*, u.display_name, u.deputy_email,
                   p.canonical_display_name AS person_display_name,
                   COALESCE(v.is_truck,0) AS vehicle_is_truck
            FROM workday_assignments a
            LEFT JOIN app_users u ON u.id = a.user_id
            LEFT JOIN crew_people p ON p.id = a.person_id
            LEFT JOIN crew_vehicles v ON v.id = a.vehicle_id
            WHERE a.roster_day_id = ?
            ORDER BY a.sort_order, LOWER(a.role_label), a.id
            """,
            (roster_day_id,),
        ).fetchall()]
        for item in rows:
            resolved_id = _canonical_person_id_conn(conn, _optional_int(item.get("person_id")))
            if resolved_id is None:
                continue
            person = conn.execute(
                "SELECT canonical_display_name,app_user_id FROM crew_people WHERE id=?",
                (resolved_id,),
            ).fetchone()
            if person is None:
                continue
            item["person_id"] = resolved_id
            item["person_display_name"] = str(person["canonical_display_name"] or "")
            item["assignee_label"] = str(person["canonical_display_name"] or item.get("assignee_label") or "")
            item["user_id"] = _optional_int(person["app_user_id"])
        return rows


def list_roster_day_versions(roster_day_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT v.*, u.display_name AS published_by_name
            FROM roster_day_versions v
            LEFT JOIN app_users u ON u.id = v.published_by_user_id
            WHERE v.roster_day_id = ?
            ORDER BY v.version_number DESC
            """,
            (roster_day_id,),
        ).fetchall()


def list_workday_roles(*, include_disabled: bool = False) -> list[sqlite3.Row]:
    with get_connection() as conn:
        where = "" if include_disabled else "WHERE is_active = 1"
        return conn.execute(
            f"""
            SELECT * FROM workday_role_catalogue
            {where}
            ORDER BY display_order, LOWER(display_label)
            """
        ).fetchall()


def list_crew_teams(*, include_inactive: bool = False) -> list[dict[str, object]]:
    with get_connection() as conn:
        where = "" if include_inactive else "WHERE t.active=1"
        rows = [dict(row) for row in conn.execute(
            f"""
            SELECT t.*,
                   (SELECT COUNT(*) FROM crew_person_teams m
                    WHERE m.team_id=t.id AND m.active=1) member_count
            FROM crew_teams t {where}
            ORDER BY t.sort_order,LOWER(t.display_name),t.id
            """
        ).fetchall()]
        for team in rows:
            team["members"] = [dict(row) for row in conn.execute(
                """
                SELECT p.id,p.canonical_display_name,m.is_primary
                FROM crew_person_teams m
                JOIN crew_people p ON p.id=m.crew_person_id
                WHERE m.team_id=? AND m.active=1 AND p.is_active=1
                  AND p.merged_into_person_id IS NULL
                ORDER BY m.is_primary DESC,LOWER(p.canonical_display_name)
                """,
                (int(team["id"]),),
            ).fetchall()]
        return rows


def get_default_team_id() -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM crew_teams WHERE stable_key='northern-team' AND active=1 LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None


def save_crew_team(
    *, team_id: int | None, display_name: str, active: bool,
    sort_order: int, actor_user_id: int,
) -> tuple[bool, str, int | None]:
    label = re.sub(r"\s+", " ", str(display_name or "").strip())[:100]
    if not label:
        return False, "Team name is required.", None
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        if team_id is None:
            base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()).strip("-") or "team"
            key = base
            suffix = 1
            while conn.execute("SELECT 1 FROM crew_teams WHERE stable_key=?", (key,)).fetchone():
                suffix += 1
                key = f"{base}-{suffix}"
            cursor = conn.execute(
                "INSERT INTO crew_teams(stable_key,display_name,active,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (key, label, 1 if active else 0, int(sort_order), now, now),
            )
            saved_id = int(cursor.lastrowid)
            action = "team_created"
        else:
            existing = conn.execute("SELECT id FROM crew_teams WHERE id=?", (team_id,)).fetchone()
            if not existing:
                return False, "Team was not found.", None
            conn.execute(
                "UPDATE crew_teams SET display_name=?,active=?,sort_order=?,updated_at=? WHERE id=?",
                (label, 1 if active else 0, int(sort_order), now, team_id),
            )
            saved_id = int(team_id)
            action = "team_updated"
        conn.execute(
            "INSERT INTO crew_team_audit(action,team_id,actor_user_id,details,created_at) VALUES (?,?,?,?,?)",
            (action, saved_id, actor_user_id, json.dumps({"display_name": label, "active": active, "sort_order": int(sort_order)}), now),
        )
    return True, "Team saved.", saved_id


def set_crew_person_team(
    *, person_id: int, team_id: int, active: bool,
    is_primary: bool, actor_user_id: int,
) -> tuple[bool, str]:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        person_id = _canonical_person_id_conn(conn, person_id) or 0
        if not person_id or not conn.execute("SELECT 1 FROM crew_teams WHERE id=?", (team_id,)).fetchone():
            return False, "Crew member or team was not found."
        if is_primary and active:
            conn.execute("UPDATE crew_person_teams SET is_primary=0,updated_at=? WHERE crew_person_id=?", (now, person_id))
        conn.execute(
            """
            INSERT INTO crew_person_teams(crew_person_id,team_id,is_primary,active,created_at,updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(crew_person_id,team_id) DO UPDATE SET
                is_primary=excluded.is_primary,active=excluded.active,updated_at=excluded.updated_at
            """,
            (person_id, team_id, 1 if is_primary and active else 0, 1 if active else 0, now, now),
        )
        conn.execute(
            "INSERT INTO crew_team_audit(action,team_id,crew_person_id,actor_user_id,details,created_at) VALUES (?,?,?,?,?,?)",
            ("member_added" if active else "member_removed", team_id, person_id, actor_user_id, json.dumps({"primary": bool(is_primary and active)}), now),
        )
    return True, "Team membership saved."


def set_location_primary_team(
    *, location_key: str, location_label: str, team_id: int | None,
    actor_user_id: int,
) -> bool:
    key = calendar_location_key(location_key or location_label)
    label = re.sub(r"\s+", " ", str(location_label or "").strip())[:200]
    if not key or not label:
        return False
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        if team_id is not None and not conn.execute("SELECT 1 FROM crew_teams WHERE id=?", (team_id,)).fetchone():
            return False
        conn.execute(
            """
            INSERT INTO location_team_mappings(location_key,location_label,primary_team_id,updated_by_user_id,updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(location_key) DO UPDATE SET location_label=excluded.location_label,
                primary_team_id=excluded.primary_team_id,updated_by_user_id=excluded.updated_by_user_id,
                updated_at=excluded.updated_at
            """,
            (key, label, team_id, actor_user_id, now),
        )
    return True


def list_location_team_mappings() -> dict[str, dict[str, object]]:
    with get_connection() as conn:
        return {
            str(row["location_key"]): dict(row)
            for row in conn.execute(
                """
                SELECT m.*,t.display_name team_name
                FROM location_team_mappings m
                LEFT JOIN crew_teams t ON t.id=m.primary_team_id
                """
            ).fetchall()
        }


def crew_picker_records(selected_team_id: int | None = None) -> list[dict[str, object]]:
    refresh_crew_directory()
    with get_connection() as conn:
        _sync_crew_search_terms(conn)
        rows = [dict(row) for row in conn.execute(
            """
            SELECT p.id,p.canonical_display_name,p.deputy_employee_id,
                   GROUP_CONCAT(DISTINCT CASE WHEN m.active=1 AND t.id IS NOT NULL THEN m.team_id END) team_ids,
                   GROUP_CONCAT(DISTINCT CASE WHEN m.active=1 AND t.id IS NOT NULL THEN t.display_name END) team_names
            FROM crew_people p
            LEFT JOIN crew_person_teams m ON m.crew_person_id=p.id
            LEFT JOIN crew_teams t ON t.id=m.team_id AND t.active=1
            WHERE p.is_active=1 AND p.merged_into_person_id IS NULL
            GROUP BY p.id
            ORDER BY LOWER(p.canonical_display_name),p.id
            """
        ).fetchall()]
        rows = [row for row in rows if not is_placeholder_crew_name(row.get("canonical_display_name"))]
        terms_by_person: dict[int, list[str]] = {}
        for term in conn.execute(
            "SELECT crew_person_id,normalized_term FROM crew_identity_search_terms ORDER BY id"
        ).fetchall():
            terms_by_person.setdefault(int(term["crew_person_id"]), []).append(str(term["normalized_term"]))
    for row in rows:
        team_ids = [int(value) for value in str(row.get("team_ids") or "").split(",") if value.isdigit()]
        row["team_ids"] = team_ids
        row["team_names"] = [value for value in str(row.get("team_names") or "").split(",") if value]
        row["selected_team_member"] = bool(selected_team_id and selected_team_id in team_ids)
        row["search_text"] = " ".join(dict.fromkeys(terms_by_person.get(int(row["id"]), []) + [normalise_person_identity(row["canonical_display_name"])]))
    return sorted(rows, key=lambda row: (not bool(row["selected_team_member"]), str(row["canonical_display_name"]).casefold()))


def list_crew_vehicles(*, include_inactive: bool = False) -> list[dict[str, object]]:
    with get_connection() as conn:
        _sync_vehicle_catalogue(conn)
        where = "" if include_inactive else "WHERE v.active=1"
        return [dict(row) for row in conn.execute(
            f"""
            SELECT v.*,t.display_name team_name FROM crew_vehicles v
            LEFT JOIN crew_teams t ON t.id=v.team_id
            {where}
            ORDER BY v.sort_order,LOWER(v.display_label),v.id
            """
        ).fetchall()]


def save_crew_vehicle(
    *, vehicle_id: int | None, display_label: str, aliases: list[str],
    active: bool, sort_order: int, team_id: int | None, notes: str,
    actor_user_id: int, is_truck: bool = False,
) -> tuple[bool, str, int | None]:
    label = re.sub(r"\s+", " ", str(display_label or "").strip())[:100]
    key = _vehicle_catalogue_key(label)
    if not key:
        return False, "Vehicle label is required.", None
    clean_aliases = list(dict.fromkeys(re.sub(r"\s+", " ", str(value).strip())[:100] for value in aliases if str(value).strip()))
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        if vehicle_id is None:
            cursor = conn.execute(
                """
                INSERT INTO crew_vehicles(stable_key,display_label,aliases,active,is_truck,sort_order,team_id,notes,source,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,'admin',?,?)
                ON CONFLICT(stable_key) DO UPDATE SET display_label=excluded.display_label,
                    aliases=excluded.aliases,active=excluded.active,is_truck=excluded.is_truck,sort_order=excluded.sort_order,
                    team_id=excluded.team_id,notes=excluded.notes,source='admin',updated_at=excluded.updated_at
                """,
                (key, label, json.dumps(clean_aliases), 1 if active else 0, 1 if is_truck else 0, int(sort_order), team_id, notes.strip()[:500], now, now),
            )
            row = conn.execute("SELECT id FROM crew_vehicles WHERE stable_key=?", (key,)).fetchone()
            saved_id = int(row["id"] if row else cursor.lastrowid)
            action = "vehicle_created"
        else:
            if not conn.execute("SELECT 1 FROM crew_vehicles WHERE id=?", (vehicle_id,)).fetchone():
                return False, "Vehicle was not found.", None
            conn.execute(
                "UPDATE crew_vehicles SET display_label=?,aliases=?,active=?,is_truck=?,sort_order=?,team_id=?,notes=?,source='admin',updated_at=? WHERE id=?",
                (label, json.dumps(clean_aliases), 1 if active else 0, 1 if is_truck else 0, int(sort_order), team_id, notes.strip()[:500], now, vehicle_id),
            )
            saved_id = vehicle_id
            action = "vehicle_updated"
        conn.execute(
            "INSERT INTO crew_vehicle_audit(action,vehicle_id,actor_user_id,details,created_at) VALUES (?,?,?,?,?)",
            (action, saved_id, actor_user_id, json.dumps({"label": label, "aliases": clean_aliases, "active": active, "is_truck": is_truck}), now),
        )
    return True, "Vehicle saved.", saved_id


def save_workday_role(
    *,
    role_key: str,
    display_label: str,
    aliases: list[str],
    display_order: int,
    is_active: bool,
    is_built_in: bool = False,
) -> str:
    key = canonical_role_key(role_key or display_label)
    if not key or not display_label.strip():
        raise ValueError("Role name is required.")
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO workday_role_catalogue (
                role_key, display_label, aliases, display_order, is_active,
                is_built_in, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(role_key) DO UPDATE SET
                display_label = excluded.display_label,
                aliases = excluded.aliases,
                display_order = excluded.display_order,
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (
                key,
                display_label.strip()[:100],
                json.dumps([str(alias).strip()[:100] for alias in aliases if str(alias).strip()]),
                int(display_order),
                1 if is_active else 0,
                1 if is_built_in else 0,
                now,
                now,
            ),
        )
    return key


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
    title: str = "",
    custom_location: str = "",
    end_time: str = "",
    break_minutes: int = 0,
    source_reference: str = "",
    provenance: str = "manual",
    linked_deputy_event_id: str = "",
    duplicate_resolution: str = "keep_separate",
    team_id: int | None = None,
    truck_start_offset_minutes: int = 0,
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
            canonical_location_key = track_key
            identity_key = track_key
            suffix = 1
            while conn.execute(
                "SELECT 1 FROM roster_days WHERE roster_date = ? AND track_key = ?",
                (roster_date, identity_key),
            ).fetchone() is not None:
                suffix += 1
                identity_key = f"{track_key}-manual-{suffix}"
            cursor = conn.execute(
                """
                INSERT INTO roster_days (
                    roster_date, track_key, canonical_location_key, track_label, race_type, day_type,
                    start_origin, finish_destination, office_start,
                    on_track_time, first_race_time, last_race_time, race_count,
                    notes, hotel_assignments, title, custom_location, end_time,
                    break_minutes, source_reference, provenance, linked_deputy_event_id,
                    duplicate_resolution, team_id, truck_start_offset_minutes, status, published_snapshot, created_by_user_id,
                    updated_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', '', ?, ?, ?, ?)
                """,
                (
                    roster_date, identity_key, canonical_location_key, track_label, race_type, day_type,
                    start_origin, finish_destination, office_start,
                    on_track_time, first_race_time, last_race_time, race_count,
                    notes, hotel_assignments, title, custom_location, end_time,
                    max(0, int(break_minutes or 0)), source_reference, provenance,
                    linked_deputy_event_id, duplicate_resolution, team_id, max(0, int(truck_start_offset_minutes or 0)),
                    updated_by_user_id, updated_by_user_id, now, now,
                ),
            )
            saved_id = int(cursor.lastrowid)
        else:
            saved_id = int(existing["id"])
            status = "changes_pending" if str(existing["published_snapshot"] or "").strip() else "draft"
            conn.execute(
                """
                UPDATE roster_days
                SET roster_date = ?, canonical_location_key = ?, track_label = ?, race_type = ?, day_type = ?,
                    start_origin = ?, finish_destination = ?, office_start = ?,
                    on_track_time = ?, first_race_time = ?,
                    last_race_time = ?, race_count = ?, notes = ?, hotel_assignments = ?, status = ?,
                    title = ?, custom_location = ?, end_time = ?, break_minutes = ?,
                    source_reference = ?, provenance = ?, linked_deputy_event_id = ?, duplicate_resolution = ?, team_id = ?,
                    truck_start_offset_minutes = ?,
                    updated_by_user_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    roster_date, track_key, track_label, race_type, day_type,
                    start_origin, finish_destination, office_start,
                    on_track_time, first_race_time, last_race_time, race_count,
                    notes, hotel_assignments, status,
                    title, custom_location, end_time, max(0, int(break_minutes or 0)),
                    source_reference, provenance, linked_deputy_event_id, duplicate_resolution, team_id,
                    max(0, int(truck_start_offset_minutes or 0)),
                    updated_by_user_id, now, saved_id,
                ),
            )

        prior_rows = [dict(row) for row in conn.execute(
            "SELECT assignment_key,assignment_state,person_id,role_label FROM workday_assignments WHERE roster_day_id=?",
            (saved_id,),
        ).fetchall()]
        prior_by_key = {str(row.get("assignment_key") or ""): row for row in prior_rows}
        saved_assignment_keys: set[str] = set()
        conn.execute("DELETE FROM workday_assignments WHERE roster_day_id = ?", (saved_id,))
        for assignment in assignments:
            person_id = _canonical_person_id_conn(conn, _optional_int(assignment.get("person_id")))
            canonical_user_id = _optional_int(assignment.get("user_id"))
            canonical_label = str(assignment.get("assignee_label") or "").strip()
            if person_id is not None:
                person = conn.execute(
                    "SELECT canonical_display_name,app_user_id FROM crew_people WHERE id=? AND is_active=1",
                    (person_id,),
                ).fetchone()
                if person is None:
                    continue
                canonical_user_id = _optional_int(person["app_user_id"])
                canonical_label = str(person["canonical_display_name"] or canonical_label)
            assignment_key = str(assignment.get("assignment_key") or "").strip()[:80]
            key_used_elsewhere = bool(assignment_key and conn.execute(
                "SELECT 1 FROM workday_assignments WHERE assignment_key=? AND roster_day_id!=? LIMIT 1",
                (assignment_key, saved_id),
            ).fetchone())
            if not assignment_key or key_used_elsewhere or assignment_key in {
                str(item.get("assignment_key") or "") for item in assignments
                if item is not assignment and str(item.get("assignment_key") or "")
            }:
                assignment_key = f"position-{uuid.uuid4().hex}"
            vehicle_id = _optional_int(assignment.get("vehicle_id"))
            vehicle_label = str(assignment.get("vehicle_label") or "").strip()
            vehicle_key = str(assignment.get("vehicle_key") or "").strip()
            if vehicle_id is not None:
                vehicle_row = conn.execute(
                    "SELECT id,stable_key,display_label FROM crew_vehicles WHERE id=? AND active=1",
                    (vehicle_id,),
                ).fetchone()
                if vehicle_row is None:
                    vehicle_id = None
                else:
                    vehicle_key = str(vehicle_row["stable_key"])
                    vehicle_label = str(vehicle_row["display_label"])
            conn.execute(
                """
                INSERT INTO workday_assignments (
                    roster_day_id, person_id, user_id, assignee_label,
                    role_key, role_label, assignment_state, transport_mode,
                    vehicle_key, vehicle_label, vehicle_id, custom_transport_text,
                    assignment_note, sort_order, assignment_key, eligible_team_id,
                    eligible_all_teams, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saved_id,
                    person_id,
                    canonical_user_id,
                    canonical_label,
                    str(assignment.get("role_key") or "").strip(),
                    str(assignment.get("role_label") or "").strip(),
                    str(assignment.get("assignment_state") or "assigned").strip(),
                    str(assignment.get("transport_mode") or "unassigned").strip(),
                    vehicle_key,
                    vehicle_label,
                    vehicle_id,
                    str(assignment.get("custom_transport_text") or "").strip(),
                    str(assignment.get("assignment_note") or "").strip(),
                    int(assignment.get("sort_order")) if assignment.get("sort_order") is not None else 999999,
                    assignment_key,
                    _optional_int(assignment.get("eligible_team_id")) or team_id,
                    1 if assignment.get("eligible_all_teams") else 0,
                    now,
                    now,
                ),
            )
            saved_assignment_keys.add(assignment_key)
            prior = prior_by_key.get(assignment_key)
            if prior and (
                str(prior.get("assignment_state") or "") != str(assignment.get("assignment_state") or "")
                or _optional_int(prior.get("person_id")) != person_id
            ):
                conn.execute(
                    "INSERT INTO workday_audit_events(roster_day_id,assignment_key,event_type,actor_user_id,crew_person_id,details,created_at) VALUES (?,?,?,?,?,?,?)",
                    (saved_id, assignment_key, "assignment_changed", updated_by_user_id, person_id,
                     json.dumps({"old_state": prior.get("assignment_state"), "new_state": assignment.get("assignment_state"), "role": assignment.get("role_label")}), now),
                )
        for removed_key in set(prior_by_key) - saved_assignment_keys:
            prior = prior_by_key[removed_key]
            conn.execute(
                "INSERT INTO workday_audit_events(roster_day_id,assignment_key,event_type,actor_user_id,crew_person_id,details,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    saved_id,
                    removed_key,
                    "assignment_removed",
                    updated_by_user_id,
                    _optional_int(prior.get("person_id")),
                    json.dumps({"state": prior.get("assignment_state"), "role": prior.get("role_label")}),
                    now,
                ),
            )
        active_open_keys = {
            str(item.get("assignment_key") or "")
            for item in assignments
            if str(item.get("assignment_state") or "") == "open"
        }
        removed_open_keys = {
            str(item.get("assignment_key") or "")
            for item in prior_rows
            if str(item.get("assignment_state") or "") == "open"
        } - active_open_keys
        for assignment_key in removed_open_keys:
            conn.execute(
                """
                UPDATE workday_open_position_applications
                SET status='cancelled_position',reviewed_at=?,reviewed_by_user_id=?,updated_at=?
                WHERE roster_day_id=? AND assignment_key=? AND status='pending'
                """,
                (now, updated_by_user_id, now, saved_id, assignment_key),
            )
            conn.execute(
                "INSERT INTO workday_audit_events(roster_day_id,assignment_key,event_type,actor_user_id,details,created_at) VALUES (?,?,?,?,?,?)",
                (saved_id, assignment_key, "open_position_removed", updated_by_user_id, "{}", now),
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
        _rebuild_workday_visibility_conn(conn)
    return version_number


def visible_workday_ids_for_user(start_date: str, end_date: str, user_id: int) -> set[int]:
    with get_connection() as conn:
        return {
            int(row["roster_day_id"])
            for row in conn.execute(
                """
                SELECT v.roster_day_id
                FROM workday_user_visibility v
                JOIN roster_days d ON d.id=v.roster_day_id
                WHERE v.user_id=? AND d.roster_date BETWEEN ? AND ?
                """,
                (user_id, start_date, end_date),
            ).fetchall()
        }


def _workday_window(row: sqlite3.Row | dict[str, object]) -> tuple[datetime | None, datetime | None]:
    values = dict(row)
    date_text = str(values.get("roster_date") or "")
    start_text = str(values.get("office_start") or "")
    end_text = str(values.get("end_time") or "")
    try:
        timezone = get_settings().timezone
        start = datetime.fromisoformat(f"{date_text}T{start_text}").replace(tzinfo=timezone) if start_text else None
        finish = datetime.fromisoformat(f"{date_text}T{end_text}").replace(tzinfo=timezone) if end_text else None
    except ValueError:
        return None, None
    if start and finish and finish <= start:
        finish += timedelta(days=1)
    return start, finish


def workday_vehicle_conflicts(roster_day_id: int) -> list[dict[str, object]]:
    """Find canonical catalogue vehicles used by another workday on this date."""
    with get_connection() as conn:
        target = conn.execute("SELECT * FROM roster_days WHERE id=?", (roster_day_id,)).fetchone()
        if target is None:
            return []
        rows = conn.execute(
            """
            SELECT DISTINCT other.id roster_day_id,other.roster_date,other.track_label,
                   other.custom_location,other.title,other.office_start,other.end_time,
                   vehicle.id vehicle_id,vehicle.display_label vehicle_label
            FROM workday_assignments mine
            JOIN crew_vehicles vehicle ON vehicle.id=mine.vehicle_id
            JOIN roster_days other ON other.roster_date=? AND other.id!=mine.roster_day_id
            JOIN workday_assignments theirs ON theirs.roster_day_id=other.id
                AND theirs.vehicle_id=mine.vehicle_id AND theirs.assignment_state='assigned'
            WHERE mine.roster_day_id=? AND mine.assignment_state='assigned'
              AND mine.transport_mode='vehicle' AND mine.vehicle_id IS NOT NULL
            ORDER BY LOWER(vehicle.display_label),other.office_start,other.id
            """,
            (str(target["roster_date"] or ""), roster_day_id),
        ).fetchall()
    target_start, target_end = _workday_window(target)
    conflicts: list[dict[str, object]] = []
    for row in rows:
        other_start, other_end = _workday_window(row)
        if target_start and target_end and other_start and other_end:
            level = "overlap" if target_start < other_end and other_start < target_end else "same_day"
        else:
            level = "possible"
        conflicts.append({
            **dict(row),
            "level": level,
            "message": (
                "Vehicle times overlap" if level == "overlap"
                else "Vehicle is already used on another workday today" if level == "same_day"
                else "Possible vehicle conflict · timing is still TBC"
            ),
            "location_label": str(row["custom_location"] or row["track_label"] or row["title"] or "Workday"),
            "time_range": f"{row['office_start'] or 'TBC'}–{row['end_time'] or 'TBC'}",
        })
    return conflicts


def workday_assignment_conflicts(
    *, crew_person_id: int, roster_day_id: int,
) -> list[dict[str, object]]:
    conflicts: list[dict[str, object]] = []
    with get_connection() as conn:
        person_id = _canonical_person_id_conn(conn, crew_person_id)
        target = conn.execute("SELECT * FROM roster_days WHERE id=?", (roster_day_id,)).fetchone()
        if person_id is None or target is None:
            return [{"reason": "identity", "message": "Crew identity or workday was not found."}]
        target_start, target_end = _workday_window(target)
        target_date = str(target["roster_date"] or "")
        same_day_role = conn.execute(
            """
            SELECT role_label FROM workday_assignments
            WHERE roster_day_id=? AND person_id=? AND assignment_state='assigned'
            ORDER BY sort_order,id LIMIT 1
            """,
            (roster_day_id, person_id),
        ).fetchone()
        if same_day_role:
            role = str(same_day_role["role_label"] or "another position")
            conflicts.append({"reason": "same_event", "message": f"Already assigned to {role} on this workday."})
        app_user = conn.execute("SELECT app_user_id FROM crew_people WHERE id=?", (person_id,)).fetchone()
        app_user_id = _optional_int(app_user["app_user_id"]) if app_user else None
        if app_user_id is not None:
            for shift in conn.execute(
                """
                SELECT id,title,start_at,end_at,date FROM shifts
                WHERE owner_user_id=? AND date=? AND deleted_from_source=0
                """,
                (app_user_id, target_date),
            ).fetchall():
                try:
                    other_start = datetime.fromisoformat(str(shift["start_at"] or ""))
                    other_end = datetime.fromisoformat(str(shift["end_at"] or ""))
                    if other_start.tzinfo is None:
                        other_start = other_start.replace(tzinfo=get_settings().timezone)
                    if other_end.tzinfo is None:
                        other_end = other_end.replace(tzinfo=get_settings().timezone)
                except ValueError:
                    other_start = other_end = None
                overlap = not all((target_start, target_end, other_start, other_end)) or (
                    target_start < other_end and other_start < target_end
                )
                if overlap:
                    conflicts.append({
                        "reason": "deputy_overlap" if all((target_start, target_end, other_start, other_end)) else "same_date_unknown",
                        "message": "Not available - already rostered." if all((target_start, target_end, other_start, other_end)) else "Already rostered that day.",
                        "source": "Deputy",
                        "event_id": int(shift["id"]),
                    })
        for other in conn.execute(
            """
            SELECT d.*,a.role_label FROM roster_days d
            JOIN workday_assignments a ON a.roster_day_id=d.id
            WHERE d.id!=? AND d.roster_date=?
              AND TRIM(COALESCE(d.published_snapshot,''))!=''
              AND a.person_id=? AND a.assignment_state='assigned'
            """,
            (roster_day_id, target_date, person_id),
        ).fetchall():
            other_start, other_end = _workday_window(other)
            overlap = not all((target_start, target_end, other_start, other_end)) or (
                target_start < other_end and other_start < target_end
            )
            if overlap:
                known = all((target_start, target_end, other_start, other_end))
                conflicts.append({
                    "reason": "manual_overlap" if known else "same_date_unknown",
                    "message": "Not available - already rostered." if known else "Already rostered that day.",
                    "source": "Re-Deputy",
                    "event_id": int(other["id"]),
                    "role": str(other["role_label"] or ""),
                })
    unique: dict[tuple[str, object], dict[str, object]] = {}
    for item in conflicts:
        unique[(str(item.get("reason")), item.get("event_id") or item.get("message"))] = item
    return list(unique.values())


def list_open_workday_positions(
    start_date: str,
    end_date: str,
    *,
    app_user_id: int | None,
    include_all_for_admin: bool = False,
) -> list[dict[str, object]]:
    with get_connection() as conn:
        person_id = None
        team_ids: set[int] = set()
        if app_user_id is not None:
            person = conn.execute(
                "SELECT id FROM crew_people WHERE app_user_id=? AND is_active=1 AND merged_into_person_id IS NULL",
                (app_user_id,),
            ).fetchone()
            if person:
                person_id = int(person["id"])
                team_ids = {
                    int(row["team_id"])
                    for row in conn.execute(
                        "SELECT team_id FROM crew_person_teams WHERE crew_person_id=? AND active=1",
                        (person_id,),
                    ).fetchall()
                }
        rows = [dict(row) for row in conn.execute(
            """
            SELECT a.*,d.roster_date,d.track_label,d.custom_location,d.title,d.day_type,
                   d.office_start,d.end_time,d.team_id workday_team_id,t.display_name team_name
            FROM workday_assignments a
            JOIN roster_days d ON d.id=a.roster_day_id
            LEFT JOIN crew_teams t ON t.id=COALESCE(a.eligible_team_id,d.team_id)
            WHERE d.roster_date BETWEEN ? AND ?
              AND TRIM(COALESCE(d.published_snapshot,''))!=''
              AND a.assignment_state='open'
            ORDER BY d.roster_date,d.office_start,a.sort_order,a.id
            """,
            (start_date, end_date),
        ).fetchall()]
        applications_by_key: dict[tuple[int, str], list[dict[str, object]]] = {}
        for application in conn.execute(
            """
            SELECT ap.*,p.canonical_display_name applicant_name
            FROM workday_open_position_applications ap
            JOIN crew_people p ON p.id=ap.crew_person_id
            WHERE ap.roster_day_id IN (
                SELECT id FROM roster_days WHERE roster_date BETWEEN ? AND ?
            ) ORDER BY ap.applied_at,ap.id
            """,
            (start_date, end_date),
        ).fetchall():
            item = dict(application)
            applications_by_key.setdefault((int(item["roster_day_id"]), str(item["assignment_key"])), []).append(item)
    result = []
    for row in rows:
        eligible_team_id = _optional_int(row.get("eligible_team_id")) or _optional_int(row.get("workday_team_id"))
        eligible = bool(row.get("eligible_all_teams")) or eligible_team_id is None or eligible_team_id in team_ids
        if not include_all_for_admin and not eligible:
            continue
        key = (int(row["roster_day_id"]), str(row.get("assignment_key") or ""))
        applications = applications_by_key.get(key, [])
        for application in applications:
            application["conflicts"] = workday_assignment_conflicts(
                crew_person_id=int(application["crew_person_id"]),
                roster_day_id=int(row["roster_day_id"]),
            ) if application.get("status") == "pending" else []
        own = next((item for item in reversed(applications) if _optional_int(item.get("app_user_id")) == app_user_id), None)
        conflicts = workday_assignment_conflicts(crew_person_id=person_id, roster_day_id=int(row["roster_day_id"])) if person_id else []
        row.update({
            "source_kind": "manual_open_position",
            "source_label": "Re-Deputy open position",
            "location_label": str(row.get("custom_location") or row.get("track_label") or row.get("title") or "Work day"),
            "area_display": str(row.get("role_label") or "Open position"),
            "date": str(row.get("roster_date") or ""),
            "time_range": f"{row.get('office_start') or 'TBC'}-{row.get('end_time') or 'TBC'}",
            "eligible": eligible,
            "conflicts": conflicts,
            "can_apply": bool(eligible and person_id and not conflicts and (not own or own.get("status") not in {"pending", "accepted"})),
            "own_application": own,
            "applications": applications,
            "application_count": sum(1 for item in applications if item.get("status") == "pending"),
        })
        result.append(row)
    return result


def apply_for_open_workday_position(
    *, roster_day_id: int, assignment_key: str, app_user_id: int,
) -> tuple[bool, str, int | None]:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        person = conn.execute(
            "SELECT id FROM crew_people WHERE app_user_id=? AND is_active=1 AND merged_into_person_id IS NULL",
            (app_user_id,),
        ).fetchone()
        if not person:
            return False, "Your app account is not linked to a crew identity.", None
        person_id = int(person["id"])
        position = conn.execute(
            """
            SELECT a.*,d.team_id workday_team_id,d.published_snapshot
            FROM workday_assignments a JOIN roster_days d ON d.id=a.roster_day_id
            WHERE a.roster_day_id=? AND a.assignment_key=? AND a.assignment_state='open'
            """,
            (roster_day_id, assignment_key),
        ).fetchone()
        if not position or not str(position["published_snapshot"] or "").strip():
            return False, "This open position is no longer available.", None
        eligible_team_id = _optional_int(position["eligible_team_id"]) or _optional_int(position["workday_team_id"])
        if not int(position["eligible_all_teams"] or 0) and eligible_team_id is not None:
            member = conn.execute(
                "SELECT 1 FROM crew_person_teams WHERE crew_person_id=? AND team_id=? AND active=1",
                (person_id, eligible_team_id),
            ).fetchone()
            if not member:
                return False, "This position is offered to another team.", None
        existing = conn.execute(
            """
            SELECT id,status FROM workday_open_position_applications
            WHERE roster_day_id=? AND assignment_key=? AND crew_person_id=?
              AND status IN ('pending','accepted') ORDER BY id DESC LIMIT 1
            """,
            (roster_day_id, assignment_key, person_id),
        ).fetchone()
        if existing:
            return True, "Application already sent.", int(existing["id"])
    conflicts = workday_assignment_conflicts(crew_person_id=person_id, roster_day_id=roster_day_id)
    if conflicts:
        return False, str(conflicts[0].get("message") or "Already rostered that day."), None
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO workday_open_position_applications(
                roster_day_id,assignment_key,crew_person_id,app_user_id,status,
                applied_at,conflict_snapshot,updated_at
            ) VALUES (?,?,?,?, 'pending',?,?,?)
            """,
            (roster_day_id, assignment_key, person_id, app_user_id, now, "[]", now),
        )
        application_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO workday_audit_events(roster_day_id,assignment_key,event_type,actor_user_id,crew_person_id,details,created_at) VALUES (?,?,?,?,?,?,?)",
            (roster_day_id, assignment_key, "application_created", app_user_id, person_id, json.dumps({"application_id": application_id}), now),
        )
    return True, "Application sent.", application_id


def withdraw_open_workday_application(
    *, roster_day_id: int, assignment_key: str, app_user_id: int,
) -> tuple[bool, str]:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM workday_open_position_applications
            WHERE roster_day_id=? AND assignment_key=? AND app_user_id=? AND status='pending'
            ORDER BY id DESC LIMIT 1
            """,
            (roster_day_id, assignment_key, app_user_id),
        ).fetchone()
        if not row:
            return False, "No pending application was found."
        conn.execute(
            "UPDATE workday_open_position_applications SET status='withdrawn',withdrawn_at=?,updated_at=? WHERE id=?",
            (now, now, int(row["id"])),
        )
        conn.execute(
            "INSERT INTO workday_audit_events(roster_day_id,assignment_key,event_type,actor_user_id,crew_person_id,details,created_at) VALUES (?,?,?,?,?,?,?)",
            (roster_day_id, assignment_key, "application_withdrawn", app_user_id, int(row["crew_person_id"]), json.dumps({"application_id": int(row["id"])}), now),
        )
    return True, "Application withdrawn."


def review_open_workday_application(
    *, application_id: int, action: str, reviewer_user_id: int,
    override_conflict: bool = False, admin_note: str = "",
) -> tuple[bool, str, int | None]:
    if action not in {"accept", "decline"}:
        return False, "Choose accept or decline.", None
    with get_connection() as conn:
        application = conn.execute(
            "SELECT * FROM workday_open_position_applications WHERE id=? AND status='pending'",
            (application_id,),
        ).fetchone()
        if not application:
            return False, "Pending application was not found.", None
        roster_day_id = int(application["roster_day_id"])
        assignment_key = str(application["assignment_key"])
        person_id = int(application["crew_person_id"])
    conflicts = workday_assignment_conflicts(crew_person_id=person_id, roster_day_id=roster_day_id) if action == "accept" else []
    if conflicts and not override_conflict:
        return False, str(conflicts[0].get("message") or "The applicant is now unavailable."), roster_day_id
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        if action == "decline":
            conn.execute(
                "UPDATE workday_open_position_applications SET status='declined',reviewed_at=?,reviewed_by_user_id=?,admin_note=?,updated_at=? WHERE id=?",
                (now, reviewer_user_id, admin_note.strip()[:500], now, application_id),
            )
            event_type = "application_declined"
            message = "Application declined."
        else:
            person = conn.execute(
                "SELECT canonical_display_name,app_user_id FROM crew_people WHERE id=? AND is_active=1",
                (person_id,),
            ).fetchone()
            position = conn.execute(
                "SELECT id FROM workday_assignments WHERE roster_day_id=? AND assignment_key=? AND assignment_state='open'",
                (roster_day_id, assignment_key),
            ).fetchone()
            if not person or not position:
                return False, "This position is no longer open.", roster_day_id
            conn.execute(
                """
                UPDATE workday_assignments SET person_id=?,user_id=?,assignee_label=?,
                    assignment_state='assigned',updated_at=? WHERE id=?
                """,
                (person_id, person["app_user_id"], person["canonical_display_name"], now, int(position["id"])),
            )
            conn.execute(
                "UPDATE workday_open_position_applications SET status='accepted',reviewed_at=?,reviewed_by_user_id=?,admin_note=?,conflict_snapshot=?,updated_at=? WHERE id=?",
                (now, reviewer_user_id, admin_note.strip()[:500], json.dumps(conflicts), now, application_id),
            )
            conn.execute(
                """
                UPDATE workday_open_position_applications SET status='declined',reviewed_at=?,
                    reviewed_by_user_id=?,updated_at=?
                WHERE roster_day_id=? AND assignment_key=? AND id!=? AND status='pending'
                """,
                (now, reviewer_user_id, now, roster_day_id, assignment_key, application_id),
            )
            event_type = "application_accepted_conflict_override" if conflicts else "application_accepted"
            message = "Applicant assigned locally in Re-Deputy."
        conn.execute(
            "INSERT INTO workday_audit_events(roster_day_id,assignment_key,event_type,actor_user_id,crew_person_id,details,created_at) VALUES (?,?,?,?,?,?,?)",
            (roster_day_id, assignment_key, event_type, reviewer_user_id, person_id, json.dumps({"application_id": application_id, "conflicts": conflicts}), now),
        )
    return True, message, roster_day_id


def resolve_workday_snapshot_assignments(assignments: list[object]) -> list[dict[str, object]]:
    resolved: list[dict[str, object]] = []
    with get_connection() as conn:
        for raw in assignments:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            vehicle_id = _optional_int(item.get("vehicle_id"))
            if vehicle_id is not None:
                vehicle = conn.execute("SELECT is_truck FROM crew_vehicles WHERE id=?", (vehicle_id,)).fetchone()
                item["vehicle_is_truck"] = bool(vehicle and vehicle["is_truck"])
            person_id = _canonical_person_id_conn(conn, _optional_int(item.get("person_id")))
            if person_id is not None:
                person = conn.execute(
                    "SELECT canonical_display_name,app_user_id FROM crew_people WHERE id=?",
                    (person_id,),
                ).fetchone()
                if person is not None:
                    item["person_id"] = person_id
                    item["user_id"] = _optional_int(person["app_user_id"])
                    item["assignee_label"] = str(person["canonical_display_name"] or item.get("assignee_label") or "")
            resolved.append(item)
    return resolved


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
            FROM roster_days
            WHERE TRIM(COALESCE(track_label, '')) != ''
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


def list_crew_work_location_labels() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT display_name AS name
            FROM crew_known_locations
            WHERE TRIM(COALESCE(display_name, '')) != ''
            UNION
            SELECT locations.name
            FROM deputy_schedule_locations locations
            WHERE TRIM(COALESCE(locations.name, '')) != ''
              AND EXISTS (
                  SELECT 1 FROM deputy_schedule_shifts shifts
                  WHERE shifts.area_location_id = locations.location_id
              )
            UNION
            SELECT track_label AS name
            FROM roster_days
            WHERE TRIM(COALESCE(track_label, '')) != ''
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
    labels: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = str(row["name"] or "").strip()
        key = calendar_location_key(label)
        if not key or key in seen or label.lower() in {"web", "shift", "vehicles", "travel"}:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def list_track_map_location_rules() -> list[sqlite3.Row]:
    with get_connection() as conn:
        try:
            return conn.execute(
                "SELECT * FROM track_map_location_rules ORDER BY location_label COLLATE NOCASE"
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def get_track_map_location_rule(location_key: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        try:
            return conn.execute(
                "SELECT * FROM track_map_location_rules WHERE location_key = ?",
                (location_key,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None


def upsert_track_map_location_rule(
    *,
    location_key: str,
    location_label: str,
    classification: str,
    canonical_venue_key: str = "",
    canonical_venue_label: str = "",
    source: str = "admin",
    note: str = "",
    updated_at: str | None = None,
) -> None:
    if classification not in {"venue", "alias", "excluded"}:
        raise ValueError("Invalid track-map location classification.")
    updated_at = updated_at or datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO track_map_location_rules (
                location_key, location_label, classification,
                canonical_venue_key, canonical_venue_label, source, note, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(location_key) DO UPDATE SET
                location_label = excluded.location_label,
                classification = excluded.classification,
                canonical_venue_key = excluded.canonical_venue_key,
                canonical_venue_label = excluded.canonical_venue_label,
                source = excluded.source,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (
                location_key, location_label, classification,
                canonical_venue_key or None, canonical_venue_label or None,
                source, note or None, updated_at,
            ),
        )


def delete_track_map_location_rule(location_key: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM track_map_location_rules WHERE location_key = ?", (location_key,))


def list_track_map_migration_warnings() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM track_map_migration_warnings ORDER BY created_at DESC, id DESC"
        ).fetchall()


def get_track_map_migration_warning(warning_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM track_map_migration_warnings WHERE id = ?",
            (warning_id,),
        ).fetchone()


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
            meeting_id = str(meeting.get("meeting_id") or "").strip()
            meeting_url = str(meeting.get("meeting_url") or "").strip()
            discovery_source = str(meeting.get("discovery_source") or "").strip()
            discovered_at = str(meeting.get("discovered_at") or "").strip()
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
                    meeting_id, meeting_url, discovery_source, discovered_at,
                    source_url, source_hash, raw_text, first_seen_at,
                    last_seen_at, last_synced_at, is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_hash) DO UPDATE SET
                    meeting_date = excluded.meeting_date,
                    racecourse_key = excluded.racecourse_key,
                    racecourse = excluded.racecourse,
                    club_name = excluded.club_name,
                    meeting_id = COALESCE(NULLIF(excluded.meeting_id, ''), love_racing_meetings.meeting_id),
                    meeting_url = COALESCE(NULLIF(excluded.meeting_url, ''), love_racing_meetings.meeting_url),
                    discovery_source = COALESCE(NULLIF(excluded.discovery_source, ''), love_racing_meetings.discovery_source),
                    discovered_at = COALESCE(NULLIF(excluded.discovered_at, ''), love_racing_meetings.discovered_at),
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
                    meeting_id,
                    meeting_url,
                    discovery_source,
                    discovered_at,
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


def get_love_racing_planning_meeting(meeting_row_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT meeting.*,
                   detail.race_count AS scheduled_race_count,
                   detail.first_race_time AS scheduled_first_race_time,
                   detail.last_race_time AS scheduled_last_race_time
            FROM love_racing_meetings meeting
            LEFT JOIN love_racing_meeting_details detail
              ON detail.meeting_id = meeting.meeting_id
            WHERE meeting.id = ? AND meeting.is_active = 1
            """,
            (meeting_row_id,),
        ).fetchone()


def merge_love_racing_meeting_identities(
    meetings: list[dict[str, object]],
    discovered_at: str,
    discovery_source: str = "Love Racing browser calendar",
) -> dict[str, int]:
    matched = 0
    ambiguous = 0
    with get_connection() as conn:
        for meeting in meetings:
            meeting_id = str(meeting.get("meeting_id") or "").strip()
            meeting_url = str(meeting.get("meeting_url") or "").strip()
            meeting_date = str(meeting.get("date") or meeting.get("meeting_date") or "").strip()
            racecourse = str(meeting.get("racecourse") or "").strip()
            racecourse_key = str(
                meeting.get("racecourse_key") or calendar_location_key(racecourse)
            ).strip()
            club_name = str(meeting.get("club_name") or "").strip()
            if not meeting_id or not meeting_url or not meeting_date or not racecourse_key:
                continue
            candidates = conn.execute(
                """
                SELECT *
                FROM love_racing_meetings
                WHERE is_active = 1
                  AND meeting_date = ?
                  AND racecourse_key = ?
                ORDER BY id
                """,
                (meeting_date, racecourse_key),
            ).fetchall()
            if len(candidates) > 1 and club_name:
                club_key = calendar_location_key(club_name)
                club_matches = [
                    row for row in candidates
                    if calendar_location_key(row["club_name"]) == club_key
                ]
                if len(club_matches) == 1:
                    candidates = club_matches
            if len(candidates) != 1:
                ambiguous += 1
                continue
            conn.execute(
                """
                UPDATE love_racing_meetings
                SET meeting_id = ?,
                    meeting_url = ?,
                    discovery_source = ?,
                    discovered_at = ?
                WHERE id = ?
                """,
                (
                    meeting_id,
                    meeting_url,
                    discovery_source,
                    discovered_at,
                    int(candidates[0]["id"]),
                ),
            )
            conn.execute(
                """
                INSERT INTO love_racing_meeting_details (
                    meeting_id, meeting_date, canonical_venue_key,
                    canonical_venue_label, club, meeting_url,
                    lifecycle_status, fetch_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'discovered', 'ready', ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    meeting_date = excluded.meeting_date,
                    canonical_venue_key = excluded.canonical_venue_key,
                    canonical_venue_label = excluded.canonical_venue_label,
                    club = excluded.club,
                    meeting_url = excluded.meeting_url,
                    updated_at = excluded.updated_at
                """,
                (
                    meeting_id,
                    meeting_date,
                    racecourse_key,
                    racecourse,
                    club_name,
                    meeting_url,
                    discovered_at,
                    discovered_at,
                ),
            )
            matched += 1
    return {"matched": matched, "ambiguous": ambiguous}


def fetch_love_racing_details_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM love_racing_meeting_details
            WHERE meeting_date BETWEEN ? AND ?
            ORDER BY meeting_date, canonical_venue_label, meeting_id
            """,
            (start_date, end_date),
        ).fetchall()


def get_love_racing_meeting_detail(meeting_id: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM love_racing_meeting_details WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()


def upsert_love_racing_meeting_detail_identity(
    *,
    meeting_id: str,
    meeting_date: str,
    canonical_venue_key: str,
    canonical_venue_label: str,
    club: str,
    meeting_url: str,
    discovered_at: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO love_racing_meeting_details (
                meeting_id, meeting_date, canonical_venue_key,
                canonical_venue_label, club, meeting_url,
                lifecycle_status, fetch_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'discovered', 'ready', ?, ?)
            ON CONFLICT(meeting_id) DO UPDATE SET
                meeting_date = excluded.meeting_date,
                canonical_venue_key = excluded.canonical_venue_key,
                canonical_venue_label = excluded.canonical_venue_label,
                club = excluded.club,
                meeting_url = excluded.meeting_url,
                updated_at = excluded.updated_at
            """,
            (
                meeting_id,
                meeting_date,
                canonical_venue_key,
                canonical_venue_label,
                club,
                meeting_url,
                discovered_at,
                discovered_at,
            ),
        )


def list_love_racing_detail_diagnostics(
    start_date: str,
    end_date: str,
) -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT details.*,
                   jobs.status AS queue_status,
                   jobs.requested_reason,
                   jobs.next_attempt_at AS job_next_attempt_at
            FROM love_racing_meeting_details details
            LEFT JOIN love_racing_detail_jobs jobs
              ON jobs.id = (
                  SELECT candidate.id
                  FROM love_racing_detail_jobs candidate
                  WHERE candidate.meeting_id = details.meeting_id
                  ORDER BY candidate.id DESC
                  LIMIT 1
              )
            WHERE details.meeting_date BETWEEN ? AND ?
            ORDER BY details.meeting_date, details.canonical_venue_label
            """,
            (start_date, end_date),
        ).fetchall()
    return [dict(row) for row in rows]


def merge_love_racing_programme(
    meeting_id: str,
    programme: dict[str, object],
    checked_at: str,
) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM love_racing_meeting_details WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        if row is None:
            return {"updated": False, "material_change": False}
        existing = dict(row)
        try:
            existing_races = {
                int(item["number"]): str(item.get("scheduled_start") or "")
                for item in json.loads(str(existing.get("races_json") or "[]"))
                if isinstance(item, dict) and int(item.get("number") or 0) > 0
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            existing_races = {}
        incoming_races = {}
        for item in programme.get("races") or []:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get("number") or 0)
            except (TypeError, ValueError):
                continue
            if number <= 0:
                continue
            incoming_races[number] = str(item.get("scheduled_start") or "")
        existing_lifecycle = str(existing.get("lifecycle_status") or "discovered")
        incoming_lifecycle = str(programme.get("lifecycle_status") or "awaiting_schedule")
        accept_complete_revision = (
            incoming_lifecycle == "complete" and existing_lifecycle != "historical"
        )
        preserve_confirmed = (
            existing_lifecycle == "historical"
            or (existing_lifecycle == "complete" and not accept_complete_revision)
        )
        merged_races = dict(existing_races)
        for number, scheduled_start in incoming_races.items():
            if scheduled_start and not (
                preserve_confirmed and existing_races.get(number)
            ):
                merged_races[number] = scheduled_start
            else:
                merged_races.setdefault(number, "")
        races_json = json.dumps(
            [
                {"number": number, "scheduled_start": merged_races[number]}
                for number in sorted(merged_races)
            ],
            separators=(",", ":"),
        )
        incoming_count = programme.get("race_count")
        race_count = existing.get("race_count")
        if incoming_count not in (None, "") and (
            race_count in (None, "") or accept_complete_revision
        ):
            race_count = int(incoming_count)
        incoming_first = str(programme.get("first_race_time") or "")
        incoming_last = str(programme.get("last_race_time") or "")
        first_race = (
            incoming_first
            if incoming_first and accept_complete_revision
            else str(existing.get("first_race_time") or "") or incoming_first
        )
        last_race = (
            incoming_last
            if incoming_last and accept_complete_revision
            else str(existing.get("last_race_time") or "") or incoming_last
        )
        if existing_lifecycle in {"complete", "historical"} and incoming_lifecycle != "complete":
            lifecycle = existing_lifecycle
        else:
            lifecycle = incoming_lifecycle
        checked = datetime.fromisoformat(checked_at)
        meeting_day = datetime.fromisoformat(str(existing["meeting_date"])).date()
        race_morning_confirmed_at = existing.get("race_morning_confirmed_at")
        post_meeting_checked_at = existing.get("post_meeting_checked_at")
        if lifecycle == "complete" and checked.date() == meeting_day and checked.hour >= 6:
            race_morning_confirmed_at = checked_at
        if checked.date() > meeting_day:
            lifecycle = "historical"
            post_meeting_checked_at = checked_at
        material_change = any(
            (
                str(existing.get("race_count") or "") != str(race_count or ""),
                str(existing.get("first_race_time") or "") != first_race,
                str(existing.get("last_race_time") or "") != last_race,
                str(existing.get("races_json") or "[]") != races_json,
            )
        )
        conn.execute(
            """
            UPDATE love_racing_meeting_details
            SET lifecycle_status = ?,
                fetch_status = 'ok',
                race_count = ?,
                race_count_last_confirmed_at = CASE
                    WHEN ? IS NOT NULL THEN ? ELSE race_count_last_confirmed_at END,
                first_race_time = ?,
                first_race_last_confirmed_at = CASE
                    WHEN ? <> '' THEN ? ELSE first_race_last_confirmed_at END,
                last_race_time = ?,
                last_race_last_confirmed_at = CASE
                    WHEN ? <> '' THEN ? ELSE last_race_last_confirmed_at END,
                races_json = ?,
                parser_diagnostics = ?,
                page_last_checked_at = ?,
                page_content_hash = ?,
                last_material_change_at = CASE WHEN ? THEN ? ELSE last_material_change_at END,
                failure_count = 0,
                last_failure_at = NULL,
                last_error_summary = NULL,
                next_retry_at = NULL,
                race_morning_confirmed_at = ?,
                post_meeting_checked_at = ?,
                updated_at = ?
            WHERE meeting_id = ?
            """,
            (
                lifecycle,
                race_count,
                incoming_count
                if existing.get("race_count") in (None, "") or accept_complete_revision
                else None,
                checked_at,
                first_race,
                incoming_first
                if not existing.get("first_race_time") or accept_complete_revision
                else "",
                checked_at,
                last_race,
                incoming_last
                if not existing.get("last_race_time") or accept_complete_revision
                else "",
                checked_at,
                races_json,
                json.dumps(list(programme.get("diagnostics") or []), separators=(",", ":")),
                checked_at,
                str(programme.get("content_hash") or ""),
                1 if material_change else 0,
                checked_at,
                race_morning_confirmed_at,
                post_meeting_checked_at,
                checked_at,
                meeting_id,
            ),
        )
    return {"updated": True, "material_change": material_change, "lifecycle_status": lifecycle}


def mark_love_racing_detail_fetch_failed(
    meeting_id: str,
    *,
    failed_at: str,
    error_summary: str,
    next_retry_at: str,
) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT failure_count FROM love_racing_meeting_details WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        if row is None:
            return 0
        failure_count = int(row["failure_count"] or 0) + 1
        conn.execute(
            """
            UPDATE love_racing_meeting_details
            SET fetch_status = 'failed',
                failure_count = ?,
                last_failure_at = ?,
                last_error_summary = ?,
                next_retry_at = ?,
                updated_at = ?
            WHERE meeting_id = ?
            """,
            (
                failure_count,
                failed_at,
                error_summary[:500],
                next_retry_at,
                failed_at,
                meeting_id,
            ),
        )
    return failure_count


def queue_love_racing_detail_job(
    meeting_id: str,
    *,
    reason: str,
    priority: int,
    requested_at: str,
) -> bool:
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM love_racing_detail_jobs
            WHERE meeting_id = ? AND status IN ('queued', 'fetching')
            """,
            (meeting_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE love_racing_detail_jobs
                SET priority = MAX(priority, ?),
                    requested_reason = CASE
                        WHEN requested_reason LIKE '%' || ? || '%' THEN requested_reason
                        ELSE TRIM(COALESCE(requested_reason, '') || ', ' || ?)
                    END
                WHERE id = ?
                """,
                (priority, reason, reason, int(existing["id"])),
            )
            return False
        conn.execute(
            """
            INSERT INTO love_racing_detail_jobs (
                meeting_id, requested_reason, priority, status,
                requested_at, next_attempt_at
            )
            VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (meeting_id, reason, priority, requested_at, requested_at),
        )
        conn.execute(
            "UPDATE love_racing_meeting_details SET fetch_status = 'queued' WHERE meeting_id = ?",
            (meeting_id,),
        )
    return True


def claim_love_racing_detail_jobs(now: str, limit: int = 3) -> list[dict[str, object]]:
    try:
        stale_before = (
            datetime.fromisoformat(now) - timedelta(minutes=30)
        ).isoformat(timespec="seconds")
    except ValueError:
        stale_before = now
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE love_racing_detail_jobs
            SET status = 'queued', started_at = NULL,
                last_error = 'Recovered after an interrupted fetch.'
            WHERE status = 'fetching'
              AND started_at IS NOT NULL
              AND started_at <= ?
            """,
            (stale_before,),
        )
        rows = conn.execute(
            """
            SELECT jobs.*, details.meeting_url, details.meeting_date,
                   details.canonical_venue_key, details.canonical_venue_label
            FROM love_racing_detail_jobs jobs
            JOIN love_racing_meeting_details details ON details.meeting_id = jobs.meeting_id
            WHERE jobs.status = 'queued'
              AND COALESCE(jobs.next_attempt_at, jobs.requested_at) <= ?
            ORDER BY jobs.priority DESC, jobs.requested_at, jobs.id
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE love_racing_detail_jobs
                SET status = 'fetching', started_at = ?, attempts = attempts + 1
                WHERE id = ?
                """,
                (now, int(row["id"])),
            )
            conn.execute(
                "UPDATE love_racing_meeting_details SET fetch_status = 'fetching' WHERE meeting_id = ?",
                (row["meeting_id"],),
            )
    return [dict(row) for row in rows]


def finish_love_racing_detail_job(
    job_id: int,
    *,
    status: str,
    completed_at: str,
    last_error: str = "",
    next_attempt_at: str = "",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE love_racing_detail_jobs
            SET status = ?, completed_at = ?, last_error = ?,
                next_attempt_at = NULLIF(?, '')
            WHERE id = ?
            """,
            (status, completed_at, last_error[:500], next_attempt_at, job_id),
        )


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


def _canonical_person_id_conn(conn: sqlite3.Connection, person_id: int | None) -> int | None:
    current = _optional_int(person_id)
    seen: set[int] = set()
    while current is not None and current not in seen:
        seen.add(current)
        row = conn.execute(
            "SELECT merged_into_person_id FROM crew_people WHERE id = ?",
            (current,),
        ).fetchone()
        if row is None:
            return None
        target = _optional_int(row["merged_into_person_id"])
        if target is None:
            return current
        current = target
    return None


def canonical_person_id(person_id: int | None) -> int | None:
    with get_connection() as conn:
        return _canonical_person_id_conn(conn, person_id)


def get_user_canonical_person_id(user_id: int) -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM crew_people
            WHERE app_user_id = ? AND is_active = 1 AND merged_into_person_id IS NULL
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return int(row["id"]) if row is not None else None


def fetch_self_travel_preferences_between(start_date: str, end_date: str) -> list[dict[str, object]]:
    with get_connection() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM user_event_transport_preferences
                WHERE event_date BETWEEN ? AND ? AND self_travel = 1
                ORDER BY event_date, user_id, id
                """,
                (start_date, end_date),
            ).fetchall()
        ]
        for row in rows:
            row["canonical_person_id"] = _canonical_person_id_conn(
                conn, _optional_int(row.get("canonical_person_id"))
            )
        return rows


def set_user_event_self_travel(
    *,
    user_id: int,
    canonical_person_id: int,
    event_kind: str,
    event_id: str,
    event_date: str,
    location_key: str,
    self_travel: bool,
    source: str = "user",
) -> bool:
    clean_kind = str(event_kind or "").strip().lower()
    clean_event_id = str(event_id or "").strip()
    if clean_kind not in {"deputy_shift", "manual_workday"} or not clean_event_id:
        return False
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        linked = conn.execute(
            """
            SELECT id FROM crew_people
            WHERE id = ? AND app_user_id = ? AND is_active = 1
              AND merged_into_person_id IS NULL
            """,
            (canonical_person_id, user_id),
        ).fetchone()
        if linked is None:
            return False
        existing = conn.execute(
            """
            SELECT * FROM user_event_transport_preferences
            WHERE user_id = ? AND event_kind = ? AND event_id = ?
            """,
            (user_id, clean_kind, clean_event_id),
        ).fetchone()
        old_value = int(existing["self_travel"] or 0) if existing is not None else 0
        new_value = 1 if self_travel else 0
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO user_event_transport_preferences (
                    user_id, canonical_person_id, event_kind, event_id,
                    event_date, location_key, self_travel, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, canonical_person_id, clean_kind, clean_event_id,
                    event_date, location_key, new_value, source, now, now,
                ),
            )
            preference_id = int(cursor.lastrowid)
        else:
            preference_id = int(existing["id"])
            conn.execute(
                """
                UPDATE user_event_transport_preferences
                SET canonical_person_id = ?, event_date = ?, location_key = ?,
                    self_travel = ?, source = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    canonical_person_id, event_date, location_key,
                    new_value, source, now, preference_id,
                ),
            )
        if old_value != new_value:
            conn.execute(
                """
                INSERT INTO user_event_transport_preference_audit (
                    preference_id, user_id, canonical_person_id, event_kind,
                    event_id, event_date, location_key, old_self_travel,
                    new_self_travel, source, changed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preference_id, user_id, canonical_person_id, clean_kind,
                    clean_event_id, event_date, location_key, old_value,
                    new_value, source, now,
                ),
            )
    return True


def _published_visibility_map_conn(conn: sqlite3.Connection) -> dict[int, dict[int, int | None]]:
    visibility: dict[int, dict[int, int | None]] = {}
    rows = conn.execute(
        "SELECT id, published_snapshot FROM roster_days WHERE TRIM(COALESCE(published_snapshot, '')) != ''"
    ).fetchall()
    for row in rows:
        try:
            snapshot = json.loads(str(row["published_snapshot"] or ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        day_users: dict[int, int | None] = {}
        for assignment in snapshot.get("assignments", []):
            if not isinstance(assignment, dict) or str(assignment.get("assignment_state") or "assigned") == "open":
                continue
            source_person_id = _optional_int(assignment.get("person_id"))
            person_id = _canonical_person_id_conn(conn, source_person_id)
            user_id = None
            if person_id is not None:
                person = conn.execute(
                    "SELECT app_user_id FROM crew_people WHERE id = ? AND is_active = 1",
                    (person_id,),
                ).fetchone()
                user_id = _optional_int(person["app_user_id"] if person is not None else None)
            if user_id is None and source_person_id is None:
                user_id = _optional_int(assignment.get("user_id"))
            if user_id is not None:
                active = conn.execute(
                    "SELECT 1 FROM app_users WHERE id = ? AND is_active = 1",
                    (user_id,),
                ).fetchone()
                if active is not None:
                    day_users[user_id] = person_id
        for hotel in snapshot.get("hotel_assignments", []):
            if not isinstance(hotel, dict):
                continue
            user_id = _optional_int(hotel.get("user_id"))
            if user_id is not None:
                person = conn.execute(
                    "SELECT id FROM crew_people WHERE app_user_id = ? AND is_active = 1 AND merged_into_person_id IS NULL",
                    (user_id,),
                ).fetchone()
                day_users[user_id] = _optional_int(person["id"] if person else None)
        visibility[int(row["id"])] = day_users
    return visibility


def _rebuild_workday_visibility_conn(conn: sqlite3.Connection) -> dict[str, int]:
    before = {
        (int(row["roster_day_id"]), int(row["user_id"]))
        for row in conn.execute("SELECT roster_day_id, user_id FROM workday_user_visibility").fetchall()
    }
    desired = _published_visibility_map_conn(conn)
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    conn.execute("DELETE FROM workday_user_visibility")
    for roster_day_id, users in desired.items():
        for user_id, person_id in users.items():
            conn.execute(
                """
                INSERT INTO workday_user_visibility (
                    roster_day_id, user_id, canonical_person_id, source, created_at, updated_at
                ) VALUES (?, ?, ?, 'canonical_assignment', ?, ?)
                """,
                (roster_day_id, user_id, person_id, now, now),
            )
    after = {(day_id, user_id) for day_id, users in desired.items() for user_id in users}
    changed_days = {day_id for day_id, _user_id in before.symmetric_difference(after)}
    return {
        "visibility_rows": len(after),
        "visibility_rows_added": len(after - before),
        "visibility_rows_removed": len(before - after),
        "published_workdays_repaired": len(changed_days),
    }


def rebuild_workday_visibility() -> dict[str, int]:
    with get_connection() as conn:
        return _rebuild_workday_visibility_conn(conn)


def _merge_crew_people_conn(
    conn: sqlite3.Connection,
    source_person_id: int,
    target_person_id: int,
    *,
    merged_by_user_id: int | None,
    reason: str,
) -> dict[str, int]:
    source_id = _canonical_person_id_conn(conn, source_person_id)
    target_id = _canonical_person_id_conn(conn, target_person_id)
    if source_id is None or target_id is None:
        raise ValueError("Crew identity was not found.")
    if source_id == target_id:
        return {"already_merged": 1}
    source = conn.execute("SELECT * FROM crew_people WHERE id = ?", (source_id,)).fetchone()
    target = conn.execute("SELECT * FROM crew_people WHERE id = ?", (target_id,)).fetchone()
    if source is None or target is None:
        raise ValueError("Crew identity was not found.")
    if source["deputy_employee_id"] is not None:
        raise ValueError("Only an account-only duplicate can be merged automatically.")
    source_user_id = _optional_int(source["app_user_id"])
    target_user_id = _optional_int(target["app_user_id"])
    if source_user_id is not None and target_user_id not in (None, source_user_id):
        raise ValueError("The canonical crew identity is already linked to a different app account.")
    counts = {
        "draft_assignments": int(conn.execute(
            "SELECT COUNT(*) n FROM workday_assignments a JOIN roster_days d ON d.id=a.roster_day_id WHERE a.person_id=? AND TRIM(COALESCE(d.published_snapshot,''))=''",
            (source_id,),
        ).fetchone()["n"]),
        "published_assignments": int(conn.execute(
            "SELECT COUNT(*) n FROM workday_assignments a JOIN roster_days d ON d.id=a.roster_day_id WHERE a.person_id=? AND TRIM(COALESCE(d.published_snapshot,''))!=''",
            (source_id,),
        ).fetchone()["n"]),
        "personal_evidence": int(conn.execute(
            "SELECT COUNT(*) n FROM deputy_personal_assignment_evidence WHERE canonical_person_id=?",
            (source_id,),
        ).fetchone()["n"]),
        "aliases": int(conn.execute(
            "SELECT COUNT(*) n FROM crew_aliases WHERE person_id=?",
            (source_id,),
        ).fetchone()["n"]),
    }
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    if source_user_id is not None:
        conn.execute("UPDATE crew_people SET app_user_id=NULL WHERE id=?", (source_id,))
        conn.execute("UPDATE crew_people SET app_user_id=?, updated_at=? WHERE id=?", (source_user_id, now, target_id))
    conn.execute("UPDATE workday_assignments SET person_id=?, user_id=COALESCE(?, user_id), updated_at=? WHERE person_id=?", (target_id, source_user_id, now, source_id))
    conn.execute("UPDATE deputy_personal_assignment_evidence SET canonical_person_id=? WHERE canonical_person_id=?", (target_id, source_id))
    conn.execute("UPDATE app_user_deputy_identity SET canonical_person_id=? WHERE canonical_person_id=?", (target_id, source_id))
    target_aliases = {
        str(row["normalized_alias"])
        for row in conn.execute("SELECT normalized_alias FROM crew_aliases WHERE person_id=?", (target_id,)).fetchall()
    }
    for alias in conn.execute("SELECT * FROM crew_aliases WHERE person_id=?", (source_id,)).fetchall():
        key = str(alias["normalized_alias"] or "")
        conflict = conn.execute("SELECT 1 FROM crew_aliases WHERE normalized_alias=? AND person_id NOT IN (?,?)", (key, source_id, target_id)).fetchone()
        if key and key not in target_aliases and conflict is None:
            conn.execute("UPDATE crew_aliases SET person_id=?, updated_at=? WHERE id=?", (target_id, now, int(alias["id"])))
            target_aliases.add(key)
        else:
            conn.execute("DELETE FROM crew_aliases WHERE id=?", (int(alias["id"]),))
    combined_note = "\n".join(filter(None, [str(target["admin_note"] or "").strip(), str(source["admin_note"] or "").strip()]))
    conn.execute(
        """
        UPDATE crew_people
        SET is_active=0, app_user_id=NULL, merged_into_person_id=?, merged_at=?,
            merged_by_user_id=?, merge_reason=?, updated_at=?
        WHERE id=?
        """,
        (target_id, now, merged_by_user_id, reason[:500], now, source_id),
    )
    if combined_note != str(target["admin_note"] or ""):
        conn.execute("UPDATE crew_people SET admin_note=?, updated_at=? WHERE id=?", (combined_note, now, target_id))
    counts.update(_rebuild_workday_visibility_conn(conn))
    conn.execute(
        """
        INSERT INTO crew_identity_merge_audit (
            source_person_id,target_person_id,app_user_id,merged_at,merged_by_user_id,
            merge_reason,affected_counts
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (source_id,target_id,source_user_id,now,merged_by_user_id,reason[:500],json.dumps(counts,sort_keys=True)),
    )
    return counts


def merge_crew_people(source_person_id: int, target_person_id: int, *, merged_by_user_id: int | None, reason: str) -> dict[str, int]:
    with get_connection() as conn:
        return _merge_crew_people_conn(conn, source_person_id, target_person_id, merged_by_user_id=merged_by_user_id, reason=reason)


def crew_link_change_preview(person_id: int, app_user_id: int) -> dict[str, object] | None:
    with get_connection() as conn:
        target_id = _canonical_person_id_conn(conn, person_id)
        if target_id is None:
            return None
        target = conn.execute("SELECT * FROM crew_people WHERE id=?", (target_id,)).fetchone()
        user = conn.execute("SELECT id,display_name,deputy_email FROM app_users WHERE id=?", (app_user_id,)).fetchone()
        source = conn.execute(
            "SELECT * FROM crew_people WHERE app_user_id=? AND merged_into_person_id IS NULL AND id!=?",
            (app_user_id, target_id),
        ).fetchone()
        if target is None or user is None or source is None:
            return None
        counts = {
            "draft_assignments": int(conn.execute(
                "SELECT COUNT(*) n FROM workday_assignments a JOIN roster_days d ON d.id=a.roster_day_id WHERE a.person_id=? AND TRIM(COALESCE(d.published_snapshot,''))=''",
                (int(source["id"]),),
            ).fetchone()["n"]),
            "published_assignments": int(conn.execute(
                "SELECT COUNT(*) n FROM workday_assignments a JOIN roster_days d ON d.id=a.roster_day_id WHERE a.person_id=? AND TRIM(COALESCE(d.published_snapshot,''))!=''",
                (int(source["id"]),),
            ).fetchone()["n"]),
            "visibility_records": int(conn.execute("SELECT COUNT(*) n FROM workday_user_visibility WHERE user_id=?", (app_user_id,)).fetchone()["n"]),
            "aliases": int(conn.execute("SELECT COUNT(*) n FROM crew_aliases WHERE person_id=?", (int(source["id"]),)).fetchone()["n"]),
            "personal_evidence": int(conn.execute("SELECT COUNT(*) n FROM deputy_personal_assignment_evidence WHERE canonical_person_id=?", (int(source["id"]),)).fetchone()["n"]),
        }
        return {
            "app_user_id": app_user_id,
            "app_user_name": str(user["display_name"] or user["deputy_email"] or "App user"),
            "source_person_id": int(source["id"]),
            "source_name": str(source["canonical_display_name"] or "Crew identity"),
            "source_is_account_only": source["deputy_employee_id"] is None,
            "target_person_id": target_id,
            "target_name": str(target["canonical_display_name"] or "Crew identity"),
            "target_employee_id": _optional_int(target["deputy_employee_id"]),
            "counts": counts,
        }


def transfer_app_user_link(app_user_id: int, target_person_id: int) -> dict[str, int]:
    with get_connection() as conn:
        target_id = _canonical_person_id_conn(conn, target_person_id)
        if target_id is None:
            raise ValueError("Crew identity was not found.")
        target = conn.execute("SELECT app_user_id FROM crew_people WHERE id=?", (target_id,)).fetchone()
        if target is None or target["app_user_id"] not in (None, app_user_id):
            raise ValueError("The proposed crew identity is linked to another account.")
        now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
        conn.execute("UPDATE crew_people SET app_user_id=NULL,updated_at=? WHERE app_user_id=? AND id!=?", (now, app_user_id, target_id))
        conn.execute("UPDATE crew_people SET app_user_id=?,updated_at=? WHERE id=?", (app_user_id, now, target_id))
        conn.execute("UPDATE workday_assignments SET user_id=NULL,updated_at=? WHERE user_id=? AND person_id!=?", (now, app_user_id, target_id))
        conn.execute("UPDATE workday_assignments SET user_id=?,updated_at=? WHERE person_id=?", (app_user_id, now, target_id))
        return _rebuild_workday_visibility_conn(conn)


def _identity_evidence_rows_conn(conn: sqlite3.Connection) -> list[dict[str, object]]:
    users = conn.execute("SELECT id,display_name,deputy_email FROM app_users WHERE is_active=1 ORDER BY id").fetchall()
    result: list[dict[str, object]] = []
    for user in users:
        evidence = conn.execute(
            """
            SELECT deputy_employee_id, COUNT(*) AS evidence_rows, MIN(first_seen_at) AS first_seen,
                   MAX(last_confirmed_at) AS last_seen
            FROM deputy_personal_assignment_evidence
            WHERE owner_user_id=? AND deputy_employee_id IS NOT NULL
              AND status IN ('confirmed','possibly_missing','historical_locked')
            GROUP BY deputy_employee_id ORDER BY evidence_rows DESC, deputy_employee_id
            """,
            (int(user["id"]),),
        ).fetchall()
        current = conn.execute(
            "SELECT * FROM crew_people WHERE app_user_id=? AND merged_into_person_id IS NULL LIMIT 1",
            (int(user["id"]),),
        ).fetchone()
        item = dict(user)
        item["current_person_id"] = _optional_int(current["id"] if current else None)
        item["current_person_name"] = str(current["canonical_display_name"] if current else "")
        item["evidence_ids"] = [int(row["deputy_employee_id"]) for row in evidence]
        employee_id = item["evidence_ids"][0] if len(item["evidence_ids"]) == 1 else None
        target = conn.execute("SELECT * FROM crew_people WHERE deputy_employee_id=? LIMIT 1", (employee_id,)).fetchone() if employee_id is not None else None
        item["deputy_employee_id"] = employee_id
        item["recommended_person_id"] = _optional_int(target["id"] if target else None)
        item["recommended_person_name"] = str(target["canonical_display_name"] if target else "")
        item["first_confirmed_at"] = str(evidence[0]["first_seen"] if len(evidence) == 1 else "")
        item["last_confirmed_at"] = str(evidence[0]["last_seen"] if len(evidence) == 1 else "")
        if not evidence:
            item["status"] = "no_evidence"
        elif len(evidence) > 1:
            item["status"] = "conflicting"
        elif target is None:
            item["status"] = "ambiguous"
        elif current is not None and int(current["id"]) == int(target["id"]):
            item["status"] = "correct"
        elif target["app_user_id"] not in (None, int(user["id"])):
            item["status"] = "conflicting"
        else:
            item["status"] = "repair_available"
        result.append(item)
    return result


def _reconcile_authenticated_identities_conn(conn: sqlite3.Connection, *, apply: bool, trigger_source: str, actor_user_id: int | None = None) -> dict[str, object]:
    _sync_crew_directory(conn)
    visibility_before = {
        (int(row["roster_day_id"]), int(row["user_id"]))
        for row in conn.execute("SELECT roster_day_id,user_id FROM workday_user_visibility").fetchall()
    } if apply else set()
    rows = _identity_evidence_rows_conn(conn)
    report: dict[str, object] = {
        "app_users_inspected": len(rows), "correct_links_retained": 0,
        "duplicate_identities_merged": 0, "links_repaired": 0,
        "published_workdays_repaired": 0, "visibility_rows_added": 0,
        "ambiguous_accounts": 0, "conflicting_accounts": 0,
    }
    for item in rows:
        status = str(item["status"])
        if status == "correct":
            report["correct_links_retained"] = int(report["correct_links_retained"]) + 1
        elif status == "ambiguous" or status == "no_evidence":
            report["ambiguous_accounts"] = int(report["ambiguous_accounts"]) + 1
        elif status == "conflicting":
            report["conflicting_accounts"] = int(report["conflicting_accounts"]) + 1
        if not apply or status not in {"correct", "repair_available"}:
            continue
        user_id = int(item["id"])
        employee_id = _optional_int(item["deputy_employee_id"])
        target_id = _optional_int(item["recommended_person_id"])
        current_id = _optional_int(item["current_person_id"])
        if employee_id is None or target_id is None:
            continue
        if status == "repair_available":
            if current_id is not None and current_id != target_id:
                current = conn.execute("SELECT deputy_employee_id FROM crew_people WHERE id=?", (current_id,)).fetchone()
                if current is None or current["deputy_employee_id"] is not None:
                    report["conflicting_accounts"] = int(report["conflicting_accounts"]) + 1
                    continue
                _merge_crew_people_conn(
                    conn, current_id, target_id, merged_by_user_id=actor_user_id,
                    reason="Authenticated personal Deputy capture confirmed the canonical employee identity.",
                )
                report["duplicate_identities_merged"] = int(report["duplicate_identities_merged"]) + 1
                report["links_repaired"] = int(report["links_repaired"]) + 1
            else:
                now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
                conn.execute("UPDATE crew_people SET app_user_id=?,updated_at=? WHERE id=?", (user_id, now, target_id))
                report["links_repaired"] = int(report["links_repaired"]) + 1
        capture = conn.execute(
            "SELECT id FROM deputy_web_captures WHERE owner_user_id=? ORDER BY captured_at DESC,id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO app_user_deputy_identity (
                app_user_id,deputy_employee_id,canonical_person_id,first_confirmed_at,
                last_confirmed_at,evidence_capture_id,confidence_source,status,conflict_details,updated_at
            ) VALUES (?,?,?,?,?,?,'authenticated_personal_capture','confirmed','',?)
            ON CONFLICT(app_user_id) DO UPDATE SET deputy_employee_id=excluded.deputy_employee_id,
                canonical_person_id=excluded.canonical_person_id,
                first_confirmed_at=COALESCE(app_user_deputy_identity.first_confirmed_at,excluded.first_confirmed_at),
                last_confirmed_at=excluded.last_confirmed_at,evidence_capture_id=excluded.evidence_capture_id,
                confidence_source=excluded.confidence_source,status='confirmed',conflict_details='',updated_at=excluded.updated_at
            """,
            (user_id,employee_id,target_id,item.get("first_confirmed_at") or now,item.get("last_confirmed_at") or now,_optional_int(capture["id"] if capture else None),now),
        )
    visibility = _rebuild_workday_visibility_conn(conn) if apply else {"visibility_rows": 0}
    visibility_after = {
        (int(row["roster_day_id"]), int(row["user_id"]))
        for row in conn.execute("SELECT roster_day_id,user_id FROM workday_user_visibility").fetchall()
    } if apply else set()
    report["visibility_rows"] = visibility.get("visibility_rows", 0)
    report["visibility_rows_added"] = len(visibility_after - visibility_before)
    report["published_workdays_repaired"] = len({
        day_id for day_id, _user_id in visibility_before.symmetric_difference(visibility_after)
    })
    report["orphan_synthetic_identities"] = int(conn.execute(
        """
        SELECT COUNT(*) n FROM crew_people p
        WHERE p.is_active=1 AND p.deputy_employee_id IS NULL AND p.app_user_id IS NULL
          AND p.merged_into_person_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM workday_assignments a WHERE a.person_id=p.id)
        """
    ).fetchone()["n"])
    report["accounts"] = rows
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    conn.execute("INSERT INTO identity_reconciliation_runs(run_at,applied,trigger_source,report) VALUES (?,?,?,?)", (now,1 if apply else 0,trigger_source,json.dumps(report,default=str)))
    return report


def reconcile_authenticated_identities(*, apply: bool, trigger_source: str = "admin", actor_user_id: int | None = None) -> dict[str, object]:
    with get_connection() as conn:
        return _reconcile_authenticated_identities_conn(conn, apply=apply, trigger_source=trigger_source, actor_user_id=actor_user_id)


def identity_link_diagnostics() -> list[dict[str, object]]:
    with get_connection() as conn:
        _sync_crew_directory(conn)
        return _identity_evidence_rows_conn(conn)


def is_placeholder_crew_name(value: object) -> bool:
    key = normalise_person_identity(value)
    return key in {"tbctbc", "tbc2tbc2"}


def list_crew_people(*, include_merged: bool = False, include_placeholders: bool = False) -> list[dict[str, object]]:
    refresh_crew_directory()
    with get_connection() as conn:
        people = [dict(row) for row in conn.execute(
            """
            SELECT p.*, u.display_name AS app_user_name, u.deputy_email AS app_user_email
            FROM crew_people p
            LEFT JOIN app_users u ON u.id = p.app_user_id
            WHERE (? = 1 OR p.merged_into_person_id IS NULL)
            ORDER BY (p.merged_into_person_id IS NOT NULL), p.is_active DESC, LOWER(p.canonical_display_name), p.id
            """
            , (1 if include_merged else 0,)
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
    if not include_placeholders:
        people = [person for person in people if not is_placeholder_crew_name(person.get("canonical_display_name"))]
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
        _rebuild_workday_visibility_conn(conn)
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
                   m.personal_start_time, m.personal_finish_time,
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
                   m.personal_start_time, m.personal_finish_time,
                   m.updated_at AS marks_updated_at
            FROM shifts s
            LEFT JOIN shift_marks m ON m.shift_id = s.id
            WHERE s.id = ?
              {owner_sql}
            """,
            params,
        ).fetchone()
    return row


def fetch_user_time_overrides_between(user_id: int, start_date: str, end_date: str) -> list[dict[str, object]]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM user_event_time_overrides WHERE user_id=? AND event_date BETWEEN ? AND ?", (user_id, start_date, end_date)).fetchall()]


def set_user_event_personal_time(*, user_id: int, canonical_person_id: int, event_kind: str, event_id: str, event_date: str, personal_start_time: str, personal_finish_time: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        if not personal_start_time and not personal_finish_time:
            conn.execute("DELETE FROM user_event_time_overrides WHERE user_id=? AND event_kind=? AND event_id=?", (user_id, event_kind, event_id))
            return
        conn.execute("""INSERT INTO user_event_time_overrides(user_id,canonical_person_id,event_kind,event_id,event_date,personal_start_time,personal_finish_time,updated_at)
                      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,event_kind,event_id) DO UPDATE SET canonical_person_id=excluded.canonical_person_id,
                      event_date=excluded.event_date,personal_start_time=excluded.personal_start_time,personal_finish_time=excluded.personal_finish_time,updated_at=excluded.updated_at""",
                     (user_id, canonical_person_id, event_kind, event_id, event_date, personal_start_time, personal_finish_time, now))


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
                personal_start_time = ?,
                personal_finish_time = ?,
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
                values.get("personal_start_time", ""),
                values.get("personal_finish_time", ""),
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
                   COALESCE(s.area_location_id, a.location_id) AS schedule_location_id,
                   COALESCE(locations.name, '') AS location_name,
                   (
                     SELECT GROUP_CONCAT(observer_key || char(31) || last_seen_at, char(30))
                     FROM deputy_schedule_observations observation
                     WHERE observation.source_shift_id = s.source_shift_id
                       AND observation.active = 1
                       AND observation.observer_key LIKE '%:native_get_rosters'
                   ) AS native_observation_contexts
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a ON a.area_id = s.area_id
            LEFT JOIN deputy_schedule_locations locations
              ON locations.location_id = COALESCE(s.area_location_id, a.location_id)
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
                SUM(CASE WHEN is_open = 1 AND employee_id IS NULL AND TRIM(COALESCE(employee_name, '')) = '' THEN 1 ELSE 0 END) AS open_rows,
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
            SELECT s.*,COALESCE(s.area_location_id,a.location_id) schedule_location_id,
                   l.name location_name
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a ON a.area_id=s.area_id
            LEFT JOIN deputy_schedule_locations l ON l.location_id=COALESCE(s.area_location_id,a.location_id)
            WHERE s.is_open = 1
              AND s.employee_id IS NULL
              AND TRIM(COALESCE(s.employee_name, '')) = ''
            ORDER BY
                s.date ASC,
                s.start_at ASC,
                COALESCE(s.area_roster_sort_order, 999999),
                s.area_name ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def fetch_open_deputy_schedule_between(start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.*,COALESCE(s.area_location_id,a.location_id) schedule_location_id,
                   l.name location_name
            FROM deputy_schedule_shifts s
            LEFT JOIN deputy_schedule_areas a ON a.area_id=s.area_id
            LEFT JOIN deputy_schedule_locations l ON l.location_id=COALESCE(s.area_location_id,a.location_id)
            WHERE s.date BETWEEN ? AND ?
              AND s.is_open = 1
              AND s.employee_id IS NULL
              AND TRIM(COALESCE(s.employee_name, '')) = ''
            ORDER BY
                s.date ASC,
                s.start_at ASC,
                COALESCE(s.area_roster_sort_order, 999999),
                s.area_name ASC
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
                   l.name AS location_name,
                   (
                     SELECT GROUP_CONCAT(observer_key || char(31) || last_seen_at, char(30))
                     FROM deputy_schedule_observations observation
                     WHERE observation.source_shift_id = s.source_shift_id
                       AND observation.active = 1
                       AND observation.observer_key LIKE '%:native_get_rosters'
                   ) AS native_observation_contexts
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


def get_recent_source_payloads(limit: int = 6, *, owner_user_id: int | None = None) -> list[sqlite3.Row]:
    with get_connection() as conn:
        owner_clause = "AND owner_user_id = ?" if owner_user_id is not None else ""
        params: tuple[object, ...] = (owner_user_id, limit) if owner_user_id is not None else (limit,)
        rows = conn.execute(
            f"""
            SELECT *
            FROM shifts
            WHERE source_payload IS NOT NULL
              AND source_payload != ''
              {owner_clause}
            ORDER BY start_at DESC
            LIMIT ?
            """,
            params,
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
    "vehicle", "vehicles", "travel", "overnighter", "travelthenovernighter", "outofregion",
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
            LEFT JOIN crew_aliases a ON a.person_id = p.id
            WHERE p.is_active = 1
              AND (
                REPLACE(REPLACE(LOWER(p.canonical_display_name),'-',''),' ','') = ?
                OR a.normalized_alias = ?
              )
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
        excluded_location_ids = {
            value
            for value in (_optional_int(item) for item in coverage.get("excluded_location_ids") or [])
            if value is not None
        }
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date)
            or mode not in {"all", "selected"}
            or (mode == "selected" and not location_ids)
        ):
            continue
        result.append({
            "start_date": start_date, "end_date": end_date, "mode": mode,
            "location_ids": location_ids, "excluded_location_ids": excluded_location_ids,
        })
    return result


def _known_travel_family_location_ids(conn: sqlite3.Connection) -> set[int]:
    """Return locally evidenced Travel participant locations without guessing IDs."""
    result = set()
    rows = conn.execute(
        """
        SELECT a.name, a.location_id
        FROM deputy_schedule_areas a
        UNION ALL
        SELECT s.area_name, COALESCE(s.area_location_id, a.location_id)
        FROM deputy_schedule_shifts s
        LEFT JOIN deputy_schedule_areas a ON a.area_id = s.area_id
        """
    ).fetchall()
    for row in rows:
        location_id = _optional_int(row["location_id"])
        if location_id is not None and is_travel_participant_cohort(row["name"]):
            result.add(location_id)
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
        elif coverage["excluded_location_ids"]:
            placeholders = ", ".join("?" for _ in coverage["excluded_location_ids"])
            location_sql = f"AND COALESCE(s.area_location_id, a.location_id) NOT IN ({placeholders})"
            params.extend(sorted(coverage["excluded_location_ids"]))
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
            "is_open": bool(int(row.get("is_open") or 0)) and employee_id is None and not employee_name,
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
        "initial_population": bool(
            old_person is not None
            and not (old_person or {}).get("employee_id")
            and not str((old_person or {}).get("employee_name") or "").strip()
            and not bool((old_person or {}).get("is_open"))
            and new_person is not None
        ),
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
                        0 if _event_lock_row(conn, date_text, location_id) is not None or change.get("initial_population") else 1,
                        (
                            "historical_discrepancy"
                            if _event_lock_row(conn, date_text, location_id) is not None
                            else "initial_population"
                            if change.get("initial_population")
                            else "assignment_change"
                        ),
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
            "is_open": deputy_shift_is_available(row),
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
        WHERE e.status IN ('confirmed', 'possibly_missing')
          AND e.production_position = 1
          AND e.date >= ?
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
        personal_positions = {str(row["position_key"]) for row in evidence_rows}
        previous_named = {
            str(item["position_key"]) for item in before_snapshots.get((date_text, location_id), [])
            if item.get("identity") != "open"
        }
        # A venue's Area catalogue is not an event roster: roles vary by event
        # and using every known Area creates false gaps. Current event rows and
        # personal evidence establish expectations; prior same-event rows are
        # checked separately below so an exact complete retry can prove removal.
        expected_positions = {
            str(item["position_key"]) for item in incoming
            if str(item["position_key"]) in CORE_EVENT_POSITION_KEYS
        } | personal_positions
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
    owner_user_id: int | None,
    partial_scopes: set[tuple[str, int]] | None = None,
) -> int:
    coverage_rows = payload.get("schedule_coverage")
    if not isinstance(coverage_rows, list):
        return 0

    remove_ids: set[int] = set()
    observer_key = (
        f"user:{owner_user_id}:direct_schedule"
        if owner_user_id is not None else "system:direct_schedule"
    )
    partial_scopes = partial_scopes or set()
    known_travel_location_ids = _known_travel_family_location_ids(conn)
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
        excluded_location_ids = {
            value
            for value in (_optional_int(item) for item in coverage.get("excluded_location_ids") or [])
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
            if (
                mode == "all"
                and _optional_int(row["schedule_location_id"])
                in known_travel_location_ids | excluded_location_ids
            ):
                continue
            # Absence is evidence only for the account that made this capture. A
            # different account may legitimately see a different slice of the
            # shared schedule, so it must never retire somebody else's evidence.
            observed_here = conn.execute(
                "SELECT 1 FROM deputy_schedule_observations WHERE source_shift_id=? AND observer_key=?",
                (source_shift_id, observer_key),
            ).fetchone()
            if observed_here is None:
                continue
            conn.execute(
                """
                UPDATE deputy_schedule_observations
                SET active=0,last_absent_at=?
                WHERE source_shift_id=? AND observer_key=?
                """,
                (str(payload.get("captured_at") or datetime.utcnow().isoformat()), source_shift_id, observer_key),
            )
            if conn.execute(
                "SELECT 1 FROM deputy_schedule_observations WHERE source_shift_id=? AND active=1 LIMIT 1",
                (source_shift_id,),
            ).fetchone() is None:
                remove_ids.add(source_shift_id)

    if remove_ids:
        conn.executemany(
            "DELETE FROM deputy_schedule_shifts WHERE source_shift_id = ?",
            ((source_shift_id,) for source_shift_id in sorted(remove_ids)),
        )
    return len(remove_ids)


def _migrate_legacy_schedule_observations(conn: sqlite3.Connection) -> None:
    """Move pre-source-aware shared observations into the direct evidence channel."""
    rows = conn.execute(
        """SELECT * FROM deputy_schedule_observations
           WHERE observer_key='system'
              OR (observer_key GLOB 'user:[0-9]*' AND instr(substr(observer_key, 6), ':')=0)"""
    ).fetchall()
    for row in rows:
        legacy_key = str(row["observer_key"])
        destination = f"{legacy_key}:direct_schedule"
        existing = conn.execute(
            "SELECT * FROM deputy_schedule_observations WHERE source_shift_id=? AND observer_key=?",
            (row["source_shift_id"], destination),
        ).fetchone()
        first_seen = min(str(row["first_seen_at"]), str(existing["first_seen_at"])) if existing else str(row["first_seen_at"])
        last_seen = max(str(row["last_seen_at"]), str(existing["last_seen_at"])) if existing else str(row["last_seen_at"])
        absent_values = [str(value) for value in (row["last_absent_at"], existing["last_absent_at"] if existing else None) if value]
        latest_absent = max(absent_values) if absent_values else None
        newest = row if not existing or str(row["last_seen_at"]) >= str(existing["last_seen_at"]) else existing
        # A later absence is the current state; a same-time positive sighting wins.
        active = (0 if latest_absent > last_seen else 1) if latest_absent else int(newest["active"])
        last_absent = latest_absent if not active else None
        conn.execute(
            """INSERT INTO deputy_schedule_observations
               (source_shift_id,observer_key,observer_user_id,first_seen_at,last_seen_at,active,last_absent_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(source_shift_id,observer_key) DO UPDATE SET
                 observer_user_id=excluded.observer_user_id,first_seen_at=excluded.first_seen_at,
                 last_seen_at=excluded.last_seen_at,active=excluded.active,last_absent_at=excluded.last_absent_at""",
            (row["source_shift_id"], destination, row["observer_user_id"], first_seen, last_seen, active, last_absent),
        )
        conn.execute(
            "DELETE FROM deputy_schedule_observations WHERE source_shift_id=? AND observer_key=?",
            (row["source_shift_id"], legacy_key),
        )


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
    native_schedule_shift_ids = {int(value) for value in payload.get("native_schedule_shift_ids") or [] if str(value).isdigit()}
    direct_schedule_shift_ids = {int(value) for value in payload.get("direct_schedule_shift_ids") or [] if str(value).isdigit()}
    if "native_schedule_shift_ids" not in payload and "direct_schedule_shift_ids" not in payload:
        direct_schedule_shift_ids = {int(shift_id) for shift_id in schedule_shift_lookup if str(shift_id).isdigit()}

    with get_connection() as conn:
        _migrate_legacy_schedule_observations(conn)
        lock_completed_events(conn)
        authoritative_coverage = _authoritative_schedule_coverage(payload)
        known_travel_location_ids = _known_travel_family_location_ids(conn)
        for coverage in authoritative_coverage:
            if coverage["mode"] == "all":
                coverage["excluded_location_ids"].update(known_travel_location_ids)
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
            sources = set()
            if source_shift_id in native_schedule_shift_ids:
                sources.add("native_get_rosters")
            if source_shift_id in direct_schedule_shift_ids:
                sources.add("direct_schedule")
            for source in sources:
                observer_key = (
                    f"user:{owner_user_id}:{source}"
                    if owner_user_id is not None else f"system:{source}"
                )
                conn.execute(
                    """
                    INSERT INTO deputy_schedule_observations (
                        source_shift_id,observer_key,observer_user_id,first_seen_at,last_seen_at,active,last_absent_at
                    ) VALUES (?,?,?,?,?,1,NULL)
                    ON CONFLICT(source_shift_id,observer_key) DO UPDATE SET
                        observer_user_id=excluded.observer_user_id,
                        last_seen_at=excluded.last_seen_at,
                        active=1,
                        last_absent_at=NULL
                    """,
                    (source_shift_id, observer_key, owner_user_id, captured_at, captured_at),
                )
                if source == "direct_schedule":
                    legacy_key = f"user:{owner_user_id}" if owner_user_id is not None else "system"
                    conn.execute(
                        "UPDATE deputy_schedule_observations SET active=0,last_absent_at=? WHERE source_shift_id=? AND observer_key=?",
                        (captured_at, source_shift_id, legacy_key),
                    )
            employee_id = _optional_int(shift.get("employee"))
            employee_name = str(shift.get("employeeName") or "").strip()
            if employee_id is not None and employee_name:
                normalized_name = normalise_person_identity(employee_name)
                conn.execute(
                    """
                    INSERT INTO deputy_employee_name_history (
                        deputy_employee_id,observed_name,normalized_name,first_seen_at,last_seen_at,observation_count
                    ) VALUES (?,?,?,?,?,1)
                    ON CONFLICT(deputy_employee_id,normalized_name) DO UPDATE SET
                        observed_name=excluded.observed_name,
                        last_seen_at=excluded.last_seen_at,
                        observation_count=deputy_employee_name_history.observation_count+1
                    """,
                    (employee_id, employee_name, normalized_name, captured_at, captured_at),
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
                for shift_id in direct_schedule_shift_ids
            },
            owner_user_id,
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
        identity_report = _reconcile_authenticated_identities_conn(
            conn,
            apply=True,
            trigger_source="successful_personal_capture",
        ) if owner_user_id is not None and own_counts["seen"] else {}
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
        "identity_links_repaired": int(identity_report.get("links_repaired", 0))
        + int(identity_report.get("duplicate_identities_merged", 0)),
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
            """SELECT * FROM track_maps
               WHERE track_key = ?
                 AND (status = 'ok' OR TRIM(COALESCE(manual_file_name, '')) != '')""",
            (track_key,),
        ).fetchone()


def list_track_maps() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM track_maps ORDER BY track_label, track_key"
        ).fetchall()


def set_track_map_manual_override(
    *,
    track_key: str,
    track_label: str,
    file_name: str,
    content_type: str,
    image_hash: str,
    image_width: int,
    image_height: int,
    byte_size: int,
    updated_at: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO track_maps (
                track_key, track_label, course_label, status, checked_at, updated_at,
                manual_file_name, manual_content_type, manual_image_hash,
                manual_image_width, manual_image_height, manual_byte_size, manual_updated_at
            ) VALUES (?, ?, ?, 'manual', '', '', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_key) DO UPDATE SET
                track_label = CASE
                    WHEN TRIM(COALESCE(track_maps.track_label, '')) = '' THEN excluded.track_label
                    ELSE track_maps.track_label
                END,
                course_label = CASE
                    WHEN TRIM(COALESCE(track_maps.course_label, '')) = '' THEN excluded.course_label
                    ELSE track_maps.course_label
                END,
                manual_file_name = excluded.manual_file_name,
                manual_content_type = excluded.manual_content_type,
                manual_image_hash = excluded.manual_image_hash,
                manual_image_width = excluded.manual_image_width,
                manual_image_height = excluded.manual_image_height,
                manual_byte_size = excluded.manual_byte_size,
                manual_updated_at = excluded.manual_updated_at
            """,
            (
                track_key, track_label, track_label, file_name, content_type, image_hash,
                max(0, int(image_width or 0)), max(0, int(image_height or 0)),
                max(0, int(byte_size or 0)), updated_at,
            ),
        )


def clear_track_map_manual_override(track_key: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT manual_file_name FROM track_maps WHERE track_key = ?",
            (track_key,),
        ).fetchone()
        if row is None:
            return ""
        conn.execute(
            """
            UPDATE track_maps
            SET manual_file_name = NULL,
                manual_content_type = NULL,
                manual_image_hash = NULL,
                manual_image_width = NULL,
                manual_image_height = NULL,
                manual_byte_size = NULL,
                manual_updated_at = NULL,
                status = CASE WHEN TRIM(COALESCE(file_name, '')) != '' THEN status ELSE 'unavailable' END
            WHERE track_key = ?
            """,
            (track_key,),
        )
        return str(row["manual_file_name"] or "")


def migrate_track_map_alias_overrides(
    mappings: list[dict[str, str]],
    migrated_at: str | None = None,
) -> dict[str, int]:
    """Move active alias overrides onto canonical rows without deleting cached files."""
    migrated_at = migrated_at or datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    adopted = 0
    conflicts = 0
    deduplicated = 0
    with get_connection() as conn:
        for mapping in sorted(mappings, key=lambda item: (item["canonical_key"], item["alias_key"])):
            alias_key = str(mapping.get("alias_key") or "").strip()
            canonical_key = str(mapping.get("canonical_key") or "").strip()
            if not alias_key or not canonical_key or alias_key == canonical_key:
                continue
            alias = conn.execute("SELECT * FROM track_maps WHERE track_key = ?", (alias_key,)).fetchone()
            if alias is None or not str(alias["manual_file_name"] or "").strip():
                continue
            canonical = conn.execute("SELECT * FROM track_maps WHERE track_key = ?", (canonical_key,)).fetchone()
            canonical_hash = str(canonical["manual_image_hash"] or "") if canonical is not None else ""
            alias_hash = str(alias["manual_image_hash"] or "")
            warning_type = ""
            note = ""
            if canonical is None or not str(canonical["manual_file_name"] or "").strip():
                conn.execute(
                    """
                    INSERT INTO track_maps (
                        track_key, track_label, course_label, status, checked_at, updated_at,
                        manual_file_name, manual_content_type, manual_image_hash,
                        manual_image_width, manual_image_height, manual_byte_size, manual_updated_at
                    ) VALUES (?, ?, ?, 'manual', '', '', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(track_key) DO UPDATE SET
                        manual_file_name = excluded.manual_file_name,
                        manual_content_type = excluded.manual_content_type,
                        manual_image_hash = excluded.manual_image_hash,
                        manual_image_width = excluded.manual_image_width,
                        manual_image_height = excluded.manual_image_height,
                        manual_byte_size = excluded.manual_byte_size,
                        manual_updated_at = excluded.manual_updated_at
                    """,
                    (
                        canonical_key,
                        mapping.get("canonical_label") or alias["track_label"] or canonical_key,
                        mapping.get("canonical_label") or alias["course_label"] or canonical_key,
                        alias["manual_file_name"], alias["manual_content_type"], alias["manual_image_hash"],
                        alias["manual_image_width"], alias["manual_image_height"], alias["manual_byte_size"],
                        alias["manual_updated_at"] or migrated_at,
                    ),
                )
                adopted += 1
            elif canonical_hash and canonical_hash == alias_hash:
                warning_type = "duplicate_alias_upload"
                note = "The alias upload matched the canonical manual image; the cached alias file was retained."
                deduplicated += 1
            else:
                warning_type = "conflicting_alias_upload"
                note = "The canonical manual image was kept. The different alias file remains on disk for recovery."
                conflicts += 1
            if warning_type:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO track_map_migration_warnings (
                        alias_key, alias_label, canonical_venue_key, canonical_venue_label,
                        retained_file_name, image_hash, warning_type, note, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alias_key, mapping.get("alias_label") or alias["track_label"],
                        canonical_key, mapping.get("canonical_label"), alias["manual_file_name"],
                        alias_hash, warning_type, note, migrated_at,
                    ),
                )
            conn.execute(
                """
                UPDATE track_maps
                SET manual_file_name = NULL,
                    manual_content_type = NULL,
                    manual_image_hash = NULL,
                    manual_image_width = NULL,
                    manual_image_height = NULL,
                    manual_byte_size = NULL,
                    manual_updated_at = NULL,
                    status = CASE WHEN TRIM(COALESCE(file_name, '')) != '' THEN status ELSE 'unavailable' END
                WHERE track_key = ?
                """,
                (alias_key,),
            )
    return {"adopted": adopted, "conflicts": conflicts, "deduplicated": deduplicated}


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
    raw_role_label = str(normalised.get("role_label") or normalised.get("area_name") or "").strip()
    classification = classify_deputy_evidence(
        raw_role_label,
        production_keys=CORE_EVENT_POSITION_KEYS,
        production_aliases=EVENT_POSITION_ALIASES,
    )
    location_id = _optional_int(normalised.get("area_location_id"))
    employee_id = _optional_int(normalised.get("employee_id"))
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
        "raw_role_label": classification.raw_label,
        "evidence_type": classification.evidence_type,
    })
    conn.execute(
        """
        INSERT INTO deputy_personal_assignment_evidence (
            owner_user_id, deputy_employee_id, canonical_person_id,
            source_shift_uid, source_shift_id, date, area_location_id,
            position_key, position_label, raw_role_label, evidence_type,
            production_position, participant_evidence, cohort_type, start_at, end_at,
            first_seen_at, last_seen_at, last_confirmed_at,
            missing_capture_count, status, provenance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(owner_user_id, source_shift_uid) DO UPDATE SET
            deputy_employee_id = excluded.deputy_employee_id,
            canonical_person_id = excluded.canonical_person_id,
            source_shift_id = excluded.source_shift_id,
            date = excluded.date,
            area_location_id = excluded.area_location_id,
            position_key = excluded.position_key,
            position_label = excluded.position_label,
            raw_role_label = excluded.raw_role_label,
            evidence_type = excluded.evidence_type,
            production_position = excluded.production_position,
            participant_evidence = excluded.participant_evidence,
            cohort_type = excluded.cohort_type,
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
            values["date"], location_id, classification.role_key, classification.role_label,
            classification.raw_label, classification.evidence_type,
            1 if classification.production_position else 0,
            1 if classification.participant_evidence else 0,
            classification.cohort_type, values["start_at"], values["end_at"],
            captured_at, captured_at, captured_at, evidence_status, provenance,
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
                   COALESCE(p.canonical_display_name, u.display_name) AS employee_name,
                   l.name AS location_name
            FROM deputy_personal_assignment_evidence e
            JOIN app_users u ON u.id = e.owner_user_id AND u.is_active = 1
            LEFT JOIN crew_people p ON p.id = e.canonical_person_id
            LEFT JOIN deputy_schedule_locations l ON l.location_id = e.area_location_id
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
    elif (
        shift.get("isOpen")
        and _optional_int(shift.get("employee")) is None
        and not str(shift.get("employeeName") or "").strip()
    ):
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
