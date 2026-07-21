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

Successful direct schedule-search requests record their authoritative date/location coverage in the saved capture. `save_deputy_web_schedule()` upserts returned rows and removes older rows that are missing only inside those complete coverage windows. Browser/page captures, failed searches, and responses without explicit coverage remain additive and cannot delete crew data.

If the broad location search misses a user's own roster area, the capture follows up with a targeted roster-area search. It includes the user's own area IDs and, when possible, sibling areas for the same missed Deputy location. This helps Harness/other sparse areas resolve without scanning unrelated locations.

Schedule display is scoped by both date and Deputy location ID. This keeps split-crew days clean when two meetings or work groups happen at once.

The effective schedule interpretation pass also resolves context-dependent area labels before building the crew table. In particular, an `SVT` row becomes `Sound` only when a different employee has an overlapping `VT` row for that same date and Deputy location. The same context is then applied to the signed-in user's own shift labels so month, day, and crew views agree.

### Crew/Location Groundwork

All users currently belong to one shared crew pool, `Northern Crew`. When a user's rostered shift syncs with a usable location, the app records that location in `crew_known_locations` for the shared crew. This does not filter open shifts or change the UI yet; it only leaves a clean data shape for future location, crew, or region tagging.

`crew_people` and `crew_aliases` form the canonical identity directory. It is refreshed from Deputy employee rows, registered users, roster-builder assignments, and published snapshots. Deputy employee ID is the strongest key; name-only observations are merged only on a unique normalized full-name match. Aliases belong to one person and are rejected when they would be ambiguous across active people. Schedule assignments remain authoritative; directory aliases only fill missing note/vehicle context and canonicalize display names.

### Love Racing Planning Calendar

`app/love_racing.py` first reads Love Racing's public calendar endpoint. If that endpoint is blocked, it falls back to NZTR's official final racing-calendar PDF and extracts the positioned thoroughbred meeting rows. Only racecourses already known from collected roster/location data are retained. Saved rows live in `love_racing_meetings` and are rendered as planning hints on `/month`.

`app/planning_calendar.py` is the shared refresh service used by the Admin action and the Monday 04:30 scheduler job. The PDF is parsed from memory. A successful refresh atomically replaces the previous planning snapshot and removes meetings no longer published.

These rows are intentionally not shifts. They have no crew, start time, or hours, and the month view suppresses a planning hint when the signed-in user's Deputy roster already has a shift for that same date/location. Deputy data always takes priority.

Admins can include or ignore individual saved planning locations. The preference lives in `planning_location_preferences` and filters only Love Racing planning hints and counts; it never removes or changes Deputy roster data. Ignored public rows remain in the current planning snapshot so they can be restored immediately.

### Love Racing Track Maps

`app/track_maps.py` maintains a verified catalog of official 2D map images and optional admin-uploaded overrides. A monthly scheduler job checks catalog courses already known from roster data and retains the automatic file even when a manual image is active. Manual JPEG, PNG, or WebP files are stored separately in `data/track_maps`; day views prefer them until an admin resets the track to automatic. `/track-map/{track_key}` serves the effective local file, while the admin-only automatic download route always serves the untouched acquired image.

Discovery considers the track image's `src`, `srcset`, `data-src`, `data-original`, parent link, Open Graph image, and verified catalog fallback. Love Racing's `Common/Image.ashx` proxy is converted to its direct `OnHorseFiles` source. Candidates must be supported images, have sensible decoded dimensions, and match the expected course; the largest valid official candidate wins. Width, height, byte size, candidate count, selected source URL, and refresh result are stored. A failed or lower-quality replacement never removes a working cache.

### Travel Routes And Holidays

`travel_time_defaults` remains the learning/compatibility layer. `travel_routes` stores the directed origin/destination matrix. The migration copies each legacy base-to-track default into an outbound and reverse row marked as sharing the same legacy value. Later edits can make either direction explicit.

Race-day calculation resolves start origin and finish destination separately using day-specific/published selections, user hotel assignments, parsed accommodation notes, adjacent overnight travel context, then saved routes. Return travel is never copied from an unrelated outbound leg. Missing return data produces a partial calculation and warning rather than a false finish.

`app/public_holidays.py` calculates national holidays locally, including observed-day, Easter, and legislated Matariki rules. `NZ_HOLIDAY_REGION` optionally enables supported regional anniversary rules. Templates receive one date-level holiday object and use the shared accessible marker macro.

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
- `/admin/roster-days/new` and `/admin/roster-days/{id}`: admin-only race-day draft builder and publish review.

## Local State

User notes and timing overrides live in `shift_marks` and must survive every sync. Sync code should not overwrite marks.

Themes are stored per user in `app_users.display_theme`. The CSS theme system is variable-driven so open shift badges, notice banners, assigned shifts, and special location-colour accents remain readable without changing roster logic.

Personal roster reads and shift actions are scoped by `owner_user_id` for the signed-in account. Shared Deputy schedule rows can still be displayed as crew context for the same date/location, but they are not treated as the user's own shifts.

Deputy login secrets are encrypted in `deputy_user_secrets`. The app secret comes from `APP_SECRET_KEY` or generated `data/app_secret.key`; losing/changing it means stored Deputy passwords cannot be decrypted.

