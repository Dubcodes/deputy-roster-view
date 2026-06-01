# Deputy Roster View

Deputy Roster View is a small private web app that mirrors a Deputy iCal roster feed into SQLite and shows it as a cleaner month calendar with shift details, calculated paid hours, local notes, timing adjustments, and sync history.

The app is read-only against Deputy. It never writes back to Deputy.

## Setup

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Either open `.env` and set `DEPUTY_ICAL_URL` to your Deputy calendar/iCal subscription URL, or paste the URL into Settings after the app starts.

   Keep this URL private. It can grant access to your roster feed. Do not commit `.env`, paste it into logs, or share screenshots that reveal it. If you expose the app through a tunnel, set `APP_PASSWORD`.

3. Optional: set `APP_PASSWORD` if you want HTTP Basic password protection.

   ```env
   APP_PASSWORD=your-private-password
   ```

4. Optional: set `APP_PORT` if port `8096` conflicts with another service.

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

In Portainer, create a stack from this repository. Set `APP_PORT` to whichever host port you want exposed. You can provide `DEPUTY_ICAL_URL` as an environment variable, or leave it blank and paste the URL into Settings once the app is running. The app stores its SQLite database in the bind-mounted `./data` directory.

## Syncing

- Daily sync runs at `SYNC_AT_HOUR`, default `5`, in `TZ`, default `Pacific/Auckland`.
- A pre-shift checker runs every 10 minutes and syncs once when the next shift is within `PRE_SHIFT_SYNC_MINUTES`, default `60`.
- If that upcoming shift is marked as changed, the checker runs one more follow-up sync at `CHANGED_FOLLOWUP_SYNC_MINUTES`, default `30`.
- Use the Sync Now button in the app to trigger a manual sync.

The app redacts calendar details by design and does not display the configured calendar URL.

## Navigation

On the month page, swipe left or right on a phone to move between months. On desktop, use `M` for month view, `L` for list view, `N` for next month, and `P` for previous month.

## Hours

Raw hours are calculated from `end_at - start_at`, including overnight shifts when the feed supplies the next-day end time.

Unpaid break minutes are read from Deputy's iCal event description when Deputy includes a line such as `Meal Break (Unpaid): 30 mins`. If no break line is present, the app stores `0` break minutes.

```text
paid_hours = raw_hours - break_minutes / 60
```

## Local Notes

Notes and timing adjustments are stored locally in SQLite and are never overwritten by Deputy syncs. Deputy/iCal updates only change the source roster fields.

The iCal feed only contains rostered shifts. Available/open shifts and applying for them are not included unless a future Deputy API integration is added.

## Reset Local Database

Stop the container, then remove the SQLite database in `data/`:

```bash
docker compose down
rm data/deputy_roster.sqlite3
docker compose up --build
```

This removes synced shifts, local notes, timing adjustments, and sync history.
