from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


tmp = Path(tempfile.mkdtemp(prefix="redeputy-052-"))
os.environ.update(
    {
        "DB_PATH": str(tmp / "patch.sqlite3"),
        "DATA_DIR": str(tmp),
        "APP_SECRET_KEY": "patch-052-test-key",
        "COOKIE_SECURE": "false",
        "SIGNUP_ENABLED": "false",
        "TRUSTED_DEVICE_LIMIT": "3",
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.config import get_settings
from app.contractors import activate_invite, create_contractor_invite, create_invite
from app.database import create_app_user, create_trusted_device, get_connection, init_db
from app.main import app
from app.security import SESSION_COOKIE_NAME, hash_pin, hash_session_token


def configured_limit(value: str | None) -> int:
    if value is None:
        os.environ.pop("TRUSTED_DEVICE_LIMIT", None)
    else:
        os.environ["TRUSTED_DEVICE_LIMIT"] = value
    get_settings.cache_clear()
    return get_settings().trusted_device_limit


assert configured_limit(None) == 10
assert configured_limit("") == 10
assert configured_limit("1") == 1
assert configured_limit("100") == 100
for invalid in ("0", "-1", "101", "not-a-number"):
    assert configured_limit(invalid) == 10

assert configured_limit("3") == 3
init_db()
admin = create_app_user(
    deputy_email="admin@example.invalid",
    display_name="Admin",
    pin_hash=hash_pin("1234"),
    deputy_web_url="",
    encrypted_email="",
    encrypted_password="",
)
admin_id = int(admin["id"])

# New contractor identity and initial invite are both valid and hash-only.
first_invite = create_contractor_invite("Atomic Contractor", "Fixture Ltd", admin_id)
with get_connection() as conn:
    person = conn.execute(
        "SELECT * FROM crew_people WHERE canonical_display_name='Atomic Contractor'"
    ).fetchone()
    assert person is not None
    assert person["person_type"] == "contractor"
    assert person["deputy_employee_id"] is None
    person_id = int(person["id"])
    stored = conn.execute(
        "SELECT * FROM contractor_invites WHERE id=?", (first_invite["id"],)
    ).fetchone()
    assert stored is not None and stored["crew_person_id"] == person_id
    assert str(first_invite["token"]) != str(stored["token_hash"])
    assert len(str(stored["token_hash"])) == 64

# A deterministic insert failure after the person insert rolls back both rows.
with get_connection() as conn:
    invite_count_before = int(conn.execute("SELECT COUNT(*) FROM contractor_invites").fetchone()[0])
    conn.execute(
        """CREATE TRIGGER fail_contractor_invite_insert
           BEFORE INSERT ON contractor_invites
           BEGIN
             SELECT RAISE(FAIL, 'forced contractor invite failure');
           END"""
    )
try:
    create_contractor_invite("Must Roll Back", "Failure Fixture", admin_id)
except sqlite3.IntegrityError as exc:
    assert "forced contractor invite failure" in str(exc)
else:
    raise AssertionError("Forced contractor invitation failure unexpectedly succeeded")
with get_connection() as conn:
    assert conn.execute(
        "SELECT 1 FROM crew_people WHERE canonical_display_name='Must Roll Back'"
    ).fetchone() is None
    assert int(conn.execute("SELECT COUNT(*) FROM contractor_invites").fetchone()[0]) == invite_count_before
    conn.execute("DROP TRIGGER fail_contractor_invite_insert")

# Replacement keeps the canonical identity, revokes the old invite, and activates normally.
with get_connection() as conn:
    person_count_before = int(conn.execute("SELECT COUNT(*) FROM crew_people").fetchone()[0])
replacement = create_invite(person_id, admin_id)
with get_connection() as conn:
    assert int(conn.execute("SELECT COUNT(*) FROM crew_people").fetchone()[0]) == person_count_before
    old_row = conn.execute(
        "SELECT revoked_at FROM contractor_invites WHERE id=?", (first_invite["id"],)
    ).fetchone()
    new_row = conn.execute(
        "SELECT crew_person_id,revoked_at FROM contractor_invites WHERE id=?", (replacement["id"],)
    ).fetchone()
    assert old_row["revoked_at"] is not None
    assert int(new_row["crew_person_id"]) == person_id and new_row["revoked_at"] is None
contractor = activate_invite(str(replacement["token"]), "5678")
assert contractor["account_type"] == "contractor"
assert int(contractor["contractor_person_id"]) == person_id

# Four valid logins succeed at limit 3; only the least-recently-used device loses trust.
ordinary = create_app_user(
    deputy_email="devices@example.invalid",
    display_name="Device User",
    pin_hash=hash_pin("2468"),
    deputy_web_url="",
    encrypted_email="",
    encrypted_password="",
)
ordinary_id = int(ordinary["id"])
login_clients: list[TestClient] = []
for _ in range(4):
    client = TestClient(app, follow_redirects=False)
    response = client.post("/login", data={"deputy_email": "devices@example.invalid", "pin": "2468"})
    assert response.status_code == 303 and client.cookies.get(SESSION_COOKIE_NAME)
    login_clients.append(client)
with get_connection() as conn:
    active_ids = [
        int(row["id"])
        for row in conn.execute(
            """SELECT id FROM trusted_devices
               WHERE user_id=? AND revoked_at IS NULL AND expires_at>?
               ORDER BY COALESCE(last_seen_at,created_at) DESC,id DESC""",
            (ordinary_id, datetime.now().astimezone().isoformat()),
        )
    ]
assert len(active_ids) == 3
assert login_clients[0].get("/month").status_code == 303
assert all(client.get("/month").status_code == 200 for client in login_clients[1:])

# Existing over-limit rows converge on startup, remain idempotent, and stay user-scoped.
other = create_app_user(
    deputy_email="other@example.invalid",
    display_name="Other User",
    pin_hash=hash_pin("1357"),
    deputy_web_url="",
    encrypted_email="",
    encrypted_password="",
)
other_id = int(other["id"])
future = (datetime.now().astimezone() + timedelta(days=1)).isoformat(timespec="seconds")
for index in range(2):
    create_trusted_device(
        user_id=other_id,
        token_hash=hash_session_token(f"other-{index}"),
        expires_at=future,
    )
with get_connection() as conn:
    other_active_before = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL ORDER BY id",
            (other_id,),
        )
    ]
    base = datetime.now().astimezone() - timedelta(hours=1)
    for index in range(5):
        stamp = (base + timedelta(minutes=index)).isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO trusted_devices(
                   user_id,token_hash,label,user_agent,created_at,last_seen_at,expires_at,revoked_at
               ) VALUES(?,?,?,?,?,?,?,NULL)""",
            (admin_id, hash_session_token(f"cleanup-{index}"), "", "", stamp, stamp, future),
        )
init_db()
with get_connection() as conn:
    cleanup_active_once = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL ORDER BY last_seen_at DESC,id DESC",
            (admin_id,),
        )
    ]
    assert len(cleanup_active_once) == 3
    assert [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL ORDER BY id",
            (other_id,),
        )
    ] == other_active_before
init_db()
with get_connection() as conn:
    cleanup_active_twice = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL ORDER BY last_seen_at DESC,id DESC",
            (admin_id,),
        )
    ]
assert cleanup_active_twice == cleanup_active_once

# Raising the cap revokes nothing; lowering it converges idempotently.
assert configured_limit("4") == 4
active_before_raise = set(active_ids)
create_trusted_device(
    user_id=ordinary_id,
    token_hash=hash_session_token("raised-limit-device"),
    expires_at=future,
)
with get_connection() as conn:
    active_after_raise = {
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL",
            (ordinary_id,),
        )
    }
assert len(active_after_raise) == 4 and active_before_raise.issubset(active_after_raise)
assert configured_limit("2") == 2
init_db()
with get_connection() as conn:
    lowered_once = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL ORDER BY id",
            (ordinary_id,),
        )
    ]
assert len(lowered_once) == 2
init_db()
with get_connection() as conn:
    lowered_twice = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM trusted_devices WHERE user_id=? AND revoked_at IS NULL ORDER BY id",
            (ordinary_id,),
        )
    ]
assert lowered_twice == lowered_once

# Help renders the live configured value rather than a policy literal.
assert configured_limit("20") == 20
help_client = TestClient(app, follow_redirects=False)
assert help_client.post(
    "/login", data={"deputy_email": "other@example.invalid", "pin": "1357"}
).status_code == 303
help_response = help_client.get("/help")
assert help_response.status_code == 200
assert "up to 20 trusted devices per account" in help_response.text

print("0.5.2 trusted-device and contractor atomicity smoke ok")
