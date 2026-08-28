from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp(prefix="redeputy-054-"))
os.environ.update(
    DB_PATH=str(tmp / "patch.sqlite3"),
    DATA_DIR=str(tmp),
    APP_SECRET_KEY="patch-054-test-key",
    COOKIE_SECURE="false",
    SIGNUP_ENABLED="true",
    TRUSTED_DEVICE_LIMIT="7",
)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.account_invitations import account_invite_details
from app.contractors import invite_details
from app.database import (
    create_app_user,
    create_trusted_device,
    delete_never_published_roster_day,
    get_connection,
    init_db,
    never_published_draft_deletion_status,
    publish_roster_day,
    save_roster_day,
)
from app.main import app
from app.security import SESSION_COOKIE_NAME, hash_pin, hash_session_token


def decode_handoff(location: str) -> dict[str, object]:
    fragment = urlsplit(location).fragment
    assert fragment.startswith("redeputy-invite=")
    encoded = fragment.split("=", 1)[1]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))


def invitation_row(table: str, invite_id: int) -> dict[str, object]:
    with get_connection() as conn:
        return dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (invite_id,)).fetchone())


def invitation_counts() -> tuple[int, int, int, int]:
    with get_connection() as conn:
        return tuple(int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in (
            "account_invitations", "contractor_invites", "app_users", "trusted_devices"
        ))


def assert_24_hours(expires_at: str) -> None:
    remaining = datetime.fromisoformat(expires_at) - datetime.now().astimezone()
    assert timedelta(hours=23, minutes=59) <= remaining <= timedelta(hours=24, minutes=1), remaining


def create_draft(suffix: str, assignments: list[dict[str, object]] | None = None, *, custom_location: str = "") -> int:
    return save_roster_day(
        roster_day_id=None,
        roster_date=f"2026-09-{int(suffix):02d}",
        track_key=f"fixture-{suffix}",
        track_label="Avondale",
        race_type="",
        day_type="race_day",
        start_origin="Office / Clow Place",
        finish_destination="Office / Clow Place",
        office_start="08:00",
        on_track_time="09:00",
        first_race_time="12:00",
        last_race_time="16:30",
        race_count=8,
        notes="",
        hotel_assignments="[]",
        title="Avondale",
        custom_location=custom_location,
        updated_by_user_id=admin_id,
        assignments=assignments or [],
    )


def assert_fk_ok() -> None:
    with get_connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


init_db()
admin = create_app_user(
    deputy_email="admin@example.invalid",
    display_name="Release Admin",
    pin_hash=hash_pin("2468"),
    deputy_web_url="",
    encrypted_email="",
    encrypted_password="",
)
admin_id = int(admin["id"])
with get_connection() as conn:
    conn.execute("UPDATE app_users SET is_admin=1,last_activity_at='2026-08-27T18:42:13+12:00' WHERE id=?", (admin_id,))
admin_token = "patch-054-admin-session"
create_trusted_device(
    user_id=admin_id,
    token_hash=hash_session_token(admin_token),
    expires_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat(),
)
client = TestClient(app, follow_redirects=False)
client.cookies.set(SESSION_COOKIE_NAME, admin_token)
public_client = TestClient(app, follow_redirects=False)
origin = {"origin": "http://testserver"}

# Ordinary invitation creation is PRG and carries the one-time token only in a client fragment.
account_create = client.post(
    "/admin/account-invitations",
    data={"account_email": "invited@example.invalid", "display_name": "Invited Person"},
    headers=origin,
)
assert account_create.status_code == 303
account_location = account_create.headers["location"]
account_handoff = decode_handoff(account_location)
assert account_handoff["kind"] == "account"
account_id = int(account_handoff["id"])
account_token = str(account_handoff["token"])
account_before = invitation_row("account_invitations", account_id)
assert_24_hours(str(account_before["expires_at"]))
assert account_before["token_hash"] == hashlib.sha256(account_token.encode()).hexdigest()
assert account_token not in parse_qs(urlsplit(account_location).query).values()
assert account_token not in json.dumps(account_before)

admin_get = urlsplit(account_location)._replace(fragment="").geturl()
counts_before_refresh = invitation_counts()
assert client.get(admin_get).status_code == 200
assert client.get(admin_get).status_code == 200
assert invitation_counts() == counts_before_refresh
assert invitation_row("account_invitations", account_id) == account_before

# Repeated account-link GET/HEAD scans are read-only and a failed activation remains usable.
scan_counts = invitation_counts()
for _ in range(2):
    response = public_client.get(f"/account/invite/{account_token}")
    assert response.status_code == 200 and "Activate Re-Deputy" in response.text