Error reports live in `error_reports`. They include the user's note, page/user-agent context, recent sync state, recent source payload diagnostics, and the latest redacted Deputy web capture snapshot.

Admin user diagnostics are loaded from an authenticated text endpoint only when requested. The main Admin response keeps capture summaries lightweight instead of embedding every user's full raw capture.

Per-user Deputy web diagnostics live in `deputy_web_captures`. Each capture stores a redacted payload, status, and message for the account that ran the sync, so an admin can inspect failed login/page-shape cases even after another user syncs.

Admins should prefer deactivating a user over hard deletion when someone leaves. Deactivation revokes trusted devices and stops future syncs while leaving audit history intact. Roster reset is user-scoped and clears local pulled shifts, marks, and change history so the next sync can rebuild the user's roster copy.

Deactivated accounts and revoked trusted devices are purged after 30 days. Users can deactivate themselves from Settings; admins can deactivate/reactivate users and can manually run the cleanup or purge an already deactivated user. Active users are not purged by this cleanup.

Track travel defaults live in `travel_time_defaults`. Admin-entered defaults are `manual`; learned defaults are inferred from previous saved roster notes that had both base and on-track times. An explicit preceding `Travel then Overnighter` shift can also teach the office-to-track journey for the next day's race location. `Office` and `Clow Place` are stored as one canonical base, while named hotels remain separate bases. Race-day maths uses these only when a note is missing either base or on-track timing.

Directed copies live in `travel_routes`. A race day can therefore use `Beachfront Motel -> Ruakaka` for its morning leg and `Ruakaka -> Office / Clow Place` for its return. Published roster days may save explicit `start_origin` and `finish_destination` values without changing the user's account schema.

Learning collapses duplicate user copies into one sample per track/date. Generic schedule context is excluded, and the legacy `G Cambridge` label is canonicalized to `Cambridge Greyhound` without merging it into the logically separate Harness location.

The Admin Locations section joins planning-location visibility and travel defaults for display, but their effects remain separate: Active only controls Love Racing planning hints, while travel rows supply timing fallbacks. Deputy data is unaffected by either control.

Manual roster test data lives in `roster_days`, `roster_day_assignments`, and append-only `roster_day_versions`. Editing updates a private draft. Publishing stores a complete JSON snapshot and version so crew keep seeing the previous published state until the admin explicitly publishes again. Published assignments appear only for the assigned user on month and day views; Deputy data remains visible alongside them during the trial.

`roster_days.day_type` distinguishes normal race days from occasional travel days. Structured per-user hotel allocations are stored with the draft snapshot so split-hotel crews see only their own accommodation on published views.

Settings roster insights use completed roster days only, excluding today and future rows. Adjacent rows are combined with the same rules as the day view. The recent-days audit list exposes the exact rows feeding totals so a misleading weekday or hours figure can be traced directly.

## Change Detection

Own shift changes are stored in `shift_changes`. Schedule row changes are summarized on `deputy_schedule_shifts.change_summary`.

Durable connected crew changes are stored in `deputy_schedule_event_changes`. A successful authoritative schedule window captures the effective crew snapshot before updating/pruning rows, rebuilds the effective snapshot afterwards, and compares assignments by date, Deputy location, and overlapping event period. The resulting grouped records describe replacements, moves, Sound/VT merges/splits, and open/filled positions without depending on stable Deputy shift IDs. Existing `deputy_schedule_assignment_history` rows remain available for older changes.

Crew visible change badges should only appear for assignment changes:

- person changed
- position/area changed
- open shift status changed

Day-view schedule reconciliation also suppresses an older overlapping production role for the same employee when a newer capture supplies a different role. Same-capture dual roles remain visible rather than being guessed away.

Empty `RTS` and `FM` areas are not emitted as inferred `TBC` rows. Assigned rows still display normally.

Timing-only crew schedule changes should not badge every crew row.

## Roster Integrity

Personal roster rows are also stored as durable `deputy_personal_assignment_evidence`. Effective crew display uses named shared-schedule rows first, matching confirmed personal evidence second, and TBC placeholders last. Matching uses Deputy employee identity where available and the canonical crew directory only as a safe fallback. A disagreement is retained as two-source evidence and shown as a conflict; neither source silently replaces the other.

`deputy_personal_capture_coverage` records each weekly own-roster request. One absence from a complete request marks a future shift possibly missing; two independent complete absences may retire it. Failed, partial, and truncated requests do not advance that count. Explicit Deputy cancellation is immediate.

`deputy_event_coverage` records event-level completeness. Upcoming events are checked against known production areas, the prior effective snapshot, and registered users' personal evidence. Missing evidence triggers an exact-date selected-location retry. Partial event captures never prune prior valid shared rows.

Completed shared events are recorded in `deputy_event_locks` after the latest known finish plus six hours, with an early-following-morning fallback when no finish is known. Personal shifts receive `historical_locked_at`. Locked snapshots cannot be pruned or have nonblank operational facts replaced; late conflicts go to `deputy_historical_discrepancies`. A one-time additive replay can restore missing completed rows from retained successful `deputy_web_captures`, with counts in `historical_recovery_runs`.

`shift_changes.change_category` and `user_visible` separate operational alerts from enrichment, normalization, derived values, parser reinterpretation, and historical discrepancies. Normal day history reads only user-visible records. Technical records remain in SQLite for diagnosis.
