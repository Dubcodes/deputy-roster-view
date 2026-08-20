from __future__ import annotations

import os
import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="redeputy-deputy-gate-"))
os.environ.update({"DB_PATH": str(tmp / "gate.sqlite3"), "DATA_DIR": str(tmp), "APP_SECRET_KEY": "obvious-test-key"})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import create_app_user, get_connection, init_db, save_roster_day
from app.deputy_integration import (
    _token_request,
    build_trial_preview, build_v2_shift_payload, complete_oauth, DeputyClient, execute_operation, execute_trial_batch,
    extract_v2_shift, normalize_shift, normalize_tenant_host, normalize_v2_response, resource_query,
    prepare_operation, save_config, state_hash,
)
from app.security import encrypt_text, hash_pin
from app.deputy_web import _paginate_schedule_search


class Response:
    def __init__(self, status: int, body: object):
        self.status_code, self._body, self.text = status, body, ""
    def json(self): return self._body


class FixtureDeputy:
    """Sanitized Lab Round 2 / official v2-shift fixture, never a live transport."""
    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []
        self.rosters: dict[int, dict[str, object]] = {}
        self.next_id = 70
        self.fail_create_number: int | None = None
        self.lose_after_create_number: int | None = None
        self.create_count = 0
    def post(self, url: str, **kwargs: object):
        self.calls.append(("TOKEN", url, kwargs.get("data")))
        return Response(200, {"access_token": "OBVIOUS-TEST-ACCESS", "refresh_token": "OBVIOUS-TEST-REFRESH", "expires_in": 86400,
                              "scope": "longlife_refresh_token", "endpoint": "gate.au.deputy.com"})
    def request(self, method: str, url: str, **kwargs: object):
        payload = kwargs.get("json")
        self.calls.append((method, url, payload))
        if url.endswith("/api/v1/me"):
            return Response(200, {"UserId": 901, "EmployeeId": 902, "Name": "Fixture Manager", "Permissions": ["Can_Roster_Manage"]})
        if method == "POST" and "/resource/Roster/QUERY" in url:
            body = payload or {}
            assert body["max"] <= 500 and body["sort"] == {"Id": "asc"}
            assert all(set(item) == {"field", "data", "type"} and item["type"] == "eq" for item in body["search"].values())
            rows = []
            for row in self.rosters.values():
                seconds = int(round(float(row.get("mealbreakDuration") or 0) * 3600))
                slots = ([{"strType": "B", "intStart": 32400, "intEnd": 32400 + seconds},
                          {"strType": "W", "intStart": 0, "intEnd": 32400}] if seconds else [])
                rows.append({"Id": row["id"], "Employee": row["employee"], "OperationalUnit": row["area"],
                             "StartTime": int(datetime.fromisoformat(str(row["start"])).timestamp()),
                             "EndTime": int(datetime.fromisoformat(str(row["end"])).timestamp()),
                             "Mealbreak": "2026-08-13T00:30:00+12:00" if seconds else "2026-08-13T00:00:00+12:00",
                             "Slots": slots, "Comment": row.get("note", ""), "Open": False,
                             "Published": row.get("isPublished", False), "ApprovalRequired": row.get("approvalRequired", False)})
            return Response(200, rows)
        if method == "POST" and url.endswith("/api/management/v2/shifts"):
            self.create_count += 1
            if self.fail_create_number == self.create_count:
                raise __import__("requests").Timeout("fixture response lost")
            assert set(payload) == {"data"} and set(payload["data"]) == {"shift", "override"}
            shift = dict(payload["data"]["shift"]); self.next_id += 1
            shift.update({"id": self.next_id, "canEdit": True, "timesheet": 0, "isPublished": False,
                          "mealbreakDuration": sum(int(s["end"]) - int(s["start"]) for s in shift["mealbreakSlots"]) / 3600})
            self.rosters[self.next_id] = shift
            if self.lose_after_create_number == self.create_count:
                raise __import__("requests").Timeout("fixture response lost after create")
            return Response(200, {"success": True, "data": shift})
        if method == "PUT" and "/api/management/v2/shifts/" in url:
            roster_id = int(url.rsplit("/", 1)[-1]); shift = dict(payload["data"]["shift"])
            shift.update({"id": roster_id, "canEdit": True, "timesheet": 0, "isPublished": self.rosters[roster_id].get("isPublished", False),
                          "mealbreakDuration": sum(int(s["end"]) - int(s["start"]) for s in shift["mealbreakSlots"]) / 3600})
            self.rosters[roster_id] = shift
            return Response(200, {"success": True, "data": shift})
        if method == "GET" and "/api/management/v2/shifts/" in url:
            roster_id = int(url.rsplit("/", 1)[-1])
            return Response(200, {"success": True, "data": self.rosters[roster_id]}) if roster_id in self.rosters else Response(404, {"success": False})
        if method == "POST" and url.endswith("/api/v1/supervise/roster/publish"):
            for roster_id in payload["intRosterArray"]: self.rosters[roster_id]["isPublished"] = True
            return Response(200, {"success": True})
        raise AssertionError((method, url, payload))