head = public_client.head(f"/account/invite/{account_token}")
assert head.status_code == 200 and head.headers["cache-control"] == "private, no-store"
assert invitation_counts() == scan_counts
assert invitation_row("account_invitations", account_id) == account_before
failed = public_client.post(
    f"/account/invite/{account_token}",
    data={"display_name": "Invited Person", "pin": "1234", "pin_confirm": "9999"},
)
assert failed.status_code == 400
assert account_invite_details(account_token)["available"]
activated = public_client.post(
    f"/account/invite/{account_token}",
    data={"display_name": "Invited Person", "pin": "1234", "pin_confirm": "1234"},
)
assert activated.status_code == 303 and activated.headers["location"] == "/month"
assert invitation_row("account_invitations", account_id)["consumed_at"]

# Explicit account reissue is the only path that replaces a still-pending token.
pending = client.post(
    "/admin/account-invitations",
    data={"account_email": "reissue@example.invalid", "display_name": "Reissue Person"},
    headers=origin,
)
old = decode_handoff(pending.headers["location"])
old_id, old_token = int(old["id"]), str(old["token"])
reissued = client.post(f"/admin/account-invitations/{old_id}/reissue", headers=origin)
assert reissued.status_code == 303
new = decode_handoff(reissued.headers["location"])
assert int(new["id"]) != old_id and str(new["token"]) != old_token
assert invitation_row("account_invitations", old_id)["revoked_at"]
assert not account_invite_details(old_token)["available"]
assert account_invite_details(str(new["token"]))["available"]
assert_24_hours(str(new["expiresAt"]))

# Contractor invitations follow the same fragment, expiry, scanner, failure, and reissue rules.
contractor_create = client.post(
    "/admin/contractors/invites",
    data={"contractor_name": "Fixture Contractor", "company": "Fixture Ltd"},
    headers=origin,
)
assert contractor_create.status_code == 303
contractor_handoff = decode_handoff(contractor_create.headers["location"])
contractor_id = int(contractor_handoff["id"])
contractor_token = str(contractor_handoff["token"])
contractor_before = invitation_row("contractor_invites", contractor_id)
assert_24_hours(str(contractor_before["expires_at"]))
contractor_counts = invitation_counts()
for _ in range(2):
    response = public_client.get(f"/contractor/invite/{contractor_token}")
    assert response.status_code == 200 and "Activate access" in response.text
assert public_client.head(f"/contractor/invite/{contractor_token}").status_code == 200
assert invitation_counts() == contractor_counts
assert invitation_row("contractor_invites", contractor_id) == contractor_before
failed = public_client.post(f"/contractor/invite/{contractor_token}", data={"pin": "1234", "pin_confirm": "9999"})
assert failed.status_code == 303 and invite_details(contractor_token)["available"]
contractor_reissue = client.post(f"/admin/contractors/invites/{contractor_id}/reissue", headers=origin)
new_contractor = decode_handoff(contractor_reissue.headers["location"])
assert invitation_row("contractor_invites", contractor_id)["revoked_at"]
assert not invite_details(contractor_token)["available"]
assert invite_details(str(new_contractor["token"]))["available"]
assert_24_hours(str(new_contractor["expiresAt"]))
contractor_client = TestClient(app, follow_redirects=False)
contractor_activated = contractor_client.post(
    f"/contractor/invite/{new_contractor['token']}",
    data={"pin": "8642", "pin_confirm": "8642"},
)
assert contractor_activated.status_code == 303 and contractor_activated.headers["location"] == "/contractor"
assert invitation_row("contractor_invites", int(new_contractor["id"]))["consumed_at"]

# Empty drafts, assignments, Open/TBC positions, and draft-only audit children delete atomically.
empty_id = create_draft("01")
assert never_published_draft_deletion_status(empty_id)["allowed"]
assert delete_never_published_roster_day(empty_id)["deleted"]

child_id = create_draft("02", [
    {"assignment_key": "open-fixture", "role_label": "Camera", "assignment_state": "open"},
    {"assignment_key": "tbc-fixture", "role_label": "Replay", "assignment_state": "tbc"},
])
now = datetime.now().astimezone().isoformat(timespec="seconds")
with get_connection() as conn:
    conn.execute(
        "INSERT INTO roster_day_assignments(roster_day_id,position_label,assignee_label,created_at,updated_at) VALUES(?,?,?,?,?)",
        (child_id, "Legacy role", "TBC", now, now),
    )
    conn.execute(
        "INSERT INTO workday_audit_events(roster_day_id,assignment_key,event_type,details,created_at) VALUES(?,?,?,?,?)",
        (child_id, "open-fixture", "draft_fixture", "{}", now),
    )
assert delete_never_published_roster_day(child_id)["deleted"]
with get_connection() as conn:
    for table in ("roster_days", "roster_day_assignments", "workday_assignments", "workday_audit_events"):
        column = "id" if table == "roster_days" else "roster_day_id"
        assert conn.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (child_id,)).fetchone() is None

