from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode, urlparse

import requests

from .config import get_settings
from .database import get_connection
from .security import decrypt_text, encrypt_text


WRITE_PERMISSION = "Can_Roster_Manage"
ACTIVE_OPERATION_STATES = {"prepared", "sending", "unknown"}


def now_iso() -> str:
    return datetime.now(get_settings().timezone).replace(microsecond=0).isoformat()


def normalize_tenant_host(value: str) -> str:
    raw = str(value or "").strip().lower()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443):
        raise ValueError("Deputy tenant must be an exact HTTPS hostname.")
    host = parsed.hostname.rstrip(".")
    if any(char in host for char in "* /\\") or "." not in host:
        raise ValueError("Deputy tenant hostname is invalid.")
    return host


def permission_hash(permissions: list[str]) -> str:
    canonical = json.dumps(sorted(set(str(item) for item in permissions)), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def state_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def load_config(*, include_secret: bool = False) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deputy_oauth_config WHERE id=1").fetchone()
    if row is None:
        return {"client_id": "", "client_secret": "", "authorize_path": "/oauth/authorize", "token_path": "/oauth/access_token", "write_mode": "off", "allowed_trial_hosts": []}
    item = dict(row)
    try:
        item["allowed_trial_hosts"] = json.loads(str(item.get("allowed_trial_hosts") or "[]"))
    except ValueError:
        item["allowed_trial_hosts"] = []
    item["client_secret_configured"] = bool(item.get("encrypted_client_secret"))
    item["client_secret"] = decrypt_text(str(item.get("encrypted_client_secret") or "")) if include_secret else ""
    item.pop("encrypted_client_secret", None)
    return item


def save_config(*, client_id: str, client_secret: str, write_mode: str, allowed_hosts: str, actor_user_id: int) -> None:
    mode = "trial" if write_mode == "trial" else "off"
    hosts = []
    for value in str(allowed_hosts or "").replace(",", "\n").splitlines():
        if value.strip():
            host = normalize_tenant_host(value)
            if host not in hosts:
                hosts.append(host)
    existing = load_config(include_secret=True)
    secret = client_secret or str(existing.get("client_secret") or "")
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO deputy_oauth_config(id,client_id,encrypted_client_secret,write_mode,allowed_trial_hosts,updated_by_user_id,updated_at)
               VALUES(1,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET client_id=excluded.client_id,
               encrypted_client_secret=excluded.encrypted_client_secret,write_mode=excluded.write_mode,
               allowed_trial_hosts=excluded.allowed_trial_hosts,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
            (client_id.strip(), encrypt_text(secret), mode, json.dumps(hosts), actor_user_id, now_iso()),
        )


def trial_host_allowed(host: str) -> bool:
    config = load_config()
    return config.get("write_mode") == "trial" and normalize_tenant_host(host) in set(config.get("allowed_trial_hosts") or [])


def begin_oauth(*, app_user_id: int, tenant: str, origin: str) -> str:
    config = load_config(include_secret=True)
    if not config.get("client_id") or not config.get("client_secret"):
        raise ValueError("Deputy OAuth is not configured by an Admin.")
    host = normalize_tenant_host(tenant)
    parsed_origin = urlparse(origin)
    if parsed_origin.scheme != "https" or not parsed_origin.hostname:
        raise ValueError("Connect Deputy from Re-Deputy's HTTPS address.")
    safe_origin = f"https://{parsed_origin.hostname}" + (f":{parsed_origin.port}" if parsed_origin.port and parsed_origin.port != 443 else "")
    state = secrets.token_urlsafe(32)
    now = datetime.now(get_settings().timezone).replace(microsecond=0)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO deputy_oauth_states(state_hash,app_user_id,tenant_host,redirect_origin,created_at,expires_at) VALUES(?,?,?,?,?,?)",
            (state_hash(state), app_user_id, host, safe_origin, now.isoformat(), (now + timedelta(minutes=10)).isoformat()),
        )
    callback = f"{safe_origin}/settings/deputy-api/callback"
    query = urlencode({"client_id": config["client_id"], "redirect_uri": callback, "response_type": "code", "state": state})
    return f"https://{host}{config.get('authorize_path') or '/oauth/authorize'}?{query}"


@dataclass
class DeputyClient:
    tenant_host: str
    access_token: str
    timeout: int = 20
    session: object = requests

    def request(self, method: str, path: str, **kwargs: object) -> tuple[int, object]:
        response = self.session.request(
            method,
            f"https://{self.tenant_host}{path}",
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
            timeout=self.timeout,
            **kwargs,
        )
        try:
            body = response.json()
        except ValueError:
            body = str(response.text or "")[:1000]
        return int(response.status_code), body


def _token_request(host: str, payload: dict[str, str], *, session: object = requests) -> dict[str, object]:
    config = load_config(include_secret=True)
    response = session.post(
        f"https://{host}{config.get('token_path') or '/oauth/access_token'}",
        data={**payload, "client_id": config["client_id"], "client_secret": config["client_secret"]},
        timeout=20,
    )
    if int(response.status_code) != 200:
        raise ValueError("Deputy authorization failed. Reconnect and try again.")
    data = response.json()
    if not data.get("access_token"):
        raise ValueError("Deputy did not return an access token.")
    return data


