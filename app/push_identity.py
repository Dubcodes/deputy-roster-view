from __future__ import annotations

import base64
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .config import get_settings
from .database import get_app_setting, get_connection, update_app_settings


PRIVATE_KEY_FILE = "web_push_vapid_private.pem"
PUBLIC_KEY_SETTING = "web_push_vapid_public_key"
IDENTITY_NOTICE_SETTING = "web_push_identity_notice"
_identity_lock = threading.Lock()


@dataclass(frozen=True)
class PushIdentity:
    ready: bool
    public_key: str = ""
    private_key_path: str = ""
    diagnostic: str = ""


def _public_key_text(private_key: ec.EllipticCurvePrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _load_private_key(path: Path) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("The persisted key is not a P-256 private key.")
    return key


def _write_private_key(path: Path, private_key: ec.EllipticCurvePrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


def _deactivate_existing_subscriptions(reason: str) -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """UPDATE push_subscriptions
               SET active=0,revoked_at=?,last_failure_at=?,updated_at=?
               WHERE active=1""",
            (now, now, now),
        )
    if result.rowcount:
        update_app_settings({IDENTITY_NOTICE_SETTING: reason})
    return int(result.rowcount)


def _deactivate_subscriptions_without_https_origin() -> int:
    now = datetime.now(get_settings().timezone).isoformat(timespec="seconds")
    with get_connection() as conn:
        result = conn.execute(
            """UPDATE push_subscriptions
               SET active=0,revoked_at=?,last_failure_at=?,updated_at=?
               WHERE active=1 AND app_origin NOT LIKE 'https://%'""",
            (now, now, now),
        )
    if result.rowcount:
        update_app_settings({
            IDENTITY_NOTICE_SETTING:
                "Existing notification devices need to enable notifications again from the secure HTTPS site."
        })
    return int(result.rowcount)


def ensure_push_identity() -> PushIdentity:
    path = Path(get_settings().data_dir) / PRIVATE_KEY_FILE
    with _identity_lock:
        replacement = False
        try:
            if path.exists():
                try:
                    private_key = _load_private_key(path)
                except (OSError, TypeError, ValueError):
                    replacement = True
                    private_key = ec.generate_private_key(ec.SECP256R1())
                    _write_private_key(path, private_key)
            else:
                with get_connection() as conn:
                    had_subscriptions = bool(
                        conn.execute("SELECT 1 FROM push_subscriptions LIMIT 1").fetchone()
                    )
                replacement = had_subscriptions or bool(get_app_setting(PUBLIC_KEY_SETTING))
                private_key = ec.generate_private_key(ec.SECP256R1())
                _write_private_key(path, private_key)

            public_key = _public_key_text(private_key)
            previous_public_key = get_app_setting(PUBLIC_KEY_SETTING)
            if replacement or (previous_public_key and previous_public_key != public_key):
                _deactivate_existing_subscriptions(
                    "Push identity was replaced. Existing devices need to enable notifications again."
                )
            if previous_public_key != public_key:
                update_app_settings({PUBLIC_KEY_SETTING: public_key})
            _deactivate_subscriptions_without_https_origin()
            return PushIdentity(
                ready=True,
                public_key=public_key,
                private_key_path=str(path),
                diagnostic=get_app_setting(IDENTITY_NOTICE_SETTING),
            )
        except Exception as exc:
            return PushIdentity(
                ready=False,
                diagnostic=f"Push identity could not be prepared ({type(exc).__name__}).",
            )
