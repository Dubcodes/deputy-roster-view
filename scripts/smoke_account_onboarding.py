from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
temp_dir = Path(tempfile.mkdtemp(prefix="re-deputy-account-smoke-"))
os.environ.update({"DATA_DIR": str(temp_dir), "DB_PATH": str(temp_dir / "accounts.sqlite3"), "APP_SECRET_KEY": "account-smoke-secret", "SIGNUP_ENABLED": "true", "COOKIE_SECURE": "false", "DEPUTY_WEB_URL": "https://fixture.au.deputy.com"})

from fastapi.testclient import TestClient
import app.account_invitations as account_invitation_module
from app.account_invitations import activate_account_invite, account_invite_details, create_account_invite, delete_terminal_account_invite, revoke_account_invite
from app.database import crew_picker_records, get_connection, get_deputy_user_secret, init_db, list_crew_people, list_syncable_app_users, update_deputy_user_credentials
import app.main as main_module
from app.main import app, infer_display_name_from_email
from app.security import decrypt_text, encrypt_text, valid_re_deputy_pin
from app.user_credentials import resolve_deputy_web_url, settings_for_user

init_db()
main_module.queue_manual_sync = lambda *args, **kwargs: True
client = TestClient(app)
login_html = client.get("/login").text
assert "<span>Email</span>" in login_html and "<span>Re-Deputy PIN</span>" in login_html and "Use your Deputy email and roster PIN" not in login_html
signup_html = client.get("/signup").text
assert 'name="deputy_web_url"' not in signup_html and "Deputy roster connection" in signup_html and "Re-Deputy login" in signup_html
for pin, expected in (("123", False), ("1234", True), ("1" * 32, True), ("1" * 33, False), ("abcd", False), ("12a4", False), ("１２３４", False)):
    assert valid_re_deputy_pin(pin) is expected, (pin, expected)

signup = client.post("/signup", data={"deputy_email": "worker.first@example.invalid", "deputy_password": "fixture-secret", "pin": "1234", "pin_confirm": "1234", "next_url": "/month"}, follow_redirects=False)
assert signup.status_code == 303
with get_connection() as conn:
    admin = conn.execute("SELECT * FROM app_users WHERE deputy_email='worker.first@example.invalid'").fetchone()
    assert admin and str(admin["deputy_web_url"]).startswith("https://fixture.au.deputy.com/")
admin_id = int(admin["id"])
assert infer_display_name_from_email("first.last@company.example") == "First Last"
assert infer_display_name_from_email("operations@company.example") == ""
assert resolve_deputy_web_url("not a Deputy URL", "https://fixture.au.deputy.com") == "https://fixture.au.deputy.com/#/"

replaced_old = create_account_invite("replacement.user@company.example", "Old", admin_id)
replaced_new = create_account_invite("replacement.user@company.example", "New", admin_id)
assert not account_invite_details(str(replaced_old["token"]))["available"]
assert account_invite_details(str(replaced_new["token"]))["available"]
replaced_old_post = client.post(f"/account/invite/{replaced_old['token']}", data={"pin": "5678", "pin_confirm": "5678"}, follow_redirects=False)
assert replaced_old_post.status_code == 400 and "location" not in replaced_old_post.headers

invite = create_account_invite("manager.one@company.example", "", admin_id)
with get_connection() as conn:
    stored = conn.execute("SELECT * FROM account_invitations WHERE id=?", (invite["id"],)).fetchone()
    assert stored and stored["token_hash"] != invite["token"] and invite["token"] not in tuple(str(value or "") for value in stored)
activation = client.post(f"/account/invite/{invite['token']}", data={"display_name": "Manager One", "pin": "5678", "pin_confirm": "5678", "deputy_email": "", "deputy_password": ""}, follow_redirects=False)
assert activation.status_code == 303 and activation.headers["location"] == "/month"
assert not account_invite_details(str(invite["token"]))["available"]
replay = client.post(f"/account/invite/{invite['token']}", data={"pin": "5678", "pin_confirm": "5678"}, follow_redirects=False)
assert replay.status_code == 400 and "invalid" in replay.text.lower() and "location" not in replay.headers
with get_connection() as conn:
    manager = conn.execute("SELECT * FROM app_users WHERE deputy_email='manager.one@company.example'").fetchone()
    assert manager and manager["account_type"] == "user"
    assert conn.execute("SELECT COUNT(*) FROM crew_people WHERE app_user_id=?", (manager["id"],)).fetchone()[0] == 0
manager_id = int(manager["id"])
# Repeated crew refreshes must not manufacture an identity from account profile data.
for _ in range(3):
    list_crew_people()
