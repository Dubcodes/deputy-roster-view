# Architecture

## Account identity and onboarding (0.5.1)

`app_users.deputy_email` remains the historically named Re-Deputy account/login email. Actual Deputy email and password are optional encrypted values in `deputy_user_secrets`; updating them does not change the Re-Deputy login. An account, canonical `crew_people` identity, and Deputy connection are independent. Ordinary invited management accounts may therefore have no crew link and no Deputy connection while retaining the existing Crew/global view.

`account_invitations` is separate from contractor invitations. It stores only a strong token hash, expires, revokes, and consumes invitations once. Activation creates an ordinary `account_type='user'` account without manufacturing a crew identity; Deputy credentials may be added during activation or later through the normal credential path. Normal UI uses the installation `DEPUTY_WEB_URL`, retaining a valid stored per-user URL as the first compatibility choice.

`TRUSTED_DEVICE_LIMIT` is the single device-cap policy for login-time enforcement and idempotent startup cleanup. It accepts 1 through 100, defaults safely to 10, and retains each account's most-recently-active valid devices without crossing user boundaries.

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

`crew_people` and `crew_aliases` form the canonical identity directory. It is refreshed from Deputy employee rows, roster-builder assignments, published snapshots, and explicit contractor creation—not merely from app account existence. `person_type` distinguishes employees from external contractors; contractors do not need or receive a Deputy employee ID. Deputy employee ID is the strongest employee key; name-only observations are merged only on a unique normalized full-name match. Aliases belong to one person and are rejected when they would be ambiguous across active people.

Travel participant display is a narrow exception to position reconciliation: for a matching Travel/Overnighter cohort, it unions shared schedule names with app-linked users whose authenticated personal Deputy evidence overlaps the same date, location, and cohort window. It does not inject users into production crews.

### Love Racing Planning Calendar

`app/love_racing.py` first reads Love Racing's public calendar endpoint. If that endpoint is blocked, it falls back to NZTR's official final racing-calendar PDF and extracts the positioned thoroughbred meeting rows. Only racecourses already known from collected roster/location data are retained. Saved rows live in `love_racing_meetings` and are rendered as planning hints on `/month`.

`app/planning_calendar.py` is the shared refresh service used by the Admin action and the Monday 04:30 scheduler job. The PDF is parsed from memory. A successful refresh atomically replaces the previous planning snapshot and removes meetings no longer published.

These rows are intentionally not shifts. They have no crew, start time, or hours, and the month view suppresses a planning hint when the signed-in user's Deputy roster already has a shift for that same date/location. Deputy data always takes priority.

Meeting discovery and race-programme details are separate. A browser pass resolves the official meeting ID and overview URL for each matched date/venue and stores that identity in `love_racing_meeting_details`. `app/love_racing_details.py` then parses only positive numbered `overview-info` race rows and their Scheduled Start cells. It ignores result elapsed times, race-zero placeholders, and expanded duplicate rows.

`love_racing_detail_jobs` is a global queue keyed by meeting ID, so several users rostered at one meeting create one fetch. Incomplete programmes are checked more often as race day approaches: roughly six-hourly inside 72 hours, two-hourly inside 24 hours, and hourly on race morning. Complete programmes stop frequent polling, receive one race-morning confirmation where applicable, and are retained after the meeting. HTTP/browser failures use bounded backoff.

Admin has two explicit verification paths. Meeting preview fetches and parses one official meeting page without saving it. Date-range unresolved refresh discovers exact date/venue identities and updates only the monotonic Love Racing cache for Thoroughbred days that still lack derived race fields. Neither path writes Deputy shifts or change history.

Cached race count, first race, and last race are merged independently and monotonically. A blank or results-layout page cannot erase confirmed scheduled values. Ordinary day, month, settings, and timesheet rendering never contacts Love Racing; they bulk-read cached rows by date and canonical venue.

Admins can include or ignore individual saved planning locations. The preference lives in `planning_location_preferences` and filters only Love Racing planning hints and counts; it never removes or changes Deputy roster data. Ignored public rows remain in the current planning snapshot so they can be restored immediately.

### Love Racing Track Maps

`app/track_maps.py` maintains a verified catalog of official 2D map images and optional admin-uploaded overrides. A monthly scheduler job checks catalog courses already known from roster data and retains the automatic file even when a manual image is active. Manual JPEG, PNG, or WebP files are stored separately in `data/track_maps`; day views prefer them until an admin resets the track to automatic. `/track-map/{track_key}` serves the effective local file, while the admin-only automatic download route always serves the untouched acquired image.

