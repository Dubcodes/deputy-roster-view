# Re-Deputy Deployment Checklist

Use this immediately before and after a production redeploy. Do not put secrets in this file or deployment logs.

## Before

- Confirm the intended immutable Git tag (for this release, `v0.5.3`) and its commit SHA.
- Stop the existing app and make one timestamped backup of its complete persistent data directory, or use SQLite's online backup API before copying other runtime files.
- Verify the backup includes the SQLite database, `app_secret.key` when used, `web_push_vapid_private.pem`, `track_maps/`, and every other runtime file in the data directory.
- **Preserve the existing `APP_SECRET_KEY`.** If the deployment uses `data/app_secret.key`, preserve that file instead. Never generate a replacement for an existing database.
- Use `production/docker-compose.portainer.yml`; preserve `/data/compose/22/data:/app/data`, the `deputy-roster-multi_default` network, Cloudflare routing, and existing environment values.
- Confirm there is no published host application port. Cloudflare reaches `deputy-roster-view:8000` privately on the shared Docker network.
- Keep this installation's intentional `SIGNUP_ENABLED=true` and `COOKIE_SECURE=true` HTTPS-only settings.
- Confirm Deputy trial-write mode is OFF.

## Deploy

- Set the Portainer Git stack Reference to the approved immutable tag, then Pull and redeploy without altering the existing deployment environment.
- Do not replace, clear, or remount the persistent data directory.
- Do not enable Deputy trial writes as part of a normal redeploy.

## After

- Confirm the container remains running without a restart loop and shows the intended build.
- Check login, month, a representative day, Settings, Admin, notification status, sync status, maps, and the contractor route when used.
- Check the manifest, service worker, favicon, and CSS load. An ordinary refresh should be sufficient; no service-worker unregister is expected.
- Check the UI at 320px and 375px widths.
- Confirm Deputy trial-write mode remains OFF and no background Deputy mutation occurred.

## Release and rollback

Normal release: review code, run `python scripts/release_gate.py`, push the release commit, wait for exact-SHA CI success, and create immutable tag `v0.5.3`. In Portainer set the Git Reference to `v0.5.3`, then Pull and redeploy and smoke HTTPS, version, and database integrity.

Rollback: choose a previous approved immutable tag such as `v0.5.2`, change the Portainer Git Reference, Pull and redeploy, then verify HTTPS and the database. Restore the timestamped persistent-data backup only when a database migration rollback actually requires it; changing code cannot reverse migrated data.

Changing an environment/display version label alone never changes application code. The Git tag/reference selects the code.
