from __future__ import annotations

from dataclasses import replace

from .config import Settings, get_settings
from .database import get_deputy_user_secret
from .security import decrypt_text
from .url_safety import normalize_deputy_web_url


def resolve_deputy_web_url(stored_web_url: str, configured_web_url: str) -> str:
    try:
        if stored_web_url.strip():
            return normalize_deputy_web_url(stored_web_url)
    except ValueError:
        pass
    return normalize_deputy_web_url(configured_web_url)


def deputy_email_for_user(user_id: int, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    secret = get_deputy_user_secret(user_id)
    if secret is None or not str(secret["encrypted_email"] or ""):
        return ""
    return decrypt_text(str(secret["encrypted_email"]), settings).strip().lower()


def settings_for_user(user_id: int, settings: Settings | None = None) -> Settings | None:
    settings = settings or get_settings()
    secret = get_deputy_user_secret(user_id)
    if secret is None:
        return None

    email = decrypt_text(str(secret["encrypted_email"] or ""), settings).strip().lower()
    password = decrypt_text(str(secret["encrypted_password"] or ""), settings).strip()
    encrypted_ical_url = str(secret["encrypted_ical_url"] or "")
    ical_url = decrypt_text(encrypted_ical_url, settings) if encrypted_ical_url else ""
    try:
        web_url = resolve_deputy_web_url(str(secret["deputy_web_url"] or ""), settings.deputy_web_url)
    except ValueError:
        return None
    display_name = str(secret["display_name"] or secret["deputy_email"] or "").strip()
    if not email or not password or not web_url:
        return None

    return replace(
        settings,
        deputy_web_url=web_url,
        deputy_ical_url=ical_url,
        deputy_login_email=email,
        deputy_login_password=password,
        deputy_display_name=display_name,
    )
