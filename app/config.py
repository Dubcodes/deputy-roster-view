from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_TZ = "Pacific/Auckland"


@dataclass(frozen=True)
class Settings:
    deputy_ical_url: str
    app_password: str
    tz_name: str
    sync_at_hour: int
    pre_shift_sync_minutes: int
    changed_followup_sync_minutes: int
    data_dir: str
    db_path: str

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def calendar_configured(self) -> bool:
        return bool(self.deputy_ical_url.strip())

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_password)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    tz_name = os.getenv("TZ", DEFAULT_TZ) or DEFAULT_TZ
    data_dir = os.getenv("DATA_DIR", "data")
    db_path = os.getenv("DB_PATH", str(Path(data_dir) / "deputy_roster.sqlite3"))

    return Settings(
        deputy_ical_url=os.getenv("DEPUTY_ICAL_URL", "").strip(),
        app_password=os.getenv("APP_PASSWORD", ""),
        tz_name=tz_name,
        sync_at_hour=_int_env("SYNC_AT_HOUR", 5),
        pre_shift_sync_minutes=_int_env("PRE_SHIFT_SYNC_MINUTES", 60),
        changed_followup_sync_minutes=_int_env("CHANGED_FOLLOWUP_SYNC_MINUTES", 30),
        data_dir=data_dir,
        db_path=db_path,
    )
