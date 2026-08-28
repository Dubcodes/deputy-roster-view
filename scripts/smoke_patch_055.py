from __future__ import annotations

"""Deterministic backup, recovery, and central Admin-audit regression checks."""

import json
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMP = Path(tempfile.mkdtemp(prefix="redeputy-055-safety-"))
os.environ.update({
    "DATA_DIR": str(TEMP / "data"), "DB_PATH": str(TEMP / "data" / "safety.sqlite3"),
    "BACKUP_DIR": str(TEMP / "backups"), "APP_SECRET_KEY": "055-test-secret",
    "COOKIE_SECURE": "false", "BACKUP_RETENTION_DAYS": "1",
})
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.admin_audit import list_admin_action_audit, record_admin_action
from app.backup_service import MANIFEST_NAME, backup_status, create_backup, prune_managed_backups, validate_backup
from app.config import get_settings
from app.database import create_app_user, create_trusted_device, get_connection, init_db, set_app_user_active
from app.main import app
from app.scheduler import shutdown_scheduler, start_scheduler
from app.security import SESSION_COOKIE_NAME, hash_session_token


get_settings.cache_clear()
settings = get_settings()
init_db(settings)
(Path(settings.data_dir) / "track_maps").mkdir(parents=True, exist_ok=True)
(Path(settings.data_dir) / "track_maps" / "manual-map.png").write_bytes(b"map-fixture")
(Path(settings.data_dir) / "web_push_vapid_private.pem").write_text("private-fixture", encoding="utf-8")
(Path(settings.data_dir) / "app_secret.key").write_text("fallback-secret-fixture", encoding="utf-8")

admin = create_app_user(
    deputy_email="admin-055@example.invalid", display_name="Safety Admin", pin_hash="fixture",
    deputy_web_url="", encrypted_email="", encrypted_password="",
)
target = create_app_user(
    deputy_email="purge-055@example.invalid", display_name="Purge Target", pin_hash="fixture",
    deputy_web_url="", encrypted_email="", encrypted_password="",
)
set_app_user_active(int(target["id"]), False)
actor = {"id": int(admin["id"]), "display_name": "Safety Admin", "deputy_email": "admin-055@example.invalid", "is_admin": 1}

result = create_backup(reason="manual_test", requested_by_user_id=int(admin["id"]), settings=settings, app_version="0.5.5", app_build="fixture")
assert result["status"] == "success", result
backup_dir = Path(settings.backup_dir) / str(result["backup_id"])
checked = validate_backup(backup_dir)
manifest = checked["manifest"]
assert manifest["backup_reason"] == "manual_test" and manifest["app_version"] == "0.5.5"
assert checked["validation"] == {"integrity_check": "ok", "foreign_key_check_count": 0, "foreign_key_check": []}
assert manifest["backup_database_sha256"] == result["backup_sha256"]
assert "fallback-secret-fixture" not in json.dumps(manifest) and "private-fixture" not in json.dumps(manifest)
assert (backup_dir / "persistent" / "track_maps" / "manual-map.png").is_file()

# A partial/unmanaged directory cannot be validated or selected for retention.
historical = Path(settings.backup_dir) / "20260824-165141-nzst"
historical.mkdir()
(historical / "operator-note.txt").write_text("do not touch", encoding="utf-8")
partial = Path(settings.backup_dir) / ".tmp-interrupted"
partial.mkdir()
assert not (partial / MANIFEST_NAME).exists()
try:
    validate_backup(partial)
except ValueError:
    pass
else:
    raise AssertionError("Manifest-less temporary backup was accepted")

# Retention deletes only old managed successes and keeps both the newest good
# backup and the historical unmanaged directory.
old_manifest_path = backup_dir / MANIFEST_NAME
old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
old_manifest["created_at"] = (datetime.now(settings.timezone) - timedelta(days=4)).isoformat()
old_manifest_path.write_text(json.dumps(old_manifest), encoding="utf-8")
new_result = create_backup(reason="manual_test_new", settings=settings, app_version="0.5.5", app_build="fixture")
assert new_result["status"] == "success"
removed = prune_managed_backups(settings)
assert not (Path(settings.backup_dir) / str(result["backup_id"])).exists(), removed
assert historical.exists() and (Path(settings.backup_dir) / str(new_result["backup_id"])).is_dir()

# A later failed attempt neither deletes nor hides the newest known-good backup.
blocked_root = TEMP / "not-a-directory"
blocked_root.write_text("file", encoding="utf-8")
failed = create_backup(reason="forced_failure", settings=replace(settings, backup_dir=str(blocked_root)), app_version="0.5.5")
assert failed["status"] == "failed"
assert (Path(settings.backup_dir) / str(new_result["backup_id"])).is_dir()
status = backup_status(settings)
assert status["latest_success"]["backup_id"] == new_result["backup_id"]

# Audit redaction is centralized and catches all credential-shaped values.
record_admin_action(
    actor=actor, action_key="test.redaction", action_category="account_access", target_type="user", target_id=target["id"],
    before={"password": "nope", "pin": "1234", "token": "raw-token", "safe": "before"},
    after={"oauth_refresh_token": "nope", "session_cookie": "nope", "safe": "after"}, request_path="/admin/test",
)
audit_row = list_admin_action_audit(category="account_access")[0]
audit_text = audit_row["before_json"] + audit_row["after_json"]
for forbidden in ("nope", "1234", "raw-token"):
    assert forbidden not in audit_text
assert "before" in audit_text and "after" in audit_text

