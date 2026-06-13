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
- Optional temporary tunnel through `docker-compose.tunnel.yml`

## Data Sources

### Deputy iCal

`sync_ics.py` fetches the configured Deputy calendar feed and stores/updates rows in `shifts`. In the multi-user version this is an optional backup source, not the primary login path.

### Deputy Web Capture

`deputy_web.py` logs into the Deputy web app using either a user's encrypted saved credentials or server-level env fallback credentials. It captures relevant JSON responses and saves schedule rows into `deputy_schedule_shifts`. This is used for crew/role context, open shift counts, and richer roster data.

It should prefer an All Locations schedule capture. If that is not selectable, it falls back to upcoming known roster locations.

### Multi-User Sync Queue

`user_sync_state` stores the next planned sync time, last result, and running flag for each active user with saved Deputy credentials.

`scheduler.py` does not launch every account at once. Daily and pre-shift triggers call `plan_staggered_user_syncs`, which spreads users by `USER_SYNC_STAGGER_MINUTES` plus small deterministic jitter. `run_due_user_syncs` wakes every five minutes and processes up to `USER_SYNC_BATCH_SIZE` due accounts, default one.

## Main Views

- `/month`: main landing calendar/list view.
- `/day/{yyyy-mm-dd}`: shift detail, race-day timings, Deputy crew schedule, change history, timing notes.
- `/settings`: sync control, roster snapshot, diagnostics, maintenance.
- `/sync-now`: starts background sync and redirects/polls.
- `/signup` and `/login`: one-time trusted-device flow.
- `/admin`: user/sync health and manual override audit.

## Local State

User notes and timing overrides live in `shift_marks` and must survive every sync. Sync code should not overwrite marks.

Deputy login secrets are encrypted in `deputy_user_secrets`. The app secret comes from `APP_SECRET_KEY` or generated `data/app_secret.key`; losing/changing it means stored Deputy passwords cannot be decrypted.

## Change Detection

Own shift changes are stored in `shift_changes`. Schedule row changes are summarized on `deputy_schedule_shifts.change_summary`.

Crew visible change badges should only appear for assignment changes:

- person changed
- position/area changed
- open shift status changed

Timing-only crew schedule changes should not badge every crew row.
