from __future__ import annotations

import os
import sys
import tempfile
import requests
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

tmp = Path(tempfile.mkdtemp(prefix="redeputy-release-"))
os.environ.update({
    "DB_PATH": str(tmp / "app.sqlite3"), "DATA_DIR": str(tmp),
    "APP_SECRET_KEY": "release-smoke-key", "COOKIE_SECURE": "false",
    "TRUSTED_PROXY_SOURCES": "10.0.0.8",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.contractors import activate_invite, authenticate_contractor_link, create_invite, deactivate_inactive_contractors, invite_details, revoke_invite
from app.database import create_app_user, create_trusted_device, fetch_shift, get_connection, init_db, update_shift_marks
from app.deputy_integration import (
    begin_oauth, build_v2_shift_payload, complete_oauth, connection_status, disconnect, execute_operation, extract_v2_shift, load_config, normalize_v2_response, permission_hash,
    prepare_operation, refresh_references, save_config, trial_host_allowed, verify_read_access,
    verify_write_readiness,
)
from app.main import app, decorate_shift, templates
from app.notifications import generate_due_notifications, save_notification_preferences
from app.security import SESSION_COOKIE_NAME, hash_pin, hash_session_token
from fastapi.testclient import TestClient


class Response:
    def __init__(self, status: int, body: object): self.status_code, self._body, self.text = status, body, ""
    def json(self): return self._body


class FakeDeputy:
    def __init__(self, user_id: int = 51, employee_id: int = 17, *, token: str = "ACCESS-SECRET", permissions: list[str] | None = None, name: str = "Trial Manager", deny_resources: set[str] | None = None, timeout_resources: set[str] | None = None, endpoint: str = "trial-safe.au.deputy.com", me_status: int = 200, me_timeout: bool = False):
        self.user_id, self.employee_id, self.token, self.permissions, self.name = user_id, employee_id, token, list(permissions if permissions is not None else ["Can_Roster_Manage"]), name
        self.calls, self.authorization_headers, self.rosters, self.deny_resources, self.timeout_resources, self.token_posts, self.endpoint, self.me_status, self.me_timeout = [], [], {}, set(deny_resources or set()), set(timeout_resources or set()), [], endpoint, me_status, me_timeout
        self.next_roster_id = 76
    def post(self, url: str, **kwargs: object):
        self.calls.append(("TOKEN", url)); self.token_posts.append((url, dict(kwargs.get("data") or {})))
        return Response(200, {"access_token": self.token, "refresh_token": f"ROTATED-{self.token}", "expires_in": 3600, "scope": "longlife_refresh_token", "endpoint": self.endpoint})
    def request(self, method: str, url: str, **kwargs: object):
        self.calls.append((method, url))
        self.authorization_headers.append(str((kwargs.get("headers") or {}).get("Authorization") or ""))
        if url.endswith("/api/v1/me"):
            if self.me_timeout: raise requests.Timeout("fixture /me timeout")
            return Response(self.me_status, {"UserId": self.user_id, "EmployeeId": self.employee_id, "Name": self.name, "Permissions": self.permissions})
        if method == "POST" and "/api/v1/resource/Employee/QUERY" in url:
            if "Employee" in self.timeout_resources: raise requests.Timeout("fixture Employee timeout")
            return Response(403, {}) if "Employee" in self.deny_resources else Response(200, [{"Id": 22, "UserId": 122, "DisplayName": "Readable Worker", "Active": True}])
        if method == "POST" and "/api/v1/resource/OperationalUnit/QUERY" in url:
            if "OperationalUnit" in self.timeout_resources: raise requests.Timeout("fixture OperationalUnit timeout")
            return Response(403, {}) if "OperationalUnit" in self.deny_resources else Response(200, [{"Id": 10, "OperationalUnitName": "Director", "Active": True, "ShowOnRoster": True}])
        if method == "POST" and "/api/v1/resource/Roster/QUERY" in url:
            rows = []
            for roster in self.rosters.values():
                rows.append({"Id": roster["id"], "Employee": roster["employee"], "OperationalUnit": roster["area"],
                             "StartTime": roster["start"], "EndTime": roster["end"], "Mealbreak": int(round(float(roster.get("mealbreakDuration") or 0) * 60)),
                             "Comment": roster.get("note", ""), "Open": roster.get("isOpen", False), "Published": roster.get("isPublished", False)})
            return Response(200, rows)
        if method == "POST" and url.endswith("/api/management/v2/shifts"):
            envelope = dict(kwargs.get("json") or {}); assert set(envelope) == {"data"} and set(envelope["data"]) == {"shift", "override"}
            self.next_roster_id += 1
            desired = dict(envelope["data"]["shift"]); desired.update({"id": self.next_roster_id, "canEdit": True, "mealbreakDuration": sum(int(s["end"])-int(s["start"]) for s in desired.get("mealbreakSlots", []))/3600, "isPublished": False, "timesheet": 0}); self.rosters[self.next_roster_id] = desired
            return Response(200, {"success": True, "data": desired})
        if method == "PUT" and "/api/management/v2/shifts/" in url:
            roster_id = int(url.rsplit("/", 1)[-1]); desired = dict((kwargs.get("json") or {})["data"]["shift"])
            desired.update({"id": roster_id, "canEdit": True, "mealbreakDuration": sum(int(s["end"])-int(s["start"]) for s in desired.get("mealbreakSlots", []))/3600, "isPublished": self.rosters[roster_id].get("isPublished", False), "timesheet": 0}); self.rosters[roster_id] = desired
            return Response(200, {"success": True, "data": desired})
        if method == "POST" and url.endswith("/api/v1/supervise/roster/publish"):
            for roster_id in (kwargs.get("json") or {}).get("intRosterArray", []): self.rosters[int(roster_id)]["isPublished"] = True
            return Response(200, {"success": True})
        if method == "GET" and "/api/management/v2/shifts/" in url:
            roster_id = int(url.rsplit("/", 1)[-1])
            return Response(200, {"success": True, "data": self.rosters[roster_id]}) if roster_id in self.rosters else Response(404, {"success": False, "error": {"code": "NOT_FOUND"}})
        if method == "DELETE" and "/api/management/v2/shifts/" in url:
            roster_id = int(url.rsplit("/", 1)[-1]); self.rosters.pop(roster_id, None); return Response(200, {"success": True})
        raise AssertionError((method, url, kwargs))


class UnknownCreate(FakeDeputy):
    def __init__(self):
        super().__init__(); self.create_lost = False
    def request(self, method: str, url: str, **kwargs: object):
        if method == "POST" and url.endswith("/api/management/v2/shifts"):
            self.create_lost = True
            raise requests.Timeout("response lost")
        if method == "POST" and url.endswith("/api/v1/resource/Roster/QUERY"):
            if not self.create_lost: return Response(200, [])
            return Response(200, [{"Id": 88, "Employee": 22, "OperationalUnit": 10, "StartTime": "2026-08-21T09:00:00+12:00", "EndTime": "2026-08-21T17:00:00+12:00", "Mealbreak": 0, "Comment": "Re-Deputy reconcile", "Open": False}])
        if method == "GET" and url.endswith("/api/management/v2/shifts/88"):
            return Response(200, {"success": True, "data": {"id": 88, "employee": 22, "area": 10, "start": "2026-08-21T09:00:00+12:00", "end": "2026-08-21T17:00:00+12:00", "mealbreakDuration": 0, "mealbreakSlots": [], "note": "Re-Deputy reconcile", "isOpen": False, "isPublished": False, "approvalRequired": False, "canEdit": True, "timesheet": 0}})
        return super().request(method, url, **kwargs)


class PreflightTimeout(FakeDeputy):
    def request(self, method: str, url: str, **kwargs: object):
        if method == "POST" and "/api/v1/resource/Roster/QUERY" in url:
            raise requests.Timeout("fixture timeout before CREATE transmission")
        return super().request(method, url, **kwargs)


init_db(); init_db()
assert load_config()["write_mode"] == "off"
for template_name in templates.env.list_templates():
    templates.env.get_template(template_name)
admin = create_app_user(deputy_email="admin@example.invalid", display_name="Admin", pin_hash=hash_pin("1234"), deputy_web_url="https://example.invalid", encrypted_email="", encrypted_password="")
admin_id = int(admin["id"])

with get_connection() as conn:
    shift_id = int(conn.execute("""INSERT INTO shifts(source_uid,title,start_at,end_at,date,raw_hours,paid_hours,break_minutes,owner_user_id,source_payload)
                                 VALUES('personal','[T-Test] DIR','2026-08-20T09:30:00+12:00','2026-08-20T20:00:00+12:00','2026-08-20',10.5,10.5,0,?,'{}')""", (admin_id,)).lastrowid)
update_shift_marks(shift_id, {"personal_start_time": "08:45", "personal_finish_time": "21:30"}, owner_user_id=admin_id)
personal = decorate_shift(fetch_shift(shift_id, owner_user_id=admin_id))
assert personal["display_start_label"] == "08:45" and personal["display_end_label"] == "21:30"
assert personal["display_hours_label"] == "12h 45m" and personal["personal_time_active"]
update_shift_marks(shift_id, {"personal_start_time": "08:45", "personal_finish_time": ""}, owner_user_id=admin_id)
start_only = decorate_shift(fetch_shift(shift_id, owner_user_id=admin_id))
assert start_only["display_start_label"] == "08:45" and start_only["display_end_label"] != "21:30"
update_shift_marks(shift_id, {"personal_start_time": "", "personal_finish_time": "21:30"}, owner_user_id=admin_id)
finish_only = decorate_shift(fetch_shift(shift_id, owner_user_id=admin_id))
assert finish_only["display_start_label"] != "08:45" and finish_only["display_end_label"] == "21:30"
update_shift_marks(shift_id, {"personal_start_time": "", "personal_finish_time": ""}, owner_user_id=admin_id)
cleared = decorate_shift(fetch_shift(shift_id, owner_user_id=admin_id))
assert not cleared["personal_time_active"] and cleared["display_start_label"] != "08:45"
update_shift_marks(shift_id, {"personal_start_time": "08:45", "personal_finish_time": ""}, owner_user_id=admin_id)
save_notification_preferences(admin_id, {"enabled": True, "night_before": True, "reminder_time": "19:00"})
generate_due_notifications(datetime.fromisoformat("2026-08-19T19:01:00+12:00"))
with get_connection() as conn:
    reminder = conn.execute("SELECT body FROM notification_events WHERE app_user_id=? AND event_type='night_before'", (admin_id,)).fetchone()
assert reminder is not None and "09:30" in str(reminder["body"]) and "08:45" not in str(reminder["body"])

with get_connection() as conn:
    person_id = int(conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,created_at,updated_at) VALUES('Restricted Contractor',1,?,?)", (datetime.now().isoformat(), datetime.now().isoformat())).lastrowid)
invite = create_invite(person_id, admin_id)
with get_connection() as conn:
    stored = conn.execute("SELECT token_hash FROM contractor_invites WHERE id=?", (invite["id"],)).fetchone()["token_hash"]
assert invite["token"] not in stored and invite_details(str(invite["token"]))["available"]
contractor = activate_invite(str(invite["token"]), "9876")
assert contractor["account_type"] == "contractor" and not contractor["is_admin"]
assert int(authenticate_contractor_link(str(invite["token"]), "9876")["id"]) == int(contractor["id"])
try: activate_invite(str(invite["token"]), "9876")
except ValueError: pass
else: raise AssertionError("Contractor invite replay was accepted")
replacement = create_invite(person_id, admin_id)
replacement_user = activate_invite(str(replacement["token"]), "6789")
assert int(replacement_user["id"]) == int(contractor["id"]) and int(authenticate_contractor_link(str(replacement["token"]), "6789")["id"]) == int(contractor["id"])
with get_connection() as conn:
    expired_person = int(conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,created_at,updated_at) VALUES('Expired Contractor',1,?,?)", (now := datetime.now().isoformat(), now)).lastrowid)
expired = create_invite(expired_person, admin_id)
with get_connection() as conn: conn.execute("UPDATE contractor_invites SET expires_at=? WHERE id=?", ((datetime.now().astimezone() - timedelta(days=1)).isoformat(), expired["id"]))
try: activate_invite(str(expired["token"]), "9876")
except ValueError: pass
else: raise AssertionError("Expired contractor invite was accepted")
with get_connection() as conn:
    revoked_person = int(conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,created_at,updated_at) VALUES('Revoked Contractor',1,?,?)", (now, now)).lastrowid)
revoked = create_invite(revoked_person, admin_id); revoke_invite(int(revoked["id"]))
try: activate_invite(str(revoked["token"]), "9876")
except ValueError: pass
else: raise AssertionError("Revoked contractor invite was accepted")

ordinary = create_app_user(deputy_email="user@example.invalid", display_name="Ordinary", pin_hash=hash_pin("4567"), deputy_web_url="https://example.invalid", encrypted_email="", encrypted_password="")
def client_for(user_id: int, token: str) -> TestClient:
    create_trusted_device(user_id=user_id, token_hash=hash_session_token(token), expires_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat())
    client = TestClient(app, follow_redirects=False); client.cookies.set(SESSION_COOKIE_NAME, token); return client
ordinary_client = client_for(int(ordinary["id"]), "ordinary-session")
assert ordinary_client.get("/admin").status_code == 403
assert ordinary_client.post(f"/admin/roster-days/1/deputy-trial/execute", data={"confirm": "CONFIRM"}).status_code == 403
assert ordinary_client.post("/settings/deputy-api-test", headers={"origin": "http://testserver"}).status_code == 403
for method, path in (("get", "/settings/deputy-api/callback"), ("post", "/settings/deputy-api/connect"),
                     ("post", "/settings/deputy-api/recheck"), ("post", "/settings/deputy-api/disconnect")):
    assert getattr(ordinary_client, method)(path, headers={"origin": "http://testserver"}).status_code == 403
contractor_client = client_for(int(contractor["id"]), "contractor-session")
contractor_month = contractor_client.get("/month")
assert contractor_month.status_code == 200 and "Global crew calendar" not in contractor_month.text
assert contractor_client.get("/month?scope=global").status_code == 403 and contractor_client.get("/admin").status_code == 403
assert contractor_client.get("/settings").status_code == 200 and contractor_client.get("/help").status_code == 200
assert contractor_client.get("/sync-now").status_code == 405
assert contractor_client.get("/settings/deputy-api/callback").status_code == 403
assert contractor_client.post("/settings/deputy-api/recheck", headers={"origin": "http://testserver"}).status_code == 403
assert contractor_client.post("/settings/deputy-api/connect", data={"tenant": "trial.example.deputy.com"}, headers={"origin": "http://testserver"}).status_code == 403
assert contractor_client.post("/settings/deputy-api/disconnect", headers={"origin": "http://testserver"}).status_code == 403
contractor_home = contractor_client.get("/contractor")
assert contractor_home.status_code == 303 and contractor_home.headers["location"] == "/month"
with get_connection() as conn:
    own_day = int(conn.execute("INSERT INTO roster_days(roster_date,track_key,track_label,status,created_at,updated_at) VALUES('2026-08-22','own','Own Day','published',?,?)", (now, now)).lastrowid)
    other_day = int(conn.execute("INSERT INTO roster_days(roster_date,track_key,track_label,status,created_at,updated_at) VALUES('2026-08-23','other','Other Day','published',?,?)", (now, now)).lastrowid)
    conn.execute("INSERT INTO workday_user_visibility(roster_day_id,user_id,canonical_person_id,created_at,updated_at) VALUES(?,?,?,?,?)", (own_day, int(contractor["id"]), person_id, now, now))
    own_shift = int(conn.execute("""INSERT INTO shifts(source_uid,title,start_at,end_at,date,raw_hours,paid_hours,break_minutes,owner_user_id,source_payload)
        VALUES('contractor-own','[T-Test] Camera','2026-08-22T09:00:00+12:00','2026-08-22T17:00:00+12:00','2026-08-22',8,8,0,?,'{}')""", (int(contractor["id"]),)).lastrowid)
    other_shift = int(conn.execute("""INSERT INTO shifts(source_uid,title,start_at,end_at,date,raw_hours,paid_hours,break_minutes,owner_user_id,source_payload)
        VALUES('contractor-other','[T-Test] Camera','2026-08-22T09:00:00+12:00','2026-08-22T17:00:00+12:00','2026-08-22',8,8,0,?,'{}')""", (admin_id,)).lastrowid)
    conn.execute("""INSERT INTO track_maps(track_key,track_label,course_label,file_name,content_type,status,checked_at,updated_at)
        VALUES('taupo','Taupo','Taupo','taupo-fixture.gif','image/gif','ok',?,?)""", (now, now))
(tmp / "track_maps").mkdir(exist_ok=True)
(tmp / "track_maps" / "taupo-fixture.gif").write_bytes(
    bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
)
assert contractor_client.get(f"/shift/{own_shift}").status_code == 303
assert contractor_client.post(f"/shift/{own_shift}/marks", data={"checked": "1"}, headers={"origin": "http://testserver"}).status_code == 303
assert contractor_client.get(f"/shift/{other_shift}").status_code == 404
assert contractor_client.post(f"/shift/{other_shift}/marks", data={"checked": "1"}, headers={"origin": "http://testserver"}).status_code == 404
track_map_response = contractor_client.get("/track-map/taupo")
assert track_map_response.status_code == 200 and track_map_response.headers["content-type"] == "image/gif"
assert TestClient(app, follow_redirects=False).get("/track-map/taupo").status_code in {303, 307}
assert contractor_client.post(f"/contractor/workdays/{other_day}/personal-time", data={"personal_start_time": "08:00"}, headers={"origin": "http://testserver"}).status_code == 403
assert contractor_client.post(f"/contractor/workdays/{own_day}/personal-time", data={"personal_start_time": "08:00"}, headers={"origin": "http://testserver"}).status_code == 303
assert contractor_client.post(f"/contractor/workdays/{other_day}/self-travel", data={"self_travel": "1"}, headers={"origin": "http://testserver"}).status_code == 403
assert contractor_client.get("/day/2026-08-22").status_code == 200
assert contractor_client.get("/day/2026-08-22?scope=global").status_code == 403
assert contractor_client.get("/timesheet/2026-08-22").status_code == 200
with get_connection() as conn:
    conn.execute("UPDATE app_users SET last_activity_at=? WHERE id=?", ((datetime.now().astimezone() - timedelta(days=181)).isoformat(), int(contractor["id"])))
assert deactivate_inactive_contractors() == 1
with get_connection() as conn: assert not int(conn.execute("SELECT is_active FROM app_users WHERE id=?", (int(contractor["id"]),)).fetchone()["is_active"])

host = "trial-safe.au.deputy.com"
save_config(client_id="client", client_secret="OAUTH-SECRET", callback_origin="https://redeputy.example", write_mode="trial", allowed_hosts=host, actor_user_id=admin_id)
admin_client = client_for(admin_id, "admin-session")
assert admin_client.get("/settings/deputy-api/callback").status_code == 303
assert admin_client.post("/settings/deputy-api/connect", headers={"origin": "http://testserver"}).status_code == 303
assert admin_client.post("/settings/deputy-api/recheck", headers={"origin": "http://testserver"}).status_code == 303
assert admin_client.post("/settings/deputy-api/disconnect", headers={"origin": "http://testserver"}).status_code == 303

# Public HTTPS is reconstructed only for the explicitly trusted tunnel peer.
import importlib
main_module = importlib.import_module("app.main")
original_queue_manual_sync = main_module.queue_manual_sync
main_module.queue_manual_sync = lambda _tasks, user_id=None: True
proxy_headers = {
    "Origin": "https://reviewer.example",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "reviewer.example",
}


class PeerAddressApp:
    def __init__(self, wrapped, host):
        self.wrapped = wrapped
        self.host = host

    async def __call__(self, scope, receive, send):
        scope = {**scope, "client": (self.host, 50000)}
        await self.wrapped(scope, receive, send)


trusted_proxy = TestClient(PeerAddressApp(app, "10.0.0.8"), base_url="http://app:8000", follow_redirects=False)
untrusted_proxy = TestClient(PeerAddressApp(app, "10.0.0.9"), base_url="http://app:8000", follow_redirects=False)
direct_lan = TestClient(
    PeerAddressApp(app, "192.168.0.50"), base_url="http://192.168.0.238:8000", follow_redirects=False,
)
for proxy_client in (trusted_proxy, untrusted_proxy, direct_lan):
    proxy_client.cookies.set(SESSION_COOKIE_NAME, "admin-session")
try:
    public_mutations = (
        ("/sync-now", {}),
        (f"/shift/{shift_id}/marks", {"checked": "1"}),
        ("/settings/theme", {"theme": "jade"}),
        ("/admin/clear-changed", {}),
    )
    for path, data in public_mutations:
        accepted = trusted_proxy.post(path, data=data, headers=proxy_headers)
        assert accepted.status_code == 303, (path, accepted.status_code, accepted.text)
        spoofed = untrusted_proxy.post(path, data=data, headers=proxy_headers)
        assert spoofed.status_code == 403, (path, spoofed.status_code, spoofed.text)
        direct = direct_lan.post(path, data=data, headers={"Origin": "http://192.168.0.238:8000"})
        assert direct.status_code == 303, (path, direct.status_code, direct.text)
finally:
    main_module.queue_manual_sync = original_queue_manual_sync
with get_connection() as conn:
    alias_people = conn.execute("SELECT id FROM crew_people WHERE app_user_id IN (?,?) ORDER BY id", (admin_id, int(ordinary["id"]))).fetchall()
    assert len(alias_people) == 2
    first_alias_id, second_alias_id = (int(row["id"]) for row in alias_people)
    now_alias = datetime.now().astimezone().isoformat()
    conn.execute("INSERT INTO crew_aliases(person_id,alias,normalized_alias,created_at,updated_at) VALUES(?,?,?,?,?)", (first_alias_id, "Only Alpha", "onlyalpha", now_alias, now_alias))
    conn.execute("INSERT INTO crew_aliases(person_id,alias,normalized_alias,created_at,updated_at) VALUES(?,?,?,?,?)", (second_alias_id, "Only Beta", "onlybeta", now_alias, now_alias))
admin_page = admin_client.get("/admin")
assert admin_page.status_code == 200 and "OAUTH-SECRET" not in admin_page.text and "Deputy API" in admin_page.text
assert f'id="crew-aliases-{first_alias_id}" name="aliases"' in admin_page.text
assert f'id="crew-aliases-{second_alias_id}" name="aliases"' in admin_page.text
first_alias_markup = admin_page.text.split(f'id="crew-aliases-{first_alias_id}"', 1)[1].split("</textarea>", 1)[0]
second_alias_markup = admin_page.text.split(f'id="crew-aliases-{second_alias_id}"', 1)[1].split("</textarea>", 1)[0]
assert "Only Alpha" in first_alias_markup and "Only Beta" not in first_alias_markup
assert "Only Beta" in second_alias_markup and "Only Alpha" not in second_alias_markup
assert 'autocomplete="off"' in first_alias_markup and 'spellcheck="false"' in first_alias_markup
assert admin_client.post(f"/admin/users/{int(contractor['id'])}/role", data={"is_admin": "1"}, headers={"origin": "http://testserver"}).status_code == 303
with get_connection() as conn: assert not int(conn.execute("SELECT is_admin FROM app_users WHERE id=?", (int(contractor["id"]),)).fetchone()["is_admin"])
assert admin_client.post(f"/admin/users/{int(ordinary['id'])}/role", data={"is_admin": "1"}, headers={"origin": "http://testserver"}).status_code == 303
with get_connection() as conn:
    assert int(conn.execute("SELECT is_admin FROM app_users WHERE id=?", (int(ordinary["id"]),)).fetchone()["is_admin"])
    assert conn.execute("SELECT COUNT(*) n FROM app_role_audit").fetchone()["n"] == 1
oauth_url = begin_oauth(app_user_id=admin_id, tenant=host, origin="https://redeputy.example")
assert oauth_url.startswith("https://once.deputy.com/my/oauth/login?") and parse_qs(urlparse(oauth_url).query)["scope"] == ["longlife_refresh_token"]
state = parse_qs(urlparse(oauth_url).query)["state"][0]
with get_connection() as conn:
    assert conn.execute("SELECT 1 FROM deputy_oauth_states WHERE state_hash=?", (state,)).fetchone() is None
fake = FakeDeputy()
other_state = parse_qs(urlparse(begin_oauth(app_user_id=admin_id, tenant=host, origin="https://redeputy.example")).query)["state"][0]
try: complete_oauth(state=other_state, code="code", current_user_id=int(ordinary["id"]), session=fake)
except ValueError: pass
else: raise AssertionError("OAuth callback bound to another app user")
complete_oauth(state=state, code="code", current_user_id=admin_id, session=fake)
assert fake.token_posts[0][0] == "https://once.deputy.com/my/oauth/access_token"
assert fake.token_posts[0][1]["scope"] == "longlife_refresh_token" and fake.token_posts[0][1]["redirect_uri"] == "https://redeputy.example/settings/deputy-api/callback"
bad_state = parse_qs(urlparse(begin_oauth(app_user_id=admin_id)).query)["state"][0]
attacker = FakeDeputy(endpoint="attacker.example.com")
try: complete_oauth(state=bad_state, code="code", current_user_id=admin_id, session=attacker)
except ValueError: pass
else: raise AssertionError("OAuth accepted a non-Deputy returned endpoint")
assert all("attacker.example.com" not in url for url, _payload in attacker.token_posts)
try: complete_oauth(state=state, code="code", current_user_id=admin_id, session=fake)
except ValueError: pass
else: raise AssertionError("OAuth state replay was accepted")
assert trial_host_allowed(host)
for malicious in ("attacker.example.com", f"evil.{host}"):
    try: trial_host_allowed(malicious)
    except ValueError: pass
    else: raise AssertionError("Malicious hostname passed Deputy validation")

def connect_fixture(user_id: int, deputy: FakeDeputy) -> None:
    fixture_state = parse_qs(urlparse(begin_oauth(app_user_id=user_id, tenant=host, origin="https://redeputy.example")).query)["state"][0]
    complete_oauth(state=fixture_state, code="code", current_user_id=user_id, session=deputy)

james = create_app_user(deputy_email="james@example.invalid", display_name="James", pin_hash=hash_pin("2468"), deputy_web_url="https://example.invalid", encrypted_email="", encrypted_password="")
sarah = create_app_user(deputy_email="sarah@example.invalid", display_name="Sarah", pin_hash=hash_pin("1357"), deputy_web_url="https://example.invalid", encrypted_email="", encrypted_password="")
james_id, sarah_id = int(james["id"]), int(sarah["id"])
james_fake = FakeDeputy(61, 31, token="JAMES-TOKEN", name="James Deputy")
sarah_fake = FakeDeputy(62, 32, token="SARAH-TOKEN", name="Sarah Deputy")
connect_fixture(james_id, james_fake); connect_fixture(sarah_id, sarah_fake)
verify_read_access(james_id, session=james_fake); verify_read_access(sarah_id, session=sarah_fake)
assert james_fake.authorization_headers[-1] == "Bearer JAMES-TOKEN"
assert sarah_fake.authorization_headers[-1] == "Bearer SARAH-TOKEN"
for transient in (FakeDeputy(61, 31, token="JAMES-TOKEN", me_status=500), FakeDeputy(61, 31, token="JAMES-TOKEN", me_timeout=True)):
    try: verify_read_access(james_id, session=transient)
    except ValueError: pass
    else: raise AssertionError("Temporary /me failure was accepted")
    assert connection_status(james_id)["read_ready"]
try: verify_read_access(sarah_id, session=FakeDeputy(62, 32, token="SARAH-TOKEN", me_status=401))
except PermissionError: pass
else: raise AssertionError("/me 401 did not invalidate authentication")
assert not connection_status(sarah_id)["read_ready"]
connect_fixture(sarah_id, sarah_fake)

# Read/write readiness matrix and permission changes always use the current /me snapshot.
ordinary_fake = FakeDeputy(71, 41, token="ORDINARY-TOKEN", permissions=[], name="Ordinary Deputy")
connect_fixture(int(ordinary["id"]), ordinary_fake)
assert connection_status(int(ordinary["id"]))["read_ready"]
try: verify_write_readiness(int(ordinary["id"]), session=ordinary_fake)
except PermissionError as exc: assert "roster-management" in str(exc)
else: raise AssertionError("Ordinary Deputy user became write ready")

save_config(client_id="client", client_secret="", write_mode="off", allowed_hosts=host, actor_user_id=admin_id)
assert verify_read_access(admin_id, session=fake)["read_ready"]
try: verify_write_readiness(admin_id, session=fake)
except PermissionError as exc: assert "trial writes are currently disabled" in str(exc)
else: raise AssertionError("Writes were ready while trial mode was off")
# Tenant host allowlists were removed: the initiating user's exact OAuth
# identity, Deputy permission, Admin gate and controlled mode are authoritative.
save_config(client_id="client", client_secret="", write_mode="trial", allowed_hosts="other.au.deputy.com", actor_user_id=admin_id)
assert verify_write_readiness(admin_id, session=fake)["write_ready"]

mismatch_fake = FakeDeputy(71, 19, token="ORDINARY-TOKEN", permissions=[])
try: verify_read_access(int(ordinary["id"]), session=mismatch_fake)
except PermissionError as exc: assert "identity changed" in str(exc)
else: raise AssertionError("Employee identity mismatch became read ready")
assert not connection_status(int(ordinary["id"]))["read_ready"] and not connection_status(int(ordinary["id"]))["write_ready"]

lost_permission = FakeDeputy(51, 17, token="ACCESS-SECRET", permissions=[], name="Trial Manager")
assert verify_read_access(admin_id, session=lost_permission)["read_ready"]
assert not connection_status(admin_id)["roster_manage"]
try: verify_write_readiness(admin_id, session=lost_permission)
except PermissionError: pass
else: raise AssertionError("Cached roster permission survived its removal")
gained_permission = FakeDeputy(51, 17, token="ACCESS-SECRET", permissions=["Can_Roster_Manage"], name="Trial Manager")
assert verify_read_access(admin_id, session=gained_permission)["read_ready"]
assert verify_write_readiness(admin_id, session=gained_permission)["write_ready"]

partial_refresh = refresh_references(james_id, session=FakeDeputy(61, 31, token="JAMES-TOKEN", deny_resources={"OperationalUnit"}))
assert partial_refresh["employees"] == 1 and partial_refresh["units"] == 0 and "units" in partial_refresh["errors"]
assert connection_status(james_id)["read_ready"]
for resource in ("Employee", "OperationalUnit"):
    timed = refresh_references(james_id, session=FakeDeputy(61, 31, token="JAMES-TOKEN", timeout_resources={resource}))
    assert ("employees" if resource == "Employee" else "units") in timed["errors"] and connection_status(james_id)["read_ready"]
with get_connection() as conn:
    conn.execute("UPDATE deputy_oauth_connections SET token_expires_at=? WHERE app_user_id=?", ((datetime.now().astimezone() - timedelta(minutes=5)).isoformat(), james_id))
verify_read_access(james_id, session=james_fake)
assert any(method == "TOKEN" for method, _url in james_fake.calls)
refresh_url, refresh_payload = james_fake.token_posts[-1]
assert refresh_url == f"https://{host}/oauth/access_token" and refresh_payload["grant_type"] == "refresh_token"
assert refresh_payload["scope"] == "longlife_refresh_token" and refresh_payload["redirect_uri"] == "https://redeputy.example/settings/deputy-api/callback"

now = datetime.now().astimezone().isoformat(timespec="seconds")
with get_connection() as conn:
    conn.execute("INSERT INTO deputy_reference_employees(app_user_id,tenant_host,deputy_employee_id,display_name,active,last_observed_at) VALUES(?,?,?,?,1,?)", (admin_id, host, 22, "Worker", now))
    conn.execute("INSERT INTO deputy_reference_units(app_user_id,tenant_host,deputy_unit_id,display_name,active,last_observed_at) VALUES(?,?,?,?,1,?)", (admin_id, host, 10, "Director", now))
    for fixture_id in (james_id, sarah_id):
        conn.execute("INSERT OR REPLACE INTO deputy_reference_employees(app_user_id,tenant_host,deputy_employee_id,display_name,active,last_observed_at) VALUES(?,?,?,?,1,?)", (fixture_id, host, 22, "Worker", now))
        conn.execute("INSERT OR REPLACE INTO deputy_reference_units(app_user_id,tenant_host,deputy_unit_id,display_name,active,last_observed_at) VALUES(?,?,?,?,1,?)", (fixture_id, host, 10, "Director", now))
    workday_id = int(conn.execute("INSERT INTO roster_days(roster_date,track_key,track_label,status,created_at,updated_at) VALUES('2026-08-20','test','Test','published',?,?)", (now, now)).lastrowid)
    desired = {"employee": 22, "area": 10, "start": "2026-08-20T09:00:00+12:00", "end": "2026-08-20T17:00:00+12:00", "note": "Re-Deputy trial"}
try: prepare_operation(app_user_id=int(ordinary["id"]), workday_id=workday_id, assignment_key="cross-user", operation_type="create", desired=desired, session=fake)
except PermissionError: pass
else: raise AssertionError("Admin without own OAuth connection used another Admin's token")
james_prepared = prepare_operation(app_user_id=james_id, workday_id=workday_id, assignment_key="james-owned", operation_type="create", desired=desired, session=james_fake)
sarah_prepared = prepare_operation(app_user_id=sarah_id, workday_id=workday_id, assignment_key="sarah-owned", operation_type="create", desired=desired, session=sarah_fake)
for operation_uuid, wrong_owner in ((james_prepared["operation_uuid"], sarah_id), (sarah_prepared["operation_uuid"], james_id)):
    try: execute_operation(str(operation_uuid), wrong_owner, session=sarah_fake if wrong_owner == sarah_id else james_fake)
    except ValueError: pass
    else: raise AssertionError("Cross-user prepared operation was executable")
disconnect(james_id)
try: verify_read_access(james_id, session=sarah_fake)
except PermissionError: pass
else: raise AssertionError("Disconnected James fell back to Sarah's connection")
assert "Bearer SARAH-TOKEN" not in james_fake.authorization_headers
prepared = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="create", desired=desired, session=fake)
result = execute_operation(str(prepared["operation_uuid"]), admin_id, session=fake)
assert result["status"] == "verified" and result["roster_id"] == 77 and result["readback_verified"]
assert sum(1 for method, url in fake.calls if method == "POST" and url.endswith("/api/management/v2/shifts")) == 1
publish_prepared = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="publish:test", operation_type="publish", desired={"roster_ids": [77]}, session=fake)
publish_result = execute_operation(str(publish_prepared["operation_uuid"]), admin_id, session=fake)
assert publish_result["status"] == "verified" and publish_result["published_ids"] == [77]
repeat_publish = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="publish:repeat", operation_type="publish", desired={"roster_ids": [77]}, session=fake)
repeat_result = execute_operation(str(repeat_publish["operation_uuid"]), admin_id, session=fake)
assert repeat_result["unchanged"] and sum(1 for method, url in fake.calls if method == "POST" and url.endswith("/api/v1/supervise/roster/publish")) == 1
try: prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="identity-change", operation_type="create", desired=desired, session=FakeDeputy(user_id=999, employee_id=17))
except PermissionError: pass
else: raise AssertionError("Mismatched /me identity did not block write preparation")
connect_fixture(admin_id, fake)
fake.rosters[77]["canEdit"] = False; fake.rosters[77]["timesheet"] = 9
locked_prepared = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="update", desired={**desired, "note": "changed"}, roster_id=77, session=fake)
locked = execute_operation(str(locked_prepared["operation_uuid"]), admin_id, session=fake)
assert locked["status"] == "locked" and "Timesheet #9" in locked["message"] and not any(method == "PUT" for method, _url in fake.calls)
fake.rosters[77]["canEdit"] = True; fake.rosters[77]["timesheet"] = 0
try: prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="create", desired=desired, session=fake)
except ValueError: pass
else: raise AssertionError("Duplicate local create was accepted")
delete_prepared = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="delete", desired={}, roster_id=77, session=fake)
delete_result = execute_operation(str(delete_prepared["operation_uuid"]), admin_id, session=fake)
assert delete_result["status"] == "verified" and delete_result["deleted"]
with get_connection() as conn:
    deleted_op = conn.execute("SELECT before_state FROM deputy_write_operations WHERE operation_uuid=?", (delete_prepared["operation_uuid"],)).fetchone()
    assert deleted_op["before_state"] and conn.execute("SELECT deputy_roster_id FROM deputy_roster_links WHERE stable_assignment_key='director'").fetchone()["deputy_roster_id"] is None