Raw crew locations are classified before image lookup. Built-in rules exclude operational contexts, consolidate trial labels onto their physical racecourse, and keep Harness/Greyhound venues available for manual images even when Love Racing has no automatic source. `track_map_location_rules` stores compact admin decisions for uncertain locations without changing historical roster labels. All day and Admin image lookups use the resulting canonical venue key.

Legacy manual uploads saved against an alias are adopted by the canonical venue when it has no manual image. An existing canonical override wins conflicts; the alias file remains on disk and `track_map_migration_warnings` records it for recovery instead of silently deleting it.

Discovery considers the track image's `src`, `srcset`, `data-src`, `data-original`, parent link, Open Graph image, and verified catalog fallback. Love Racing's `Common/Image.ashx` proxy is converted to its direct `OnHorseFiles` source. Candidates must be supported images, have sensible decoded dimensions, and match the expected course; the largest valid official candidate wins. Width, height, byte size, candidate count, selected source URL, and refresh result are stored. A failed or lower-quality replacement never removes a working cache.

### Travel Routes And Holidays

`travel_time_defaults` remains the learning/compatibility layer. `travel_routes` stores the directed origin/destination matrix. The migration copies each legacy base-to-track default into an outbound and reverse row marked as sharing the same legacy value. Later edits can make either direction explicit.

Race-day calculation resolves start origin and finish destination separately using day-specific/published selections, user hotel assignments, parsed accommodation notes, adjacent overnight travel context, then saved routes. Return travel is never copied from an unrelated outbound leg. Missing return data produces a partial calculation and warning rather than a false finish.

`app/public_holidays.py` calculates national holidays locally, including observed-day, Easter, and legislated Matariki rules. `NZ_HOLIDAY_REGION` optionally enables supported regional anniversary rules. Templates receive one date-level holiday object and use the shared accessible marker macro.

### Multi-User Sync Queue

`user_sync_state` stores the next planned sync time, last result, and running flag for each active user with saved Deputy credentials.

`scheduler.py` does not launch every account at once. Daily and pre-shift triggers call `plan_staggered_user_syncs`, which spreads users by `USER_SYNC_STAGGER_MINUTES` plus small deterministic jitter. `run_due_user_syncs` wakes every five minutes and processes up to `USER_SYNC_BATCH_SIZE` due accounts, default one.

After a successful roster sync, upcoming Thoroughbred date/location pairs inside 72 hours can enqueue stale meeting details. A separate scheduler job services the global Love Racing detail queue, so the user-facing roster sync response does not wait for browser captures.

## Main Views

- `/month`: main landing calendar/list view.
- `/day/{yyyy-mm-dd}`: shift detail, race-day timings, Deputy crew schedule, change history, timing notes.
- `/settings`: sync control, roster snapshot, user PIN/Deputy login maintenance, diagnostics, maintenance.
- `/help`: user-facing explanation of screens, buttons, shortcuts, and admin contacts.
- `/sync-now`: starts a background sync for the signed-in account and redirects/polls.
- `/signup` and `/login`: one-time trusted-device flow.
- `/admin`: user/sync health, per-user Deputy capture diagnostics, trusted devices, PIN/Deputy login maintenance, per-user sync, deactivate/reactivate controls, roster reset, error reports, and operational manual overrides with immutable audit history.
- `/admin/roster-days/new` and `/admin/roster-days/{id}`: admin-only race-day draft builder and publish review.

## Local State

User notes and timing overrides live in `shift_marks` and must survive every sync. Sync code should not overwrite marks.

Admin timing corrections live in versioned `admin_overrides` rows. `target_date + target_track_key + field_key` identifies the active correction; replacement supersedes the previous row and disabling retains it for audit. The view layer loads active rows by date range and applies them in the shared timing enrichment pass before user marks, Deputy note fields, Love Racing cache fields, and travel/default inference. No source snapshot or shift-change record is rewritten, and views recalculate on their next request without a sync or process restart.

Themes are stored per user in `app_users.display_theme`. The CSS theme system is variable-driven so open shift badges, notice banners, assigned shifts, and special location-colour accents remain readable without changing roster logic.

Personal roster reads and shift actions are scoped by `owner_user_id` for the signed-in account. Shared Deputy schedule rows can still be displayed as crew context for the same date/location, but they are not treated as the user's own shifts.

Deputy login secrets are encrypted in `deputy_user_secrets`. The app secret comes from `APP_SECRET_KEY` or generated `data/app_secret.key`; losing/changing it means stored Deputy passwords cannot be decrypted.

