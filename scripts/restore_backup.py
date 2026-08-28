from __future__ import annotations

"""Offline operator recovery for a validated managed Re-Deputy backup.

This script intentionally has no web route.  It requires an operator to stop
the app first; a SQLite file cannot be safely replaced while the scheduler or
web process might write to it.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.backup_service import PERSISTENT_NAMES, validate_backup  # noqa: E402
from app.config import get_settings  # noqa: E402


def _validate_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
    if integrity != ["ok"] or foreign_keys:
        raise RuntimeError("Restored database validation failed.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or restore a managed Re-Deputy backup while the app is stopped.")
    parser.add_argument("--backup", required=True, help="Managed backup directory containing manifest.json")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; never write application data")
    parser.add_argument("--app-stopped", action="store_true", help="Required assertion that the web app/scheduler is stopped")
    parser.add_argument("--confirm", default="", help="Type RESTORE for an actual replacement")
    args = parser.parse_args()

    checked = validate_backup(args.backup)
    manifest = checked["manifest"]
    database = checked["database"]
    settings = get_settings()
    target_db = Path(settings.db_path)
    print(f"backup={Path(args.backup).resolve()}")
    print(f"created_at={manifest.get('created_at')} version={manifest.get('app_version')} build={manifest.get('app_build')}")
    print(f"target_database={target_db.resolve()}")
    print("integrity_check=ok foreign_key_check_rows=0")
    if args.dry_run:
        print("dry_run=ok")
        return 0
    if not args.app_stopped or args.confirm != "RESTORE":
        raise SystemExit("Real restore requires --app-stopped and --confirm RESTORE.")

    target_db.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(settings.timezone).strftime("%Y%m%d-%H%M%S")
    emergency = target_db.with_name(target_db.name + f".pre-restore-{stamp}")
    if target_db.exists():
        shutil.copy2(target_db, emergency)
        _validate_database(emergency)
        print(f"emergency_copy={emergency}")
    temporary = target_db.with_name(target_db.name + f".restore-{stamp}.tmp")
    shutil.copy2(database, temporary)
    _validate_database(temporary)
    os.replace(temporary, target_db)
    persistent = Path(args.backup) / "persistent"
    if persistent.is_dir():
        for name in PERSISTENT_NAMES:
            source = persistent / name
            target = Path(settings.data_dir) / name
            if not source.exists():
                continue
            if source.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
    _validate_database(target_db)
    print("restore=ok integrity_check=ok foreign_key_check_rows=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
