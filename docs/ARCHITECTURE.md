# Architecture

## Stack

- FastAPI app in `app/main.py`
- Jinja templates in `app/templates/`
- CSS in `app/static/style.css`
- SQLite database through `app/database.py`
- iCal sync through `app/sync_ics.py`
- Deputy web capture through `app/deputy_web.py`
- Background schedules through `app/scheduler.py`
- Trusted-device auth through `app/auth.py` and `app/security.py`
- Per-user Deputy credential conversion through `app/user_credentials.py`
- Docker Compose deployment through `docker-compose.yml`
- Optional temporary tunnel as a separate Portainer stack using `docker-compose.tunnel.yml`

## Data Sources

### Deputy iCal

`sync_ics.py` fetches the configured Deputy calendar feed and stores/updates rows in `shifts`. In the multi-user version each account can save an encrypted iCal URL in Settings. Web capture runs first; iCal then fills missing shifts and avoids duplicating matching web-captured Deputy shift IDs.

### Deputy Web Capture

`deputy_web.py` logs into the Deputy web app using either a user's encrypted saved credentials or server-level env fallback credentials. It captures relevant JSON responses and saves schedule rows into `deputy_schedule_shifts`. It also stores Deputy location names from the schedule filter response in `deputy_schedule_locations`, so own-roster rows with only area/location IDs can display the real track instead of falling back to `Web`. This is used for crew/position context, open shift counts, and richer roster data.

It should prefer an All Locations schedule capture. If that is not selectable, it falls back to upcoming known roster locations.

After login, it also asks Deputy's own web endpoint for the user's personal published shifts over a rolling window. Defaults are 35 days back and 56 days forward, configurable with `OWN_ROSTER_LOOKBACK_DAYS` and `OWN_ROSTER_LOOKAHEAD_DAYS`. The capture is split into weekly requests because Deputy can return only the first page/chunk when asked for one large date range. Row-level Deputy `location` and `locationName` values from this endpoint are treated as authoritative for the user's own shifts.

For crew coverage, the capture also learns Deputy's primary location list and then performs weekly all-location schedule-search requests. If that broad read fails, it falls back to batched selected-location searches for known racing locations. This avoids relying only on the visible roster page when All Locations cannot be selected and helps fill shared crew rows for other users. Direct shared schedule capture is capped at 42 days ahead by default to keep multi-user syncs polite.

If the broad location search misses a user's own roster area, the capture follows up with a targeted roster-area search. It includes the user's own area IDs and, when possible, sibling areas for the same missed Deputy location. This helps Harness/other sparse areas resolve without scanning unrelated locations.

Schedule display is scoped by both date and Deputy location ID. This keeps split-crew days clean when two meetings or work groups happen at once.

### Crew/Location Groundwork

All users currently belong to one shared crew pool, `Northern Crew`. When a user's rostered shift syncs with a usable location, the app records that location in `crew_known_locations` for the shared crew. This does not filter open shifts or change the UI yet; it only leaves a clean data shape for future location, crew, or region tagging.

### Love Racing Planning Calendar

`app/love_racing.py` first reads Love Racing's public calendar endpoint. If that endpoint is blocked, it falls back to NZTR's official final racing-calendar PDF and extracts the positioned thoroughbred meeting rows. Only racecourses already known from collected roster/location data are retained. Saved rows live in `love_racing_meetings` and are rendered as planning hints on `/month`.

`app/planning_calendar.py` is the shared refresh service used by the Admin action and the Monday 04:30 scheduler job. The PDF is parsed from memory. A successful refresh atomically replaces the previous planning snapshot and removes meetings no longer published.

These rows are intentionally not shifts. They have no crew, start time, or hours, and the month view suppresses a planning hint when the signed-in user's Deputy roster already has a shift for that same date/location. Deputy data always takes priority.

Admins can include or ignore individual saved planning locations. The preference lives in `planning_location_preferences` and filters only Love Racing planning hints and counts; it never removes or changes Deputy roster data. Ignored public rows remain in the current planning snapshot so they can be restored immediately.

