# Re-Deputy Recovery Runbook

Backups are private server-side directories under `/app/backups` in the container, persisted by `/data/compose/22/backups:/app/backups`. They are not browser downloads and there is deliberately no web restore endpoint.

## Validate first

Select the backup paired with the intended release and, while the application may still be running, validate without writes:

```powershell
python scripts/restore_backup.py --backup /app/backups/<backup-id> --dry-run
```

The dry run checks the managed manifest marker, SHA-256, independent SQLite open, `integrity_check`, foreign keys, source/target paths, and recorded release metadata.

## Restore only while offline

Stop the Re-Deputy application and scheduler completely. Keep the current deployment environment and `APP_SECRET_KEY`; environment secrets are intentionally not stored in manifests. After an operator reviews the dry run, run exactly:

```powershell
python scripts/restore_backup.py --backup /app/backups/<backup-id> --app-stopped --confirm RESTORE
```

The tool first creates and validates a timestamped `.pre-restore-*` emergency copy of the current SQLite database, validates a temporary restored copy, replaces the database, restores only the backed-up runtime files (`track_maps/`, fallback `app_secret.key`, and VAPID private key when present), then validates the restored database again. Do not restore against a live application, do not use it as a normal Undo mechanism, and do not delete a failed/partial backup directory by pattern.

After starting the app, confirm HTTPS login, displayed version, a representative month/day, `integrity_check=ok`, zero foreign-key violations, and Deputy write mode off.