Error reports live in `error_reports`. They include the user's note, page/user-agent context, recent sync state, recent source payload diagnostics, and the latest redacted Deputy web capture snapshot.

Admin user diagnostics are loaded from an authenticated text endpoint only when requested. The main Admin response keeps capture summaries lightweight instead of embedding every user's full raw capture.

Per-user Deputy web diagnostics live in `deputy_web_captures`. Each capture stores a redacted payload, status, and message for the account that ran the sync, so an admin can inspect failed login/page-shape cases even after another user syncs.

Admins should prefer deactivating a user over hard deletion when someone leaves. Deactivation revokes trusted devices and stops future syncs while leaving audit history intact. Roster reset is user-scoped and clears local pulled shifts, marks, and change history so the next sync can rebuild the user's roster copy.

Deactivated accounts and revoked trusted devices are purged after 30 days. Users can deactivate themselves from Settings; admins can deactivate/reactivate users and can manually run the cleanup or purge an already deactivated user. Active users are not purged by this cleanup.

User purge is an immediate all-or-nothing SQLite transaction. Self-owned rows cascade or are explicitly removed, retained evidence detaches nullable actor/source references, and important cross-user/Deputy audit references block purge with a normal Admin notice. The complete foreign-key classification lives in `docs/USER_PURGE_POLICY.md`.

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

The day view groups those audit records by `group_id` for presentation. Canonical crew identities are applied before constructing the grouped text, so a move plus replacement plus opened position is shown once without discarding the underlying rows.

Roster-note timing extraction is line-oriented and passes every recognized clock through one validated token parser. `apply_timing_math` creates a `display_window` object containing the selected source, start, finish, duration, and range. Day, month/list, Next Up, and timesheet summaries consume that object instead of choosing fields independently.

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

Deputy vehicle evidence is a separate fact from the row's operational Unit. `interpreted_workdays` accepts an already-recorded normalized `vehicle_label` fact on a production row as defensive compatibility; a CCU1 row may therefore retain both `role_label=CCU1` and `vehicle_label=684`. It does not guess from arbitrary nested raw Deputy fields. The real property behind the unusual 28 August combined entry was not captured, so the scalar `vehicle: "684"` regression example is synthetic and does not establish Deputy's JSON schema. The normal primary representation remains separate compatible Travel/vehicle and production rows. The interpreter collects every explicit current vehicle fact for the same canonical person/cohort; a roster-note allocation wins; one current explicit value wins next; multiple distinct current values remain an explicit conflict; only then may preceding Travel evidence apply. Owner-scoped personal facts can fill a matching shared blank but never leak to another identity or overwrite a shared explicit disagreement.

Completed shared events are recorded in `deputy_event_locks` after the latest known finish plus six hours, with an early-following-morning fallback when no finish is known. Personal shifts receive `historical_locked_at`. Locked snapshots cannot be pruned or have nonblank operational facts replaced; late conflicts go to `deputy_historical_discrepancies`. A one-time additive replay can restore missing completed rows from retained successful `deputy_web_captures`, with counts in `historical_recovery_runs`.

`shift_changes.change_category` and `user_visible` separate operational alerts from enrichment, normalization, derived values, parser reinterpretation, and historical discrepancies. Normal day history reads only user-visible records. Technical records remain in SQLite for diagnosis.
## Manual Workday Builder

Admin workday drafts use `roster_days` for event metadata, `workday_assignments` for ordered person/role/transport rows, and `workday_role_catalogue` for reusable role choices. `roster_day_versions` remains the append-only publication boundary. Published views read the saved snapshot rather than the mutable draft.

The builder supports race, office, travel, training, and other work days. Race-only timing, Love Racing, and map behavior is guarded by `day_type == 'race_day'`. `published_rosters_by_date()` is the shared projection used by personal and global calendars, day pages, Next Up, and timesheets. It also normalizes historical snapshots that predate structured transport.

Legacy `roster_day_assignments` rows migrate once into `workday_assignments`, retaining their source row ID for idempotency. New saves never write the legacy table. Manual events retain their own ID and provenance and are never merged with Deputy rows merely because their date matches.

## Canonical Crew Identity

`app_users` owns authentication, credentials, trusted devices, sync state, and the account header name. `crew_people` owns the roster identity, Deputy employee ID, canonical crew name, aliases, and manual assignments. `app_user_deputy_identity` records the employee ID established by that account's authenticated personal capture; shared schedule captures are never ownership evidence.

