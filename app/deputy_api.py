from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

from .config import Settings


TOKEN_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+|OAuth\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
URL_QUERY_RE = re.compile(r"([?&][A-Za-z0-9_%-]*(?:token|key|secret|ap)[A-Za-z0-9_%-]*=)[^&\s\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class DeputyApiResult:
    status: str
    message: str
    records_seen: int = 0
    sample: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def redacted_api_text(value: str) -> str:
    value = TOKEN_RE.sub(lambda match: match.group(0).split()[0] + " [redacted]", value)
    return URL_QUERY_RE.sub(r"\1[redacted]", value)


def deputy_api_base_url(settings: Settings) -> str:
    raw_url = settings.deputy_web_url.strip() or "https://bb12c621103108.au.deputy.com/#/"
    parsed = urlsplit(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/api/v1"


def _safe_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        text = json.dumps(payload, ensure_ascii=True)
    except ValueError:
        text = response.text
    text = redacted_api_text(text.strip())
    if len(text) > 180:
        text = text[:177].rstrip() + "..."
    return text or response.reason


def _roster_sample(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("_DPMetaData")
    if not isinstance(metadata, dict):
        metadata = {}
    employee_info = metadata.get("EmployeeInfo")
    if not isinstance(employee_info, dict):
        employee_info = {}
    unit_info = metadata.get("OperationalUnitInfo")
    if not isinstance(unit_info, dict):
        unit_info = {}

    return {
        "id": record.get("Id"),
        "date": record.get("Date"),
        "start": record.get("StartTimeLocalized") or record.get("StartTime"),
        "end": record.get("EndTimeLocalized") or record.get("EndTime"),
        "employee": employee_info.get("DisplayName") or record.get("Employee"),
        "role": unit_info.get("OperationalUnitName") or unit_info.get("LabelWithCompany") or record.get("OperationalUnit"),
        "open": record.get("Open"),
        "comment": record.get("Comment"),
        "slots": len(record.get("Slots") or []),
    }


def test_deputy_roster_api(settings: Settings) -> DeputyApiResult:
    """Probe the Deputy roster API without persisting anything or logging secrets."""
    if not settings.deputy_api_configured:
        return DeputyApiResult(
            status="missing",
            message="DEPUTY_API_TOKEN is not configured. Email/password alone is not used for API roster pulls yet.",
        )

    base_url = deputy_api_base_url(settings)
    if not base_url:
        return DeputyApiResult(status="error", message="DEPUTY_WEB_URL is not a valid Deputy install URL.")

    url = f"{base_url}/resource/Roster/QUERY"
    payload = {
        "search": {
            "s1": {
                "field": "StartTime",
                "type": "gt",
                "data": str(int(time.time())),
            }
        },
        "sort": {"StartTime": "asc"},
    }
    headers = {
        "Authorization": f"Bearer {settings.deputy_api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
    except requests.RequestException as exc:
        return DeputyApiResult(status="error", message=f"Deputy API request failed: {redacted_api_text(str(exc))}")

    if response.status_code >= 400:
        return DeputyApiResult(
            status="error",
            message=f"Deputy API returned HTTP {response.status_code}: {_safe_error_message(response)}",
        )

    try:
        data = response.json()
    except ValueError:
        return DeputyApiResult(status="error", message="Deputy API responded, but it was not JSON.")

    records = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
    if not isinstance(records, list):
        return DeputyApiResult(status="error", message="Deputy API responded, but the roster shape was not recognised.")

    sample = _roster_sample(records[0]) if records and isinstance(records[0], dict) else None
    return DeputyApiResult(
        status="ok",
        message=f"Deputy API connected. It returned {len(records)} future roster records.",
        records_seen=len(records),
        sample=sample,
    )
