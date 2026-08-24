from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from .database import get_connection
from .security import hash_pin, re_deputy_pin_error, verify_pin


def _now() -> datetime:
    return datetime.now().astimezone()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_contractor_invite(name: str, company: str, created_by: int, days: int = 7) -> dict[str, object]:
    display_name = " ".join(str(name or "").split())
    organisation = " ".join(str(company or "").split())
    if not display_name:
        raise ValueError("Enter the contractor's name.")
    now = _now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO crew_people(
                   canonical_display_name,person_type,company,identity_source,deputy_employee_id,
                   current_deputy_name,app_user_id,is_active,admin_note,created_at,updated_at
               ) VALUES(?,'contractor',?,'contractor',NULL,NULL,NULL,1,'',?,?)""",
            (display_name, organisation, now, now),
        )
        person_id = int(cursor.lastrowid)
    return create_invite(person_id, created_by, days)


def create_invite(person_id: int, created_by: int, days: int = 7) -> dict[str, object]:
    token = secrets.token_urlsafe(40)
    now = _now()
    expires = now + timedelta(days=max(1, min(days, 30)))
    with get_connection() as conn:
        person = conn.execute("SELECT id,canonical_display_name,app_user_id,person_type FROM crew_people WHERE id=? AND is_active=1 AND merged_into_person_id IS NULL", (person_id,)).fetchone()
        if person is None:
            raise ValueError("Select an active canonical crew person.")
        if str(person["person_type"] or "employee") != "contractor":
            raise ValueError("Replacement invitations are available only for contractor identities.")
        if person["app_user_id"]:
            existing = conn.execute("SELECT account_type FROM app_users WHERE id=?", (person["app_user_id"],)).fetchone()
            if existing is None or str(existing["account_type"] or "user") != "contractor":
                raise ValueError("That crew person already has an ordinary app account.")
        conn.execute("UPDATE contractor_invites SET revoked_at=? WHERE crew_person_id=? AND revoked_at IS NULL", (now.isoformat(timespec="seconds"), person_id))
        cursor = conn.execute("INSERT INTO contractor_invites(token_hash,crew_person_id,created_by_user_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                              (_token_hash(token), person_id, created_by, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")))
    return {"id": int(cursor.lastrowid), "token": token, "person_name": str(person["canonical_display_name"]), "expires_at": expires.isoformat(timespec="seconds")}


def invite_details(token: str) -> dict[str, object] | None:
    with get_connection() as conn:
        row = conn.execute("""SELECT i.*,p.canonical_display_name FROM contractor_invites i JOIN crew_people p ON p.id=i.crew_person_id
                            WHERE i.token_hash=?""", (_token_hash(token),)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["available"] = not item.get("consumed_at") and not item.get("revoked_at") and str(item["expires_at"]) > _now().isoformat(timespec="seconds")
    return item


def activate_invite(token: str, pin: str) -> object:
    if error := re_deputy_pin_error(pin):
        raise ValueError(error)
    now = _now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        invite = conn.execute("""SELECT i.*,p.canonical_display_name,p.app_user_id FROM contractor_invites i JOIN crew_people p ON p.id=i.crew_person_id
                               WHERE i.token_hash=?""", (_token_hash(token),)).fetchone()
        if invite is None or invite["consumed_at"] or invite["revoked_at"] or str(invite["expires_at"]) <= now:
            raise ValueError("This contractor invite is invalid, expired, or already used.")
        if invite["app_user_id"]:
            existing = conn.execute("SELECT account_type FROM app_users WHERE id=?", (invite["app_user_id"],)).fetchone()
            if existing is None or str(existing["account_type"] or "user") != "contractor":
                raise ValueError("That crew person already has an ordinary app account.")
            user_id = int(invite["app_user_id"])
            conn.execute("UPDATE app_users SET pin_hash=?,is_active=1,deactivated_at=NULL,last_activity_at=?,updated_at=? WHERE id=? AND account_type='contractor'", (hash_pin(pin), now, now, user_id))
        else:
            internal_login = f"contractor-{invite['crew_person_id']}-{secrets.token_hex(8)}@local.invalid"
            cursor = conn.execute("""INSERT INTO app_users(deputy_email,display_name,display_theme,pin_hash,deputy_web_url,is_admin,is_active,
                                   account_type,contractor_person_id,last_activity_at,created_at,updated_at)
                                   VALUES(?,?,'jade',?,'',0,1,'contractor',?,?,?,?)""",
                                  (internal_login, invite["canonical_display_name"], hash_pin(pin), invite["crew_person_id"], now, now, now))
            user_id = int(cursor.lastrowid)
            conn.execute("UPDATE crew_people SET app_user_id=?,updated_at=? WHERE id=? AND app_user_id IS NULL", (user_id, now, invite["crew_person_id"]))
        conn.execute("UPDATE contractor_invites SET consumed_at=?,activated_user_id=? WHERE id=? AND consumed_at IS NULL", (now, user_id, invite["id"]))
        return conn.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()


def authenticate_contractor_link(token: str, pin: str) -> object:
    with get_connection() as conn:
        user = conn.execute("""SELECT u.* FROM contractor_invites i JOIN app_users u ON u.id=i.activated_user_id
                               WHERE i.token_hash=? AND i.consumed_at IS NOT NULL AND i.revoked_at IS NULL AND u.is_active=1 AND u.account_type='contractor'""",
                            (_token_hash(token),)).fetchone()
    if user is None or not verify_pin(pin, str(user["pin_hash"] or "")):
        raise ValueError("Contractor link or PIN was not recognised.")
    return user


def revoke_invite(invite_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE contractor_invites SET revoked_at=? WHERE id=? AND consumed_at IS NULL", (_now().isoformat(timespec="seconds"), invite_id))


def contractor_admin_rows() -> dict[str, list[dict[str, object]]]:
    with get_connection() as conn:
        invites = [dict(r) for r in conn.execute("""SELECT i.id,i.created_at,i.expires_at,i.consumed_at,i.revoked_at,p.canonical_display_name,p.company
                                                   FROM contractor_invites i JOIN crew_people p ON p.id=i.crew_person_id ORDER BY i.id DESC LIMIT 30""")]
        accounts = [dict(r) for r in conn.execute("""SELECT u.id,u.display_name,u.is_active,u.last_activity_at,u.contractor_person_id,p.company
                                                     FROM app_users u LEFT JOIN crew_people p ON p.id=u.contractor_person_id
                                                     WHERE u.account_type='contractor' ORDER BY u.display_name""")]
    return {"invites": invites, "accounts": accounts}


def deactivate_inactive_contractors(days: int = 180) -> int:
    cutoff = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute("""UPDATE app_users SET is_active=0,deactivated_at=?,updated_at=? WHERE account_type='contractor' AND is_active=1
                               AND COALESCE(NULLIF(last_activity_at,''),created_at)<?""", (_now().isoformat(timespec="seconds"), _now().isoformat(timespec="seconds"), cutoff))
    return int(result.rowcount)