An unambiguous employee-ID match may link the account directly or merge an account-only synthetic person into the Deputy-backed canonical person. Merges are transactional, audited in `crew_identity_merge_audit`, and retire the source through `merged_into_person_id` rather than deleting it. Runtime assignment reads follow that redirect, while immutable published JSON snapshots remain untouched.

`workday_user_visibility` materializes personal access for published manual workdays from canonical assignment people and their linked active app users. It is rebuilt after publication, identity repair, and later account linking. This lets existing workdays become visible without republishing or creating a content change.

## Local Transport Preferences

`user_event_transport_preferences` stores a signed-in user's reversible "Making my own way" choice against a stable Deputy shift or manual-workday ID and canonical crew identity. `user_event_transport_preference_audit` records each actual toggle. The read layer overlays that choice on the latest roster transport; it never edits Deputy shifts, manual roster snapshots, vehicle evidence, or Changed state. Turning it off therefore reveals the newest underlying transport assignment.

Historical `not_required` assignments remain readable, but the normal new-assignment picker no longer offers that state.

## Effective Personal Start

`app/workday_timing.py` is the shared source for a published manual assignment's effective personal start and hours. An explicit personal start wins; otherwise a race day's configured truck offset applies only to vehicles classified `is_truck`; all other assignments retain the event start. Tender and OB are seeded as the initial truck vehicles, and Admin may change the classification. The event time and Deputy data are never rewritten.

## Web Push

`app/notifications.py` uses the existing APScheduler process to generate, deduplicate, and deliver standards-based Web Push events. The app generates its P-256 VAPID identity once and stores the private key in the persistent data volume; each device supplies its validated same-origin HTTPS app origin when subscribing. Preferences belong to an app user; endpoint subscriptions belong to individual devices and may only be managed by their authenticated owner. Published manual versions, effective Deputy changes, reminders, eligible team-classified open positions, weekly digests, and persisted tests feed one global queue. Draft workdays and Love Racing planning rows do not notify.

`app/static/service-worker.js` handles push display and same-origin relative deep links only. It deliberately does not add an offline application cache.

Immediate notification tests first require an active subscription for the signed-in
account, so an unavailable device does not create an orphan queue row. The delivery
worker separately marks legacy/raced queued events with no devices as failed without
raising. Push identity and subscription ownership remain per-account.

## Deputy OAuth Read And Write Identity

Re-Deputy roles remain `user` and `admin`; application administration is separate
from Deputy authority. Every Deputy service accepts the authenticated Re-Deputy user
as an explicit input and resolves only that user's encrypted OAuth connection. There
is no cross-user, shared, Admin, or failed-connection credential fallback.

Connection presence, read readiness, and write readiness are separate states. Read
verification authenticates `/me`, binds both Deputy UserId and EmployeeId, and updates
the current permission snapshot without requiring roster-management permission or a
trial host. Write verification immediately repeats that read check, requires the
fresh `Can_Roster_Manage` capability, exact trial-host allow-list membership, and
trial mode. Trial mode defaults to off. The monitored write flow additionally keeps
operation ownership, prepared-operation identity and permission snapshots, duplicate
and Timesheet locks, explicit confirmation, and read-back/unknown-result handling.

Employee and OperationalUnit reference refreshes are read-only and independent: a
permission denial for one resource preserves the other resource's useful cache and
does not invalidate a correctly authenticated connection. Local mappings display
cached per-user references without write readiness and changes require the acting
Admin's own currently readable reference IDs.

## Page Timing Diagnostics

Month, day, Settings, and Admin responses include a `Server-Timing` header with the
total application processing duration. The metric contains no account, roster, URL,
or credential data and supports deployed response-time comparisons without adding
sensitive diagnostics to ordinary request logs.

## Workday Review Flow

The manual workday builder keeps its existing draft/version/publication boundary. Its compact editor supports searchable reusable roles, one-day custom roles, roleless attendees, open positions, and exceptional Deputy locations behind advanced controls. Saving redirects to a read-only review view; only an explicit publish action changes what crew can see. A saved Love Racing planning row may prefill a new private race-day draft, but remains planning evidence and never publishes automatically.

## Teams, Pickers, Vehicles, And Applications

`crew_teams` and `crew_person_teams` model reusable many-to-many operational teams. Northern Team is seeded as the default team, while membership remains empty until an admin classifies people. `location_team_mappings` may supply a team when a Love Racing planning row starts a draft. Existing workdays remain unclassified unless edited.

