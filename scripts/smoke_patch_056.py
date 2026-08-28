from __future__ import annotations

"""Deterministic 0.5.6 operational-safety closure regressions."""

import importlib
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMP = Path(tempfile.mkdtemp(prefix="redeputy-056-safety-"))
os.environ.update({
    "DATA_DIR": str(TEMP / "data"), "DB_PATH": str(TEMP / "data" / "safety.sqlite3"),
    "BACKUP_DIR": str(TEMP / "backups"), "APP_SECRET_KEY": "056-test-secret", "COOKIE_SECURE": "false",
})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.admin_audit import list_admin_action_audit
from app.backup_service import backup_status, create_backup
from app.config import get_settings
from app.database import create_app_user, create_trusted_device, get_connection, init_db, set_app_user_active
from app.main import app
from app.security import SESSION_COOKIE_NAME, hash_session_token
from app.version import APP_VERSION


get_settings.cache_clear()
settings = get_settings()
init_db(settings)
admin = create_app_user(deputy_email="admin-056@example.invalid", display_name="Safety Admin", pin_hash="fixture", deputy_web_url="", encrypted_email="", encrypted_password="")
old = create_app_user(deputy_email="old-056@example.invalid", display_name="Old inactive", pin_hash="fixture", deputy_web_url="", encrypted_email="", encrypted_password="")
reset_target = create_app_user(deputy_email="reset-056@example.invalid", display_name="Reset target", pin_hash="fixture", deputy_web_url="", encrypted_email="", encrypted_password="")
set_app_user_active(int(old["id"]), False)
old_when = (datetime.now(settings.timezone) - timedelta(days=40)).isoformat(timespec="seconds")
with get_connection() as conn:
    conn.execute("UPDATE app_users SET deactivated_at=?,updated_at=? WHERE id=?", (old_when, old_when, int(old["id"])))
    conn.execute("INSERT INTO shifts(source_uid,title,date,owner_user_id) VALUES(?,?,?,?)", ("056-reset-shift", "Fixture", "2026-08-29", int(reset_target["id"])))
    shift_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute("INSERT INTO shift_marks(shift_id,private_note) VALUES(?,?)", (shift_id, "must never enter audit"))
    conn.execute("INSERT INTO shift_changes(shift_id,changed_at,field_name,old_value,new_value) VALUES(?,?,?,?,?)", (shift_id, old_when, "title", "old", "new"))

main_module = importlib.import_module("app.main")
original_start = main_module.start_scheduler
main_module.start_scheduler = lambda: None
try:
    main_module.on_startup()
finally:
    main_module.start_scheduler = original_start
with get_connection() as conn:
    assert conn.execute("SELECT 1 FROM app_users WHERE id=?", (int(old["id"]),)).fetchone(), "startup purged an old inactive account"

token = "admin-session-056"
create_trusted_device(user_id=int(admin["id"]), token_hash=hash_session_token(token), expires_at=(datetime.now(settings.timezone) + timedelta(days=1)).isoformat())
with TestClient(app) as client:
    client.cookies.set(SESSION_COOKIE_NAME, token)
    main_module = importlib.import_module("app.main")
    original_backup = main_module.create_backup
    main_module.create_backup = lambda **_kwargs: {"status": "failed"}
    try:
        blocked_cleanup = client.post("/admin/cleanup", follow_redirects=False)
        blocked_reset = client.post(f"/admin/users/{int(reset_target['id'])}/reset-roster", follow_redirects=False)
    finally:
        main_module.create_backup = original_backup
    assert blocked_cleanup.status_code == blocked_reset.status_code == 303
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM app_users WHERE id=?", (int(old["id"]),)).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM shifts WHERE owner_user_id=?", (int(reset_target["id"]),)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM shift_marks WHERE shift_id=?", (shift_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM shift_changes WHERE shift_id=?", (shift_id,)).fetchone()[0] == 1

    cleaned = client.post("/admin/cleanup", follow_redirects=False)
    assert cleaned.status_code == 303
    with get_connection() as conn:
        assert not conn.execute("SELECT 1 FROM app_users WHERE id=?", (int(old["id"]),)).fetchone()
        runs = conn.execute("SELECT COUNT(*) FROM backup_runs WHERE reason='before_inactive_cleanup' AND status='success'").fetchone()[0]
        assert runs == 1

    reset = client.post(f"/admin/users/{int(reset_target['id'])}/reset-roster", follow_redirects=False)
    assert reset.status_code == 303
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM shifts WHERE owner_user_id=?", (int(reset_target["id"]),)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM backup_runs WHERE reason='before_roster_reset' AND status='success'").fetchone()[0] == 1

    assert client.post("/admin/users/99999/devices/99999/revoke", follow_redirects=False).status_code == 303
    assert client.post(f"/admin/users/{int(admin['id'])}/role", data={"is_admin": "0"}, follow_redirects=False).status_code == 303
    assert client.post("/admin/travel-defaults", data={"track_label": "", "travel_minutes": ""}, follow_redirects=False).status_code == 303
    assert client.post("/admin/travel-defaults", data={"track_label": "Fixture Track", "base_label": "Office / Clow Place", "travel_minutes": "45"}, follow_redirects=False).status_code == 303
    rows = list_admin_action_audit(limit=100)
    outcomes = {}
    for row in rows:
        outcomes.setdefault(str(row["action_key"]), str(row["outcome"]))
    assert outcomes["trusted_device.revoke"] in {"not_found", "unchanged"}
    assert outcomes["user.role"] == "blocked"
    assert outcomes["travel_default.save"] == "completed"
    travel = next(row for row in rows if row["action_key"] == "travel_default.save")
    assert '"travel_minutes":45' in str(travel["after_json"])
    audit_text = "".join(str(row["before_json"]) + str(row["after_json"]) for row in rows)
    assert "must never enter audit" not in audit_text and "056-test-secret" not in audit_text

scheduled = create_backup(reason="scheduled", settings=settings, app_version=APP_VERSION, app_build="fixture")
assert scheduled["status"] == "success"
latest = backup_status(settings)
assert latest["latest_success"]["backup_id"] == scheduled["backup_id"]
assert latest["latest_failure"] is None
manifest = (Path(settings.backup_dir) / str(scheduled["backup_id"]) / "manifest.json").read_text(encoding="utf-8")
assert f'"app_version": "{APP_VERSION}"' in manifest

print("0.5.6 operational safety closure smoke ok")
