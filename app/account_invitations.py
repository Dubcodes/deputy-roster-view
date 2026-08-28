from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from .database import get_connection, persist_deputy_user_credentials
from .security import hash_pin, re_deputy_pin_error


def _now() -> datetime:
    return datetime.now().astimezone()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_account_invite(
    account_email: str,
    display_name: str,
    created_by: int,
    days: int = 1,
    *,
    crew_person_id: int | None = None,
) -> dict[str, object]:
    with get_connection() as conn:
        return _create_account_invite_conn(
            conn,
            account_email,
            display_name,
            created_by,
            days,
            crew_person_id=crew_person_id,
        )


def _create_account_invite_conn(
    conn: sqlite3.Connection,
    account_email: str,
    display_name: str,
    created_by: int,
    days: int = 1,
    *,
    crew_person_id: int | None = None,
) -> dict[str, object]:
    email = account_email.strip().lower()
    if "@" not in email:
        raise ValueError("Enter the Re-Deputy account email.")
    token = secrets.token_urlsafe(40)
    now = _now()
    expires = now + timedelta(days=max(1, min(days, 30)))
    if conn.execute("SELECT 1 FROM app_users WHERE LOWER(deputy_email)=LOWER(?)", (email,)).fetchone():
        raise ValueError("That Re-Deputy account email is already in use.")
    conn.execute(
        "UPDATE account_invitations SET revoked_at=? WHERE LOWER(account_email)=LOWER(?) AND consumed_at IS NULL AND revoked_at IS NULL",
        (now.isoformat(timespec="seconds"), email),
    )
    if crew_person_id is not None:
        person = conn.execute(
            "SELECT id,app_user_id,person_type FROM crew_people WHERE id=? AND is_active=1 AND merged_into_person_id IS NULL",
            (crew_person_id,),
        ).fetchone()
        if person is None or person["app_user_id"] is not None or str(person["person_type"] or "employee") != "employee":
            raise ValueError("Select an active, unlinked canonical crew person.")
    cursor = conn.execute(
        "INSERT INTO account_invitations(token_hash,account_email,display_name,created_by_user_id,created_at,expires_at,crew_person_id) VALUES(?,?,?,?,?,?,?)",
        (_token_hash(token), email, display_name.strip(), created_by, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"), crew_person_id),
    )
    return {"id": int(cursor.lastrowid), "token": token, "account_email": email, "display_name": display_name.strip(), "expires_at": expires.isoformat(timespec="seconds")}


def reissue_account_invite(invite_id: int, created_by: int) -> dict[str, object]:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        invite = conn.execute(
            "SELECT account_email,display_name,crew_person_id FROM account_invitations WHERE id=?",
            (invite_id,),
        ).fetchone()
        if invite is None:
            raise ValueError("Invitation not found.")
        return _create_account_invite_conn(
            conn,
            str(invite["account_email"] or ""),
            str(invite["display_name"] or ""),
            created_by,
            crew_person_id=int(invite["crew_person_id"]) if invite["crew_person_id"] is not None else None,
        )


def account_invite_details(token: str) -> dict[str, object] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM account_invitations WHERE token_hash=?", (_token_hash(token),)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["available"] = not item.get("consumed_at") and not item.get("revoked_at") and str(item["expires_at"]) > _now().isoformat(timespec="seconds")
    return item


def activate_account_invite(
    token: str,
    pin: str,
    display_name: str,
    *,
    deputy_web_url: str = "",
    encrypted_email: str = "",
    encrypted_password: str = "",
) -> object:
    if error := re_deputy_pin_error(pin):
        raise ValueError(error)
    credential_values = (deputy_web_url.strip(), encrypted_email.strip(), encrypted_password.strip())
    if any(credential_values) and not all(credential_values):
        raise ValueError("Deputy credentials must be saved as one complete set.")
    now = _now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        invite = conn.execute("SELECT * FROM account_invitations WHERE token_hash=?", (_token_hash(token),)).fetchone()
        if invite is None or invite["consumed_at"] or invite["revoked_at"] or str(invite["expires_at"]) <= now:
            raise ValueError("This Re-Deputy invitation is invalid, expired, or already used.")
        email = str(invite["account_email"] or "").strip().lower()
        if conn.execute("SELECT 1 FROM app_users WHERE LOWER(deputy_email)=LOWER(?)", (email,)).fetchone():
            raise ValueError("That Re-Deputy account already exists.")
        cursor = conn.execute(
            """INSERT INTO app_users(deputy_email,display_name,display_theme,pin_hash,deputy_web_url,is_admin,is_active,account_type,created_at,updated_at)
               VALUES(?,?,'jade',?,'',0,1,'user',?,?)""",
            (email, display_name.strip(), hash_pin(pin), now, now),
        )
        user_id = int(cursor.lastrowid)
        if invite["crew_person_id"] is not None:
            linked = conn.execute(
                "UPDATE crew_people SET app_user_id=?,identity_source='admin_link',updated_at=? WHERE id=? AND app_user_id IS NULL AND is_active=1 AND merged_into_person_id IS NULL",
                (user_id, now, int(invite["crew_person_id"])),
            )
            if linked.rowcount != 1:
                raise ValueError("The selected crew person is no longer available to link.")
        if all(credential_values):
            persist_deputy_user_credentials(
                conn,
                user_id=user_id,
                deputy_web_url=credential_values[0],
                encrypted_email=credential_values[1],
                encrypted_password=credential_values[2],
                now=now,
            )
        changed = conn.execute(
            "UPDATE account_invitations SET consumed_at=?,activated_user_id=? WHERE id=? AND consumed_at IS NULL AND revoked_at IS NULL",
            (now, user_id, invite["id"]),
        )
        if changed.rowcount != 1:
            raise ValueError("This Re-Deputy invitation has already been used.")
        return conn.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()


def revoke_account_invite(invite_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE account_invitations SET revoked_at=? WHERE id=? AND consumed_at IS NULL", (_now().isoformat(timespec="seconds"), invite_id))


def delete_terminal_account_invite(invite_id: int) -> bool:
    """Delete only a terminal invitation record; never its activated account/person."""
    now = _now().isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """DELETE FROM account_invitations
               WHERE id=? AND (consumed_at IS NOT NULL OR revoked_at IS NOT NULL OR expires_at<=?)""",
            (invite_id, now),
        )
    return bool(result.rowcount)


def account_invite_admin_rows() -> list[dict[str, object]]:
    now = _now().isoformat(timespec="seconds")
    with get_connection() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT id,account_email,display_name,created_at,expires_at,consumed_at,revoked_at,crew_person_id FROM account_invitations ORDER BY id DESC LIMIT 30"
        )]
    for item in rows:
        item["available"] = not item.get("consumed_at") and not item.get("revoked_at") and str(item["expires_at"]) > now
    return rows
