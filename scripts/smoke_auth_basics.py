from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="redeputy-auth-"))
os.environ.update({"DB_PATH": str(tmp / "auth.sqlite3"), "DATA_DIR": str(tmp), "APP_SECRET_KEY": "obvious-auth-test-key", "SIGNUP_ENABLED": "false"})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth import clear_login_failures
from app.database import create_app_user, get_connection, init_db, revoke_trusted_device_for_user
from app.main import app
from app.security import SESSION_COOKIE_NAME, hash_pin

init_db()
user = create_app_user(
    deputy_email="auth@example.invalid",
    display_name="Auth Fixture",
    pin_hash=hash_pin("1234"),
    deputy_web_url="https://example.au.deputy.com/#/",
    encrypted_email="",
    encrypted_password="",
)
user_id = int(user["id"])
client = TestClient(app, follow_redirects=False)
assert client.get("/login").status_code == 200
assert client.get("/month").status_code == 303

invalid = client.post("/login", data={"deputy_email": "auth@example.invalid", "pin": "9999"})
assert invalid.status_code == 303 and "Email+or+PIN+was+not+recognised" in invalid.headers["location"]
for _ in range(4):
    client.post("/login", data={"deputy_email": "auth@example.invalid", "pin": "9999"})
blocked_valid = client.post("/login", data={"deputy_email": "auth@example.invalid", "pin": "1234"})
assert "Email+or+PIN+was+not+recognised" in blocked_valid.headers["location"]

clear_login_failures("auth@example.invalid")
external_next = client.post("/login", data={"deputy_email": "auth@example.invalid", "pin": "1234", "next_url": "https://evil.example/"})
assert external_next.status_code == 303 and external_next.headers["location"] == "/month"
assert client.cookies.get(SESSION_COOKIE_NAME) and client.get("/month").status_code == 200

logout = client.get("/logout")
assert logout.status_code == 303 and logout.headers["location"] == "/login"
assert client.get("/month").status_code == 303

login = client.post("/login", data={"deputy_email": "auth@example.invalid", "pin": "1234"})
assert login.status_code == 303 and client.get("/month").status_code == 200
with get_connection() as conn:
    device_id = int(conn.execute("SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()["id"])
assert revoke_trusted_device_for_user(user_id, device_id) == 1
assert client.get("/month").status_code == 303

with get_connection() as conn:
    conn.execute("UPDATE app_users SET is_active=0 WHERE id=?", (user_id,))
inactive = client.post("/login", data={"deputy_email": "auth@example.invalid", "pin": "1234"})
assert inactive.status_code == 303 and "Email+or+PIN+was+not+recognised" in inactive.headers["location"]

print("authentication basics smoke ok")