with get_connection() as conn:
    assert conn.execute("SELECT COUNT(*) FROM crew_people WHERE app_user_id=?", (manager_id,)).fetchone()[0] == 0
    now_placeholder = datetime.now().isoformat()
    conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,created_at,updated_at) VALUES('TBC TBC',1,?,?)", (now_placeholder, now_placeholder))
    conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,created_at,updated_at) VALUES('tbc2 tbc2',1,?,?)", (now_placeholder, now_placeholder))
assert not {"TBC TBC", "tbc2 tbc2"} & {row["canonical_display_name"] for row in list_crew_people()}
assert not {"TBC TBC", "tbc2 tbc2"} & {row["canonical_display_name"] for row in crew_picker_records()}
with get_connection() as conn:
    assert conn.execute("SELECT COUNT(*) FROM crew_people WHERE canonical_display_name IN ('TBC TBC','tbc2 tbc2')").fetchone()[0] == 2
assert manager_id not in {int(row["id"]) for row in list_syncable_app_users()}
manager_client = TestClient(app)
manager_login = manager_client.post("/login", data={"deputy_email": "manager.one@company.example", "pin": "5678", "next_url": "/month"}, follow_redirects=False)
assert manager_login.status_code == 303
month = manager_client.get("/month")
crew = manager_client.get("/month?view=crew")
settings = manager_client.get("/settings")
assert month.status_code == crew.status_code == 200
assert "Deputy roster not connected" in month.text and 'action="/sync-now"' not in settings.text
blocked_sync = manager_client.post("/sync-now", follow_redirects=False)
assert blocked_sync.status_code == 303 and "not+connected" in blocked_sync.headers["location"]
admin_relogin = client.post("/login", data={"deputy_email": "worker.first@example.invalid", "pin": "1234", "next_url": "/admin"}, follow_redirects=False)
assert admin_relogin.status_code == 303
admin_blocked_sync = client.post(f"/admin/users/{manager_id}/sync", follow_redirects=False)
assert admin_blocked_sync.status_code == 303 and "saved+Deputy+login+details" in admin_blocked_sync.headers["location"]

atomic_invite = create_account_invite("atomic.user@company.example", "Atomic User", admin_id)
original_persist = account_invitation_module.persist_deputy_user_credentials
def fail_credential_persistence(*args, **kwargs):
    raise RuntimeError("fixture credential failure")
account_invitation_module.persist_deputy_user_credentials = fail_credential_persistence
try:
    try:
        activate_account_invite(
            str(atomic_invite["token"]), "7890", "Atomic User",
            deputy_web_url="https://fixture.au.deputy.com/",
            encrypted_email="fixture-email",
            encrypted_password="fixture-password",
        )
        raise AssertionError("credential failure should roll back activation")
    except RuntimeError as exc:
        assert "fixture credential failure" in str(exc)
finally:
    account_invitation_module.persist_deputy_user_credentials = original_persist
assert account_invite_details(str(atomic_invite["token"]))["available"]
with get_connection() as conn:
    assert conn.execute("SELECT COUNT(*) FROM app_users WHERE deputy_email='atomic.user@company.example'").fetchone()[0] == 0

concurrent_invite = create_account_invite("concurrent.user@company.example", "Concurrent User", admin_id)
def concurrent_activation() -> str:
    try:
        activate_account_invite(str(concurrent_invite["token"]), "7890", "Concurrent User")
        return "activated"
    except ValueError:
        return "rejected"
with ThreadPoolExecutor(max_workers=2) as executor:
    outcomes = list(executor.map(lambda _: concurrent_activation(), range(2)))
assert sorted(outcomes) == ["activated", "rejected"]
with get_connection() as conn:
    assert conn.execute("SELECT COUNT(*) FROM app_users WHERE deputy_email='concurrent.user@company.example'").fetchone()[0] == 1

update_deputy_user_credentials(user_id=manager_id, deputy_email="different.deputy@example.invalid", deputy_web_url="https://fixture.au.deputy.com/", encrypted_email=encrypt_text("different.deputy@example.invalid"), encrypted_password=encrypt_text("fixture-password"))
with get_connection() as conn:
    unchanged = conn.execute("SELECT deputy_email FROM app_users WHERE id=?", (manager_id,)).fetchone()[0]
assert unchanged == "manager.one@company.example"
secret = get_deputy_user_secret(manager_id)
assert secret and decrypt_text(secret["encrypted_email"]) == "different.deputy@example.invalid"
assert manager_id in {int(row["id"]) for row in list_syncable_app_users()}
manager_settings = settings_for_user(manager_id)
assert manager_settings and manager_settings.deputy_login_email == "different.deputy@example.invalid"
with get_connection() as conn:
    conn.execute("UPDATE app_users SET deputy_web_url='not a Deputy URL' WHERE id=?", (manager_id,))
