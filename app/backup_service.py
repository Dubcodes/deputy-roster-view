from __future__ import annotations

"""Consistent, private on-disk backups for the local Re-Deputy data store."""

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings


MANIFEST_NAME = "manifest.json"
MANAGED_BY = "re-deputy-backup-v1"
DATABASE_NAME = "deputy_roster.sqlite3"
PERSISTENT_NAMES = ("track_maps", "app_secret.key", "web_push_vapid_private.pem")
_backup_lock = threading.Lock()


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(microsecond=0)


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:180]}".strip(": ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validation(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(path)
    try:
        integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    finally:
        conn.close()
    return {
        "integrity_check": "ok" if integrity == ["ok"] else integrity,
        "foreign_key_check_count": len(foreign_keys),
        "foreign_key_check": [] if not foreign_keys else [list(item) for item in foreign_keys[:20]],
    }


def _copy_persistent_data(settings: Settings, destination: Path) -> list[dict[str, object]]:
    source_root = Path(settings.data_dir)
    inventory: list[dict[str, object]] = []
    for name in PERSISTENT_NAMES:
        source = source_root / name
        if not source.exists():
            continue
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("*.tmp", "*.part", "__pycache__"))
            size = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
            inventory.append({"name": name, "kind": "directory", "bytes": size, "classification": "authoritative_or_recovery_required"})
        else:
            shutil.copy2(source, target)
            classification = "secret_recovery_material" if name in {"app_secret.key", "web_push_vapid_private.pem"} else "authoritative_persistent_data"
            inventory.append({"name": name, "kind": "file", "bytes": target.stat().st_size, "classification": classification})
    return inventory