# Published state, forged version history, and retained Deputy/write relationships all block deletion.
published_id = create_draft("03")
publish_roster_day(published_id, '{"fixture":true}', admin_id)
assert not never_published_draft_deletion_status(published_id)["allowed"]
assert not delete_never_published_roster_day(published_id)["deleted"]

version_id = create_draft("04")
with get_connection() as conn:
    conn.execute(
        "INSERT INTO roster_day_versions(roster_day_id,version_number,snapshot,published_by_user_id,published_at) VALUES(?,?,?,?,?)",
        (version_id, 1, "{}", admin_id, now),
    )
assert not delete_never_published_roster_day(version_id)["deleted"]

deputy_id = create_draft("05")
with get_connection() as conn:
    conn.execute(
        """INSERT INTO deputy_roster_links(
               tenant_host,workday_id,stable_assignment_key,deputy_employee_id,deputy_unit_id,
               context_type,ownership,created_at,updated_at
           ) VALUES(?,?,?,?,?,'production','observed',?,?)""",
        ("fixture.au.deputy.com", deputy_id, "retained-fixture", 44, 55, now, now),
    )
assert not delete_never_published_roster_day(deputy_id)["deleted"]

write_id = create_draft("08")
with get_connection() as conn:
    conn.execute(
        """INSERT INTO deputy_write_operations(
               operation_uuid,app_user_id,tenant_host,deputy_user_id,deputy_employee_id,
               permission_hash,permission_snapshot,workday_id,stable_assignment_key,
               operation_type,desired_state,status,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), admin_id, "fixture.au.deputy.com", 1, 2, "hash", "{}", write_id,
            "retained-write-fixture", "create", "{}", "verified", now, now,
        ),
    )
assert not delete_never_published_roster_day(write_id)["deleted"]
assert not delete_never_published_roster_day(999999)["deleted"]

# Route authorization and normal-notice behavior are safe for blocked and missing IDs.
blocked_route = client.post(f"/admin/roster-days/{published_id}/delete", headers=origin)
assert blocked_route.status_code == 303 and "cannot+be+deleted" in blocked_route.headers["location"]
assert blocked_route.headers["location"].endswith("#manual-work-days")
missing_route = client.post("/admin/roster-days/999999/delete", headers=origin)
assert missing_route.status_code == 303 and missing_route.headers["location"].endswith("#manual-work-days")

ordinary = create_app_user(
    deputy_email="ordinary@example.invalid", display_name="Ordinary", pin_hash=hash_pin("1357"),
    deputy_web_url="", encrypted_email="", encrypted_password="",
)
ordinary_token = "patch-054-ordinary-session"
create_trusted_device(
    user_id=int(ordinary["id"]), token_hash=hash_session_token(ordinary_token),
    expires_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat(),
)
ordinary_client = TestClient(app, follow_redirects=False)
ordinary_client.cookies.set(SESSION_COOKIE_NAME, ordinary_token)
denied_id = create_draft("06")
assert ordinary_client.post(f"/admin/roster-days/{denied_id}/delete", headers=origin).status_code == 403
assert never_published_draft_deletion_status(denied_id)["allowed"]

# The reproducible Avondale mismatch is presentation-only: race-day review prefers track over stale custom_location.
avondale_id = create_draft("07", custom_location="Office / Clow Place")
builder = client.get(f"/admin/roster-days/{avondale_id}?mode=review")
assert builder.status_code == 200
assert "<dt>Location</dt><dd>Avondale</dd>" in builder.text
assert "<dt>Location</dt><dd>Office / Clow Place</dd>" not in builder.text

# Structural UI checks cover compact panels, exact shared icons, date-only Help contacts, and scripts.
admin_page = client.get("/admin")
assert admin_page.status_code == 200
assert 'data-admin-disclosure-key="manual-overrides"' in admin_page.text
assert '<strong>Manual overrides</strong>' in admin_page.text
admin_template = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
location_head = re.search(r'<div class="location-management-head"[^>]*>(.*?)</div>', admin_template, re.S)
assert location_head and all(label in location_head.group(1) for label in (
    "Location + details", "Travel", "Source / evidence", "Team / active", "Actions"
))
assert location_head.group(1).count("<span>") == 5
assert "/static/admin-context.js" in admin_page.text and "/static/admin-invitations.js" in admin_page.text
assert 'data-admin-disclosure-key="manual-work-days"' in admin_page.text
help_page = client.get("/help")
assert help_page.status_code == 200
assert "Calendar / List" in help_page.text and 'class="help-term"' in help_page.text
assert "Last active Thu 27 Aug" in help_page.text
assert "18:42" not in help_page.text
assert "up to 7 trusted devices" in help_page.text

assert_fk_ok()
print("0.5.4 invitation, draft deletion, Admin continuity, and Help smoke ok")