manager_settings = settings_for_user(manager_id)
assert manager_settings and manager_settings.deputy_web_url == "https://fixture.au.deputy.com/#/"
settings_html = manager_client.get("/settings").text
assert 'value="different.deputy@example.invalid"' in settings_html
manager_relogin = TestClient(app).post("/login", data={"deputy_email": "manager.one@company.example", "pin": "5678", "next_url": "/month"}, follow_redirects=False)
assert manager_relogin.status_code == 303
admin_html = client.get("/admin").text
assert "different.deputy@example.invalid" not in admin_html
credential_email = client.get(f"/admin/users/{manager_id}/deputy-credential-email")
assert credential_email.status_code == 200 and credential_email.json()["deputy_email"] == "different.deputy@example.invalid"
assert credential_email.headers.get("cache-control") == "private, no-store"

connected_invite = create_account_invite("connected.manager@company.example", "Connected Manager", admin_id)
connected_activation = client.post(f"/account/invite/{connected_invite['token']}", data={"display_name": "Connected Manager", "pin": "6789", "pin_confirm": "6789", "deputy_email": "connected.deputy@example.invalid", "deputy_password": "fixture-password"}, follow_redirects=False)
assert connected_activation.status_code == 303
with get_connection() as conn:
    connected = conn.execute("SELECT id FROM app_users WHERE deputy_email='connected.manager@company.example'").fetchone()
assert connected and decrypt_text(get_deputy_user_secret(int(connected["id"]))["encrypted_email"]) == "connected.deputy@example.invalid"
with get_connection() as conn:
    invite_count = conn.execute("SELECT COUNT(*) FROM account_invitations").fetchone()[0]
init_db()
with get_connection() as conn:
    assert conn.execute("SELECT COUNT(*) FROM account_invitations").fetchone()[0] == invite_count

revoked = create_account_invite("revoked.user@company.example", "", admin_id)
revoke_account_invite(int(revoked["id"]))
assert not account_invite_details(str(revoked["token"]))["available"]
revoked_post = client.post(f"/account/invite/{revoked['token']}", data={"pin": "5678", "pin_confirm": "5678"}, follow_redirects=False)
assert revoked_post.status_code == 400 and "location" not in revoked_post.headers
assert delete_terminal_account_invite(int(revoked["id"]))
with get_connection() as conn:
    assert conn.execute("SELECT COUNT(*) FROM account_invitations WHERE id=?", (revoked["id"],)).fetchone()[0] == 0
expired = create_account_invite("expired.user@company.example", "", admin_id)
with get_connection() as conn:
    conn.execute("UPDATE account_invitations SET expires_at=? WHERE id=?", ((datetime.now().astimezone() - timedelta(minutes=1)).isoformat(), expired["id"]))
assert not account_invite_details(str(expired["token"]))["available"]
expired_post = client.post(f"/account/invite/{expired['token']}", data={"pin": "5678", "pin_confirm": "5678"}, follow_redirects=False)
assert expired_post.status_code == 400 and "location" not in expired_post.headers

# Explicit Admin linking is optional and links only the selected existing identity.
with get_connection() as conn:
    link_person_id = int(conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,created_at,updated_at) VALUES('Explicit Link Person',1,?,?)", (datetime.now().isoformat(), datetime.now().isoformat())).lastrowid)
linked_invite = create_account_invite("linked.account@company.example", "Linked Account", admin_id, crew_person_id=link_person_id)
linked_user = activate_account_invite(str(linked_invite["token"]), "2468", "Linked Account")
with get_connection() as conn:
    assert int(conn.execute("SELECT app_user_id FROM crew_people WHERE id=?", (link_person_id,)).fetchone()[0]) == int(linked_user["id"])

worker_login = client.post("/login", data={"deputy_email": "worker.first@example.invalid", "pin": "1234", "next_url": "/month"}, follow_redirects=False)
assert worker_login.status_code == 303
admin_invite = client.post("/admin/account-invitations", data={"account_email": "route.invite@company.example", "display_name": ""}, follow_redirects=False)
assert admin_invite.status_code == 200 and "Activation link — copy now" in admin_invite.text
assert admin_invite.headers.get("cache-control") == "private, no-store" and "location" not in admin_invite.headers
invalid_reset = client.post(f"/admin/users/{manager_id}/pin", data={"pin": "1" * 33, "pin_confirm": "1" * 33}, follow_redirects=False)
assert invalid_reset.status_code == 303 and "4%E2%80%9332" in invalid_reset.headers["location"]
print("account onboarding smoke ok")