def _record_run(values: dict[str, object], settings: Settings) -> None:
    # Keep backup failure visibility durable, but never include raw paths outside
    # the managed backup root or exception internals.
    from .database import get_connection

    with get_connection(settings) as conn:
        conn.execute(
            """INSERT INTO backup_runs(
                attempted_at,completed_at,reason,requested_by_user_id,backup_id,status,
                backup_path,backup_size_bytes,backup_sha256,integrity_result,foreign_key_check_count,failure_reason
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                values.get("attempted_at"), values.get("completed_at"), values.get("reason"),
                values.get("requested_by_user_id"), values.get("backup_id"), values.get("status"),
                values.get("backup_path"), values.get("backup_size_bytes"), values.get("backup_sha256"),
                values.get("integrity_result"), values.get("foreign_key_check_count"), values.get("failure_reason"),
            ),
        )


def _managed_manifest(path: Path) -> dict[str, Any] | None:
    manifest_path = path / MANIFEST_NAME
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("managed_by") != MANAGED_BY:
        return None
    return parsed


def prune_managed_backups(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    root = Path(settings.backup_dir)
    if not root.is_dir():
        return []
    cutoff = _now(settings).timestamp() - (settings.backup_retention_days * 86400)
    managed: list[tuple[float, Path]] = []
    for candidate in root.iterdir():
        if not candidate.is_dir() or candidate.name.startswith(".tmp-"):
            continue
        manifest = _managed_manifest(candidate)
        if not manifest or manifest.get("status") != "success":
            continue
        try:
            created = datetime.fromisoformat(str(manifest["created_at"])).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        managed.append((created, candidate))
    if not managed:
        return []
    newest = max(managed, key=lambda item: item[0])[1]
    removed: list[str] = []
    for created, candidate in managed:
        if candidate == newest or created >= cutoff:
            continue
        # The manifest check above is the only authority for deletion; timestamp
        # shaped historical/operator directories are deliberately untouched.
        for attempt in range(20):
            try:
                shutil.rmtree(candidate)
                removed.append(candidate.name)
                break
            except PermissionError:
                # Preserve a managed backup if a local scanner still holds it;
                # the next scheduled retention pass can retry safely.
                if attempt == 19:
                    break
                time.sleep(0.1)
    return removed


def create_backup(
    *,
    reason: str,
    requested_by_user_id: int | None = None,
    settings: Settings | None = None,
    app_version: str = "unknown",
    app_build: str = "unknown",
) -> dict[str, object]:
    """Serialize scheduled and manual backups in this application process."""
    active_settings = settings or get_settings()
    attempted = _now(active_settings)
    if not _backup_lock.acquire(blocking=False):
        result = {
            "attempted_at": attempted.isoformat(), "completed_at": _now(active_settings).isoformat(),
            "reason": reason, "requested_by_user_id": requested_by_user_id,
            "backup_id": "", "status": "failed", "backup_path": "", "backup_size_bytes": 0,
            "backup_sha256": "", "integrity_result": "failed", "foreign_key_check_count": None,
            "failure_reason": "A backup is already in progress.",
        }
        try:
            _record_run(result, active_settings)
        except Exception:
            pass
        return result
    try:
        return _create_backup_unlocked(
            reason=reason,
            requested_by_user_id=requested_by_user_id,
            settings=active_settings,
            app_version=app_version,
            app_build=app_build,
        )
    finally:
        _backup_lock.release()


def _create_backup_unlocked(
    *,
    reason: str,
    requested_by_user_id: int | None = None,
    settings: Settings | None = None,
    app_version: str = "unknown",
    app_build: str = "unknown",
) -> dict[str, object]:
    """Make a validated backup and atomically publish it only on success."""
    settings = settings or get_settings()
    attempted = _now(settings)
    root = Path(settings.backup_dir)
    backup_id = attempted.strftime("%Y%m%d-%H%M%S-") + attempted.tzname().lower()
    final_dir = root / backup_id
    temporary_dir: Path | None = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            backup_id = f"{backup_id}-{uuid.uuid4().hex[:8]}"
            final_dir = root / backup_id
        temporary_dir = root / f".tmp-{backup_id}-{uuid.uuid4().hex}"
        temporary_dir.mkdir(mode=0o700)
        backup_db = temporary_dir / DATABASE_NAME
        source_db = Path(settings.db_path)
        if not source_db.is_file():
            raise FileNotFoundError("The configured SQLite database does not exist.")
        # sqlite3.Connection.backup coordinates with SQLite rather than copying a
        # live database file, so WAL state and concurrent readers are consistent.
        source = sqlite3.connect(source_db)
        target = sqlite3.connect(backup_db)
        try:
            source.execute("PRAGMA foreign_keys = ON")
            target.execute("PRAGMA foreign_keys = ON")
            source.backup(target)
        finally:
            target.close()
            source.close()
        validation = _validation(backup_db)
        if validation["integrity_check"] != "ok" or validation["foreign_key_check_count"]:
            raise RuntimeError("Backup validation failed.")
        persistent_dir = temporary_dir / "persistent"
        persistent_dir.mkdir(mode=0o700)
        inventory = _copy_persistent_data(settings, persistent_dir)
        manifest = {
            "managed_by": MANAGED_BY,
            "status": "success",
            "created_at": attempted.isoformat(),
            "timezone": settings.tz_name,
            "backup_reason": reason,
            "app_version": app_version,
            "app_build": app_build,
            "source_database_filename": source_db.name,
            "source_database_size_bytes": source_db.stat().st_size,
            "backup_database_filename": DATABASE_NAME,
            "backup_database_size_bytes": backup_db.stat().st_size,
            "backup_database_sha256": _sha256(backup_db),
            "integrity_check": validation["integrity_check"],
            "foreign_key_check_count": validation["foreign_key_check_count"],
            "application_data_inventory": inventory,
        }
        pending_manifest = temporary_dir / (MANIFEST_NAME + ".pending")
        pending_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Linux production uses an atomic same-volume directory rename.  Windows
        # can deny an open-directory rename; its fallback copies only an
        # *incomplete* directory and publishes manifest.json last, so a partial
        # attempt can never be mistaken for a successful managed backup.
        try:
            temporary_dir.rename(final_dir)
        except PermissionError:
            shutil.copytree(temporary_dir, final_dir)
            try:
                shutil.rmtree(temporary_dir)
            except PermissionError:
                # A local virus scanner can briefly hold a just-validated SQLite
                # file.  Leave this manifest-less temporary directory alone; it
                # is never valid, never retained, and cannot masquerade as a
                # completed backup.
                pass
        os.replace(final_dir / (MANIFEST_NAME + ".pending"), final_dir / MANIFEST_NAME)
        temporary_dir = None
        completed = _now(settings).isoformat()
        result = {
            "attempted_at": attempted.isoformat(), "completed_at": completed, "reason": reason,
            "requested_by_user_id": requested_by_user_id, "backup_id": backup_id, "status": "success",
            "backup_path": backup_id, "backup_size_bytes": manifest["backup_database_size_bytes"],
            "backup_sha256": manifest["backup_database_sha256"], "integrity_result": "ok",
            "foreign_key_check_count": 0, "failure_reason": "",
        }
        _record_run(result, settings)
        try:
            prune_managed_backups(settings)
        except Exception:
            # Retention is never allowed to invalidate a completed backup.
            pass
        return result
    except Exception as exc:
        if temporary_dir and temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)
        result = {
            "attempted_at": attempted.isoformat(), "completed_at": _now(settings).isoformat(), "reason": reason,
            "requested_by_user_id": requested_by_user_id, "backup_id": backup_id, "status": "failed",
            "backup_path": "", "backup_size_bytes": 0, "backup_sha256": "", "integrity_result": "failed",
            "foreign_key_check_count": None, "failure_reason": _safe_error(exc),
        }
        try:
            _record_run(result, settings)
        except Exception:
            pass
        return result


def validate_backup(backup_dir: str | Path) -> dict[str, object]:
    directory = Path(backup_dir)
    manifest = _managed_manifest(directory)
    if manifest is None:
        raise ValueError("Backup manifest is missing or is not a managed Re-Deputy backup.")
    database = directory / DATABASE_NAME
    if not database.is_file():
        raise ValueError("Backup database is missing.")
    if _sha256(database) != str(manifest.get("backup_database_sha256") or ""):
        raise ValueError("Backup SHA-256 does not match its manifest.")
    validation = _validation(database)
    if validation["integrity_check"] != "ok" or validation["foreign_key_check_count"]:
        raise ValueError("Backup SQLite validation failed.")
    return {"manifest": manifest, "database": database, "validation": validation}


def backup_status(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    from .database import get_connection

    with get_connection(settings) as conn:
        latest_success = conn.execute(
            "SELECT * FROM backup_runs WHERE status='success' ORDER BY completed_at DESC,id DESC LIMIT 1"
        ).fetchone()
        latest_failure = conn.execute(
            "SELECT * FROM backup_runs WHERE status='failed' ORDER BY attempted_at DESC,id DESC LIMIT 1"
        ).fetchone()
    success_data = dict(latest_success) if latest_success else None
    failure_data = dict(latest_failure) if latest_failure else None
    unresolved_failure = bool(
        failure_data and (
            not success_data or str(failure_data.get("attempted_at") or "") > str(success_data.get("completed_at") or "")
        )
    )
    return {
        "enabled": settings.backup_enabled,
        "directory": settings.backup_dir,
        "directory_exists": Path(settings.backup_dir).is_dir(),
        "retention_days": settings.backup_retention_days,
        "schedule": f"{settings.backup_hour:02d}:{settings.backup_minute:02d}",
        "latest_success": success_data,
        # Historical failures are retained in backup_runs but are not a current
        # warning after a later successful backup has recovered service.
        "latest_failure": failure_data if unresolved_failure else None,
        "unresolved_failure": unresolved_failure,
    }
