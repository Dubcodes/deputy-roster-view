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

## Location IDs Are Learned From Deputy

The personal shift endpoint often returns only area/location IDs, so the app stores Deputy's schedule filter location list and reuses it during import. Dynamic Deputy names win over old hard-coded fallbacks because the fallback IDs can be incomplete or stale.

## Schedule Coverage Uses Batched Searches

Deputy's schedule UI can fail to expose All Locations in a headless capture, especially for non-admin users. After login, the app now uses the learned location list to query upcoming racing schedules in weekly batches. This is still read-only and staggered per user, but it gives the shared crew database better coverage than clicking track filters one by one.

Deputy rejects the tempting `areaIds` schedule-search shape with invalid-format errors, so the app does not use targeted area-ID searches. Area overrides are allowed only for confirmed Deputy IDs, such as Joshua's H-Cambridge Side 1 area, and should be treated as display/import fallbacks rather than the primary data source. Overrides also relabel existing saved rows at display time, so old `Web / Shift` rows can improve without waiting for Deputy to resend every field.

## Stagger User Syncs

Multiple users should not all hit Deputy at 5am or at the same pre-shift window. The scheduler plans per-user sync windows with configurable spacing and small deterministic jitter, then runs due accounts in a small batch, default one account at a time.

## Trusted Devices Are Long-Lived And Sliding

The user wants phone access without repeated logins. Trusted-device tokens are stored hashed in the database and refreshed on each authenticated request. `TRUSTED_DEVICE_DAYS` is the per-activity expiry window, while admin revocation and logout still end access.

## Change Visibility

The UI should highlight changes, but avoid noisy false positives:

- Own shift timing/note/title changes should flag the shift as changed.
- Crew row badges should only show assignment changes, not timing-only changes.

## Raw Data Belongs In Diagnostics

Raw iCal and Deputy web capture data are useful for debugging, but too noisy for the main phone UI. Keep them collapsed in settings with copy buttons.

## Open Shifts

Open/available shifts are detected from saved Deputy schedule rows. A visible marker can link through `/sync-now` first so the app refreshes before showing details.

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