### Multi-User Sync Queue

`user_sync_state` stores the next planned sync time, last result, and running flag for each active user with saved Deputy credentials.

`scheduler.py` does not launch every account at once. Daily and pre-shift triggers call `plan_staggered_user_syncs`, which spreads users by `USER_SYNC_STAGGER_MINUTES` plus small deterministic jitter. `run_due_user_syncs` wakes every five minutes and processes up to `USER_SYNC_BATCH_SIZE` due accounts, default one.

## Main Views

- `/month`: main landing calendar/list view.
- `/day/{yyyy-mm-dd}`: shift detail, race-day timings, Deputy crew schedule, change history, timing notes.
- `/settings`: sync control, roster snapshot, user PIN/Deputy login maintenance, diagnostics, maintenance.
- `/help`: user-facing explanation of screens, buttons, shortcuts, and admin contacts.
- `/sync-now`: starts a background sync for the signed-in account and redirects/polls.
- `/signup` and `/login`: one-time trusted-device flow.
- `/admin`: user/sync health, per-user Deputy capture diagnostics, trusted devices, PIN/Deputy login maintenance, per-user sync, deactivate/reactivate controls, roster reset, error reports, and manual override audit.

## Local State

User notes and timing overrides live in `shift_marks` and must survive every sync. Sync code should not overwrite marks.

Themes are stored per user in `app_users.display_theme`. The CSS theme system is variable-driven so open shift badges, notice banners, assigned shifts, and special location-colour accents remain readable without changing roster logic.

Personal roster reads and shift actions are scoped by `owner_user_id` for the signed-in account. Shared Deputy schedule rows can still be displayed as crew context for the same date/location, but they are not treated as the user's own shifts.

Deputy login secrets are encrypted in `deputy_user_secrets`. The app secret comes from `APP_SECRET_KEY` or generated `data/app_secret.key`; losing/changing it means stored Deputy passwords cannot be decrypted.

Error reports live in `error_reports`. They include the user's note, page/user-agent context, recent sync state, recent source payload diagnostics, and the latest redacted Deputy web capture snapshot.

Per-user Deputy web diagnostics live in `deputy_web_captures`. Each capture stores a redacted payload, status, and message for the account that ran the sync, so an admin can inspect failed login/page-shape cases even after another user syncs.

Admins should prefer deactivating a user over hard deletion when someone leaves. Deactivation revokes trusted devices and stops future syncs while leaving audit history intact. Roster reset is user-scoped and clears local pulled shifts, marks, and change history so the next sync can rebuild the user's roster copy.

Deactivated accounts and revoked trusted devices are purged after 30 days. Users can deactivate themselves from Settings; admins can deactivate/reactivate users and can manually run the cleanup or purge an already deactivated user. Active users are not purged by this cleanup.

Track travel defaults live in `travel_time_defaults`. Admin-entered defaults are `manual`; learned defaults are inferred from previous saved roster notes that had both base and on-track times. An explicit preceding `Travel then Overnighter` shift can also teach the office-to-track journey for the next day's race location. `Office` and `Clow Place` are stored as one canonical base, while named hotels remain separate bases. Race-day maths uses these only when a note is missing either base or on-track timing.

The Admin Locations section joins planning-location visibility and travel defaults for display, but their effects remain separate: Active only controls Love Racing planning hints, while travel rows supply timing fallbacks. Deputy data is unaffected by either control.

## Change Detection

Own shift changes are stored in `shift_changes`. Schedule row changes are summarized on `deputy_schedule_shifts.change_summary`.

Crew visible change badges should only appear for assignment changes:

- person changed
- position/area changed
- open shift status changed

Day-view schedule reconciliation also suppresses an older overlapping production role for the same employee when a newer capture supplies a different role. Same-capture dual roles remain visible rather than being guessed away.

Timing-only crew schedule changes should not badge every crew row.