`crew_identity_search_terms` is rebuilt from canonical names, Deputy observations/history, aliases, linked account display names/email, and employee IDs. The Admin-only builder receives normalized safe search metadata, renders one canonical result per `crew_people.id`, and saves that ID. `crew_vehicles` provides stable vehicle IDs, aliases, ordering, optional team affinity, and discovered/admin provenance.

`workday_open_position_applications` stores local applications against stable assignment keys. Eligibility and overlap are checked server-side from the authenticated account's canonical person. Acceptance updates only the manual assignment, republishes the Re-Deputy snapshot/visibility, and writes local audit events. No application path calls Deputy capture or write APIs.
# Deputy OAuth, trial writes, and contractors (2026.08.13.1)

`app/deputy_integration.py` owns exact HTTPS tenant normalization, encrypted installation OAuth configuration, one-time user-bound OAuth state, per-user encrypted tokens/refresh, `/me` identity and permission revalidation, paged Employee/OperationalUnit reference caches, ID-based mappings, durable assignment-to-Roster links, prepared operation records, per-assignment active locks, read-back verification, unknown-result reconciliation, Timesheet locks, and sanitized audit summaries. Only `off` and `trial` write modes exist; trial uses exact configured tenant hosts.

The trial sync is deliberately separate from normal Re-Deputy publishing. It previews CREATE/UPDATE/UNCHANGED/LOCAL ONLY, requires an explicit monitored confirmation including the tenant, writes via v2 single-shift endpoints, and publishes verified IDs with legacy mode 4. Existing external/observed rosters are not adopted automatically. Deputy Open, confirmation mode 5, claims/offers, and Timesheets have no write path.

`app/contractors.py` manages 24-hour hash-only invites, one-time activation, PIN hashing, canonical-person linkage, restricted re-login, revocation, and 180-day inactivity deactivation. A new contractor identity and its initial invitation are inserted through one connection-scoped transaction; replacement invitations reuse the same validated insertion helper. Middleware and route checks constrain contractor sessions to My work, logout, and their own assigned-day personal time/self-travel operations.

## 0.5.4 Admin continuity and invitation handoff

Ordinary Admin POST forms keep their server-rendered redirect flow. `admin-context.js` stores open disclosure keys and scroll position in `sessionStorage` for at most two minutes, consumes the record once on `/admin`, and restores nested panels without creating an AJAX Admin application. Invitation creation also uses POST → 303 → GET, but its one-time plaintext token is carried only in a client fragment, removed immediately, and retained only in that Admin tab's `sessionStorage`. The database continues to store only the strong token hash. GET and HEAD activation views are read-only; successful activation, explicit revoke/reissue, or stored expiry is the only terminal transition.

Never-published workday deletion preflights every schema and logical relationship to `roster_days` under an immediate SQLite transaction. Draft-only assignments and audit children cascade with foreign keys enabled. Published snapshots/versions, Deputy links/write operations, applications, materialized visibility, personal overlays/audits, or notification history block deletion.

The authoritative experimental evidence is retained in `DEPUTY_API_LAB_REPORT.md` and `DEPUTY_API_LAB_ROUND2.md`.
# Deputy protocol release gate (2026.08.13.3)

Deputy browser/password capture remains the existing roster-source mechanism. Per-user OAuth is separate and supplies read/reference access plus an explicitly initiated, exact-tenant trial-write path. The general/multi-install OAuth flow always authorizes and exchanges the initial code at `once.deputy.com`; only the regional `*.{au,eu,uk,us}.deputy.com` endpoint returned by Deputy is stored. Refresh goes to that verified endpoint. The callback origin is Admin-configured and is never derived from the request Host header.

Single-shift writes use `/api/management/v2/shifts` with `data.shift` and `data.override`; all v2 responses pass through one `success/data` extractor and normalized shift model. Resource Roster queries use supported `s1/s2/...` filters, immutable ID sorting, 500-row pagination, and exact operational-date bounds. The legacy publish endpoint remains isolated to mode 4. OAuth is the only Deputy API authority; the retired global bearer-token probe has no production path.

Trial mode is off on fresh databases and has no production counterpart. The current write scope is assigned production shifts only. Travel, vehicle context, Open, TBC, and Making my own way stay local. Whole-workday preflight runs before mutation, and any unresolved mutation prevents publish.

## Timing semantics

Raw Deputy component rows retain their source start/end. The canonical rostered
human-workday span produced by `interpret_deputy_workdays()` drives reminders,
notification revision/dedupe, and the personal day header. Paid hours remain
break-adjusted timesheet presentation. Race-day calculations are operational
estimates and never replace rostered reminder timing. Personal start/finish
overrides are per-user display/timesheet data and never mutate Deputy evidence.
