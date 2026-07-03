# Decisions

## Keep The Stack Simple

Use FastAPI, Jinja, SQLite, APScheduler, requests, icalendar, and Playwright only where needed for Deputy web capture. Avoid a heavy frontend framework.

## iCal Is A Backup Roster Source

iCal is stable enough for backup roster data and does not need Deputy API access, but multi-user signup is based on Deputy login credentials. Each user can store their own encrypted iCal URL. A missing iCal URL should not fail a web-capture sync.

Deputy web capture runs first. iCal then fills missing shifts for the same account and skips events that already have a matching web-captured Deputy shift. If an iCal-only row later appears in web capture, the web row adopts that existing row so local notes do not split across duplicates.

## Deputy Web Capture Adds Crew Context

The user does not have an official API token. The app uses logged-in web capture to read the same schedule data Deputy shows in the browser. This remains read-only.

## Personal Roster Capture Uses Weekly Windows

Deputy's logged-in shift endpoint can return only the first page/chunk when asked for one very wide date range. The app still keeps the configurable lookback/lookahead window, but fetches the user's own roster in weekly slices and merges the rows locally so future roster weeks are not silently missed.

Defaults should stay modest for multi-user use: 35 days back and 56 days forward for each user's own roster. The direct shared schedule search is capped separately so the app can cover upcoming crew context without scraping months of unnecessary data.

## Location IDs Are Learned From Deputy

The personal shift endpoint can return row-level `location` and `locationName` fields, so those values are treated as the source of truth for a user's own shifts. The app also stores Deputy's schedule filter/location lists and reuses them during import. Dynamic Deputy names win over old hard-coded fallbacks because the fallback IDs can be incomplete or stale.

If Deputy sends a bare `Web / Shift` row, display code may apply a narrow known-event fallback from user-provided legacy roster data. These fallbacks should be date/time scoped and only fill details Deputy omitted; they must not override richer Deputy data.

## Schedule Coverage Uses Batched Searches

Deputy's schedule UI can fail to expose All Locations in a headless capture, especially for non-admin users. After login, the app now queries upcoming schedule data in weekly windows using Deputy's all-location search mode, with selected known racing locations as a fallback. This is still read-only and staggered per user, but it gives the shared crew database better coverage than clicking track filters one by one.

The direct search tries Deputy's all-location schedule mode first because it covers the same shared crew rows with fewer requests. If that fails, it falls back to selected known racing location IDs. Static IDs are only a safety net for display/import gaps and should be corrected whenever live Deputy row metadata disagrees with them.

Deputy rejects the tempting `areaIds` schedule-search shape with invalid-format errors, so the app does not use targeted area-ID searches. Area overrides are allowed only for confirmed Deputy IDs, such as the H-Cambridge position and vehicle areas seen in user captures, and should be treated as display/import fallbacks rather than the primary data source. Overrides also relabel existing saved rows at display time, so old `Web / Shift` rows can improve without waiting for Deputy to resend every field.

## Stagger User Syncs

Multiple users should not all hit Deputy at 5am or at the same pre-shift window. The scheduler plans per-user sync windows with configurable spacing and small deterministic jitter, then runs due accounts in a small batch, default one account at a time.

## Trusted Devices Are Long-Lived And Sliding

The user wants phone access without repeated logins. Trusted-device tokens are stored hashed in the database and refreshed on each authenticated request. `TRUSTED_DEVICE_DAYS` is the per-activity expiry window, while admin revocation and logout still end access.

Admins can revoke a trusted device, reset a user's PIN, and clear changed flags after broad parser/display tuning creates noisy change badges.

Theme selection is stored per app user so different people can keep their own colour palette across trusted devices.

## Change Visibility

The UI should highlight changes, but avoid noisy false positives:

- Own shift timing/note/title changes should flag the shift as changed.
- Crew row badges should only show assignment changes, not timing-only changes.

## Raw Data Belongs In Diagnostics

Raw iCal and Deputy web capture data are useful for debugging, but too noisy for the main phone UI. Keep them collapsed in settings with copy buttons.

User-submitted error reports should save a redacted diagnostic snapshot so the admin can compare what the app saw with what Deputy showed at the time.

