from __future__ import annotations

import hashlib
import json
import re
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
OAUTH_HOST = "once.deputy.com"
OAUTH_SCOPE = "longlife_refresh_token"
OAUTH_AUTHORIZE_PATH = "/my/oauth/login"
OAUTH_INITIAL_TOKEN_PATH = "/my/oauth/access_token"
OAUTH_REFRESH_PATH = "/oauth/access_token"


def now_iso() -> str:
    return datetime.now(get_settings().timezone).replace(microsecond=0).isoformat()


def normalize_tenant_host(value: str) -> str:
    raw = str(value or "").strip().lower()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Deputy tenant must be an exact HTTPS hostname.")
    host = parsed.hostname.rstrip(".")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(?:au|eu|uk|us)\.deputy\.com", host):
        raise ValueError("Deputy tenant must be an exact regional *.deputy.com hostname.")
    return host


def normalize_callback_origin(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("OAuth callback origin must be a configured HTTPS origin without a path.")
    return f"https://{parsed.hostname.lower()}" + (f":{parsed.port}" if parsed.port and parsed.port != 443 else "")


def permission_hash(permissions: list[str]) -> str:
    canonical = json.dumps(sorted(set(str(item) for item in permissions)), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def state_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def load_config(*, include_secret: bool = False) -> dict[str, object]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deputy_oauth_config WHERE id=1").fetchone()
    if row is None:
        return {"client_id": "", "client_secret": "", "callback_origin": "", "write_mode": "off", "allowed_trial_hosts": []}
    item = dict(row)
    try:
        item["allowed_trial_hosts"] = json.loads(str(item.get("allowed_trial_hosts") or "[]"))
    except ValueError:
        item["allowed_trial_hosts"] = []
    item["client_secret_configured"] = bool(item.get("encrypted_client_secret"))
    item["client_secret"] = decrypt_text(str(item.get("encrypted_client_secret") or "")) if include_secret else ""
    item.pop("encrypted_client_secret", None)
    return item


def save_config(*, client_id: str, client_secret: str, write_mode: str, actor_user_id: int, callback_origin: str = "", allowed_hosts: str = "") -> None:
    mode = "trial" if write_mode == "trial" else "off"
    existing = load_config(include_secret=True)
    secret = client_secret or str(existing.get("client_secret") or "")
    callback = normalize_callback_origin(callback_origin) if callback_origin.strip() else str(existing.get("callback_origin") or "")
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO deputy_oauth_config(id,client_id,encrypted_client_secret,callback_origin,write_mode,allowed_trial_hosts,updated_by_user_id,updated_at)
               VALUES(1,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET client_id=excluded.client_id,
               encrypted_client_secret=excluded.encrypted_client_secret,write_mode=excluded.write_mode,
               callback_origin=excluded.callback_origin,allowed_trial_hosts=excluded.allowed_trial_hosts,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at""",
            (client_id.strip(), encrypt_text(secret), callback, mode, "[]", actor_user_id, now_iso()),
        )


def trial_host_allowed(host: str) -> bool:
    # Kept as a compatibility helper for callers from older releases. Tenant
    # identity still has to be a valid Deputy hostname and OAuth is per-user,
    # but there is no second Admin-maintained hostname allowlist.
    normalize_tenant_host(host)
    return load_config().get("write_mode") == "trial"


def begin_oauth(*, app_user_id: int, tenant: str = "", origin: str = "") -> str:
    config = load_config(include_secret=True)
    if not config.get("client_id") or not config.get("client_secret"):
        raise ValueError("Deputy OAuth is not configured by an Admin.")
    safe_origin = normalize_callback_origin(str(config.get("callback_origin") or ""))
    state = secrets.token_urlsafe(32)
    now = datetime.now(get_settings().timezone).replace(microsecond=0)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO deputy_oauth_states(state_hash,app_user_id,tenant_host,redirect_origin,created_at,expires_at) VALUES(?,?,?,?,?,?)",
            (state_hash(state), app_user_id, OAUTH_HOST, safe_origin, now.isoformat(), (now + timedelta(minutes=10)).isoformat()),
        )
    callback = f"{safe_origin}/settings/deputy-api/callback"
    query = urlencode({"client_id": config["client_id"], "redirect_uri": callback, "response_type": "code", "scope": OAUTH_SCOPE, "state": state})
    return f"https://{OAUTH_HOST}{OAUTH_AUTHORIZE_PATH}?{query}"


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


def _token_request(host: str, path: str, payload: dict[str, str], *, session: object = requests) -> dict[str, object]:
    config = load_config(include_secret=True)
    try:
        response = session.post(
            f"https://{host}{path}",
            data={**payload, "client_id": config["client_id"], "client_secret": config["client_secret"], "scope": OAUTH_SCOPE},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise ValueError("Deputy authorization is temporarily unavailable.") from exc
    if int(response.status_code) != 200:
        raise PermissionError("Deputy authorization failed. Reconnect and try again.")
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("Deputy returned an unexpected authorization response.") from exc
    if not isinstance(data, dict):
        raise ValueError("Deputy returned an unexpected authorization response.")
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
    callback = f"{row['redirect_origin']}/settings/deputy-api/callback"
    tokens = _token_request(OAUTH_HOST, OAUTH_INITIAL_TOKEN_PATH, {"grant_type": "authorization_code", "code": code, "redirect_uri": callback}, session=session)
    host = normalize_tenant_host(str(tokens.get("endpoint") or ""))
    client = DeputyClient(host, str(tokens["access_token"]), session=session)
    try:
        status, me = client.request("GET", "/api/v1/me")
    except requests.RequestException as exc:
        raise ValueError("Deputy identity verification is temporarily unavailable.") from exc
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
        "write_ready": read_ready and roster_manage and config.get("write_mode") == "trial",
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
            callback_origin = normalize_callback_origin(str(load_config().get("callback_origin") or ""))
            tokens = _token_request(normalize_tenant_host(str(item["tenant_host"])), OAUTH_REFRESH_PATH, {"grant_type": "refresh_token", "refresh_token": refresh, "redirect_uri": f"{callback_origin}/settings/deputy-api/callback"}, session=session)
        except PermissionError as exc:
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
    try:
        status, me = client.request("GET", "/api/v1/me")
    except requests.RequestException as exc:
        raise ValueError("Deputy is temporarily unavailable; your connection was not changed.") from exc
    if status == 401:
        with get_connection() as conn:
            conn.execute("UPDATE deputy_oauth_connections SET status='authentication_unavailable',unavailable_reason=?,updated_at=? WHERE app_user_id=?", ("Deputy identity could not be authenticated.", now_iso(), app_user_id))
        raise PermissionError("Deputy identity could not be verified. Reconnect your account.")
    if status != 200 or not isinstance(me, dict):
        raise ValueError("Deputy is temporarily unavailable; your connection was not changed.")
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
    return {**verified, "write_ready": True}


def resource_query(client: DeputyClient, resource: str, *, search: dict[str, object] | None = None) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[int] = set()
    start = 0
    while True:
        filters = {f"s{index}": {"field": field, "data": data, "type": "eq"} for index, (field, data) in enumerate((search or {}).items(), 1)}
        status, body = client.request("POST", f"/api/v1/resource/{resource}/QUERY", json={"search": filters, "sort": {"Id": "asc"}, "start": start, "max": 500})
        if status != 200 or not isinstance(body, list):
            raise ValueError(f"Deputy {resource} reference refresh failed.")
        for row in body:
            if isinstance(row, dict) and int(row.get("Id") or 0) not in seen:
                seen.add(int(row["Id"])); result.append(row)
        if len(body) < 500:
            break
        start += 500
    return result


def roster_candidates(client: DeputyClient, desired: dict[str, object]) -> list[NormalizedShift]:
    operational_date = _instant(desired.get("start"))
    if operational_date is None:
        raise ValueError("Complete workday timing is required.")
    rows = resource_query(client, "Roster", search={
        "Date": operational_date.astimezone(get_settings().timezone).date().isoformat(),
        "Employee": int(desired.get("employee") or 0),
        "OperationalUnit": int(desired.get("area") or 0),
    })
    normalized = [normalize_shift(row, resource=True) for row in rows]
    return [row for row in normalized if row.employee_id == int(desired.get("employee") or 0) and row.area_id == int(desired.get("area") or 0)
            and row.start and row.start.astimezone(get_settings().timezone).date() == operational_date.astimezone(get_settings().timezone).date()]


def employee_day_candidates(client: DeputyClient, desired: dict[str, object]) -> list[NormalizedShift]:
    operational_date = _instant(desired.get("start"))
    if operational_date is None:
        return []
    rows = resource_query(client, "Roster", search={
        "Date": operational_date.astimezone(get_settings().timezone).date().isoformat(),
        "Employee": int(desired.get("employee") or 0),
    })
    normalized = [normalize_shift(row, resource=True) for row in rows]
    return [row for row in normalized if row.employee_id == int(desired.get("employee") or 0) and row.start
            and row.start.astimezone(get_settings().timezone).date() == operational_date.astimezone(get_settings().timezone).date()]


def shifts_overlap(left: NormalizedShift, right: NormalizedShift) -> bool:
    return bool(left.start and left.end and right.start and right.end and left.start < right.end and right.start < left.end)


def preflight_trial_batch(preview: dict[str, object], *, app_user_id: int, session: object = requests) -> list[str]:
    blockers = list(preview.get("blockers") or [])
    verified = verify_write_readiness(app_user_id, session=session)
    client: DeputyClient = verified["client"]
    for action in preview.get("actions") or []:
        operation = str(action.get("operation") or "")
        desired = dict(action.get("desired") or {})
        label = str(action.get("role_label") or action.get("assignment_key") or "Assignment")
        if operation == "create":
            exact = [row for row in roster_candidates(client, desired) if _business_equal(row, desired)]
            if len(exact) > 1:
                blockers.append(f"{label}: multiple exact Deputy rosters exist; operator reconciliation is required.")
            intended = normalized_desired(desired)
            conflicts = [row for row in employee_day_candidates(client, desired) if not _business_equal(row, desired) and shifts_overlap(row, intended)]
            if conflicts:
                blockers.append(f"{label}: an overlapping Deputy roster already exists for Employee #{intended.employee_id}.")
        elif operation in {"update", "delete", "unchanged"}:
            roster_id = int(action.get("roster_id") or 0)
            if not roster_id:
                blockers.append(f"{label}: the linked Deputy roster ID is missing.")
                continue
            status, body = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
            if status != 200:
                blockers.append(f"{label}: Roster #{roster_id} could not be verified before mutation.")
                continue
            try:
                current = normalize_v2_response(body)
            except ValueError:
                blockers.append(f"{label}: Roster #{roster_id} returned an unexpected v2 response.")
                continue
            if current.can_edit is False or current.timesheet_id:
                suffix = f" #{current.timesheet_id}" if current.timesheet_id else ""
                blockers.append(f"{label}: locked by Timesheet{suffix}.")
            if operation == "unchanged" and not _business_equal(current, desired):
                blockers.append(f"{label}: Deputy changed outside Re-Deputy; review before continuing.")
            if operation in {"update", "delete"}:
                with get_connection() as conn:
                    link = conn.execute("SELECT last_verified_state FROM deputy_roster_links WHERE tenant_host=? AND stable_assignment_key=?", (verified["tenant_host"], action.get("assignment_key"))).fetchone()
                if link is None or not _matches_stored_baseline(current, link["last_verified_state"]):
                    blockers.append(f"{label}: Deputy changed outside Re-Deputy since the last verified read-back.")
    return sorted(set(blockers))


def refresh_references(app_user_id: int, *, session: object = requests) -> dict[str, object]:
    verified = verify_read_access(app_user_id, session=session)
    client = verified["client"]
    employees: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    errors: dict[str, str] = {}
    try:
        employees = resource_query(client, "Employee")
    except (ValueError, requests.RequestException):
        errors["employees"] = "Employee references are unavailable or permission denied."
    try:
        units = resource_query(client, "OperationalUnit")
    except (ValueError, requests.RequestException):
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


@dataclass(frozen=True)
class NormalizedShift:
    roster_id: int | None
    employee_id: int
    area_id: int
    start: datetime | None
    end: datetime | None
    break_minutes: int
    note: str
    is_open: bool
    is_published: bool
    approval_required: bool
    timesheet_id: int | None
    can_edit: bool | None


class DeputyOperationError(RuntimeError):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


def deputy_error(status: int, body: object, action: str) -> DeputyOperationError:
    sanitized = json.dumps(body, separators=(",", ":"))[:1000].lower() if isinstance(body, (dict, list)) else str(body)[:1000].lower()
    if status == 401:
        kind = "AUTH"
    elif status == 403:
        kind = "LOCKED" if "timesheet" in sanitized or "locked" in sanitized else "PERMISSION"
    elif "overlap" in sanitized or "start_time" in sanitized or "end_time" in sanitized:
        kind = "OVERLAP"
    elif "timesheet" in sanitized or "shift_validation" in sanitized and "edit" in sanitized:
        kind = "LOCKED"
    elif status == 400:
        kind = "VALIDATION"
    elif status == 429 or status >= 500:
        kind = "TRANSIENT"
    else:
        kind = "UNKNOWN"
    return DeputyOperationError(kind, f"Deputy {action} was not accepted ({status}; {kind}).")


def _instant(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(float(value), tz=get_settings().timezone)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=get_settings().timezone)
    return parsed


def extract_v2_shift(body: object) -> dict[str, object]:
    if not isinstance(body, dict) or body.get("success") is not True or "data" not in body:
        raise ValueError("Deputy returned an unexpected v2 shift response.")
    data = body["data"]
    if isinstance(data, dict) and isinstance(data.get("shift"), dict):
        data = data["shift"]
    if not isinstance(data, dict):
        raise ValueError("Deputy returned an unexpected v2 shift response.")
    return data


def normalize_shift(value: dict[str, object], *, resource: bool = False) -> NormalizedShift:
    def first(*names: str) -> object:
        return next((value[name] for name in names if value.get(name) is not None), None)
    slots = first("mealbreakSlots", "MealbreakSlots", "Slots") or []
    break_minutes = _resource_break_minutes(slots, first("Mealbreak", "mealbreak")) if resource else _v2_break_minutes(slots, first("mealbreakDuration"))
    timesheet = first("timesheet", "MatchedByTimesheet", "matchedByTimesheet")
    return NormalizedShift(
        roster_id=int(first("id", "Id") or 0) or None,
        employee_id=int(first("employee", "Employee") or 0), area_id=int(first("area", "OperationalUnit") or 0),
        start=_instant(first("start", "startAt", "StartTime")), end=_instant(first("end", "endAt", "EndTime")),
        break_minutes=break_minutes, note=str(first("note", "Comment") or ""),
        is_open=bool(first("isOpen", "Open") or False), is_published=bool(first("isPublished", "Published") or False),
        approval_required=bool(first("approvalRequired", "ApprovalRequired", "ConfirmStatus") or False),
        timesheet_id=int(timesheet or 0) or None,
        can_edit=value.get("canEdit") if isinstance(value.get("canEdit"), bool) else None,
    )


def _slot_is_break(slot: dict[str, object]) -> bool:
    values = {str(slot.get(name) or "").strip().upper() for name in ("strType", "type", "slotType", "Type")}
    return bool(values & {"B", "BREAK", "MEAL_BREAK", "MEAL BREAK"})


def _slot_seconds(slot: dict[str, object]) -> int:
    for start_name, end_name in (("intStart", "intEnd"), ("start", "end"), ("Start", "End")):
        try:
            start, end = int(slot[start_name]), int(slot[end_name])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= start <= end <= 172800:
            return end - start
    return 0


def _resource_mealbreak_fallback(value: object) -> int:
    if not isinstance(value, str):
        return 0
    match = re.fullmatch(r"(?:\d{4}-\d{2}-\d{2}T)?(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", value.strip())
    if not match:
        return 0
    hours, minutes, seconds = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    if hours > 23 or minutes > 59 or seconds > 59:
        return 0
    return int(round((hours * 3600 + minutes * 60 + seconds) / 60))


def _resource_break_minutes(slots: object, mealbreak: object) -> int:
    if isinstance(slots, list):
        seconds = sum(_slot_seconds(slot) for slot in slots if isinstance(slot, dict) and _slot_is_break(slot))
        if seconds:
            return int(round(seconds / 60))
    return _resource_mealbreak_fallback(mealbreak)


def _v2_break_minutes(slots: object, duration: object) -> int:
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return max(0, int(round(float(duration) * 60)))
    if isinstance(duration, str) and re.fullmatch(r"\d+(?:\.\d+)?", duration.strip()):
        return max(0, int(round(float(duration) * 60)))
    if isinstance(slots, list):
        return int(round(sum(_slot_seconds(slot) for slot in slots if isinstance(slot, dict) and _slot_is_break(slot)) / 60))
    return 0


def normalize_v2_response(body: object) -> NormalizedShift:
    return normalize_shift(extract_v2_shift(body))


def normalized_desired(desired: dict[str, object]) -> NormalizedShift:
    return normalize_shift({
        "employee": desired.get("employee"), "area": desired.get("area"), "start": desired.get("start"), "end": desired.get("end"),
        "mealbreakDuration": float(int(desired.get("break_minutes") or 0)) / 60, "note": desired.get("note"),
        "isOpen": bool(desired.get("is_open", False)), "approvalRequired": bool(desired.get("approval_required", False)),
    })


def build_v2_shift_payload(desired: dict[str, object]) -> dict[str, object]:
    shift: dict[str, object] = {
        "area": int(desired["area"]), "employee": int(desired["employee"]), "start": str(desired["start"]), "end": str(desired["end"]),
        "note": str(desired.get("note") or ""), "isOpen": bool(desired.get("is_open", False)), "approvalRequired": bool(desired.get("approval_required", False)),
    }
    break_minutes = int(desired.get("break_minutes") or 0)
    shift["mealbreakSlots"] = ([{"slotType": "BREAK", "state": 3, "canStartEarly": True, "canEndEarly": True,
                                  "isMandatory": True, "type": "MEAL_BREAK", "start": 0, "end": break_minutes * 60}]
                                if break_minutes else [])
    return {"data": {"shift": shift, "override": {"shiftValidation": False, "publishValidation": False}}}


def _business_equal(current: NormalizedShift | dict[str, object], desired: NormalizedShift | dict[str, object]) -> bool:
    left = current if isinstance(current, NormalizedShift) else normalize_shift(current, resource=any(k in current for k in ("Id", "Employee", "OperationalUnit")))
    right = desired if isinstance(desired, NormalizedShift) else normalized_desired(desired)
    return (left.employee_id == right.employee_id and left.area_id == right.area_id
            and left.start is not None and right.start is not None and left.start.astimezone(get_settings().timezone) == right.start.astimezone(get_settings().timezone)
            and left.end is not None and right.end is not None and left.end.astimezone(get_settings().timezone) == right.end.astimezone(get_settings().timezone)
            and left.break_minutes == right.break_minutes and left.note == right.note
            and left.is_open == right.is_open and left.approval_required == right.approval_required)


def _matches_stored_baseline(current: NormalizedShift, stored: object) -> bool:
    try:
        raw = json.loads(str(stored or ""))
        return isinstance(raw, dict) and _business_equal(current, normalize_shift(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _reconcile_after_network_error(client: DeputyClient, operation: dict[str, object], desired: dict[str, object], roster_id: int | None,
                                   *, create_transmitted: bool = False) -> dict[str, object]:
    op_type = str(operation["operation_type"])
    if op_type == "create":
        if not create_transmitted:
            return _finish_operation(str(operation["operation_uuid"]), "unknown", None,
                                     {"message": "Create was not known to have reached Deputy; no roster ownership was claimed."},
                                     False, "UNKNOWN_NETWORK_RESULT")
        candidates = roster_candidates(client, desired)
        exact = [row for row in candidates if _business_equal(row, desired) and not row.is_open]
        if len(exact) == 1:
            adopted = int(exact[0].roster_id or 0)
            if adopted:
                status, body = client.request("GET", f"/api/management/v2/shifts/{adopted}")
                if status == 200 and _business_equal(normalize_v2_response(body), desired):
                    _save_roster_link(operation, desired, adopted, extract_v2_shift(body),
                                      ownership="ownership_unconfirmed", replace_ownership=True)
                    return _finish_operation(
                        str(operation["operation_uuid"]), "unknown", adopted,
                        {"reconciled": True, "message": "An equivalent Deputy roster exists, but the lost create response means ownership is unconfirmed. It cannot be deleted by Re-Deputy."},
                        False, "UNKNOWN_NETWORK_RESULT",
                    )
        if len(exact) > 1:
            return _finish_operation(str(operation["operation_uuid"]), "ambiguous", None, {"message": "Multiple exact Deputy candidates require manual reconciliation."}, False, "AMBIGUOUS")
        return _finish_operation(str(operation["operation_uuid"]), "unknown", None, {"message": "No exact Deputy candidate found; controlled retry requires review."}, False, "UNKNOWN_NETWORK_RESULT")
    if op_type in {"update", "delete"} and roster_id:
        status, body = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
        if op_type == "delete" and status == 404:
            return _finish_operation(str(operation["operation_uuid"]), "verified", roster_id, {"deleted": True, "reconciled": True}, True)
        if op_type == "update" and status == 200 and _business_equal(normalize_v2_response(body), desired):
            return _finish_operation(str(operation["operation_uuid"]), "verified", roster_id, {"reconciled": True}, True)
    if op_type == "publish":
        ids = [int(value) for value in desired.get("roster_ids") or []]
        states = [client.request("GET", f"/api/management/v2/shifts/{value}") for value in ids]
        if all(status == 200 and normalize_v2_response(body).is_published for status, body in states):
            return _finish_operation(str(operation["operation_uuid"]), "verified", None, {"published_ids": ids, "reconciled": True}, True)
    return _finish_operation(str(operation["operation_uuid"]), "unknown", roster_id, {"message": "Deputy result is unknown; reconciliation is required."}, False, "UNKNOWN_NETWORK_RESULT")


def _save_roster_link(operation: dict[str, object], desired: dict[str, object], roster_id: int, readback: dict[str, object], *,
                      ownership: str = "re_deputy_created_trial", replace_ownership: bool = False) -> None:
    ownership_update = "ownership=excluded.ownership," if replace_ownership else ""
    with get_connection() as conn:
        conn.execute(f"""INSERT INTO deputy_roster_links(tenant_host,workday_id,stable_assignment_key,crew_person_id,deputy_employee_id,deputy_unit_id,deputy_roster_id,context_type,ownership,last_desired_hash,last_verified_hash,last_verified_state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_host,stable_assignment_key) DO UPDATE SET workday_id=excluded.workday_id,
            crew_person_id=excluded.crew_person_id,deputy_employee_id=excluded.deputy_employee_id,deputy_unit_id=excluded.deputy_unit_id,
            deputy_roster_id=excluded.deputy_roster_id,context_type=excluded.context_type,{ownership_update}last_desired_hash=excluded.last_desired_hash,
            last_verified_hash=excluded.last_verified_hash,last_verified_state=excluded.last_verified_state,updated_at=excluded.updated_at""",
            (operation["tenant_host"], operation["workday_id"], operation["stable_assignment_key"], desired.get("crew_person_id"), int(desired.get("employee") or 0), int(desired.get("area") or 0), roster_id,
             str(desired.get("context_type") or "production"), ownership, normalized_hash(desired), normalized_hash(readback), json.dumps(readback)[:10000], now_iso(), now_iso()))


def mapping_snapshot(app_user_id: int) -> dict[str, object]:
    status = connection_status(app_user_id)
    host = str(status.get("tenant_host") or "")
    if not host:
        return {"host": "", "employees": [], "units": [], "people": [], "person_mappings": {}, "unit_mappings": {}}
    with get_connection() as conn:
        employees = [dict(r) for r in conn.execute("SELECT * FROM deputy_reference_employees WHERE app_user_id=? AND tenant_host=? ORDER BY display_name", (app_user_id, host))]
        units = [dict(r) for r in conn.execute("SELECT * FROM deputy_reference_units WHERE app_user_id=? AND tenant_host=? ORDER BY display_name", (app_user_id, host))]
        people = [dict(r) for r in conn.execute("SELECT id,canonical_display_name FROM crew_people WHERE is_active=1 AND merged_into_person_id IS NULL AND COALESCE(person_type,'employee')='employee' ORDER BY canonical_display_name")]
        person_mappings = {int(r["crew_person_id"]): int(r["deputy_employee_id"]) for r in conn.execute("SELECT * FROM deputy_person_mappings WHERE tenant_host=?", (host,))}
        unit_mappings = {str(r["mapping_key"]): dict(r) for r in conn.execute("SELECT * FROM deputy_unit_mappings WHERE tenant_host=?", (host,))}
    return {"host": host, "employees": employees, "units": units, "people": people, "person_mappings": person_mappings, "unit_mappings": unit_mappings}


def save_person_mapping(*, app_user_id: int, crew_person_id: int, deputy_employee_id: int) -> None:
    verified = verify_read_access(app_user_id)
    with get_connection() as conn:
        person = conn.execute(
            "SELECT person_type FROM crew_people WHERE id=? AND is_active=1 AND merged_into_person_id IS NULL",
            (crew_person_id,),
        ).fetchone()
        if person is None or str(person["person_type"] or "employee") != "employee":
            raise ValueError("Contractors cannot be mapped to Deputy employees.")
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
        assignments = [dict(r) for r in conn.execute("""SELECT a.*,COALESCE(v.is_truck,0) vehicle_is_truck
            FROM workday_assignments a LEFT JOIN crew_vehicles v ON v.id=a.vehicle_id
            WHERE a.roster_day_id=? ORDER BY a.sort_order,a.id""", (workday_id,))]
        people = {int(r["crew_person_id"]): int(r["deputy_employee_id"]) for r in conn.execute("""SELECT m.* FROM deputy_person_mappings m
            JOIN deputy_reference_employees e ON e.app_user_id=? AND e.tenant_host=m.tenant_host AND e.deputy_employee_id=m.deputy_employee_id AND e.active=1
            JOIN crew_people p ON p.id=m.crew_person_id AND COALESCE(p.person_type,'employee')='employee'
            WHERE m.tenant_host=?""", (app_user_id, host))}
        units = {str(r["mapping_key"]): dict(r) for r in conn.execute("""SELECT m.* FROM deputy_unit_mappings m
            JOIN deputy_reference_units u ON u.app_user_id=? AND u.tenant_host=m.tenant_host AND u.deputy_unit_id=m.deputy_unit_id AND u.active=1
            WHERE m.tenant_host=?""", (app_user_id, host))}
        links = {str(r["stable_assignment_key"]): dict(r) for r in conn.execute("SELECT * FROM deputy_roster_links WHERE tenant_host=? AND workday_id=?", (host, workday_id))}
    start_text = str(workday["office_start"] or "")
    finish_text = str(workday["end_time"] or "")
    actions: list[dict[str, object]] = []; local_only: list[dict[str, str]] = []; blockers: list[str] = []
    if not start_text or not finish_text:
        blockers.append("Deputy write unavailable until the full workday Start and Finish are known; race markers are not shift boundaries.")
    employee_counts: dict[int, int] = {}
    for assignment in assignments:
        if str(assignment.get("assignment_state") or "assigned") == "assigned" and assignment.get("person_id"):
            employee = people.get(int(assignment["person_id"]))
            if employee:
                employee_counts[employee] = employee_counts.get(employee, 0) + 1
    current_keys: set[str] = set()
    for assignment in assignments:
        state = str(assignment.get("assignment_state") or "assigned")
        if state != "assigned" or not assignment.get("person_id"):
            local_only.append({"assignment_key": str(assignment.get("assignment_key") or ""), "reason": "Open Position · Local only" if state == "open" else "TBC · Local only"})
            continue
        key = str(assignment.get("assignment_key") or assignment["id"]); current_keys.add(key)
        employee = people.get(int(assignment["person_id"])); unit = units.get(str(assignment.get("role_key") or ""))
        if employee and employee_counts.get(employee, 0) > 1:
            local_only.append({"assignment_key": key, "reason": "Multiple full-duration roles for one employee · Local only until resolved"})
            blockers.append(f"Employee #{employee} has multiple overlapping full-duration roles; no Deputy mutation is permitted.")
            continue
        if not employee or not unit or not start_text or not finish_text:
            local_only.append({"assignment_key": key, "reason": "Missing acting-user-readable Employee/Area or complete timing · Local only"})
            blockers.append(f"{assignment.get('role_label') or key}: complete timing and acting-user-readable Employee/Area mappings are required.")
            continue
        if str(unit.get("context_type") or "") != "production_role":
            local_only.append({"assignment_key": key, "reason": "Travel / vehicle / non-production context · Local only"})
            continue
        day = date.fromisoformat(str(workday["roster_date"])); start_dt = datetime.combine(day, time.fromisoformat(start_text), tzinfo=get_settings().timezone)
        if bool(assignment.get("vehicle_is_truck")) and int(workday["truck_start_offset_minutes"] or 0) > 0:
            start_dt -= timedelta(minutes=int(workday["truck_start_offset_minutes"] or 0))
        end_dt = datetime.combine(day, time.fromisoformat(finish_text), tzinfo=get_settings().timezone)
        if end_dt <= start_dt: end_dt += timedelta(days=1)
        link = links.get(key)
        desired = {"area": int(unit["deputy_unit_id"]), "employee": employee, "crew_person_id": int(assignment["person_id"]),
                   "start": start_dt.isoformat(), "end": end_dt.isoformat(), "break_minutes": int(workday["break_minutes"] or 0),
                   "note": f"Re-Deputy trial · workday {workday_id} · {key}", "is_open": False, "approval_required": False,
                   "context_type": str(unit["context_type"])}
        op = "update" if link and link.get("deputy_roster_id") else "create"
        if link and str(link.get("last_desired_hash") or "") == normalized_hash(desired): op = "unchanged"
        actions.append({"operation": op, "assignment_key": key, "role_label": assignment.get("role_label"), "desired": desired, "roster_id": link.get("deputy_roster_id") if link else None})
    for key, link in links.items():
        if key not in current_keys and link.get("ownership") == "re_deputy_created_trial" and link.get("deputy_roster_id"):
            actions.append({"operation": "delete", "assignment_key": key, "role_label": "Removed assignment", "desired": {}, "roster_id": int(link["deputy_roster_id"])})
    counts = {name: sum(1 for row in actions if row["operation"] == name) for name in ("create", "update", "delete", "unchanged")}
    counts.update({"local_only": len(local_only)})
    return {"workday_id": workday_id, "workday_label": f"{workday['roster_date']} · {workday['track_label']}", "acting_user_id": app_user_id,
            "tenant_host": host, "connected_identity": str(verified["me"].get("Name") or "Deputy user"),
            "connected_user_id": int(verified["me"]["UserId"]), "connected_employee_id": int(verified["me"]["EmployeeId"]),
            "scope": "Assigned production shifts only. Travel, vehicles, Open, TBC, and Making my own way remain local-only.",
            "actions": actions, "local_only": local_only, "blockers": sorted(set(blockers)), "counts": counts}


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
    except ValueError as exc:
        return _finish_operation(operation_uuid, "failed", row["deputy_roster_id"], {"message": str(exc)}, False, "TRANSIENT")
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
    desired = json.loads(str(operation["desired_state"])); roster_id = operation.get("deputy_roster_id")
    link_ownership = "re_deputy_created_trial"
    op_type = str(operation["operation_type"])
    create_transmitted = False
    try:
        if op_type == "create":
            exact = [row for row in roster_candidates(client, desired) if _business_equal(row, desired)]
            if len(exact) > 1:
                return _finish_operation(operation_uuid, "ambiguous", None, {"message": "Multiple exact Deputy candidates require operator reconciliation."}, False, "OVERLAP")
            intended = normalized_desired(desired)
            conflicts = [row for row in employee_day_candidates(client, desired) if not _business_equal(row, desired) and shifts_overlap(row, intended)]
            if conflicts:
                return _finish_operation(operation_uuid, "failed", None, {"message": "An overlapping Deputy roster exists for this employee."}, False, "OVERLAP")
            if exact:
                roster_id = int(exact[0].roster_id or 0)
                link_ownership = "adopted_existing"
            else:
                create_transmitted = True
                status, body = client.request("POST", "/api/management/v2/shifts", json=build_v2_shift_payload(desired))
                if status != 200:
                    raise deputy_error(status, body, "create")
                created = normalize_v2_response(body)
                roster_id = int(created.roster_id or 0)
                if not roster_id:
                    raise ValueError("Deputy create response did not contain a roster ID.")
            with get_connection() as conn:
                conn.execute("UPDATE deputy_write_operations SET deputy_roster_id=?,updated_at=? WHERE operation_uuid=?", (roster_id, now_iso(), operation_uuid))
        elif op_type == "update":
            if not roster_id: raise ValueError("Update requires a known Deputy roster ID.")
            get_status, current = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
            if get_status != 200: raise RuntimeError("Deputy roster could not be read before update.")
            current_shift = normalize_v2_response(current)
            with get_connection() as conn:
                conn.execute("UPDATE deputy_write_operations SET before_state=?,updated_at=? WHERE operation_uuid=?", (json.dumps(extract_v2_shift(current))[:10000], now_iso(), operation_uuid))
            if current_shift.can_edit is False or current_shift.timesheet_id:
                raise PermissionError(f"Locked by Timesheet #{current_shift.timesheet_id or ''}".rstrip(" #"))
            with get_connection() as conn:
                link = conn.execute("SELECT last_verified_state FROM deputy_roster_links WHERE tenant_host=? AND stable_assignment_key=?", (verified["tenant_host"], operation["stable_assignment_key"])).fetchone()
            if link is None or not _matches_stored_baseline(current_shift, link["last_verified_state"]):
                raise PermissionError("Deputy changed outside Re-Deputy since the last verified read-back.")
            if _business_equal(current_shift, desired):
                return _finish_operation(operation_uuid, "verified", roster_id, {"unchanged": True}, True)
            status, body = client.request("PUT", f"/api/management/v2/shifts/{roster_id}", json=build_v2_shift_payload(desired))
            if status != 200: raise deputy_error(status, body, "update")
            normalize_v2_response(body)
        elif op_type == "delete":
            with get_connection() as conn:
                link = conn.execute("SELECT * FROM deputy_roster_links WHERE tenant_host=? AND stable_assignment_key=?", (verified["tenant_host"], operation["stable_assignment_key"])).fetchone()
            if link is None or link["ownership"] != "re_deputy_created_trial" or not roster_id:
                raise PermissionError("Only a Re-Deputy-created trial roster can be deleted.")
            get_status, current = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
            if get_status != 200: raise RuntimeError("Deputy roster could not be read before delete.")
            current_shift = normalize_v2_response(current)
            with get_connection() as conn:
                conn.execute("UPDATE deputy_write_operations SET before_state=?,updated_at=? WHERE operation_uuid=?", (json.dumps(extract_v2_shift(current))[:10000], now_iso(), operation_uuid))
            if current_shift.can_edit is False or current_shift.timesheet_id:
                raise PermissionError(f"Locked by Timesheet #{current_shift.timesheet_id or ''}".rstrip(" #"))
            if not _matches_stored_baseline(current_shift, link["last_verified_state"]):
                raise PermissionError("Deputy changed outside Re-Deputy since the last verified read-back.")
            status, body = client.request("DELETE", f"/api/management/v2/shifts/{roster_id}")
            if status != 200: raise deputy_error(status, body, "delete")
            if not isinstance(body, dict) or body.get("success") is not True: raise ValueError("Deputy returned an unexpected delete response.")
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
                if read_status != 200: raise ConnectionError("Deputy publish readiness could not be verified.")
                if not normalize_v2_response(current).is_published: unpublished.append(value)
            if not unpublished: return _finish_operation(operation_uuid, "verified", None, {"unchanged": True}, True)
            status, body = client.request("POST", "/api/v1/supervise/roster/publish", json={"intMode": 4, "blnAllLocationsMode": 0, "intRosterArray": unpublished})
            if status != 200: raise deputy_error(status, body, "publish")
            for value in unpublished:
                read_status, current = client.request("GET", f"/api/management/v2/shifts/{value}")
                if read_status != 200 or not normalize_v2_response(current).is_published:
                    raise ConnectionError("Deputy publish result is unknown; reconciliation required.")
            return _finish_operation(operation_uuid, "verified", None, {"published_ids": unpublished}, True)
        else:
            raise ValueError("Unsupported Deputy operation.")
        verify_status, readback = client.request("GET", f"/api/management/v2/shifts/{roster_id}")
        if verify_status != 200:
            raise ConnectionError("Deputy result unknown after write; reconciliation required.")
        normalized_readback = normalize_v2_response(readback)
        if not _business_equal(normalized_readback, desired):
            raise ConnectionError("Deputy read-back did not match the intended shift.")
        _save_roster_link(operation, desired, int(roster_id), extract_v2_shift(readback), ownership=link_ownership,
                          replace_ownership=(op_type == "create"))
        return _finish_operation(operation_uuid, "verified", roster_id, {"readback": "verified"}, True)
    except requests.RequestException:
        try:
            return _reconcile_after_network_error(client, operation, desired, roster_id, create_transmitted=create_transmitted)
        except requests.RequestException:
            return _finish_operation(operation_uuid, "unknown", roster_id, {"message": "Deputy result is unknown; reconciliation is required."}, False, "UNKNOWN_NETWORK_RESULT")
    except PermissionError as exc:
        status = "locked" if "Timesheet" in str(exc) else "failed"
        return _finish_operation(operation_uuid, status, roster_id, {"message": str(exc)}, False, "LOCKED" if status == "locked" else "PERMISSION")
    except ConnectionError as exc:
        return _finish_operation(operation_uuid, "unknown", roster_id, {"message": str(exc)}, False, "UNKNOWN_NETWORK_RESULT")
    except ValueError as exc:
        return _finish_operation(operation_uuid, "failed", roster_id, {"message": str(exc)[:500]}, False, "VALIDATION")
    except DeputyOperationError as exc:
        status = "locked" if exc.error_class == "LOCKED" else ("ambiguous" if exc.error_class == "OVERLAP" else "failed")
        return _finish_operation(operation_uuid, status, roster_id, {"message": str(exc)[:500]}, False, exc.error_class)
    except Exception as exc:
        return _finish_operation(operation_uuid, "failed", roster_id, {"message": str(exc)[:500]}, False, "TRANSIENT" if isinstance(exc, RuntimeError) and "(5" in str(exc) else "UNKNOWN")


def _finish_operation(operation_uuid: str, status: str, roster_id: int | None, result: dict[str, object], verified: bool, error_class: str | None = None) -> dict[str, object]:
    with get_connection() as conn:
        conn.execute("UPDATE deputy_write_operations SET status=?,deputy_roster_id=COALESCE(?,deputy_roster_id),error_class=?,sanitized_result=?,readback_verified=?,completed_at=?,updated_at=? WHERE operation_uuid=?",
                     (status, roster_id, error_class, json.dumps(result)[:10000], 1 if verified else 0, now_iso(), now_iso(), operation_uuid))
    return {"operation_uuid": operation_uuid, "status": status, "roster_id": roster_id, "readback_verified": verified, **result}


def execute_trial_batch(preview: dict[str, object], *, app_user_id: int, session: object = requests) -> tuple[list[dict[str, object]], list[str]]:
    try:
        blockers = preflight_trial_batch(preview, app_user_id=app_user_id, session=session)
    except (ValueError, PermissionError, requests.RequestException) as exc:
        return [], [str(exc)[:500]]
    if blockers:
        return [], blockers
    results: list[dict[str, object]] = []
    intended = [action for action in preview.get("actions") or [] if action.get("operation") in {"create", "update", "delete"}]
    for action in intended:
        prepared = prepare_operation(app_user_id=app_user_id, workday_id=int(preview["workday_id"]), assignment_key=str(action["assignment_key"]),
                                     operation_type=str(action["operation"]), desired=dict(action.get("desired") or {}), roster_id=action.get("roster_id"), session=session)
        outcome = execute_operation(str(prepared["operation_uuid"]), app_user_id, session=session)
        results.append(outcome)
        if outcome.get("status") != "verified" or not outcome.get("readback_verified"):
            return results, []
    verified_ids = [int(row["roster_id"]) for row in results if row.get("roster_id")]
    verified_ids.extend(int(action["roster_id"]) for action in preview.get("actions") or [] if action.get("operation") == "unchanged" and action.get("roster_id"))
    if verified_ids and len(results) == len(intended):
        prepared = prepare_operation(app_user_id=app_user_id, workday_id=int(preview["workday_id"]), assignment_key=f"publish:{preview['workday_id']}",
                                     operation_type="publish", desired={"roster_ids": verified_ids}, session=session)
        results.append(execute_operation(str(prepared["operation_uuid"]), app_user_id, session=session))
    return results, []


def write_audit_summary(limit: int = 30) -> dict[str, object]:
    with get_connection() as conn:
        counts = {str(row["status"]): int(row["n"]) for row in conn.execute("SELECT status,COUNT(*) n FROM deputy_write_operations GROUP BY status")}
        rows = [dict(row) for row in conn.execute("""SELECT o.*,u.display_name actor_name,r.track_label workday_label
            FROM deputy_write_operations o JOIN app_users u ON u.id=o.app_user_id LEFT JOIN roster_days r ON r.id=o.workday_id
            ORDER BY o.created_at DESC LIMIT ?""", (limit,)).fetchall()]
    for row in rows:
        try:
            desired = json.loads(str(row.get("desired_state") or "{}"))
        except (TypeError, ValueError):
            desired = {}
        row["recovery_summary"] = {name: desired.get(name) for name in ("employee", "area", "start", "end", "break_minutes", "note") if desired.get(name) not in (None, "")}
        row.pop("permission_snapshot", None); row.pop("desired_state", None); row.pop("before_state", None)
    return {"counts": counts, "rows": rows, "mode": load_config().get("write_mode", "off")}
