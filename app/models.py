from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ShiftEvent:
    source_uid: str
    source_url_hash: str
    title: str
    description: str
    location: str
    start_at: datetime
    end_at: datetime
    date: str
    raw_hours: float
    break_minutes: int
    paid_hours: float
    source_link: str
    source_status: str
    source_payload: str
