# Re-Deputy

Re-Deputy is a small private multi-user web app that mirrors Deputy roster data into SQLite and shows a clearer calendar plus local workday planning, contractor access, notifications, and narrowly gated Deputy roster-write tooling.

Normal roster sync, iCal, web capture, planning, travel, vehicles, Open/TBC positions, Making My Own Way and contractor workflows are read-only against Deputy. Optional per-user OAuth supports an explicit Admin-triggered controlled workflow for assigned production shifts; write mode is off by default. Re-Deputy Admin is not Deputy authority: every mutation uses the initiating Admin's own OAuth identity, requires that identity's current Deputy roster-management permission, and preserves preview, overlap, optimistic-drift, read-back and audit safeguards. There are no automatic/background writes and no browser-write fallback.

Unknown or ambiguous results are never blindly retried. Externally created matching rows may be adopted for comparison/update but do not thereby gain Re-Deputy deletion ownership. Travel, vehicles, Open/TBC, contractors and production-wide writes remain outside the controlled workflow.

## Setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Optional legacy fallback: open `.env` and set `DEPUTY_ICAL_URL` to a Deputy calendar/iCal subscription URL. For normal multi-user use, each person should paste their own iCal URL into Settings after signup.

   The iCal URL is a backup feed. Keep it private. It can grant access to a roster feed. Do not commit `.env`, paste it into logs, or share screenshots that reveal it.

3. Set and preserve a stable `APP_SECRET_KEY` for deployment.

   ```env
   APP_SECRET_KEY=make-this-long-random-and-private
   ```

   If this is left blank, the app creates `data/app_secret.key` on first run. Keep either the existing env key or that generated file unchanged across every redeploy: replacing it makes stored Deputy and OAuth credentials unreadable. Never generate a replacement key for an existing data volume.

4. The first browser to open the app will be sent to `/signup`. The first signed-up user becomes admin. Each user enters their Deputy email, Deputy password, and a local PIN. PINs are hashed, Deputy passwords are encrypted, and the device receives a long-lived trusted-device cookie. Signup queues that user's first roster sync automatically.

   Useful env values:

   ```env
   TRUSTED_DEVICE_DAYS=730
   TRUSTED_DEVICE_LIMIT=10
   SIGNUP_ENABLED=false
   COOKIE_SECURE=false
   TRUSTED_PROXY_SOURCES=deputy-roster-tunnel
   ```

5. Optional: set Deputy web env values if you want a server-level fallback account. It is unconfigured by default. Normal multi-user sync uses the encrypted per-user credentials entered at `/signup`, which continue to work without a global URL. Do not put secrets in Git.

   ```env
   DEPUTY_WEB_URL=https://your-business.au.deputy.com/#/
   DEPUTY_LOGIN_EMAIL=you@example.com
   DEPUTY_LOGIN_PASSWORD=your-deputy-password
   DEPUTY_DISPLAY_NAME=Your Name
   ```

   The app does not require a Deputy API token. If an API token is present, the settings page can test it, but the main path uses logged-in Deputy web capture.

6. Optional local-development only: set `APP_PORT` if port `8096` conflicts with another service.

   ```env
   APP_PORT=8123
   ```

## Getting The Deputy Calendar URL

In Deputy, look for the calendar subscription/export option for your roster. Copy the iCal/calendar feed URL and paste it into the app's Settings page. In multi-user mode this URL belongs to the signed-in account and is stored encrypted.

`DEPUTY_ICAL_URL` in `.env` is only a legacy/global fallback for older single-user installs.

If the URL has previously been pasted into chat, logs, or another shared place, regenerate or reset it in Deputy if Deputy provides that option.

## Run Locally With Docker Compose

```bash
docker compose -f docker-compose.dev.yml up --build
```

Open:

```text
http://localhost:8096
```

On another machine, use:

```text
http://SERVER-IP:8096
```

