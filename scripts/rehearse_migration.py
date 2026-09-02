from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--empty", action="store_true")
parser.add_argument("database")
args = parser.parse_args()
database_path = Path(args.database).resolve()
create_representative_fixture = not args.empty and not database_path.exists()
os.environ["DB_PATH"] = str(database_path)
os.environ["DATA_DIR"] = str(database_path.parent)
os.environ["APP_SECRET_KEY"] = "obvious-migration-rehearsal-key"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import create_app_user, create_trusted_device, init_db


def counts() -> dict[str, int]:
    connection = sqlite3.connect(database_path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    names = (
        "app_users", "deputy_user_secrets", "user_sync_state", "trusted_devices", "account_invitations",
        "shifts", "shift_marks", "shift_changes", "crew_people", "crew_aliases",
        "deputy_schedule_shifts", "deputy_schedule_observations",
        "deputy_personal_assignment_evidence", "deputy_event_locks",
        "crew_teams", "crew_team_members", "crew_vehicles", "travel_routes", "track_maps", "roster_days",
        "workday_assignments", "roster_day_versions", "workday_open_position_applications", "notification_events",
        "notification_preferences", "push_subscriptions", "deputy_oauth_connections", "deputy_oauth_config",
        "deputy_reference_employees", "deputy_reference_units", "deputy_person_mappings", "deputy_unit_mappings",
        "deputy_write_operations", "deputy_roster_links",
    )
    return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] if name in tables else 0 for name in names}


if create_representative_fixture:
    init_db()
    fixture_user = create_app_user(
        deputy_email="migration.fixture@example.invalid",
        display_name="Migration Fixture",
        pin_hash="fixture-pin-hash",
        deputy_web_url="https://fixture.au.deputy.com/",
        encrypted_email="fixture-encrypted-email",
        encrypted_password="fixture-encrypted-password",
    )
    create_trusted_device(
        user_id=int(fixture_user["id"]),
        token_hash="fixture-device-token-hash",
        expires_at="2099-01-01T00:00:00+00:00",
        label="Migration fixture device",
    )
    connection = sqlite3.connect(database_path)
    now = "2026-08-31T12:00:00+12:00"
    user_id = int(fixture_user["id"])
    connection.execute(
        """INSERT INTO crew_people
           (canonical_display_name, identity_source, deputy_employee_id, current_deputy_name,
            app_user_id, is_active, created_at, updated_at)
           VALUES ('Migration Fixture', 'linked_account', 7001, 'Migration Fixture', ?, 1, ?, ?)""",
        (user_id, now, now),
    )
    person_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        "INSERT INTO crew_aliases(person_id,alias,normalized_alias,created_at,updated_at) VALUES(?,?,?,?,?)",
        (person_id, "Fixture", "fixture", now, now),
    )
    connection.execute(
        """INSERT INTO shifts
           (source_uid,source_url_hash,title,description,location,start_at,end_at,date,raw_hours,
            break_minutes,paid_hours,last_synced_at,first_seen_at,last_changed_at,owner_user_id,
            source_status,source_payload)
           VALUES ('migration:shift','fixture','[T-Travel] Overnighter','Fixture note','',
                   '2026-09-01T12:00:00+12:00','2026-09-01T17:00:00+12:00','2026-09-01',
                   5,0,5,?,?,?,?, 'published','{}')""",
        (now, now, now, user_id),
    )
    shift_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        "INSERT INTO shift_marks(shift_id,checked,private_note,updated_at) VALUES(?,1,'preserve me',?)",
        (shift_id, now),
    )
    connection.execute(
        """INSERT INTO shift_changes
           (shift_id,changed_at,field_name,old_value,new_value,change_category,user_visible)
           VALUES(?,?,'role','Travel then Overnighter','Overnighter','source_change',1)""",
        (shift_id, now),
    )
    connection.execute(
        "INSERT INTO deputy_schedule_locations(location_id,name,address,updated_at) VALUES(105,'Travel','',?)",
        (now,),
    )
    connection.execute(
        """INSERT INTO deputy_schedule_shifts
           (source_shift_id,captured_at,area_id,area_name,area_location_id,employee_id,employee_name,
            start_at,end_at,date,duration,is_published,note,raw_payload)
           VALUES(81001,?,1412,'Travel then Overnighter',105,7001,'Migration Fixture',
                  '2026-09-01T12:00:00+12:00','2026-09-01T17:00:00+12:00','2026-09-01',18000,1,'','{}')""",
        (now,),
    )
    connection.execute(
        """INSERT INTO deputy_schedule_observations
           (source_shift_id,observer_key,observer_user_id,first_seen_at,last_seen_at,active)
           VALUES(81001,'fixture-observer',?,?,?,1)""",
        (user_id, now, now),
    )
    connection.execute(
        """INSERT INTO deputy_event_locks
           (date,area_location_id,event_start_at,event_end_at,locked_at,lock_reason,recovered_from_capture)
           VALUES('2026-09-01',105,'2026-09-01T12:00:00+12:00','2026-09-01T17:00:00+12:00',?,'fixture',0)""",
        (now,),
    )
    connection.execute("DROP TABLE deputy_personal_assignment_evidence")
    connection.execute(
        """CREATE TABLE deputy_personal_assignment_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id INTEGER NOT NULL,
            deputy_employee_id INTEGER, canonical_person_id INTEGER, source_shift_uid TEXT NOT NULL,
            source_shift_id TEXT, date TEXT NOT NULL, area_location_id INTEGER NOT NULL,
            position_key TEXT NOT NULL, position_label TEXT NOT NULL, start_at TEXT NOT NULL,
            end_at TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            last_confirmed_at TEXT NOT NULL, missing_capture_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'confirmed', provenance TEXT,
            UNIQUE(owner_user_id, source_shift_uid),
            FOREIGN KEY (owner_user_id) REFERENCES app_users(id) ON DELETE CASCADE,
            FOREIGN KEY (canonical_person_id) REFERENCES crew_people(id) ON DELETE SET NULL
        )"""
    )
    for index, label in enumerate(("Director", "Overnighter", "Special Ops Thing"), start=1):
        connection.execute(
            """INSERT INTO deputy_personal_assignment_evidence
               (owner_user_id,deputy_employee_id,canonical_person_id,source_shift_uid,source_shift_id,
                date,area_location_id,position_key,position_label,start_at,end_at,first_seen_at,
                last_seen_at,last_confirmed_at,status,provenance)
               VALUES(?,?,?, ?,?,'2026-09-01',105,?,?,
                      '2026-09-01T12:00:00+12:00','2026-09-01T17:00:00+12:00',?,?,?,'confirmed','{}')""",
            (user_id, 7001, person_id, f"legacy:{index}", str(index), label.casefold().replace(" ", ""), label, now, now, now),
        )
    connection.execute("DELETE FROM app_settings WHERE key='personal_evidence_classification_v1'")
    connection.execute("DROP TABLE account_invitations")
    connection.commit()
    connection.close()

