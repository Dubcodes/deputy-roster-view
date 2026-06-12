# Decisions

## Keep The Stack Simple

Use FastAPI, Jinja, SQLite, APScheduler, requests, icalendar, and Playwright only where needed for Deputy web capture. Avoid a heavy frontend framework.

## iCal Is The Base Roster Source

iCal is stable enough for the user's own shifts and does not need Deputy API access. It is kept as the base/fallback data source.

## Deputy Web Capture Adds Crew Context

The user does not have an official API token. The app uses logged-in web capture to read the same schedule data Deputy shows in the browser. This remains read-only.

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

The day page should keep Race Day close to the Deputy roster-note wording, not split it into lots of boxes. Show a short plain list, such as:

- `Trucks 0815`
- `Clow Place 0830`
- `On track 0845`
- `Records 1030 Live 1100`
- `10 races 1110 | 1624`

Free-text roster note remains available behind Raw roster note.