# Scheduler startup is idempotent and adds only the configured backup job.
scheduler = start_scheduler(settings)
assert scheduler.get_job("daily_database_backup") is not None
assert start_scheduler(settings) is scheduler
shutdown_scheduler()

# The Admin manual control is private and creates an audit entry.  Destructive
# routes fail closed when their prerequisite backup returns failure.
session_token = "admin-session-055"
create_trusted_device(
    user_id=int(admin["id"]), token_hash=hash_session_token(session_token),
    expires_at=(datetime.now(settings.timezone) + timedelta(days=1)).isoformat(),
)
with TestClient(app) as client:
    client.cookies.set(SESSION_COOKIE_NAME, session_token)
    page = client.get("/admin")
    assert page.status_code == 200 and "Safety &amp; recovery" in page.text and "Admin audit" in page.text
    manual = client.post("/admin/backups/create", follow_redirects=False)
    assert manual.status_code == 303
    with get_connection() as conn:
        conn.execute("INSERT INTO roster_days(roster_date,track_key,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?)", ("2026-10-01", "safety-fixture", "Safety draft", "draft", "2026-08-28T00:00:00+12:00", "2026-08-28T00:00:00+12:00"))
        draft_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    main_module = importlib.import_module("app.main")
    # A successful destructive operation has its write-ahead audit row before
    # the prerequisite backup and deletion; the completed row contains result.
    deleted_draft = client.post(f"/admin/roster-days/{draft_id}/delete", follow_redirects=False)
    assert deleted_draft.status_code == 303
    with get_connection() as conn:
        assert not conn.execute("SELECT 1 FROM roster_days WHERE id=?", (draft_id,)).fetchone()
        audit = conn.execute("SELECT outcome,before_json,after_json FROM admin_action_audit WHERE action_key='workday.delete_unpublished_draft' ORDER BY id DESC LIMIT 1").fetchone()
        assert audit and audit["outcome"] == "deleted" and '"deleted":true' in audit["after_json"]

    purge_success = create_app_user(
        deputy_email="purge-success-055@example.invalid", display_name="Purge Success", pin_hash="fixture",
        deputy_web_url="", encrypted_email="", encrypted_password="",
    )
    set_app_user_active(int(purge_success["id"]), False)
    purged = client.post(f"/admin/users/{int(purge_success['id'])}/purge", follow_redirects=False)
    assert purged.status_code == 303
    with get_connection() as conn:
        assert not conn.execute("SELECT 1 FROM app_users WHERE id=?", (int(purge_success["id"]),)).fetchone()
        safety_run = conn.execute("SELECT status FROM backup_runs WHERE reason='before_user_purge' ORDER BY id DESC LIMIT 1").fetchone()
        purge_audit = conn.execute("SELECT outcome FROM admin_action_audit WHERE action_key='user.purge' ORDER BY id DESC LIMIT 1").fetchone()
        assert safety_run and safety_run["status"] == "success" and purge_audit and purge_audit["outcome"] == "purged"

    with get_connection() as conn:
        conn.execute("INSERT INTO roster_days(roster_date,track_key,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?)", ("2026-10-02", "audit-fixture", "Audit blocked draft", "draft", "2026-08-28T00:00:00+12:00", "2026-08-28T00:00:00+12:00"))
        audit_blocked_draft_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    original_audit = main_module.record_admin_action
    main_module.record_admin_action = lambda **_kwargs: (_ for _ in ()).throw(OSError("audit unavailable"))
    try:
        audit_blocked = client.post(f"/admin/roster-days/{audit_blocked_draft_id}/delete", follow_redirects=False)
    finally:
        main_module.record_admin_action = original_audit
    assert audit_blocked.status_code == 503
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM roster_days WHERE id=?", (audit_blocked_draft_id,)).fetchone()

    with get_connection() as conn:
        conn.execute("INSERT INTO roster_days(roster_date,track_key,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?)", ("2026-10-03", "failure-fixture", "Safety backup failure draft", "draft", "2026-08-28T00:00:00+12:00", "2026-08-28T00:00:00+12:00"))
        draft_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    original_backup = main_module.create_backup
    main_module.create_backup = lambda **_kwargs: {"status": "failed"}
    try:
        with get_connection() as conn:
            assert conn.execute("SELECT 1 FROM app_users WHERE id=?", (int(target["id"]),)).fetchone()
        blocked_purge = client.post(f"/admin/users/{int(target['id'])}/purge", follow_redirects=False)
        blocked_draft = client.post(f"/admin/roster-days/{draft_id}/delete", follow_redirects=False)
    finally:
        main_module.create_backup = original_backup
    assert blocked_purge.status_code == blocked_draft.status_code == 303
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM app_users WHERE id=?", (int(target["id"]),)).fetchone(), blocked_purge.headers.get("location")
        assert conn.execute("SELECT 1 FROM roster_days WHERE id=?", (draft_id,)).fetchone()

# The offline recovery utility validates a good backup and rejects tampering.
dry_run = subprocess.run([sys.executable, "scripts/restore_backup.py", "--backup", str(Path(settings.backup_dir) / str(new_result["backup_id"])), "--dry-run"], cwd=ROOT, capture_output=True, text=True, check=True)
assert "dry_run=ok" in dry_run.stdout
tampered = Path(settings.backup_dir) / str(new_result["backup_id"]) / "deputy_roster.sqlite3"
with tampered.open("ab") as handle:
    handle.write(b"tamper")
try:
    validate_backup(tampered.parent)
except ValueError:
    pass
else:
    raise AssertionError("SHA mismatch was accepted")

print("0.5.5 backup, recovery, and central Admin audit smoke ok")
