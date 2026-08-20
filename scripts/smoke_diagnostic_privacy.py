from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = Path(tempfile.mkdtemp(prefix="redeputy-diagnostic-privacy-"))
os.environ.update(DATA_DIR=str(TEMP), DB_PATH=str(TEMP / "privacy.sqlite3"), APP_SECRET_KEY="privacy-fixture")

from app.database import create_app_user, get_connection, get_recent_source_payloads, init_db, save_deputy_web_capture_diagnostic
from app.main import build_error_report_diagnostics, diagnostic_source_payloads

init_db()
user_a = create_app_user(deputy_email="a-only@example.test", display_name="A Only", pin_hash="fixture", deputy_web_url="", encrypted_email="", encrypted_password="")
user_b = create_app_user(deputy_email="b-only@example.test", display_name="B Only", pin_hash="fixture", deputy_web_url="", encrypted_email="", encrypted_password="")
with get_connection() as conn:
    for user, marker in ((user_a, "A-ONLY-SOURCE"), (user_b, "B-ONLY-SOURCE")):
        conn.execute(
            """INSERT INTO shifts(source_uid,title,start_at,end_at,date,owner_user_id,source_payload)
               VALUES(?,?,?,?,?,?,?)""",
            (f"privacy-{user['id']}", marker, "2026-08-21T09:00:00+12:00", "2026-08-21T17:00:00+12:00",
             "2026-08-21", int(user["id"]), json.dumps({"marker": marker})),
        )
save_deputy_web_capture_diagnostic(owner_user_id=int(user_a["id"]), captured_at="2026-08-21T09:00:00+12:00", status="ok", message="fixture", payload="A-ONLY-CAPTURE")
save_deputy_web_capture_diagnostic(owner_user_id=int(user_b["id"]), captured_at="2026-08-21T09:00:00+12:00", status="ok", message="fixture", payload="B-ONLY-CAPTURE")

assert "A-ONLY-SOURCE" in json.dumps(diagnostic_source_payloads(owner_user_id=int(user_a["id"])))
assert "B-ONLY-SOURCE" not in json.dumps(diagnostic_source_payloads(owner_user_id=int(user_a["id"])))
assert {row["owner_user_id"] for row in get_recent_source_payloads()} == {int(user_a["id"]), int(user_b["id"])}
request = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/settings", "query_string": b"", "headers": [], "server": ("testserver", 80)})
report_a = build_error_report_diagnostics(request, dict(user_a))
assert "A-ONLY-CAPTURE" in report_a and "B-ONLY-CAPTURE" not in report_a
assert "A-ONLY-SOURCE" in report_a and "B-ONLY-SOURCE" not in report_a
print("two-user diagnostic privacy smoke ok")