Full per-user capture text is fetched only after an admin asks to load it. This keeps the Admin page responsive as users and captured schedule rows grow.

## Open Shifts

Open/available shifts are detected from saved Deputy schedule rows. A visible marker should open the day details instead of starting a sync, because an accidental tap on the month view should not trigger a long Deputy capture.

Applying for shifts is not implemented.

## Race-Day UI

The day page should keep Race Day close to the Deputy roster-note wording, not split it into lots of boxes. Show a short two-column list, such as:

- `Trucks` / `08:15`
- `Clow Place` / `08:30`
- `On track` / `08:45`
- `Records` / `10:30`
- `Live`, `First cross`, or `FX` / `11:00`
- `10 races` / `11:10 | 16:24`

Free-text roster note remains available behind Raw roster note.

## Default Travel Times

Some Deputy notes omit base or on-track timing. The app stores default travel times per track/base so maths can still show a useful breakdown. Manual admin defaults win over learned defaults. Learned defaults are derived from previous roster notes only when both base and on-track times were present.

Office and Clow Place are the same physical base and are canonicalized to one `Office / Clow Place` row. Named hotels remain separate bases so an out-of-region track can have several hotel-to-track journeys without guessing which hotel applies.

An explicit `Travel then Overnighter` shift on the day before a race may teach the office-to-track journey for that next-day location. It does not teach hotel-to-track travel; that requires a named hotel and a real known duration.

The same roster note can be captured under several users. Travel learning counts that as one track/date sample so crew size does not bias the chosen default. Generic contractor/vehicle context is excluded. `G Cambridge` is treated as an alias of `Cambridge Greyhound`, while Cambridge Harness stays separate.

Planning visibility and travel defaults share one Admin Locations list for clarity. The Active switch still affects only public planning hints, and deleting a travel row does not delete a public calendar location.

## Public Racing Calendars

Love Racing's public RaceInfo/calendar pages expose meeting date, club/meeting, and racecourse information in the page content, but not dependable Trackside start/finish/crew data. The app stores matching future rows in `love_racing_meetings` only for locations already known from collected roster data.

Planning-location visibility is an Admin preference layered over the saved public snapshot. Ignoring a location hides its planning markers and summary counts without deleting the source rows or affecting Deputy. This keeps the action reversible and avoids another network scan when a location is included again.

Love Racing entries are planning hints, not shifts. They render with a Love Racing gold/location-colour gradient and are suppressed when confirmed Deputy data already exists for that user/date/location. This keeps the calendar useful without confusing public race meetings with rostered work.

Selecting a planning marker stays inside the app and opens that date's day view. The detail block shows only saved calendar facts and the Love Racing source; it has no external link and does not infer crew, positions, or timing.

The live Love Racing calendar endpoint may return HTTP 403 to server-side requests. Refresh therefore falls back to NZTR's official final calendar PDF, using its positioned weekly columns and thoroughbred club codes. Static aliases may identify a known location, but must not introduce locations the collected roster data has never seen. Refresh runs weekly and replaces the prior snapshot so schedule corrections remove stale markers without retaining downloaded files.

## Manual Roster Publishing Trial

The replacement-roster trial uses an explicit draft, review, publish flow. Saving never changes what crew see. The builder compares the draft with the last published snapshot and highlights timing, notes, person, position, and vehicle changes before an admin publishes a new version.

Known Deputy areas seed the position list, active app users seed the crew list, and known vehicle areas seed vehicles. Office-to-track defaults may suggest arrival time but never lock it. Greyhound remains selectable while it is still operational; the data model does not hard-code the four current race-day types as permanent database columns.

Future personal calendar feeds should use a separate random revocable token per user because calendar clients cannot complete the normal PIN/cookie login. Store only a token hash. The feed must include only that user's published assignments, use a stable UID per roster day/user, and increment `SEQUENCE` when a roster version is published. Initially expose office start, track, position, vehicle, and essential notes; do not invent an end time when it is unknown.

Spreadsheet paste must populate a draft preview and must never publish directly. Build the column adapter only after a real spreadsheet sample is available.
