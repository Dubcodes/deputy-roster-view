# Deputy Roster View

Deputy Roster View is a small private web app that mirrors Deputy roster data into SQLite and shows it as a cleaner month calendar with shift details, crew assignments, local notes, timing adjustments, and sync history.

The app is read-only against Deputy. It never writes back to Deputy.

## Setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Optional: open `.env` and set `DEPUTY_ICAL_URL` to your Deputy calendar/iCal subscription URL, or paste the URL into Settings after the app starts.

   The iCal URL is now a backup feed. Keep it private. It can grant access to a roster feed. Do not commit `.env`, paste it into logs, or share screenshots that reveal it.

3. Optional: set a stable `APP_SECRET_KEY`.

   ```env
   APP_SECRET_KEY=make-this-long-random-and-private
   ```

   If this is left blank, the app creates `data/app_secret.key` on first run. Keep either the env key or that generated file safe because it is used to encrypt stored Deputy login secrets.

4. The first browser to open the app will be sent to `/signup`. The first signed-up user becomes admin. Each user enters their Deputy email, Deputy password, and a local PIN. PINs are hashed, Deputy passwords are encrypted, and the device receives a long-lived trusted-device cookie.

   Useful env values:

   ```env
   TRUSTED_DEVICE_DAYS=730
   SIGNUP_ENABLED=true
   COOKIE_SECURE=false
   ```

5. Optional: set Deputy web env values if you want a server-level fallback account. Normal multi-user sync uses the encrypted credentials entered at `/signup`. Do not put secrets in Git.

   ```env
   DEPUTY_WEB_URL=https://your-business.au.deputy.com/#/
   DEPUTY_LOGIN_EMAIL=you@example.com
   DEPUTY_LOGIN_PASSWORD=your-deputy-password
   DEPUTY_DISPLAY_NAME=Your Name
   DEPUTY_API_TOKEN=your-deputy-api-token
   ```

   The app does not require a Deputy API token. If an API token is present, the settings page can test it, but the main path uses logged-in Deputy web capture.

5. Optional: set `APP_PORT` if port `8096` conflicts with another service.

   ```env
   APP_PORT=8123
   ```

## Getting The Deputy Calendar URL

In Deputy, look for the calendar subscription/export option for your roster. Copy the iCal/calendar feed URL and paste it into the app's Settings page or place it only in `.env` as `DEPUTY_ICAL_URL`.

If the URL has previously been pasted into chat, logs, or another shared place, regenerate or reset it in Deputy if Deputy provides that option.

## Run With Docker Compose

```bash
docker compose up --build
```

Open:

```text
http://localhost:8096
```

On another machine, use:

```text
http://SERVER-IP:8096
```

If you changed `APP_PORT`, use that port instead.

## Portainer

In Portainer, create a stack from this repository. Set `APP_PORT` to whichever host port you want exposed. You can provide `DEPUTY_ICAL_URL` as an environment variable, or leave it blank and paste the URL into Settings once the app is running. The app stores its SQLite database, generated app secret, and local data in the bind-mounted `./data` directory.

For the new multi-user flow, open the app and complete `/signup`. For temporary testing through trycloudflared, keep `COOKIE_SECURE=false`. If you move to a permanent HTTPS-only domain later, set `COOKIE_SECURE=true`.

If using the older Deputy web diagnostics fallback, set `DEPUTY_LOGIN_EMAIL`, `DEPUTY_LOGIN_PASSWORD`, and `DEPUTY_DISPLAY_NAME` as Portainer environment variables. The app shows whether the login is configured, but never displays the password. Use Settings -> Capture Web Data to check whether the logged-in web app exposes richer roster data. Once configured, normal syncs also refresh this Deputy web crew data.

If you do have a Deputy API token, set `DEPUTY_API_TOKEN` and use Settings -> Test Deputy API. Most normal users will not have this.

## Temporary Trycloudflared URL

For temporary testing, enable the built-in tunnel profile:

```env
COMPOSE_PROFILES=tunnel
```

Redeploy the stack, then open the `cloudflared` container logs and copy the `trycloudflare.com` URL.

If your Portainer screen supports additional compose files, you can also deploy the normal compose file plus the tunnel overlay:

```text
docker-compose.yml
docker-compose.tunnel.yml
```

In Portainer's Git stack screen, set `docker-compose.yml` as the main compose path and add `docker-compose.tunnel.yml` under Additional paths.

The tunnel points to the app over the internal Docker network at `http://deputy-roster-view:8000`. Do not put Deputy passwords or app secrets in the tunnel compose file.

## Syncing

- Daily sync runs at `SYNC_AT_HOUR`, default `5`, in `TZ`, default `Pacific/Auckland`.
- A pre-shift checker runs every 10 minutes and syncs once around `EARLY_PRE_SHIFT_SYNC_HOURS`, default `12`, before the next shift.
- It syncs again when the next shift is within `PRE_SHIFT_SYNC_MINUTES`, default `60`.
- If that upcoming shift is marked as changed, the checker runs one more follow-up sync at `CHANGED_FOLLOWUP_SYNC_MINUTES`, default `30`.
- For multi-user scheduled syncs, users are queued and staggered with `USER_SYNC_STAGGER_MINUTES`, default `7`, plus `USER_SYNC_JITTER_MINUTES`, default `2`.
- `USER_SYNC_BATCH_SIZE` defaults to `1`, so only one account is captured per runner pass.
- Manual Sync and Update uses the currently logged-in user's saved Deputy login immediately.
- iCal is optional backup. If no iCal URL is configured, the sync will skip that source and still use Deputy web capture.

The app redacts calendar details by design and does not display the configured calendar URL.

## Trusted Devices

`TRUSTED_DEVICE_DAYS` controls how long a phone/browser is trusted after activity. The default is `730`.

The app refreshes the trusted-device expiry on each authenticated request, so the timer effectively resets while the user keeps using the app. Admin revocation, logout, clearing browser cookies, changing the app secret, or browser cookie limits can still require login again.

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

The app displays whatever events Deputy puts in the iCal feed. If an open shift appears in that feed and later disappears, the app marks it as cancelled/removed after the next sync. Applying for available shifts is not supported unless a future Deputy API integration is added.

## Reset Local Database

Stop the container, then remove the SQLite database in `data/`:

```bash
docker compose down
rm data/deputy_roster.sqlite3
docker compose up --build
```

This removes synced shifts, local notes, timing adjustments, and sync history.