# Deleted Re-Deputy binding followed by a real replacement POST acquires fresh Re-Deputy provenance.
replacement = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="create", desired=desired, session=fake)
replacement_result = execute_operation(str(replacement["operation_uuid"]), admin_id, session=fake)
assert replacement_result["status"] == "verified" and replacement_result["roster_id"] == 78
with get_connection() as conn:
    assert conn.execute("SELECT ownership FROM deputy_roster_links WHERE stable_assignment_key='director'").fetchone()["ownership"] == "re_deputy_created_trial"
created_update_desired = {**desired, "note": "Re-Deputy replacement update"}
created_update = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="update", desired=created_update_desired, roster_id=78, session=fake)
assert execute_operation(str(created_update["operation_uuid"]), admin_id, session=fake)["status"] == "verified"
with get_connection() as conn:
    assert conn.execute("SELECT ownership FROM deputy_roster_links WHERE stable_assignment_key='director'").fetchone()["ownership"] == "re_deputy_created_trial"
replacement_delete = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="delete", desired={}, roster_id=78, session=fake)
assert execute_operation(str(replacement_delete["operation_uuid"]), admin_id, session=fake)["status"] == "verified"

# The same stable key may then adopt an exact external roster, replacing stale delete authority.
external = dict(build_v2_shift_payload(desired)["data"]["shift"])
external.update({"id": 88, "canEdit": True, "mealbreakDuration": 0, "isPublished": False, "timesheet": 0})
fake.rosters[88] = external
adoption = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="create", desired=desired, session=fake)
adoption_result = execute_operation(str(adoption["operation_uuid"]), admin_id, session=fake)
assert adoption_result["status"] == "verified" and adoption_result["roster_id"] == 88
with get_connection() as conn:
    assert conn.execute("SELECT ownership FROM deputy_roster_links WHERE stable_assignment_key='director'").fetchone()["ownership"] == "adopted_existing"
