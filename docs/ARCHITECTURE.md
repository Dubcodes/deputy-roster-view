# Architecture

## Stack

- FastAPI app in `app/main.py`
- Jinja templates in `app/templates/`
- CSS in `app/static/style.css`
- SQLite database through `app/database.py`
- iCal sync through `app/sync_ics.py`
- Deputy web capture through `app/deputy_web.py`
- Background schedules through `app/scheduler.py`
- Docker Compose deployment through `docker-compose.yml`

## Data Sources

### Deputy iCal

`sync_ics.py` fetches the configured Deputy calendar feed and stores/updates rows in `shifts`. This is the source for the user's own rostered shifts.

### Deputy Web Capture

`deputy_web.py` logs into the Deputy web app using env credentials, captures relevant JSON responses, and saves schedule rows into `deputy_schedule_shifts`. This is used for crew/role context and open shift counts.

It should prefer an All Locations schedule capture. If that is not selectable, it falls back to upcoming known roster locations.

## Main Views

- `/month`: main landing calendar/list view.
- `/day/{yyyy-mm-dd}`: shift detail, race-day timings, Deputy crew schedule, change history, timing notes.
- `/settings`: sync control, roster snapshot, diagnostics, maintenance.
- `/sync-now`: starts background sync and redirects/polls.

## Local State

User notes and timing overrides live in `shift_marks` and must survive every sync. Sync code should not overwrite marks.

## Change Detection

Own shift changes are stored in `shift_changes`. Schedule row changes are summarized on `deputy_schedule_shifts.change_summary`.

Crew visible change badges should only appear for assignment changes:

- person changed
- position/area changed
- open shift status changed

Timing-only crew schedule changes should not badge every crew row.