If you changed local-development `APP_PORT`, use that port instead. Do not use plain `docker compose up` for local development: the repository-root Compose file is the hardened production definition.

## Portainer

Portainer production consumes the root `docker-compose.yml` directly: Git repo → root Compose → Pull and redeploy. It intentionally publishes no host application port, binds `/data/compose/22/data:/app/data` and `/data/compose/22/backups:/app/backups`, and exposes port 8000 only on `deputy-roster-multi_default` for the separately running Cloudflare tunnel. Preserve existing Portainer environment values, but no new variable is required: the app uses either an externally supplied `APP_SECRET_KEY` or the stable `/app/data/app_secret.key` fallback. `docker-compose.dev.yml` is the separate published-port local-development option. Immutable tags remain release and rollback evidence.

This production definition deliberately defaults `SIGNUP_ENABLED=true` and `COOKIE_SECURE=true` for the current HTTPS-only installation. Change policy only through an explicit reviewed operator decision.

Normal production deployment uses the existing Git-backed stack, existing repository/reference, and root Compose: after `main` points to an exact-SHA green released commit, click Pull and redeploy. Do not edit the Git Reference for each routine release. Immutable tags remain release evidence and known rollback points; change the Reference only for an exceptional explicit rollback/pin, then Pull and redeploy. Editing a visible version/environment label does not select or change application code.

`TRUSTED_PROXY_SOURCES` is a comma-separated allowlist of proxy peer IPs, CIDRs, or controlled Docker DNS names. Only requests whose actual network peer matches this list may use `X-Forwarded-Proto`/`X-Forwarded-Host` to reconstruct the browser-visible origin. The shipped Compose default names the separate `deputy-roster-tunnel` service; if your tunnel service uses another name, set that exact Docker DNS name. Direct LAN clients are not proxy-trusted and continue to use their actual HTTP origin.

If using Deputy web sync, set `DEPUTY_LOGIN_EMAIL`, `DEPUTY_LOGIN_PASSWORD`, and `DEPUTY_DISPLAY_NAME` as Portainer environment variables. The app shows whether the login is configured, but never displays the password. Use Settings -> Sync my roster to refresh roster data; normal syncs also refresh available Deputy web crew data.

## Temporary Trycloudflared URL

For temporary testing, run `cloudflared` as a separate Portainer stack. This keeps app redeploys from recreating the tunnel container and changing the temporary URL.

Before a real HTTPS deployment, set `COOKIE_SECURE=true`; leaving it false permits the trusted-device cookie over HTTP. This installation deliberately keeps `SIGNUP_ENABLED=true`; do not silently change that policy during a release or rollback.

1. In Portainer, open the app container and copy its network name. It will usually look like `deputy-roster-multi_default`.
2. Create a second stack, for example `deputy-roster-tunnel`.
3. Use `docker-compose.tunnel.yml` from this repo, or paste this compose:

```yaml
services:
  deputy-roster-tunnel:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate --url ${TUNNEL_TARGET_URL:-http://deputy-roster-view:8000}
    restart: unless-stopped
    networks:
      - roster_net

networks:
  roster_net:
    external: true
    name: ${APP_NETWORK:-deputy-roster-multi_default}
```

4. In the tunnel stack env, set `APP_NETWORK` to the app stack network name.
5. If the default target does not resolve, set `TUNNEL_TARGET_URL` to the actual app container name, such as `http://deputy-roster-multi-deputy-roster-view-1:8000`.
6. Deploy the tunnel stack, then open the tunnel container logs and copy the `trycloudflare.com` URL.

This URL is still temporary. It should survive app redeploys, but Cloudflare may issue a new URL if the tunnel container is recreated or restarted.

For a stable public URL, create a named Cloudflare Tunnel in Cloudflare and run that as a separate tunnel stack instead. Do not put Deputy passwords or app secrets in the tunnel compose file.

## Syncing

