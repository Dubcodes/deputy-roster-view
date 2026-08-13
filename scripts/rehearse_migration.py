from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

database_path = Path(sys.argv[1]).resolve()
os.environ["DB_PATH"] = str(database_path)
os.environ["DATA_DIR"] = str(database_path.parent)
os.environ["APP_SECRET_KEY"] = "obvious-migration-rehearsal-key"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db


def counts() -> dict[str, int]:
    connection = sqlite3.connect(database_path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    names = ("app_users", "shifts", "crew_people", "roster_days", "notification_events", "deputy_oauth_connections", "deputy_roster_links")
    return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] if name in tables else 0 for name in names}


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
connection.close()
init_db()
after_twice = counts()
assert all(after_once[name] >= before[name] for name in before)
assert after_once == after_twice
assert mode == "off"
assert assignment_duplicates == 0 and link_collisions == 0
print("preserved=" + ",".join(f"{name}:{before[name]}" for name in before))
print(f"assignment_duplicate_groups={assignment_duplicates} link_collision_groups={link_collisions} effective_write_mode={mode}")
print("migration rehearsal twice ok")
