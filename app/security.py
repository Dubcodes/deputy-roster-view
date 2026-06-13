from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from .config import Settings, get_settings


PIN_HASH_ALGORITHM = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 390_000
SESSION_COOKIE_NAME = "drv_trusted_device"


def now_iso() -> str:
    return datetime.now(get_settings().timezone).isoformat(timespec="seconds")


def session_expires_at(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (datetime.now(settings.timezone) + timedelta(days=settings.trusted_device_days)).isoformat(timespec="seconds")


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PIN_HASH_ITERATIONS)
    return "$".join(
        [
            PIN_HASH_ALGORITHM,
            str(PIN_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_pin(pin: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != PIN_HASH_ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(40)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_or_create_fernet_key(settings: Settings | None = None) -> bytes:
    settings = settings or get_settings()
    configured = settings.app_secret_key.strip()
    if configured:
        return _normalise_fernet_key(configured)

    os.makedirs(settings.data_dir, exist_ok=True)
    key_path = Path(settings.data_dir) / "app_secret.key"
    if key_path.exists():
        return _normalise_fernet_key(key_path.read_text(encoding="utf-8").strip())

    key = Fernet.generate_key()
    key_path.write_text(key.decode("ascii"), encoding="utf-8")
    return key


def encrypt_text(value: str, settings: Settings | None = None) -> str:
    if value == "":
        return ""
    return Fernet(get_or_create_fernet_key(settings)).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str, settings: Settings | None = None) -> str:
    if value == "":
        return ""
    return Fernet(get_or_create_fernet_key(settings)).decrypt(value.encode("ascii")).decode("utf-8")


def _normalise_fernet_key(value: str) -> bytes:
    candidate = value.encode("ascii", errors="ignore")
    try:
        Fernet(candidate)
        return candidate
    except Exception:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)
