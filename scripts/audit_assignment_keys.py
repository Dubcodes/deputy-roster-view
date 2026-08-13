from __future__ import annotations

import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
assignment_duplicates = connection.execute(
    "SELECT COUNT(*) FROM (SELECT assignment_key FROM workday_assignments WHERE COALESCE(assignment_key,'')<>'' GROUP BY assignment_key HAVING COUNT(*)>1)"
).fetchone()[0]
link_collisions = connection.execute(
    "SELECT COUNT(*) FROM (SELECT stable_assignment_key FROM deputy_roster_links GROUP BY stable_assignment_key HAVING COUNT(*)>1)"
).fetchone()[0]
assignment_rows = connection.execute("SELECT COUNT(*) FROM workday_assignments").fetchone()[0]
link_rows = connection.execute("SELECT COUNT(*) FROM deputy_roster_links").fetchone()[0]
print(f"assignment_rows={assignment_rows} assignment_duplicate_groups={assignment_duplicates}")
print(f"link_rows={link_rows} link_collision_groups={link_collisions}")
raise SystemExit(1 if assignment_duplicates or link_collisions else 0)
