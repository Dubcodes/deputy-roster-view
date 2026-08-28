# Re-Deputy Deployment Checklist

Use this immediately before and after a production redeploy. Do not put secrets in this file or deployment logs. Production backups are private server files, never app downloads.

## Before

1. Identify the running immutable tag and SHA, and record both.
2. Create a fresh production backup from **Safety & recovery** (or the same offline backup engine) and record its backup ID/path.
3. Verify the backup completed with `integrity_check=ok` and zero `foreign_key_check` rows.
4. Confirm the target immutable tag and its exact commit SHA.
5. Preserve `/data/compose/22/data:/app/data`, `/data/compose/22/backups:/app/backups`, and the existing `APP_SECRET_KEY`. If the deployment instead uses `data/app_secret.key`, preserve it as private recovery material.
6. Use `production/docker-compose.portainer.yml`; retain the `deputy-roster-multi_default` network and Cloudflare routing. There must be no published host application port: Cloudflare reaches `deputy-roster-view:8000` privately.
7. Keep `SIGNUP_ENABLED=true`, `COOKIE_SECURE=true`, `TRUSTED_DEVICE_LIMIT`, and Deputy trial-write mode **OFF**.

## Deploy

Set the Portainer Git stack Reference to the approved immutable tag, then Pull and redeploy without changing existing deployment environment values or remounting persistent data. Do not enable Deputy writes during a normal deployment.

## After

1. Confirm the container stays running and reports the intended build/tag.
2. Run SQLite integrity/FK checks and record the deployed tag paired with the pre-deploy backup ID.
3. Smoke HTTPS login, month, a representative day, Settings, Admin, notifications, maps, and contractor flow when used.
4. Confirm static assets and responsive views at 320px and 375px.
5. Confirm Deputy write mode remains **OFF** and no background Deputy mutation occurred.

## Recovery and rollback

**Code rollback** means selecting the previous immutable Git tag in Portainer and redeploying that code. Changing a displayed version string never rolls code back.

**Database rollback** is separate and exceptional. Stop the stack/app, validate the specifically paired backup with:

```powershell
python scripts/restore_backup.py --backup /app/backups/<backup-id> --dry-run
```

Only after review, with the app stopped, run:

```powershell
python scripts/restore_backup.py --backup /app/backups/<backup-id> --app-stopped --confirm RESTORE
```

The restore tool validates manifest/SHA/SQLite, creates an emergency copy of the current DB, restores the database and backed-up persistent files, then rechecks integrity/FKs. Start the app only after it succeeds, then repeat authentication/version/HTTPS smoke checks. `APP_SECRET_KEY` supplied by the deployment environment must be preserved separately; it is never written into a backup manifest or Git.
