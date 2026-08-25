from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path


tmp = Path(tempfile.mkdtemp(prefix="redeputy-053-"))
os.environ.update(
    {
        "DB_PATH": str(tmp / "patch.sqlite3"),
        "DATA_DIR": str(tmp),
        "APP_SECRET_KEY": "patch-053-test-key",
        "COOKIE_SECURE": "false",
        "SIGNUP_ENABLED": "true",
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.account_invitations import activate_account_invite, create_account_invite
from app.contractors import activate_invite, create_contractor_invite
from app.database import (
    create_app_user,
    create_trusted_device,
    get_connection,
    init_db,
    purge_app_user,
    set_app_user_active,
)
from app.main import app
from app.security import SESSION_COOKIE_NAME, hash_pin, hash_session_token


def create_user(email: str, name: str) -> object:
    return create_app_user(
        deputy_email=email,
        display_name=name,
        pin_hash=hash_pin("2468"),
        deputy_web_url="",
        encrypted_email="",
        encrypted_password="",
    )


def assert_fk_ok() -> None:
    with get_connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def assert_user_missing(user_id: int) -> None:
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM app_users WHERE id=?", (user_id,)).fetchone() is None
    assert_fk_ok()


init_db()
admin = create_user("existing-admin@example.invalid", "Existing Admin")
admin_id = int(admin["id"])
admin_token = "admin-purge-session"
create_trusted_device(
    user_id=admin_id,
    token_hash=hash_session_token(admin_token),
    expires_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat(),
)
client = TestClient(app, follow_redirects=False)
client.cookies.set(SESSION_COOKIE_NAME, admin_token)

# A and D: a plain inactive account, including its trusted device, purges cleanly.
plain = create_user("plain@example.invalid", "Plain User")
plain_id = int(plain["id"])
create_trusted_device(
    user_id=plain_id,
    token_hash=hash_session_token("plain-device"),
    expires_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat(),
)
set_app_user_active(plain_id, False)
plain_result = purge_app_user(plain_id)
assert plain_result["status"] == "purged" and plain_result["users"] == 1
assert plain_result["devices"] == 1
assert_user_missing(plain_id)

# B and C: consumed invitation plus promote/demote role audit rows are self-related.
invite = create_account_invite("invited@example.invalid", "Invited User", admin_id)
invited = activate_account_invite(str(invite["token"]), "5678", "Invited User")
invited_id = int(invited["id"])
now = datetime.now().astimezone().isoformat(timespec="seconds")
for is_admin in ("1", "0"):
    role_response = client.post(
        f"/admin/users/{invited_id}/role",
        data={"is_admin": is_admin},
        headers={"origin": "http://testserver"},
    )
    assert role_response.status_code == 303 and "Admin+permission+updated" in role_response.headers["location"]
deactivate_response = client.post(
    f"/admin/users/{invited_id}/deactivate",
    headers={"origin": "http://testserver"},
)
assert deactivate_response.status_code == 303
purge_response = client.post(
    f"/admin/users/{invited_id}/purge",
    headers={"origin": "http://testserver"},
)
assert purge_response.status_code == 303 and "Purged+inactive+user+data" in purge_response.headers["location"]
with get_connection() as conn:
    assert conn.execute(
        "SELECT 1 FROM account_invitations WHERE activated_user_id=?", (invited_id,)
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM app_role_audit WHERE target_user_id=?", (invited_id,)
    ).fetchone() is None
assert_user_missing(invited_id)

# E: only an eligible account-synthetic identity is removed.
synthetic_user = create_user("synthetic@example.invalid", "Synthetic User")
synthetic_user_id = int(synthetic_user["id"])
with get_connection() as conn:
    synthetic_person_id = int(
        conn.execute(
            """INSERT INTO crew_people(
                   canonical_display_name,person_type,identity_source,deputy_employee_id,
                   app_user_id,is_active,created_at,updated_at
               ) VALUES('Synthetic User','employee','account_synthetic',NULL,?,1,?,?)""",
            (synthetic_user_id, now, now),
        ).lastrowid
    )
set_app_user_active(synthetic_user_id, False)
assert purge_app_user(synthetic_user_id)["status"] == "purged"
with get_connection() as conn:
    assert conn.execute("SELECT 1 FROM crew_people WHERE id=?", (synthetic_person_id,)).fetchone() is None
assert_fk_ok()

# Deputy-backed and manual identities are preserved and detached.
for suffix, identity_source, employee_id in (
    ("deputy", "observed", 91001),
    ("manual", "manual", None),
):
    linked = create_user(f"{suffix}@example.invalid", f"{suffix.title()} Person")
    linked_id = int(linked["id"])
    with get_connection() as conn:
        person_id = int(
            conn.execute(
                """INSERT INTO crew_people(
                       canonical_display_name,person_type,identity_source,deputy_employee_id,
                       app_user_id,is_active,created_at,updated_at
                   ) VALUES(?,'employee',?,?,?,1,?,?)""",
                (f"{suffix.title()} Person", identity_source, employee_id, linked_id, now, now),
            ).lastrowid
        )
    set_app_user_active(linked_id, False)
    assert purge_app_user(linked_id)["status"] == "purged"
    with get_connection() as conn:
        retained = conn.execute("SELECT * FROM crew_people WHERE id=?", (person_id,)).fetchone()
        assert retained is not None and retained["app_user_id"] is None
    assert_fk_ok()

# Contractor identity and invite history survive while the deleted account link detaches.
contractor_invite = create_contractor_invite("Retained Contractor", "Fixture Ltd", admin_id)
contractor = activate_invite(str(contractor_invite["token"]), "9753")
contractor_id = int(contractor["id"])
contractor_person_id = int(contractor["contractor_person_id"])
set_app_user_active(contractor_id, False)
assert purge_app_user(contractor_id)["status"] == "purged"
with get_connection() as conn:
    retained_contractor = conn.execute(
        "SELECT * FROM crew_people WHERE id=?", (contractor_person_id,)
    ).fetchone()
    retained_invite = conn.execute(
        "SELECT * FROM contractor_invites WHERE id=?", (contractor_invite["id"],)
    ).fetchone()
    assert retained_contractor is not None and retained_contractor["person_type"] == "contractor"
    assert retained_contractor["app_user_id"] is None
    assert retained_invite is not None and retained_invite["activated_user_id"] is None
assert_fk_ok()

# F and G: active and nonexistent users return explicit results without exceptions.
active = create_user("active@example.invalid", "Active User")
active_id = int(active["id"])
active_result = purge_app_user(active_id)
assert active_result["status"] == "still_active" and not active_result["purged"]
with get_connection() as conn:
    assert conn.execute("SELECT 1 FROM app_users WHERE id=?", (active_id,)).fetchone()
missing_result = purge_app_user(999999)
assert missing_result["status"] == "not_found" and not missing_result["purged"]
assert_fk_ok()

# H: retention-critical Deputy write audit blocks before any personal cleanup begins.
blocked = create_user("blocked@example.invalid", "Blocked User")
blocked_id = int(blocked["id"])
create_trusted_device(
    user_id=blocked_id,
    token_hash=hash_session_token("blocked-device"),
    expires_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat(),
)
with get_connection() as conn:
    shift_id = int(
        conn.execute(
            """INSERT INTO shifts(
                   source_uid,title,start_at,end_at,date,owner_user_id,source_payload
               ) VALUES('blocked-shift','Blocked','2026-08-25T09:00:00+12:00',
                        '2026-08-25T17:00:00+12:00','2026-08-25',?,'{}')""",
            (blocked_id,),
        ).lastrowid
    )
    conn.execute(
        """INSERT INTO deputy_write_operations(
               operation_uuid,app_user_id,tenant_host,deputy_user_id,deputy_employee_id,
               permission_hash,permission_snapshot,stable_assignment_key,operation_type,
               desired_state,status,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), blocked_id, "fixture.au.deputy.com", 1, 2, "hash", "{}",
            "retained-audit", "create", "{}", "verified", now, now,
        ),
    )
set_app_user_active(blocked_id, False)
blocked_result = purge_app_user(blocked_id)
assert blocked_result["status"] == "blocked" and "Deputy write audit history" in blocked_result["reason"]
with get_connection() as conn:
    assert conn.execute("SELECT 1 FROM app_users WHERE id=?", (blocked_id,)).fetchone()
    assert conn.execute("SELECT 1 FROM trusted_devices WHERE user_id=?", (blocked_id,)).fetchone()
    assert conn.execute("SELECT 1 FROM shifts WHERE id=?", (shift_id,)).fetchone()
    assert conn.execute(
        "SELECT 1 FROM deputy_write_operations WHERE app_user_id=?", (blocked_id,)
    ).fetchone()
assert_fk_ok()

# The Admin route turns expected blocked states into a normal notice redirect.
blocked_route = client.post(
    f"/admin/users/{blocked_id}/purge",
    headers={"origin": "http://testserver"},
)
assert blocked_route.status_code == 303
assert "retained+audit+history" in blocked_route.headers["location"]

# Shared notice rendering loads the one-shot cleaner and preserves the v0.5.2 Admin layout.
notice_page = client.get("/admin?scope=accounts&notice=Saved&filter=inactive")
assert notice_page.status_code == 200
assert '<div class="notice" data-one-shot-notice>Saved</div>' in notice_page.text
assert '/static/one-shot-notice.js' in notice_page.text
assert notice_page.text.index("Shared roster control") < notice_page.text.index("Accounts &amp; access")
assert "Re-Deputy account invitations" in notice_page.text
assert "Company / organisation (optional)" in notice_page.text
assert_fk_ok()

print("0.5.3 safe user-purge and one-shot notice smoke ok")