def complete_oauth(*, state: str, code: str, current_user_id: int, session: object = requests) -> dict[str, object]:
    now = datetime.now(get_settings().timezone).replace(microsecond=0)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deputy_oauth_states WHERE state_hash=?", (state_hash(state),)).fetchone()
        if row is None or row["consumed_at"] or datetime.fromisoformat(str(row["expires_at"])) <= now:
            raise ValueError("Deputy connection state is invalid, expired, or already used.")
        if int(row["app_user_id"]) != current_user_id:
            raise ValueError("Deputy connection belongs to another Re-Deputy account.")
        conn.execute("UPDATE deputy_oauth_states SET consumed_at=? WHERE state_hash=?", (now.isoformat(), state_hash(state)))
    host = str(row["tenant_host"])
    callback = f"{row['redirect_origin']}/settings/deputy-api/callback"
    tokens = _token_request(host, {"grant_type": "authorization_code", "code": code, "redirect_uri": callback}, session=session)
    client = DeputyClient(host, str(tokens["access_token"]), session=session)
    status, me = client.request("GET", "/api/v1/me")
    if status != 200 or not isinstance(me, dict) or me.get("UserId") is None or me.get("EmployeeId") is None:
        raise ValueError("Deputy identity verification failed.")
    permissions = [str(item) for item in me.get("Permissions") or []]
    expires = now + timedelta(seconds=max(60, int(tokens.get("expires_in") or 3600)))
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO deputy_oauth_connections(app_user_id,tenant_host,deputy_user_id,deputy_employee_id,display_label,
               encrypted_access_token,encrypted_refresh_token,token_expires_at,permissions_json,permission_hash,last_verified_at,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,'connected',?,?)
               ON CONFLICT(app_user_id) DO UPDATE SET tenant_host=excluded.tenant_host,deputy_user_id=excluded.deputy_user_id,
               deputy_employee_id=excluded.deputy_employee_id,display_label=excluded.display_label,encrypted_access_token=excluded.encrypted_access_token,
               encrypted_refresh_token=excluded.encrypted_refresh_token,token_expires_at=excluded.token_expires_at,
               permissions_json=excluded.permissions_json,permission_hash=excluded.permission_hash,last_verified_at=excluded.last_verified_at,
               status='connected',unavailable_reason=NULL,updated_at=excluded.updated_at""",
            (current_user_id, host, int(me["UserId"]), int(me["EmployeeId"]), str(me.get("Name") or "Deputy user"),
             encrypt_text(str(tokens["access_token"])), encrypt_text(str(tokens.get("refresh_token") or "")), expires.isoformat(),
             json.dumps(permissions), permission_hash(permissions), now.isoformat(), now.isoformat(), now.isoformat()),
        )
    return connection_status(current_user_id)


def connection_status(app_user_id: int) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deputy_oauth_connections WHERE app_user_id=?", (app_user_id,)).fetchone()
    if row is None:
        return {
            "connected": False,
            "read_ready": False,
            "write_ready": False,
            "connection_state": "disconnected",
            "read_reason": "Connect your Deputy account to enable read access.",
            "write_reason": "Connect and verify your Deputy account first.",
        }
    item = dict(row)
    try:
        permissions = [str(value) for value in json.loads(str(item.get("permissions_json") or "[]"))]
    except (TypeError, ValueError):
        permissions = []
    connection_state = str(item.get("status") or "authentication_unavailable")
    read_ready = connection_state == "connected" and bool(item.get("last_verified_at"))
    config = load_config()
    roster_manage = WRITE_PERMISSION in permissions
    allowed_hosts = set(str(value) for value in config.get("allowed_trial_hosts") or [])
    if not read_ready:
        write_reason = {
            "identity_mismatch": "Deputy identity mismatch. Reconnect and review this connection.",
            "authentication_unavailable": "Deputy authentication is unavailable. Reconnect your account.",
            "unavailable": "Deputy authentication is unavailable. Reconnect your account.",
        }.get(connection_state, "Verify your Deputy read access first.")
    elif not roster_manage:
        write_reason = "Your Deputy account does not have roster-management permission."
    elif config.get("write_mode") != "trial":
        write_reason = "Available, but trial writes are currently disabled."
    elif str(item["tenant_host"]) not in allowed_hosts:
        write_reason = "This tenant is not approved for trial writes."
    else:
        write_reason = "Trial ready"
    read_reason = {
        "identity_mismatch": "Deputy identity mismatch. Reconnect and review this connection.",
        "authentication_unavailable": "Deputy authentication is expired or unavailable.",
        "unavailable": "Deputy authentication is expired or unavailable.",
    }.get(connection_state, "Ready" if read_ready else "Read access has not been verified.")
    return {
        "connected": True,
        "read_ready": read_ready,
        "write_ready": read_ready and roster_manage and config.get("write_mode") == "trial" and str(item["tenant_host"]) in allowed_hosts,
        "write_label": "Trial ready" if write_reason == "Trial ready" else (write_reason if write_reason.startswith("Available,") else "Not available"),
        "connection_state": connection_state,
        "read_reason": read_reason,
        "write_reason": write_reason,
        "tenant_host": item["tenant_host"], "deputy_user_id": item["deputy_user_id"],
        "deputy_employee_id": item["deputy_employee_id"], "display_label": item["display_label"],
        "permissions": permissions, "roster_manage": roster_manage,
        "last_verified_at": item["last_verified_at"], "status": item["status"], "unavailable_reason": item["unavailable_reason"],
    }


def disconnect(app_user_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM deputy_oauth_connections WHERE app_user_id=?", (app_user_id,))


def client_for_user(app_user_id: int, *, session: object = requests) -> tuple[DeputyClient, dict[str, object]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deputy_oauth_connections WHERE app_user_id=?", (app_user_id,)).fetchone()
    if row is None:
        raise PermissionError("Connect your own Deputy account first.")
    item = dict(row)
    if item.get("status") == "identity_mismatch":
        raise PermissionError("Deputy identity mismatch. Reconnect and review this connection.")
    access = decrypt_text(str(item["encrypted_access_token"] or ""))
    refresh = decrypt_text(str(item["encrypted_refresh_token"] or ""))
    if item.get("token_expires_at") and datetime.fromisoformat(str(item["token_expires_at"])) <= datetime.now(get_settings().timezone) + timedelta(minutes=1):
        if not refresh:
            with get_connection() as conn:
                conn.execute("UPDATE deputy_oauth_connections SET status='authentication_unavailable',unavailable_reason=?,updated_at=? WHERE app_user_id=?", ("Stored Deputy authentication has expired.", now_iso(), app_user_id))
            raise PermissionError("Deputy connection expired. Reconnect your account.")
        try:
            tokens = _token_request(str(item["tenant_host"]), {"grant_type": "refresh_token", "refresh_token": refresh}, session=session)
        except ValueError as exc:
            with get_connection() as conn:
                conn.execute("UPDATE deputy_oauth_connections SET status='authentication_unavailable',unavailable_reason=?,updated_at=? WHERE app_user_id=?", ("Deputy token refresh failed.", now_iso(), app_user_id))
            raise PermissionError("Deputy refresh failed. Reconnect your account.") from exc
        access = str(tokens["access_token"])
        refresh = str(tokens.get("refresh_token") or refresh)
        expiry = datetime.now(get_settings().timezone) + timedelta(seconds=int(tokens.get("expires_in") or 3600))
        with get_connection() as conn:
            conn.execute("UPDATE deputy_oauth_connections SET encrypted_access_token=?,encrypted_refresh_token=?,token_expires_at=?,updated_at=? WHERE app_user_id=?", (encrypt_text(access), encrypt_text(refresh), expiry.isoformat(), now_iso(), app_user_id))
    return DeputyClient(str(item["tenant_host"]), access, session=session), item


def verify_read_access(app_user_id: int, *, session: object = requests) -> dict[str, object]:
    client, stored = client_for_user(app_user_id, session=session)
    status, me = client.request("GET", "/api/v1/me")
    if status != 200 or not isinstance(me, dict):
        with get_connection() as conn:
            conn.execute("UPDATE deputy_oauth_connections SET status='authentication_unavailable',unavailable_reason=?,updated_at=? WHERE app_user_id=?", ("Deputy identity could not be authenticated.", now_iso(), app_user_id))
        raise PermissionError("Deputy identity could not be verified. Reconnect your account.")
    permissions = [str(item) for item in me.get("Permissions") or []]
    if int(me.get("UserId") or 0) != int(stored["deputy_user_id"]) or int(me.get("EmployeeId") or 0) != int(stored["deputy_employee_id"]):
        with get_connection() as conn:
            conn.execute("UPDATE deputy_oauth_connections SET status='identity_mismatch',unavailable_reason=?,updated_at=? WHERE app_user_id=?", ("Deputy returned a different user or employee identity.", now_iso(), app_user_id))
        raise PermissionError("Deputy identity changed. Recheck or reconnect your Deputy account.")
    new_hash = permission_hash(permissions)
    with get_connection() as conn:
        observed = now_iso()
        conn.execute("""UPDATE deputy_oauth_connections SET display_label=?,permissions_json=?,permission_hash=?,
                     last_verified_at=?,status='connected',unavailable_reason=NULL,updated_at=? WHERE app_user_id=?""",
                     (str(me.get("Name") or stored.get("display_label") or "Deputy user"), json.dumps(permissions), new_hash, observed, observed, app_user_id))
    return {"client": client, "me": me, "tenant_host": client.tenant_host, "permission_hash": new_hash, "permissions": permissions, "read_ready": True}


def verify_write_readiness(app_user_id: int, *, session: object = requests) -> dict[str, object]:
    verified = verify_read_access(app_user_id, session=session)
    if WRITE_PERMISSION not in verified["permissions"]:
        raise PermissionError("Your Deputy account does not have roster-management permission.")
    config = load_config()
    if config.get("write_mode") != "trial":
        raise PermissionError("Deputy roster writes are available, but trial writes are currently disabled.")
    if verified["tenant_host"] not in set(str(value) for value in config.get("allowed_trial_hosts") or []):
        raise PermissionError("This Deputy tenant is not approved for trial writes.")
    return {**verified, "write_ready": True}


def resource_query(client: DeputyClient, resource: str, *, search: dict[str, object] | None = None) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[int] = set()
    start = 0
    while True:
        status, body = client.request("POST", f"/api/v1/resource/{resource}/QUERY", json={"search": search or {}, "sort": {"Id": "asc"}, "start": start, "max": 500})
        if status != 200 or not isinstance(body, list):
            raise ValueError(f"Deputy {resource} reference refresh failed.")
        for row in body:
            if isinstance(row, dict) and int(row.get("Id") or 0) not in seen:
                seen.add(int(row["Id"])); result.append(row)
        if len(body) < 500:
            break
        start += 500
    return result


def refresh_references(app_user_id: int, *, session: object = requests) -> dict[str, object]:
    verified = verify_read_access(app_user_id, session=session)
    client = verified["client"]
    employees: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    errors: dict[str, str] = {}
    try:
        employees = resource_query(client, "Employee")
    except ValueError:
        errors["employees"] = "Employee references are unavailable or permission denied."
    try:
        units = resource_query(client, "OperationalUnit")
    except ValueError:
        errors["units"] = "Operational Unit references are unavailable or permission denied."
    observed = now_iso()
    with get_connection() as conn:
        for item in employees:
            conn.execute("""INSERT INTO deputy_reference_employees(app_user_id,tenant_host,deputy_employee_id,deputy_user_id,display_name,active,metadata_json,last_observed_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(app_user_id,tenant_host,deputy_employee_id) DO UPDATE SET deputy_user_id=excluded.deputy_user_id,
                display_name=excluded.display_name,active=excluded.active,metadata_json=excluded.metadata_json,last_observed_at=excluded.last_observed_at""",
                (app_user_id, client.tenant_host, int(item["Id"]), item.get("UserId"), str(item.get("DisplayName") or ""), 1 if item.get("Active", True) else 0, json.dumps({"email": item.get("Email"), "role": item.get("Role")}), observed))
        for item in units:
            conn.execute("""INSERT INTO deputy_reference_units(app_user_id,tenant_host,deputy_unit_id,display_name,active,show_on_roster,metadata_json,last_observed_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(app_user_id,tenant_host,deputy_unit_id) DO UPDATE SET display_name=excluded.display_name,
                active=excluded.active,show_on_roster=excluded.show_on_roster,metadata_json=excluded.metadata_json,last_observed_at=excluded.last_observed_at""",
                (app_user_id, client.tenant_host, int(item["Id"]), str(item.get("OperationalUnitName") or ""), 1 if item.get("Active", True) else 0, 1 if item.get("ShowOnRoster", True) else 0, json.dumps({"type": item.get("OperationalUnitType")}), observed))
    return {"employees": len(employees), "units": len(units), "errors": errors}


def normalized_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _api_desired(desired: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in desired.items() if key in {"area", "employee", "start", "end", "break", "note", "approvalRequired"}}


def _business_equal(current: dict[str, object], desired: dict[str, object]) -> bool:
    aliases = {"area": ("area", "OperationalUnit"), "employee": ("employee", "Employee"), "start": ("start", "startAt"), "end": ("end", "endAt"), "note": ("note", "Comment")}
    for key, current_keys in aliases.items():
        if key not in desired:
            continue
        actual = next((current.get(name) for name in current_keys if current.get(name) is not None), None)
        if str(actual or "") != str(desired[key] or ""):
            return False
    return True


def _reconcile_after_network_error(client: DeputyClient, operation: dict[str, object], desired: dict[str, object], roster_id: int | None) -> dict[str, object]:
    op_type = str(operation["operation_type"])
    if op_type == "create":
        candidates = resource_query(client, "Roster", search={"Employee": int(desired.get("employee") or 0), "OperationalUnit": int(desired.get("area") or 0)})
        exact = [row for row in candidates if _business_equal(row, _api_desired(desired)) and not bool(row.get("Open"))]
        if len(exact) == 1:
            adopted = int(exact[0].get("Id") or 0)
            if adopted:
                _save_roster_link(operation, desired, adopted, exact[0])
                return _finish_operation(str(operation["operation_uuid"]), "verified", adopted, {"reconciled": True}, True)
        if len(exact) > 1:
            return _finish_operation(str(operation["operation_uuid"]), "ambiguous", None, {"message": "Multiple exact Deputy candidates require manual reconciliation."}, False, "AMBIGUOUS")
        return _finish_operation(str(operation["operation_uuid"]), "unknown", None, {"message": "No exact Deputy candidate found; controlled retry requires review."}, False, "UNKNOWN_NETWORK_RESULT")
    if op_type in {"update", "delete"} and roster_id:
        status, body = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
        if op_type == "delete" and status == 404:
            return _finish_operation(str(operation["operation_uuid"]), "verified", roster_id, {"deleted": True, "reconciled": True}, True)
        current = body.get("shift", body) if isinstance(body, dict) else {}
        if op_type == "update" and status == 200 and _business_equal(current, _api_desired(desired)):
            return _finish_operation(str(operation["operation_uuid"]), "verified", roster_id, {"reconciled": True}, True)
    if op_type == "publish":
        ids = [int(value) for value in desired.get("roster_ids") or []]
        states = [client.request("GET", f"/api/management/v2/shifts/{value}") for value in ids]
        if all(status == 200 and isinstance(body, dict) and bool((body.get("shift", body)).get("isPublished") or (body.get("shift", body)).get("Published")) for status, body in states):
            return _finish_operation(str(operation["operation_uuid"]), "verified", None, {"published_ids": ids, "reconciled": True}, True)
    return _finish_operation(str(operation["operation_uuid"]), "unknown", roster_id, {"message": "Deputy result is unknown; reconciliation is required."}, False, "UNKNOWN_NETWORK_RESULT")


def _save_roster_link(operation: dict[str, object], desired: dict[str, object], roster_id: int, readback: dict[str, object]) -> None:
    with get_connection() as conn:
        conn.execute("""INSERT INTO deputy_roster_links(tenant_host,workday_id,stable_assignment_key,deputy_employee_id,deputy_unit_id,deputy_roster_id,context_type,ownership,last_desired_hash,last_verified_hash,last_verified_state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,'re_deputy_created_trial',?,?,?,?,?) ON CONFLICT(tenant_host,stable_assignment_key) DO UPDATE SET deputy_roster_id=excluded.deputy_roster_id,
            last_desired_hash=excluded.last_desired_hash,last_verified_hash=excluded.last_verified_hash,last_verified_state=excluded.last_verified_state,updated_at=excluded.updated_at""",
            (operation["tenant_host"], operation["workday_id"], operation["stable_assignment_key"], int(desired.get("employee") or 0), int(desired.get("area") or 0), roster_id,
             str(desired.get("context_type") or "production"), normalized_hash(desired), normalized_hash(readback), json.dumps(readback)[:10000], now_iso(), now_iso()))


def mapping_snapshot(app_user_id: int) -> dict[str, object]:
    status = connection_status(app_user_id)
    host = str(status.get("tenant_host") or "")
    if not host:
        return {"host": "", "employees": [], "units": [], "people": [], "person_mappings": {}, "unit_mappings": {}}
    with get_connection() as conn:
        employees = [dict(r) for r in conn.execute("SELECT * FROM deputy_reference_employees WHERE app_user_id=? AND tenant_host=? ORDER BY display_name", (app_user_id, host))]
        units = [dict(r) for r in conn.execute("SELECT * FROM deputy_reference_units WHERE app_user_id=? AND tenant_host=? ORDER BY display_name", (app_user_id, host))]
        people = [dict(r) for r in conn.execute("SELECT id,canonical_display_name FROM crew_people WHERE is_active=1 AND merged_into_person_id IS NULL ORDER BY canonical_display_name")]
        person_mappings = {int(r["crew_person_id"]): int(r["deputy_employee_id"]) for r in conn.execute("SELECT * FROM deputy_person_mappings WHERE tenant_host=?", (host,))}
        unit_mappings = {str(r["mapping_key"]): dict(r) for r in conn.execute("SELECT * FROM deputy_unit_mappings WHERE tenant_host=?", (host,))}
    return {"host": host, "employees": employees, "units": units, "people": people, "person_mappings": person_mappings, "unit_mappings": unit_mappings}


def save_person_mapping(*, app_user_id: int, crew_person_id: int, deputy_employee_id: int) -> None:
    verified = verify_read_access(app_user_id)
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM deputy_reference_employees WHERE app_user_id=? AND tenant_host=? AND deputy_employee_id=? AND active=1", (app_user_id, verified["tenant_host"], deputy_employee_id)).fetchone() is None:
            raise ValueError("That Deputy employee is not readable by your connected account.")
        conn.execute("""INSERT INTO deputy_person_mappings(tenant_host,crew_person_id,deputy_employee_id,updated_by_user_id,updated_at) VALUES(?,?,?,?,?)
                      ON CONFLICT(tenant_host,crew_person_id) DO UPDATE SET deputy_employee_id=excluded.deputy_employee_id,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
                     (verified["tenant_host"], crew_person_id, deputy_employee_id, app_user_id, now_iso()))


def save_unit_mapping(*, app_user_id: int, mapping_key: str, context_type: str, deputy_unit_id: int) -> None:
    verified = verify_read_access(app_user_id)
    kind = context_type if context_type in {"production_role", "travel", "vehicle_context", "generic"} else "generic"
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM deputy_reference_units WHERE app_user_id=? AND tenant_host=? AND deputy_unit_id=? AND active=1", (app_user_id, verified["tenant_host"], deputy_unit_id)).fetchone() is None:
            raise ValueError("That Deputy Operational Unit is not readable by your connected account.")
        conn.execute("""INSERT INTO deputy_unit_mappings(tenant_host,mapping_key,context_type,deputy_unit_id,updated_by_user_id,updated_at) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(tenant_host,mapping_key) DO UPDATE SET context_type=excluded.context_type,deputy_unit_id=excluded.deputy_unit_id,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
                     (verified["tenant_host"], mapping_key.strip(), kind, deputy_unit_id, app_user_id, now_iso()))


def build_trial_preview(app_user_id: int, workday_id: int, *, session: object = requests) -> dict[str, object]:
    verified = verify_write_readiness(app_user_id, session=session)
    host = str(verified["tenant_host"])
    with get_connection() as conn:
        workday = conn.execute("SELECT * FROM roster_days WHERE id=? AND status IN ('published','changes_pending')", (workday_id,)).fetchone()
        if workday is None:
            raise ValueError("Publish this Re-Deputy workday before trial sync.")
        assignments = [dict(r) for r in conn.execute("SELECT * FROM workday_assignments WHERE roster_day_id=? ORDER BY sort_order,id", (workday_id,))]
        people = {int(r["crew_person_id"]): int(r["deputy_employee_id"]) for r in conn.execute("SELECT * FROM deputy_person_mappings WHERE tenant_host=?", (host,))}
        units = {str(r["mapping_key"]): dict(r) for r in conn.execute("SELECT * FROM deputy_unit_mappings WHERE tenant_host=?", (host,))}
        links = {str(r["stable_assignment_key"]): dict(r) for r in conn.execute("SELECT * FROM deputy_roster_links WHERE tenant_host=? AND workday_id=?", (host, workday_id))}
    start_text = str(workday["on_track_time"] or workday["office_start"] or "")
    finish_text = str(workday["last_race_time"] or "")
    actions: list[dict[str, object]] = []; local_only: list[dict[str, str]] = []
    current_keys: set[str] = set()
    for assignment in assignments:
        state = str(assignment.get("assignment_state") or "assigned")
        if state != "assigned" or not assignment.get("person_id"):
            local_only.append({"assignment_key": str(assignment.get("assignment_key") or ""), "reason": "Open Position · Local only" if state == "open" else "TBC · Local only"})
            continue
        key = str(assignment.get("assignment_key") or assignment["id"]); current_keys.add(key)
        employee = people.get(int(assignment["person_id"])); unit = units.get(str(assignment.get("role_key") or ""))
        if not employee or not unit or not start_text or not finish_text:
            local_only.append({"assignment_key": str(assignment.get("assignment_key") or ""), "reason": "Missing explicit person/Area/time mapping · Local only"})
            continue
        day = date.fromisoformat(str(workday["roster_date"])); start_dt = datetime.combine(day, time.fromisoformat(start_text), tzinfo=get_settings().timezone)
        end_dt = datetime.combine(day, time.fromisoformat(finish_text), tzinfo=get_settings().timezone)
        if end_dt <= start_dt: end_dt += timedelta(days=1)
        link = links.get(key)
        desired = {"area": int(unit["deputy_unit_id"]), "employee": employee, "start": start_dt.isoformat(), "end": end_dt.isoformat(), "note": f"Re-Deputy trial · workday {workday_id} · {key}", "context_type": str(unit["context_type"])}
        op = "update" if link and link.get("deputy_roster_id") else "create"
        if link and str(link.get("last_desired_hash") or "") == normalized_hash(desired): op = "unchanged"
        actions.append({"operation": op, "assignment_key": key, "role_label": assignment.get("role_label"), "desired": desired, "roster_id": link.get("deputy_roster_id") if link else None})
    for key, link in links.items():
        if key not in current_keys and link.get("ownership") == "re_deputy_created_trial" and link.get("deputy_roster_id"):
            actions.append({"operation": "delete", "assignment_key": key, "role_label": "Removed assignment", "desired": {}, "roster_id": int(link["deputy_roster_id"])})
    counts = {name: sum(1 for row in actions if row["operation"] == name) for name in ("create", "update", "delete", "unchanged")}
    counts.update({"local_only": len(local_only)})
    return {"workday_id": workday_id, "tenant_host": host, "connected_identity": str(verified["me"].get("Name") or "Deputy user"), "connected_employee_id": int(verified["me"]["EmployeeId"]), "actions": actions, "local_only": local_only, "counts": counts}


def prepare_operation(*, app_user_id: int, workday_id: int, assignment_key: str, operation_type: str, desired: dict[str, object], roster_id: int | None = None, session: object = requests) -> dict[str, object]:
    verified = verify_write_readiness(app_user_id, session=session)
    if operation_type in {"create", "update"}:
        with get_connection() as conn:
            employee_ok = conn.execute("SELECT 1 FROM deputy_reference_employees WHERE app_user_id=? AND tenant_host=? AND deputy_employee_id=? AND active=1", (app_user_id, verified["tenant_host"], int(desired.get("employee") or 0))).fetchone()
            unit_ok = conn.execute("SELECT 1 FROM deputy_reference_units WHERE app_user_id=? AND tenant_host=? AND deputy_unit_id=? AND active=1", (app_user_id, verified["tenant_host"], int(desired.get("area") or 0))).fetchone()
            existing_link = conn.execute("SELECT deputy_roster_id FROM deputy_roster_links WHERE tenant_host=? AND stable_assignment_key=?", (verified["tenant_host"], assignment_key)).fetchone()
        if employee_ok is None or unit_ok is None:
            raise PermissionError("Target employee or Area is not readable by your connected Deputy account.")
        if operation_type == "create" and existing_link is not None and existing_link["deputy_roster_id"]:
            raise ValueError("This assignment already has a Deputy roster link; create is blocked.")
    operation_uuid = str(uuid.uuid4())
    with get_connection() as conn:
        try:
            conn.execute("""INSERT INTO deputy_write_operations(operation_uuid,app_user_id,tenant_host,deputy_user_id,deputy_employee_id,
                permission_hash,permission_snapshot,workday_id,stable_assignment_key,operation_type,desired_state,deputy_roster_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'prepared',?,?)""",
                (operation_uuid, app_user_id, verified["tenant_host"], int(verified["me"]["UserId"]), int(verified["me"]["EmployeeId"]),
                 verified["permission_hash"], json.dumps(verified["permissions"]), workday_id, assignment_key, operation_type,
                 json.dumps(desired, sort_keys=True), roster_id, now_iso(), now_iso()))
        except Exception as exc:
            raise ValueError("Another Deputy operation is already active for this assignment.") from exc
    return {"operation_uuid": operation_uuid, "short_id": operation_uuid[:8], "tenant_host": verified["tenant_host"], "me": verified["me"], "desired": desired, "operation_type": operation_type}


def execute_operation(operation_uuid: str, app_user_id: int, *, session: object = requests) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deputy_write_operations WHERE operation_uuid=? AND app_user_id=?", (operation_uuid, app_user_id)).fetchone()
        if row is None or row["status"] != "prepared":
            raise ValueError("Deputy operation is not available to execute.")
    try:
        verified = verify_write_readiness(app_user_id, session=session)
    except PermissionError as exc:
        return _finish_operation(operation_uuid, "failed", row["deputy_roster_id"], {"message": str(exc)}, False, "PERMISSION")
    client: DeputyClient = verified["client"]
    operation = dict(row)
    if (str(operation["tenant_host"]) != str(verified["tenant_host"])
            or int(operation["deputy_user_id"]) != int(verified["me"]["UserId"])
            or int(operation["deputy_employee_id"]) != int(verified["me"]["EmployeeId"])):
        return _finish_operation(operation_uuid, "failed", operation.get("deputy_roster_id"), {"message": "Prepared Deputy operation does not match your current Deputy identity."}, False, "PERMISSION")
    if str(operation["permission_hash"]) != str(verified["permission_hash"]):
        return _finish_operation(operation_uuid, "failed", operation.get("deputy_roster_id"), {"message": "Deputy permissions changed after this operation was prepared. Review and prepare it again."}, False, "PERMISSION")
    with get_connection() as conn:
        conn.execute("UPDATE deputy_write_operations SET status='sending',sending_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), row["id"]))
    desired = json.loads(str(operation["desired_state"])); api_desired = _api_desired(desired); roster_id = operation.get("deputy_roster_id")
    op_type = str(operation["operation_type"])
    try:
        if op_type == "create":
            status, body = client.request("POST", "/api/management/v2/shifts", json=api_desired)
            if status != 200 or not isinstance(body, dict):
                raise RuntimeError(f"Deputy create failed ({status}).")
            shift = body.get("shift") if isinstance(body.get("shift"), dict) else body
            roster_id = int(shift.get("id") or shift.get("Id") or 0)
            if not roster_id:
                raise ConnectionError("Deputy create result did not include a roster ID.")
            with get_connection() as conn:
                conn.execute("UPDATE deputy_write_operations SET deputy_roster_id=?,updated_at=? WHERE operation_uuid=?", (roster_id, now_iso(), operation_uuid))
        elif op_type == "update":
            if not roster_id: raise ValueError("Update requires a known Deputy roster ID.")
            get_status, current = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
            current_shift = current.get("shift", current) if isinstance(current, dict) else {}
            if get_status != 200: raise RuntimeError("Deputy roster could not be read before update.")
            with get_connection() as conn:
                conn.execute("UPDATE deputy_write_operations SET before_state=?,updated_at=? WHERE operation_uuid=?", (json.dumps(current_shift)[:10000], now_iso(), operation_uuid))
            timesheet_id = current_shift.get("timesheet") or current_shift.get("MatchedByTimesheet") or current_shift.get("matchedByTimesheet")
            if current_shift.get("canEdit") is False or int(timesheet_id or 0):
                raise PermissionError(f"Locked by Timesheet #{timesheet_id or ''}".rstrip(" #"))
            if _business_equal(current_shift, api_desired):
                return _finish_operation(operation_uuid, "verified", roster_id, {"unchanged": True}, True)
            status, body = client.request("PUT", f"/api/management/v2/shifts/{roster_id}", json=api_desired)
            if status != 200: raise RuntimeError(f"Deputy update failed ({status}).")
        elif op_type == "delete":
            with get_connection() as conn:
                link = conn.execute("SELECT * FROM deputy_roster_links WHERE tenant_host=? AND stable_assignment_key=?", (verified["tenant_host"], operation["stable_assignment_key"])).fetchone()
            if link is None or link["ownership"] != "re_deputy_created_trial" or not roster_id:
                raise PermissionError("Only a Re-Deputy-created trial roster can be deleted.")
            get_status, current = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
            current_shift = current.get("shift", current) if isinstance(current, dict) else {}
            if get_status != 200: raise RuntimeError("Deputy roster could not be read before delete.")
            with get_connection() as conn:
                conn.execute("UPDATE deputy_write_operations SET before_state=?,updated_at=? WHERE operation_uuid=?", (json.dumps(current_shift)[:10000], now_iso(), operation_uuid))
            timesheet_id = current_shift.get("timesheet") or current_shift.get("MatchedByTimesheet") or current_shift.get("matchedByTimesheet")
            if current_shift.get("canEdit") is False or int(timesheet_id or 0):
                raise PermissionError(f"Locked by Timesheet #{timesheet_id or ''}".rstrip(" #"))
            status, body = client.request("DELETE", f"/api/management/v2/shifts/{roster_id}")
            if status != 200: raise RuntimeError(f"Deputy delete failed ({status}).")
            verify_status, _ = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
            if verify_status != 404: raise ConnectionError("Deputy delete result is unknown.")
            with get_connection() as conn:
                conn.execute("UPDATE deputy_roster_links SET deputy_roster_id=NULL,last_verified_state='deleted',updated_at=? WHERE tenant_host=? AND stable_assignment_key=?", (now_iso(), verified["tenant_host"], operation["stable_assignment_key"]))
            return _finish_operation(operation_uuid, "verified", roster_id, {"deleted": True}, True)
        elif op_type == "publish":
            ids = [int(value) for value in desired.get("roster_ids") or []]
            unpublished = []
            for value in ids:
                read_status, current = client.request("GET", f"/api/management/v2/shifts/{value}")
                shift = current.get("shift", current) if isinstance(current, dict) else {}
                if read_status != 200: raise ConnectionError("Deputy publish readiness could not be verified.")
                if not bool(shift.get("isPublished") or shift.get("Published")): unpublished.append(value)
            if not unpublished: return _finish_operation(operation_uuid, "verified", None, {"unchanged": True}, True)
            status, body = client.request("POST", "/api/v1/supervise/roster/publish", json={"intMode": 4, "blnAllLocationsMode": 0, "intRosterArray": unpublished})
            if status != 200: raise RuntimeError(f"Deputy publish failed ({status}).")
            for value in unpublished:
                read_status, current = client.request("GET", f"/api/management/v2/shifts/{value}")
                shift = current.get("shift", current) if isinstance(current, dict) else {}
                if read_status != 200 or not bool(shift.get("isPublished") or shift.get("Published")):
                    raise ConnectionError("Deputy publish result is unknown; reconciliation required.")
            return _finish_operation(operation_uuid, "verified", None, {"published_ids": unpublished}, True)
        else:
            raise ValueError("Unsupported Deputy operation.")
        verify_status, readback = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
        if verify_status != 200:
            raise ConnectionError("Deputy result unknown after write; reconciliation required.")
        _save_roster_link(operation, desired, int(roster_id), readback if isinstance(readback, dict) else {})
        return _finish_operation(operation_uuid, "verified", roster_id, {"readback": "verified"}, True)
    except requests.RequestException:
        try:
            return _reconcile_after_network_error(client, operation, desired, roster_id)
        except requests.RequestException:
            return _finish_operation(operation_uuid, "unknown", roster_id, {"message": "Deputy result is unknown; reconciliation is required."}, False, "UNKNOWN_NETWORK_RESULT")
    except PermissionError as exc:
        status = "locked" if "Timesheet" in str(exc) else "failed"
        return _finish_operation(operation_uuid, status, roster_id, {"message": str(exc)}, False, "LOCKED" if status == "locked" else "PERMISSION")
    except ConnectionError as exc:
        return _finish_operation(operation_uuid, "unknown", roster_id, {"message": str(exc)}, False, "UNKNOWN_NETWORK_RESULT")
    except Exception as exc:
        return _finish_operation(operation_uuid, "failed", roster_id, {"message": str(exc)[:500]}, False, "FAILED")


def _finish_operation(operation_uuid: str, status: str, roster_id: int | None, result: dict[str, object], verified: bool, error_class: str | None = None) -> dict[str, object]:
    with get_connection() as conn:
        conn.execute("UPDATE deputy_write_operations SET status=?,deputy_roster_id=COALESCE(?,deputy_roster_id),error_class=?,sanitized_result=?,readback_verified=?,completed_at=?,updated_at=? WHERE operation_uuid=?",
                     (status, roster_id, error_class, json.dumps(result)[:10000], 1 if verified else 0, now_iso(), now_iso(), operation_uuid))
    return {"operation_uuid": operation_uuid, "status": status, "roster_id": roster_id, "readback_verified": verified, **result}


def write_audit_summary(limit: int = 30) -> dict[str, object]:
    with get_connection() as conn:
        counts = {str(row["status"]): int(row["n"]) for row in conn.execute("SELECT status,COUNT(*) n FROM deputy_write_operations GROUP BY status")}
        rows = [dict(row) for row in conn.execute("""SELECT o.*,u.display_name actor_name,r.track_label workday_label
            FROM deputy_write_operations o JOIN app_users u ON u.id=o.app_user_id LEFT JOIN roster_days r ON r.id=o.workday_id
            ORDER BY o.created_at DESC LIMIT ?""", (limit,)).fetchall()]
    for row in rows:
        row.pop("permission_snapshot", None); row.pop("desired_state", None); row.pop("before_state", None)
    return {"counts": counts, "rows": rows, "mode": load_config().get("write_mode", "off")}