delete_calls = sum(method == "DELETE" for method, _url in fake.calls)
adopted_delete = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="director", operation_type="delete", desired={}, roster_id=88, session=fake)
assert execute_operation(str(adopted_delete["operation_uuid"]), admin_id, session=fake)["status"] == "failed"
assert sum(method == "DELETE" for method, _url in fake.calls) == delete_calls
unknown_desired = {"employee": 22, "area": 10, "start": "2026-08-21T09:00:00+12:00", "end": "2026-08-21T17:00:00+12:00", "note": "Re-Deputy reconcile"}
unknown_fake = UnknownCreate()
unknown_prepared = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="reconcile", operation_type="create", desired=unknown_desired, session=unknown_fake)
unknown_result = execute_operation(str(unknown_prepared["operation_uuid"]), admin_id, session=unknown_fake)
assert unknown_result["status"] == "unknown" and unknown_result["roster_id"] == 88 and unknown_result["reconciled"]
with get_connection() as conn:
    assert conn.execute("SELECT ownership FROM deputy_roster_links WHERE stable_assignment_key='reconcile'").fetchone()["ownership"] == "ownership_unconfirmed"

# A timeout before POST transmission never claims an arbitrary exact roster.
preflight_fake = PreflightTimeout()
preflight_prepared = prepare_operation(app_user_id=admin_id, workday_id=workday_id, assignment_key="preflight-timeout", operation_type="create", desired=desired, session=preflight_fake)
preflight_result = execute_operation(str(preflight_prepared["operation_uuid"]), admin_id, session=preflight_fake)
assert preflight_result["status"] == "unknown" and "not known to have reached" in preflight_result["message"]
assert not any(method == "POST" and url.endswith("/api/management/v2/shifts") for method, url in preflight_fake.calls)
with get_connection() as conn:
    assert conn.execute("SELECT 1 FROM deputy_roster_links WHERE stable_assignment_key='preflight-timeout'").fetchone() is None

with get_connection() as conn:
    secret_dump = " ".join(str(value) for row in conn.execute("SELECT * FROM deputy_oauth_connections") for value in row)
assert "ACCESS-SECRET" not in secret_dump and "REFRESH-SECRET" not in secret_dump and "OAUTH-SECRET" not in secret_dump
with get_connection() as conn:
    preserved_before = {table: int(conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]) for table in ("shifts", "app_users", "notification_events", "crew_people", "crew_teams", "workday_open_position_applications", "deputy_oauth_connections", "deputy_write_operations")}
    config_before = dict(conn.execute("SELECT write_mode,allowed_trial_hosts FROM deputy_oauth_config WHERE id=1").fetchone())
init_db(); init_db()
with get_connection() as conn:
    preserved_after = {table: int(conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]) for table in preserved_before}
    config_after = dict(conn.execute("SELECT write_mode,allowed_trial_hosts FROM deputy_oauth_config WHERE id=1").fetchone())
assert all(preserved_after[table] >= count for table, count in preserved_before.items()) and config_after == config_before
init_db()
with get_connection() as conn:
    preserved_final = {table: int(conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]) for table in preserved_before}
assert preserved_final == preserved_after
print("release integration smoke ok")