- Daily sync runs at `SYNC_AT_HOUR`, default `5`, in `TZ`, default `Pacific/Auckland`.
- A pre-shift checker runs every 10 minutes and syncs once around `EARLY_PRE_SHIFT_SYNC_HOURS`, default `12`, before the next shift.
- It syncs again when the next shift is within `PRE_SHIFT_SYNC_MINUTES`, default `60`.
- If that upcoming shift is marked as changed, the checker runs one more follow-up sync at `CHANGED_FOLLOWUP_SYNC_MINUTES`, default `30`.
- For multi-user scheduled syncs, users are queued and staggered with `USER_SYNC_STAGGER_MINUTES`, default `7`, plus `USER_SYNC_JITTER_MINUTES`, default `2`.
- `USER_SYNC_BATCH_SIZE` defaults to `1`, so only one account is captured per runner pass.
- Deputy web capture asks for each user's own published shifts from `OWN_ROSTER_LOOKBACK_DAYS`, default `35`, days back through `OWN_ROSTER_LOOKAHEAD_DAYS`, default `56`, days forward.
- Manual Sync my roster uses the currently logged-in user's saved Deputy login immediately.
- iCal is optional backup. If the signed-in account has an iCal URL saved, sync uses it after Deputy web capture to fill missing shifts without duplicating web-captured shifts. If no iCal URL is configured, sync skips that source and still uses Deputy web capture.

The app redacts calendar details by design and does not display the configured calendar URL.

## Trusted Devices

`TRUSTED_DEVICE_DAYS` controls how long a device/browser is trusted after activity. The default is `730`.

`TRUSTED_DEVICE_LIMIT` controls the maximum active trusted devices per account. It accepts 1 through 100 and defaults safely to `10` when missing, blank, invalid, or outside that range. Signing in always succeeds when authentication is valid; the least recently used active device above the limit is revoked.

The app refreshes the trusted-device expiry on each authenticated request, so the timer effectively resets while the user keeps using the app. Admin revocation, logout, clearing browser cookies, changing the app secret, or browser cookie limits can still require login again.

Admins can open Settings -> Admin to revoke trusted devices, reset a user's PIN, and clear changed flags after parser/display tuning creates noisy badges.

## Navigation

On the month page, swipe left or right on a phone to move between months. On desktop, use `M` for month view, `L` for list view, `N` for next month, `P` for previous month, and `S` to sync.

## Hours

Raw hours are calculated from `end_at - start_at`, including overnight shifts when the feed supplies the next-day end time.

Unpaid break minutes are read from Deputy's iCal event description when Deputy includes a line such as `Meal Break (Unpaid): 30 mins`. If no break line is present, the app stores `0` break minutes.

```text
paid_hours = raw_hours - break_minutes / 60
```

## Local Notes

Notes and timing adjustments are stored locally in SQLite and are never overwritten by Deputy syncs. Deputy/iCal updates only change the source roster fields.

The app displays whatever events Deputy puts in the iCal feed. If an open shift appears in that feed and later disappears, the app marks it as cancelled/removed after the next sync. Open-position applications are local Re-Deputy workflow only and never apply for a Deputy shift.

## Unknown Deputy write outcomes

Never retry an unknown or ambiguous write blindly. Admin → Deputy API → Deputy writes exposes the operation ID, assignment key, roster ID when known, and the expected employee, Area, times, break and note. Inspect Deputy read-only and compare those exact fields. Zero or multiple matches remain unresolved; an exact match may only be linked as Re-Deputy-created when the original create was actually transmitted. Adopted pre-existing rosters never gain delete authority.

## Reset Local Database

Stop the container, then remove the SQLite database in `data/`:

```bash
docker compose -f docker-compose.dev.yml down
rm data/deputy_roster.sqlite3
docker compose -f docker-compose.dev.yml up --build
```

This removes synced shifts, local notes, timing adjustments, and sync history.
