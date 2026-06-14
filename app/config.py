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
    deputy_web_url: str
    deputy_login_email: str
    deputy_login_password: str
    deputy_display_name: str
    deputy_api_token: str
    app_secret_key: str
    trusted_device_days: int
    signup_enabled: bool
    cookie_secure: bool
    tz_name: str
    sync_at_hour: int
    early_pre_shift_sync_hours: int
    pre_shift_sync_minutes: int
    changed_followup_sync_minutes: int
    user_sync_stagger_minutes: int
    user_sync_jitter_minutes: int
    user_sync_batch_size: int
    own_roster_lookback_days: int
    own_roster_lookahead_days: int
    data_dir: str
    db_path: str

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def calendar_configured(self) -> bool:
        return bool(self.deputy_ical_url.strip())

    @property
    def deputy_login_configured(self) -> bool:
        return bool(self.deputy_web_url.strip() and self.deputy_login_email.strip() and self.deputy_login_password)

    @property
    def deputy_login_label(self) -> str:
        if self.deputy_display_name.strip():
            return self.deputy_display_name.strip()
        email = self.deputy_login_email.strip()
        if not email or "@" not in email:
            return email or "Not set"
        name, domain = email.split("@", 1)
        masked_name = name[:2] + "***" if len(name) > 2 else "***"
        return f"{masked_name}@{domain}"

    @property
    def deputy_api_configured(self) -> bool:
        return bool(self.deputy_api_token.strip())


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    tz_name = os.getenv("TZ", DEFAULT_TZ) or DEFAULT_TZ
    data_dir = os.getenv("DATA_DIR", "data")
    db_path = os.getenv("DB_PATH", str(Path(data_dir) / "deputy_roster.sqlite3"))

    return Settings(
        deputy_ical_url=os.getenv("DEPUTY_ICAL_URL", "").strip(),
        deputy_web_url=os.getenv("DEPUTY_WEB_URL", "https://bb12c621103108.au.deputy.com/#/").strip(),
        deputy_login_email=os.getenv("DEPUTY_LOGIN_EMAIL", "").strip(),
        deputy_login_password=os.getenv("DEPUTY_LOGIN_PASSWORD", ""),
        deputy_display_name=os.getenv("DEPUTY_DISPLAY_NAME", "").strip(),
        deputy_api_token=os.getenv("DEPUTY_API_TOKEN", "").strip(),
        app_secret_key=os.getenv("APP_SECRET_KEY", ""),
        trusted_device_days=_int_env("TRUSTED_DEVICE_DAYS", 730),
        signup_enabled=_bool_env("SIGNUP_ENABLED", True),
        cookie_secure=_bool_env("COOKIE_SECURE", False),
        tz_name=tz_name,
        sync_at_hour=_int_env("SYNC_AT_HOUR", 5),
        early_pre_shift_sync_hours=_int_env("EARLY_PRE_SHIFT_SYNC_HOURS", 12),
        pre_shift_sync_minutes=_int_env("PRE_SHIFT_SYNC_MINUTES", 60),
        changed_followup_sync_minutes=_int_env("CHANGED_FOLLOWUP_SYNC_MINUTES", 30),
        user_sync_stagger_minutes=_int_env("USER_SYNC_STAGGER_MINUTES", 7),
        user_sync_jitter_minutes=_int_env("USER_SYNC_JITTER_MINUTES", 2),
        user_sync_batch_size=_int_env("USER_SYNC_BATCH_SIZE", 1),
        own_roster_lookback_days=_int_env("OWN_ROSTER_LOOKBACK_DAYS", 35),
        own_roster_lookahead_days=_int_env("OWN_ROSTER_LOOKAHEAD_DAYS", 56),
        data_dir=data_dir,
        db_path=db_path,
    )
