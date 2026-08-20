from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable


def deputy_shift_is_available(row: dict[str, object]) -> bool:
    """Deputy Open is a raw flag; availability additionally requires no assignee."""
    employee_id = row.get("employee_id", row.get("employee"))
    employee_name = str(row.get("employee_name", row.get("employeeName")) or "").strip()
    is_open = row.get("is_open", row.get("isOpen"))
    return bool(is_open) and employee_id in (None, "") and not employee_name


def _title_parts(value: object) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^\[([^]]+)]\s*(.*)$", text)
    return (match.group(1), match.group(2) or "Shift") if match else (text or "Work day", "Shift")


def _vehicle_from_row(row: dict[str, object]) -> str:
    try:
        payload = json.loads(str(row.get("source_payload") or row.get("raw_payload") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    explicit = str(payload.get("vehicle_label") or payload.get("vehicle") or "").strip()
    if explicit:
        return explicit
    role = str(row.get("role_label") or row.get("area_name") or "").strip()
    return role if re.fullmatch(r"(?:\d{3}|Rav\w+|Tender|Transit|OB)", role, re.IGNORECASE) else ""


def interpret_deputy_workdays(rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Project traceable Deputy source rows into stable person-level workdays."""
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for raw in rows:
        row = dict(raw)
        location, title_role = _title_parts(row.get("title"))
        location = str(row.get("location_name") or location or "Work day").strip()
        role = str(row.get("role_label") or row.get("area_name") or title_role or "Shift").strip()
        date_text = str(row.get("date") or row.get("start_at") or row.get("start") or "")[:10]
        location_key = re.sub(r"\W+", "", location.casefold())
        key = (date_text, location_key)
        item = grouped.setdefault(key, {
            "date": date_text,
            "location": location,
            "production_positions": [],
            "vehicle": "",
            "rostered_start": "",
            "rostered_finish": "",
            "raw_source_shift_ids": [],
            "field_provenance": {},
            "current_roster_note_evidence": [],
            "structured_deputy_evidence": [],
        })
        source_id = row.get("source_shift_id", row.get("id"))
        if source_id not in (None, "") and source_id not in item["raw_source_shift_ids"]:
            item["raw_source_shift_ids"].append(source_id)
        vehicle = _vehicle_from_row(row)
        if vehicle:
            if not item["vehicle"]:
                item["vehicle"] = vehicle
                item["field_provenance"]["vehicle"] = "structured_deputy"
        role_is_vehicle = bool(re.fullmatch(r"(?:\d{3}|Rav\w+|Tender|Transit|OB|Travel|Vehicles?)", role, re.IGNORECASE))
        if not role_is_vehicle and role and role not in item["production_positions"]:
            item["production_positions"].append(role)
        start = str(row.get("start_at") or row.get("start") or "")[11:16]
        finish = str(row.get("end_at") or row.get("end") or "")[11:16]
        if start and (not item["rostered_start"] or start < item["rostered_start"]):
            item["rostered_start"] = start
            item["field_provenance"]["rostered_start"] = "deputy_roster"
        if finish and (not item["rostered_finish"] or finish > item["rostered_finish"]):
            item["rostered_finish"] = finish
            item["field_provenance"]["rostered_finish"] = "deputy_roster"
        item["structured_deputy_evidence"].append(row)
    result = []
    for key, item in grouped.items():
        item["production_position"] = "/".join(item.pop("production_positions")) or "Shift"
        item["logical_workday_id"] = hashlib.sha256("|".join(key).encode()).hexdigest()[:24]
        revision_source = {
            "ids": item["raw_source_shift_ids"],
            "position": item["production_position"],
            "vehicle": item["vehicle"],
            "start": item["rostered_start"],
            "finish": item["rostered_finish"],
        }
        item["revision"] = hashlib.sha256(json.dumps(revision_source, sort_keys=True).encode()).hexdigest()
        result.append(item)
    return sorted(result, key=lambda item: (str(item["date"]), str(item["rostered_start"]), str(item["location"])))
