from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from .database import get_connection, persist_deputy_user_credentials
from .security import hash_pin, re_deputy_pin_error


def _now() -> datetime:
    return datetime.now().astimezone()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_account_invite(account_email: str, display_name: str, created_by: int, days: int = 7) -> dict[str, object]:
    email = account_email.strip().lower()
    if "@" not in email:
        raise ValueError("Enter the Re-Deputy account email.")
    token = secrets.token_urlsafe(40)
    now = _now()
    expires = now + timedelta(days=max(1, min(days, 30)))
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM app_users WHERE LOWER(deputy_email)=LOWER(?)", (email,)).fetchone():
            raise ValueError("That Re-Deputy account email is already in use.")
        conn.execute(
            "UPDATE account_invitations SET revoked_at=? WHERE LOWER(account_email)=LOWER(?) AND consumed_at IS NULL AND revoked_at IS NULL",
            (now.isoformat(timespec="seconds"), email),
        )
        cursor = conn.execute(
            "INSERT INTO account_invitations(token_hash,account_email,display_name,created_by_user_id,created_at,expires_at) VALUES(?,?,?,?,?,?)",
            (_token_hash(token), email, display_name.strip(), created_by, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
        )
    return {"id": int(cursor.lastrowid), "token": token, "account_email": email, "display_name": display_name.strip(), "expires_at": expires.isoformat(timespec="seconds")}


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


def account_invite_admin_rows() -> list[dict[str, object]]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id,account_email,display_name,created_at,expires_at,consumed_at,revoked_at FROM account_invitations ORDER BY id DESC LIMIT 30"
        )]
