from __future__ import annotations

"""Small safe snapshots for central Admin mutation history."""

import json
import re
from datetime import datetime
from typing import Any, Mapping

from .config import get_settings
from .database import get_connection


_SENSITIVE_KEY = re.compile(
    r"(?:password|pin|token|secret|cookie|session|authorization|oauth|credential|vapid|encrypted|private[_-]?key)",
    re.IGNORECASE,
)
_MAX_VALUE = 500
_MAX_ITEMS = 40


def safe_snapshot(value: object) -> object:
    """Return a bounded JSON-safe value with credentials redacted centrally."""
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["_truncated"] = True
                break
            label = str(key)
            result[label] = "[redacted]" if _SENSITIVE_KEY.search(label) else safe_snapshot(item)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [safe_snapshot(item) for item in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            result.append("[truncated]")
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text[:_MAX_VALUE] + ("…" if len(text) > _MAX_VALUE else "")


def json_snapshot(value: object) -> str:
    return json.dumps(safe_snapshot(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_admin_action(
    *,
    actor: Mapping[str, object] | None,
    action_key: str,
    action_category: str,
    target_type: str = "",
    target_id: object = "",
    target_label: str = "",
    outcome: str = "completed",
    before: object | None = None,
    after: object | None = None,
    related_audit_type: str = "",
    related_audit_id: object = "",
    request_path: str = "",
    safe_note: str = "",
) -> int:
    actor = actor or {}
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO admin_action_audit(
                created_at,actor_user_id,actor_display_snapshot,actor_account_snapshot,
                action_key,action_category,target_type,target_id,target_label,outcome,
                before_json,after_json,related_audit_type,related_audit_id,request_path,safe_note
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now, int(actor["id"]) if actor.get("id") is not None else None,
                str(actor.get("display_name") or ""), str(actor.get("deputy_email") or ""),
                action_key, action_category, target_type, str(target_id or ""), target_label,
                outcome, json_snapshot(before or {}), json_snapshot(after or {}),
                related_audit_type, str(related_audit_id or ""), request_path, str(safe_snapshot(safe_note)),
            ),
        )
        return int(cursor.lastrowid)


def finalize_admin_action(
    audit_id: int,
    *,
    action_key: str,
    action_category: str,
    target_type: str,
    target_id: object = "",
    target_label: str = "",
    outcome: str,
    before: object | None = None,
    after: object | None = None,
    related_audit_type: str = "",
    related_audit_id: object = "",
    request_path: str = "",
    safe_note: str = "",
) -> None:
    """Finish a write-ahead audit row without ever turning a failure into success."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE admin_action_audit SET
                action_key=?, action_category=?, target_type=?, target_id=?, target_label=?,
                outcome=?, before_json=?, after_json=?, related_audit_type=?, related_audit_id=?, request_path=?, safe_note=?
                WHERE id=?""",
            (
                action_key, action_category, target_type, str(target_id or ""), target_label,
                outcome, json_snapshot(before or {}), json_snapshot(after or {}), related_audit_type,
                str(related_audit_id or ""), request_path,
                str(safe_snapshot(safe_note)), audit_id,
            ),
        )


def list_admin_action_audit(
    *, actor_user_id: int | None = None, category: str = "", outcome: str = "",
    date_from: str = "", date_to: str = "", limit: int = 80,
) -> list[dict[str, object]]:
    clauses = ["1=1"]
    params: list[object] = []
    if actor_user_id:
        clauses.append("actor_user_id=?"); params.append(actor_user_id)
    if category:
        clauses.append("action_category=?"); params.append(category)
    if outcome:
        clauses.append("outcome=?"); params.append(outcome)
    if date_from:
        clauses.append("created_at>=?"); params.append(date_from)
    if date_to:
        clauses.append("created_at<?"); params.append(date_to + "T23:59:59")
    params.append(max(1, min(int(limit), 200)))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM admin_action_audit WHERE {' AND '.join(clauses)} ORDER BY created_at DESC,id DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def admin_audit_count() -> int:
    with get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM admin_action_audit").fetchone()[0])