init_db(); init_db()
admin = create_app_user(deputy_email="gate@example.invalid", display_name="Gate Admin", pin_hash=hash_pin("1234"), deputy_web_url="https://example.invalid", encrypted_email="", encrypted_password="")
admin_id = int(admin["id"]); host = "gate.au.deputy.com"; now = datetime.now().astimezone().isoformat(timespec="seconds")
save_config(client_id="fixture-client", client_secret="OBVIOUS-TEST-SECRET", callback_origin="https://redeputy.example", write_mode="trial", allowed_hosts=host, actor_user_id=admin_id)
with get_connection() as conn:
    conn.execute("""INSERT INTO deputy_oauth_connections(app_user_id,tenant_host,deputy_user_id,deputy_employee_id,display_label,encrypted_access_token,encrypted_refresh_token,token_expires_at,permissions_json,permission_hash,last_verified_at,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (admin_id, host, 901, 902, "Fixture Manager", encrypt_text("OBVIOUS-TEST-ACCESS"), encrypt_text("OBVIOUS-TEST-REFRESH"), "2099-01-01T00:00:00+13:00", '["Can_Roster_Manage"]', "pending", now, "connected", now, now))
    for employee, person_name in ((201, "Normal Crew"), (202, "Truck Crew"), (203, "Duplicate Crew")):
        person_id = int(conn.execute("INSERT INTO crew_people(canonical_display_name,is_active,created_at,updated_at) VALUES(?,1,?,?)", (person_name, now, now)).lastrowid)
        conn.execute("INSERT INTO deputy_reference_employees(app_user_id,tenant_host,deputy_employee_id,display_name,active,last_observed_at) VALUES(?,?,?,?,1,?)", (admin_id, host, employee, person_name, now))
        conn.execute("INSERT INTO deputy_person_mappings(tenant_host,crew_person_id,deputy_employee_id,updated_by_user_id,updated_at) VALUES(?,?,?,?,?)", (host, person_id, employee, admin_id, now))
    people = [int(row["id"]) for row in conn.execute("SELECT id FROM crew_people ORDER BY id")]
    conn.execute("INSERT INTO deputy_reference_units(app_user_id,tenant_host,deputy_unit_id,display_name,active,last_observed_at) VALUES(?,?,?,?,1,?)", (admin_id, host, 301, "Director", now))
    conn.execute("INSERT INTO deputy_unit_mappings(tenant_host,mapping_key,context_type,deputy_unit_id,updated_by_user_id,updated_at) VALUES(?,?,?,?,?,?)", (host, "director", "production_role", 301, admin_id, now))
    truck_id = int(conn.execute("INSERT INTO crew_vehicles(stable_key,display_label,aliases,active,is_truck,sort_order,source,created_at,updated_at) VALUES('test-truck','Test Truck','[]',1,1,1,'test',?,?)", (now, now)).lastrowid)

def day(date_text: str, key: str, start: str, finish: str, *, break_minutes: int = 30, truck_offset: int = 15, assignments: list[tuple[int, int | None, str]]):
    with get_connection() as conn:
        day_id = int(conn.execute("""INSERT INTO roster_days(roster_date,track_key,track_label,status,office_start,end_time,on_track_time,last_race_time,break_minutes,truck_start_offset_minutes,created_at,updated_at)
            VALUES(?,?,?,'published',?,?,?,?,?,?,?,?)""", (date_text, key, key.title(), start, finish, "09:30", "16:25", break_minutes, truck_offset, now, now)).lastrowid)
        for index, (person_id, vehicle_id, role_key) in enumerate(assignments):
            conn.execute("""INSERT INTO workday_assignments(roster_day_id,person_id,role_key,role_label,assignment_state,assignment_key,vehicle_id,sort_order,created_at,updated_at)
                VALUES(?,?,?,'Director','assigned',?,?,?, ?,?)""", (day_id, person_id, role_key, f"{key}-{index}", vehicle_id, index, now, now))
    return day_id

fixture = FixtureDeputy()
taupo = day("2026-08-16", "taupo", "07:30", "19:30", assignments=[(people[0], None, "director"), (people[1], truck_id, "director")])
preview = build_trial_preview(admin_id, taupo, session=fixture)
assert preview["actions"][0]["desired"]["start"].endswith("07:30:00+12:00") and preview["actions"][0]["desired"]["end"].endswith("19:30:00+12:00")
assert preview["actions"][1]["desired"]["start"].endswith("07:15:00+12:00") and preview["actions"][1]["desired"]["end"].endswith("19:30:00+12:00")
assert preview["actions"][0]["desired"]["break_minutes"] == 30
ruakaka = day("2026-08-15", "ruakaka", "09:30", "22:00", assignments=[(people[0], None, "director")])
rua = build_trial_preview(admin_id, ruakaka, session=fixture)["actions"][0]["desired"]
assert rua["start"].endswith("09:30:00+12:00") and rua["end"].endswith("22:00:00+12:00")
incomplete = day("2026-08-17", "incomplete", "07:30", "", assignments=[(people[0], None, "director")])
bad = build_trial_preview(admin_id, incomplete, session=fixture)
assert bad["blockers"] and not bad["actions"] and bad["counts"]["local_only"] == 1
duplicate = day("2026-08-18", "duplicate", "08:00", "17:00", assignments=[(people[2], None, "director"), (people[2], None, "director")])
dup = build_trial_preview(admin_id, duplicate, session=fixture)
assert dup["blockers"] and not dup["actions"] and dup["counts"]["local_only"] == 2

payload = build_v2_shift_payload(preview["actions"][0]["desired"])
assert payload["data"]["shift"]["mealbreakSlots"][0]["end"] == 1800 and payload["data"]["override"] == {"shiftValidation": False, "publishValidation": False}
normalized = normalize_v2_response({"success": True, "data": {**payload["data"]["shift"], "id": 9, "mealbreakDuration": 0.5, "canEdit": False, "timesheet": 44, "isPublished": True}})
assert normalized.roster_id == 9 and normalized.break_minutes == 30 and normalized.timesheet_id == 44 and normalized.can_edit is False and normalized.is_published
resource_base = {"Id": 7, "Employee": 201, "OperationalUnit": 301, "StartTime": 1786851000, "EndTime": 1786894200, "ApprovalRequired": True}
assert normalize_shift({**resource_base, "Mealbreak": "2026-08-16T00:00:00+12:00", "Slots": []}, resource=True).break_minutes == 0
assert normalize_shift({**resource_base, "Mealbreak": "2026-08-16T00:30:00+12:00", "Slots": [{"strType": "B", "intStart": 32400, "intEnd": 34200}]}, resource=True).break_minutes == 30
assert normalize_shift({**resource_base, "Slots": [{"strType": "B", "intStart": 100, "intEnd": 1000}, {"strType": "B", "intStart": 2000, "intEnd": 2900}]}, resource=True).break_minutes == 30
assert normalize_shift({**resource_base, "Slots": [{"strType": "W", "intStart": 0, "intEnd": 28800}]}, resource=True).break_minutes == 0
assert normalize_shift({**resource_base, "Mealbreak": "not-a-duration", "Slots": [{"strType": "B", "intStart": 1, "intEnd": 1801}]}, resource=True).break_minutes == 30
fallback = normalize_shift({**resource_base, "Mealbreak": "2026-08-16T00:30:00+12:00"}, resource=True)
assert fallback.break_minutes == 30 and fallback.approval_required is True
for malformed_break in ("0.5", "30", "banana", 30, None):
    assert normalize_shift({**resource_base, "Mealbreak": malformed_break}, resource=True).break_minutes == 0
for malformed in ({"shift": {}}, {"success": False, "error": {"code": "SHIFT_VALIDATION"}}, {"success": True}):
    try: extract_v2_shift(malformed)
    except ValueError: pass
    else: raise AssertionError("Malformed v2 response was accepted")
class MalformedTokenSession:
    def __init__(self, body): self.body = body
    def post(self, url, **kwargs): return Response(200, self.body)
for malformed_token in ([], "text", {"endpoint": host}):
    try: _token_request("once.deputy.com", "/my/oauth/access_token", {"grant_type": "authorization_code"}, session=MalformedTokenSession(malformed_token))
    except ValueError: pass
    else: raise AssertionError("Malformed OAuth token response was accepted")

resource_query(DeputyClient(host, "OBVIOUS-TEST-ACCESS", session=fixture), "Roster", search={"Date": "2026-08-16", "Employee": 201, "OperationalUnit": 301})
for host_attempt in ("attacker.example.com", "evil.gate.au.deputy.com", "http://gate.au.deputy.com", "https://gate.au.deputy.com/token-recipient"):
    try: normalize_tenant_host(host_attempt)
    except ValueError: pass
    else: raise AssertionError("Malicious OAuth endpoint accepted")

# A transport-unknown second create stops the batch and cannot reach publish.
partial = {**preview, "actions": [dict(preview["actions"][0]), {**dict(preview["actions"][1]), "assignment_key": "partial-second"}], "blockers": []}
fixture.fail_create_number = 2
results, blockers = execute_trial_batch(partial, app_user_id=admin_id, session=fixture)
assert not blockers and [row["status"] for row in results] == ["verified", "unknown"], (blockers, results)
assert not any(method == "POST" and url.endswith("/api/v1/supervise/roster/publish") for method, url, _ in fixture.calls)
created_roster_id = int(results[0]["roster_id"])
fixture.rosters[created_roster_id]["note"] = "External delete drift"
delete_count = sum(method == "DELETE" for method, _, _ in fixture.calls)
drift_delete = prepare_operation(app_user_id=admin_id, workday_id=taupo, assignment_key=preview["actions"][0]["assignment_key"], operation_type="delete", desired={}, roster_id=created_roster_id, session=fixture)
delete_result = execute_operation(drift_delete["operation_uuid"], admin_id, session=fixture)
assert delete_result["status"] == "failed" and "outside Re-Deputy" in delete_result["message"]
assert sum(method == "DELETE" for method, _, _ in fixture.calls) == delete_count
fixture.fail_create_number = None
lost_day = day("2026-08-20", "lost", "08:00", "17:00", assignments=[(people[0], None, "director")])
lost_preview = build_trial_preview(admin_id, lost_day, session=fixture)
fixture.lose_after_create_number = fixture.create_count + 1
lost_results, lost_blockers = execute_trial_batch(lost_preview, app_user_id=admin_id, session=fixture)
assert not lost_blockers and lost_results[0]["status"] == "unknown" and lost_results[0].get("reconciled") is True
with get_connection() as conn:
    assert conn.execute("SELECT ownership FROM deputy_roster_links WHERE workday_id=?", (lost_day,)).fetchone()["ownership"] == "ownership_unconfirmed"
fixture.lose_after_create_number = None

# One exact existing roster is adopted without POST; update then uses v2 PUT + GET verification.
rua_preview = build_trial_preview(admin_id, ruakaka, session=fixture)
rua_desired = dict(rua_preview["actions"][0]["desired"])
exact_shift = dict(build_v2_shift_payload(rua_desired)["data"]["shift"])
exact_shift.update({"id": 88, "canEdit": True, "timesheet": 0, "isPublished": False, "mealbreakDuration": 0.5})
fixture.rosters[88] = exact_shift
posts_before = sum(method == "POST" and url.endswith("/api/management/v2/shifts") for method, url, _ in fixture.calls)
adopted, adoption_blockers = execute_trial_batch(rua_preview, app_user_id=admin_id, session=fixture)
assert not adoption_blockers and adopted[0]["roster_id"] == 88 and adopted[0]["status"] == "verified"
assert sum(method == "POST" and url.endswith("/api/management/v2/shifts") for method, url, _ in fixture.calls) == posts_before
with get_connection() as conn:
    adopted_link = dict(conn.execute("SELECT * FROM deputy_roster_links WHERE deputy_roster_id=88").fetchone())
assert adopted_link["ownership"] == "adopted_existing"
changed = {**rua_desired, "note": "Re-Deputy trial changed fixture"}
updated = prepare_operation(app_user_id=admin_id, workday_id=ruakaka, assignment_key=rua_preview["actions"][0]["assignment_key"], operation_type="update", desired=changed, roster_id=88, session=fixture)
update_result = execute_operation(updated["operation_uuid"], admin_id, session=fixture)
assert update_result["status"] == "verified" and any(method == "PUT" and url.endswith("/api/management/v2/shifts/88") for method, url, _ in fixture.calls)
with get_connection() as conn:
    assert conn.execute("SELECT ownership FROM deputy_roster_links WHERE deputy_roster_id=88").fetchone()["ownership"] == "adopted_existing"
    conn.execute("INSERT INTO deputy_reference_units(app_user_id,tenant_host,deputy_unit_id,display_name,active,last_observed_at) VALUES(?,?,?,?,1,?)", (admin_id, host, 302, "Camera", now))
provenance_desired = {**changed, "employee": 202, "area": 302, "crew_person_id": people[1], "context_type": "production"}
provenance_update = prepare_operation(app_user_id=admin_id, workday_id=ruakaka, assignment_key=rua_preview["actions"][0]["assignment_key"], operation_type="update", desired=provenance_desired, roster_id=88, session=fixture)
assert execute_operation(provenance_update["operation_uuid"], admin_id, session=fixture)["status"] == "verified"
with get_connection() as conn:
    provenance_link = dict(conn.execute("SELECT * FROM deputy_roster_links WHERE deputy_roster_id=88").fetchone())
assert (provenance_link["crew_person_id"], provenance_link["deputy_employee_id"], provenance_link["deputy_unit_id"], provenance_link["ownership"]) == (people[1], 202, 302, "adopted_existing")
blocked_delete = prepare_operation(app_user_id=admin_id, workday_id=ruakaka, assignment_key=rua_preview["actions"][0]["assignment_key"], operation_type="delete", desired={}, roster_id=88, session=fixture)
assert execute_operation(blocked_delete["operation_uuid"], admin_id, session=fixture)["status"] == "failed"
for field, external_value in (("employee", 999), ("area", 998), ("start", "2026-08-15T10:00:00+12:00"),
                              ("end", "2026-08-15T23:00:00+12:00"), ("mealbreakDuration", 0.75), ("note", "External Deputy edit")):
    original = fixture.rosters[88][field]
    fixture.rosters[88][field] = external_value
    put_count = sum(method == "PUT" for method, _, _ in fixture.calls)
    drifted = prepare_operation(app_user_id=admin_id, workday_id=ruakaka, assignment_key=rua_preview["actions"][0]["assignment_key"], operation_type="update", desired={**provenance_desired, "note": "Another local edit"}, roster_id=88, session=fixture)
    drift_result = execute_operation(drifted["operation_uuid"], admin_id, session=fixture)
    assert drift_result["status"] == "failed" and "outside Re-Deputy" in drift_result["message"]
    assert sum(method == "PUT" for method, _, _ in fixture.calls) == put_count
    fixture.rosters[88][field] = original
fixture.rosters[88]["note"] = "External unchanged drift"
publish_count = sum(method == "POST" and url.endswith("/api/v1/supervise/roster/publish") for method, url, _ in fixture.calls)
unchanged_preview = {**rua_preview, "actions": [{**rua_preview["actions"][0], "operation": "unchanged", "desired": provenance_desired, "roster_id": 88}], "blockers": []}
unchanged_results, unchanged_blockers = execute_trial_batch(unchanged_preview, app_user_id=admin_id, session=fixture)
assert not unchanged_results and any("outside Re-Deputy" in blocker for blocker in unchanged_blockers)
assert sum(method == "POST" and url.endswith("/api/v1/supervise/roster/publish") for method, url, _ in fixture.calls) == publish_count
fixture.rosters[88]["note"] = provenance_desired["note"]
with get_connection() as conn:
    conn.execute("DELETE FROM workday_assignments WHERE roster_day_id=? AND assignment_key=?", (ruakaka, rua_preview["actions"][0]["assignment_key"]))
removed_adopted_preview = build_trial_preview(admin_id, ruakaka, session=fixture)
assert not any(action["operation"] == "delete" and action.get("roster_id") == 88 for action in removed_adopted_preview["actions"])

# Multiple exact candidates and a conflicting overlap are whole-batch blockers.
fixture.rosters[89] = {**exact_shift, "id": 89}
fixture.rosters[91] = {**exact_shift, "id": 91}
ambiguous_preview = {**rua_preview, "actions": [{**rua_preview["actions"][0], "operation": "create", "roster_id": None}], "blockers": []}
ambiguous_results, ambiguous_blockers = execute_trial_batch(ambiguous_preview, app_user_id=admin_id, session=fixture)
assert not ambiguous_results and any("multiple exact" in item.lower() for item in ambiguous_blockers)
overlap_day = day("2026-08-19", "overlap", "08:00", "17:00", assignments=[(people[0], None, "director")])
overlap_preview = build_trial_preview(admin_id, overlap_day, session=fixture)
overlap_desired = overlap_preview["actions"][0]["desired"]
fixture.rosters[90] = {**dict(build_v2_shift_payload(overlap_desired)["data"]["shift"]), "id": 90, "start": "2026-08-19T09:00:00+12:00", "end": "2026-08-19T18:00:00+12:00", "note": "Conflicting fixture", "mealbreakDuration": 0.5, "canEdit": True, "timesheet": 0, "isPublished": False}
overlap_results, overlap_blockers = execute_trial_batch(overlap_preview, app_user_id=admin_id, session=fixture)
assert not overlap_results and any("overlapping" in item.lower() for item in overlap_blockers)

# The normal save path regenerates a submitted assignment key already owned by another workday.
def save_key_day(date_text: str, track_key: str) -> int:
    return save_roster_day(roster_day_id=None, roster_date=date_text, track_key=track_key, track_label=track_key, race_type="Thoroughbred", day_type="office_day",
                           start_origin="", finish_destination="", office_start="09:00", on_track_time="", first_race_time="", last_race_time="", race_count=None,
                           notes="", hotel_assignments="[]", end_time="17:00", updated_by_user_id=admin_id,
                           assignments=[{"assignment_key": "submitted-shared-key", "assignment_state": "assigned", "person_id": people[0], "role_key": "director", "role_label": "Director"}])
key_day_one = save_key_day("2026-08-24", "key-one")
key_day_two = save_key_day("2026-08-25", "key-two")
with get_connection() as conn:
    saved_keys = [row["assignment_key"] for row in conn.execute("SELECT assignment_key FROM workday_assignments WHERE roster_day_id IN (?,?) ORDER BY roster_day_id", (key_day_one, key_day_two))]
assert len(saved_keys) == 2 and len(set(saved_keys)) == 2 and saved_keys[0] == "submitted-shared-key"

# Cursor pagination keeps all successful pages, deduplicates later at the
# capture boundary, and marks a window partial if a later request fails.
async def pagination_fixture() -> None:
    calls: list[dict[str, object]] = []
    async def complete_fetch(body: dict[str, object]) -> dict[str, object]:
        calls.append(body)
        cursor = body.get("cursor")
        if not cursor:
            return {"ok": True, "status": 200, "body": {"data": {"shifts": [{"id": 1}]}, "nextCursor": "page-2"}}
        assert cursor == "page-2"
        return {"ok": True, "status": 200, "body": {"data": {"shifts": [{"id": 2}, {"id": 1}]}}}
    complete = await _paginate_schedule_search(complete_fetch, {"locationMode": "ALL"})
    assert complete["complete"] and len(complete["pages"]) == 2 and calls[1]["cursor"] == "page-2"

    async def later_failure(body: dict[str, object]) -> dict[str, object]:
        if body.get("cursor"):
            return {"ok": False, "status": 503, "body": None}
        return {"ok": True, "status": 200, "body": {"data": {"shifts": [{"id": 3}]}, "nextCursor": "again"}}
    partial = await _paginate_schedule_search(later_failure, {"locationMode": "SELECTED"})
    assert not partial["complete"] and len(partial["pages"]) == 1 and partial["error"] == "HTTP 503"

asyncio.run(pagination_fixture())

with get_connection() as conn:
    before = {table: int(conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]) for table in ("app_users", "crew_people", "roster_days", "workday_assignments")}
init_db()
with get_connection() as conn:
    after_once = {table: int(conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]) for table in before}
    assert dict(conn.execute("SELECT write_mode FROM deputy_oauth_config WHERE id=1").fetchone())["write_mode"] == "trial"
init_db()
with get_connection() as conn:
    after_twice = {table: int(conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]) for table in before}
assert all(after_once[table] >= count for table, count in before.items()) and after_once == after_twice
print("deputy final release gate smoke ok")
