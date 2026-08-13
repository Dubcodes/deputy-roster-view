# Re-Deputy Deployment Checklist

Use this immediately before and after a production redeploy. Do not put secrets in this file or deployment logs.

## Before

- Confirm the intended Git commit and `APP_BUILD`.
- Stop the existing app and make one timestamped backup of its complete persistent data directory, or use SQLite's online backup API before copying other runtime files.
- Verify the backup includes the SQLite database, `app_secret.key` when used, `web_push_vapid_private.pem`, `track_maps/`, and every other runtime file in the data directory.
- **Preserve the existing `APP_SECRET_KEY`.** If the deployment uses `data/app_secret.key`, preserve that file instead. Never generate a replacement for an existing database.
- Preserve the current Portainer external port, network membership, persistent-volume mapping, Cloudflare/tunnel routing, and environment overrides.
- Confirm the persistent host directory still maps to `/app/data`; repository Compose expects `./data:/app/data` and the default database is `/app/data/deputy_roster.sqlite3` in-container.
- Keep `SIGNUP_ENABLED=false` unless public signup is deliberately required. First-user bootstrap remains available on an empty database.
- Choose `COOKIE_SECURE=true` for permanent HTTPS-only access. Direct HTTP LAN access requires `COOKIE_SECURE=false` and acceptance of the weaker transport protection.
- Confirm Deputy trial-write mode is OFF.

## Deploy

- Pull/build the intended commit without altering the existing deployment environment.
- Recreate the app container using the existing port, network, and volume mappings.
- Do not replace, clear, or remount the persistent data directory.
- Do not enable Deputy trial writes as part of a normal redeploy.

## After

- Confirm the container remains running without a restart loop and shows the intended build.
- Check login, month, a representative day, Settings, Admin, notification status, sync status, maps, and the contractor route when used.
- Check the manifest, service worker, favicon, and CSS load. An ordinary refresh should be sufficient; no service-worker unregister is expected.
- Check the UI at 320px and 375px widths.
- Confirm Deputy trial-write mode remains OFF and no background Deputy mutation occurred.

## Rollback

1. Stop the failed container.
2. Restore the previous known-good code/image.
3. Restore the timestamped persistent-data backup if database migration rollback is necessary; Git rollback alone does not reverse a migrated database.
4. Preserve the same `APP_SECRET_KEY` (or restored `app_secret.key`).
5. Restart and smoke login, month, representative day, and Settings.