before = counts()
init_db()
after_once = counts()
connection = sqlite3.connect(database_path)
mode_row = connection.execute("SELECT write_mode FROM deputy_oauth_config WHERE id=1").fetchone()
mode = mode_row[0] if mode_row else "off"
assignment_duplicates = connection.execute(
    "SELECT COUNT(*) FROM (SELECT assignment_key FROM workday_assignments WHERE COALESCE(assignment_key,'')<>'' GROUP BY assignment_key HAVING COUNT(*)>1)"
).fetchone()[0]
link_collisions = connection.execute(
    "SELECT COUNT(*) FROM (SELECT stable_assignment_key FROM deputy_roster_links GROUP BY stable_assignment_key HAVING COUNT(*)>1)"
).fetchone()[0]
integrity_result = connection.execute("PRAGMA integrity_check").fetchone()[0]
foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
classifications = connection.execute(
    """SELECT raw_role_label,evidence_type,production_position,participant_evidence,cohort_type
       FROM deputy_personal_assignment_evidence ORDER BY id"""
).fetchall()
connection.close()
init_db()
after_twice = counts()
assert all(after_once[name] >= before[name] for name in before)
if create_representative_fixture:
    for preserved_name in ("app_users", "deputy_user_secrets", "user_sync_state", "trusted_devices"):
        assert after_once[preserved_name] == before[preserved_name] > 0
assert after_once["crew_people"] >= before["crew_people"]
if create_representative_fixture:
    for preserved_name in (
        "shifts", "shift_marks", "shift_changes", "crew_people", "crew_aliases",
        "deputy_schedule_shifts", "deputy_schedule_observations",
        "deputy_personal_assignment_evidence", "deputy_event_locks",
    ):
        assert after_once[preserved_name] == before[preserved_name] > 0
    assert classifications == [
        ("Director", "production_position", 1, 1, ""),
        ("Overnighter", "participant_cohort", 0, 1, "travel"),
        ("Special Ops Thing", "unknown", 0, 0, ""),
    ]
assert after_once == after_twice
assert mode == "off"
assert assignment_duplicates == 0 and link_collisions == 0
assert integrity_result == "ok" and not foreign_key_rows
print("preserved=" + ",".join(f"{name}:{before[name]}" for name in before))
print(f"assignment_duplicate_groups={assignment_duplicates} link_collision_groups={link_collisions} effective_write_mode={mode}")
print(f"integrity_check={integrity_result} foreign_key_check_rows={len(foreign_key_rows)}")
print("migration rehearsal twice ok")
