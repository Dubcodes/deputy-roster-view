# Project Brief

## Name

Re-Deputy

## Purpose

Build a private roster viewer that pulls Deputy roster and schedule data for multiple trusted users, stores it locally, and presents the useful work information in a compact, device-friendly way.

The app exists because Deputy's roster view is hard to scan. The main questions the app should answer quickly are:

- Where am I working?
- What is my position?
- What time do I start?
- What are the important race-day timings?
- Who else is working and what are they assigned to?
- Has anything changed since I last checked?
- Are there available/open shifts?

## Current Scope

- Capture logged-in Deputy web schedule data for roster, crew position, open shift, and count context.
- Keep Deputy iCal as an optional backup roster feed.
- Store shifts, local notes, change history, schedule rows, users, trusted devices, sync state, and sync logs in SQLite.
- Show month calendar, list view, day details, settings, help, diagnostics, manual sync, and timesheet summaries.
- Support one-time signup with encrypted Deputy credentials and long-lived trusted devices.
- Let admins revoke trusted devices, update Deputy login details, reset PINs, deactivate/reactivate users, reset a user's local roster data, clear noisy changed flags, and apply versioned date-and-venue timing corrections with retained audit history.
- Let each user choose their own display colour theme, including dark, light/gentle, and special location-colour palettes.
- Keep a lightweight shared `Northern Crew` location list so future crew/region filtering can build from locations seen in synced roster data.
- Let users change their own PIN, update their saved Deputy login details, and submit an error report with recent redacted diagnostics.
- Let users deactivate their own account, with deactivated account/device data purged after a 30-day cooling-off period.
- Let admins manage default track travel times used when Deputy roster notes are missing base/on-track timing.
- Let admins manage directed travel routes so outbound and return destinations can differ across overnight and multi-day trips.
- Keep a canonical crew directory for Deputy-only crew, app users, published assignees, and unambiguous aliases.
- Mark New Zealand public holidays consistently on calendar, day, and timesheet dates without changing pay or roster hours.
- Cache the best validated official Love Racing track-map candidate with its natural dimensions and source diagnostics.
- Cache official Thoroughbred meeting programmes and fill only race-count/first-race/last-race fields missing from Deputy, without changing roster notes or change history.
- Let admins preview one official meeting without saving it and safely backfill unresolved recent Thoroughbred days by date range.
- Let admins upload a better map for any crew-known location, download the retained automatic image, and reset to automatic without losing either source.
- Present maps by canonical racing venue: consolidate trial aliases, exclude operational locations, and let admins classify uncertain labels without rewriting roster history.
- Let admins build manual race, office, travel, training, and other work days, review changes against the last published version, and publish them to selected crew without writing to Deputy.
- Keep the manual workday editor compact on phones while allowing reusable, Deputy-discovered, and one-day custom roles, roleless attendees, deliberate open positions, and exceptional locations.
- Let an admin seed a private Thoroughbred draft from a saved Love Racing planning day; review and explicit publication are still required.
- Let a user mark their own today/future event as Making my own way. This reversible Re-Deputy-only overlay is visible for crew planning but never changes Deputy, source evidence, or Changed state.
- Keep personal roster views scoped to the signed-in user's own shifts. Shared crew context is only shown as race-day schedule context.
- Reconcile each registered user's confirmed personal assignment into missing shared crew positions without hiding source conflicts.
- Treat incomplete Deputy responses conservatively, delay future-shift removal until two complete absences, and permanently lock completed workdays.
- Keep operational change alerts separate from technical import enrichment and normalization.
- Stagger background syncs across users so Deputy is not hit by every account at once.
- Run with Docker Compose / Portainer.

## Out Of Scope For Now

- Applying for shifts.
- Writing back to Deputy.
- Official Deputy API integration unless the user later gets API access.
- Full user-specific home-origin management for Auckland-based or other future bases. The route model can accept those origins later.
- Timesheet submission and leave/time-off workflows.
- Treating public racing calendars as confirmed work. They may become a faint planning overlay later, but Deputy remains the source of truth for rostered work.
- Spreadsheet roster import until a real source spreadsheet is available to map and test.

## Primary UX

Device-friendly and especially phone-friendly. The month calendar is the landing page, but the day page must be dense, readable, and useful during work.

## Manual Work Days

Admin work days use a private draft, review, and explicit publish workflow. Supported types are race day, office day, travel day, training day, and other work. Dynamic crew rows allow optional roles, roleless attendees, deliberate open/TBC roles, and structured transport. Published days appear in assigned users' calendar, Next Up, day, weekly totals, global crew view, and timesheet. Non-race days do not invoke Love Racing, race calculations, or track maps.

App users are login accounts; canonical crew people are the humans selected for work. A successful authenticated personal Deputy capture links the account to the one crew person carrying that Deputy employee ID. Published workday visibility follows that canonical link, so differing app and Deputy display names do not create a second crew member. Unlinked attendees remain visible to the crew but do not receive personal calendar access until an app login is linked.
