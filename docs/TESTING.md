# Testing

## Required Local Checks

Run before committing:

```powershell
python -m py_compile app\main.py app\database.py app\deputy_web.py app\scheduler.py app\sync_ics.py app\config.py app\auth.py app\security.py app\user_credentials.py app\track_maps.py
git -C \\192.168.0.238\storage\projects\deputy-recalender diff --check
```

## Template Compile Check

If local Python does not have Jinja:

```powershell
python -m pip install --target .codex_tmp_jinja jinja2==3.1.5
$env:PYTHONPATH='.codex_tmp_jinja'; python -c "from jinja2 import Environment, FileSystemLoader; env=Environment(loader=FileSystemLoader('app/templates')); env.filters.update(datetime=lambda v, fmt='%a %d %b %H:%M': str(v), time=str, day_short=str, hours=str, urlencode=str); [env.get_template(t) for t in ['admin.html','base.html','help.html','login.html','month.html','day.html','roster_day_builder.html','settings.html','signup.html','timesheet.html']]; print('templates ok')"
$env:PYTHONPATH='.codex_tmp_jinja'; python scripts\smoke_render_templates.py
```

Remove `.codex_tmp_jinja` after:

```powershell
$target = Resolve-Path -LiteralPath .codex_tmp_jinja; $root = Resolve-Path -LiteralPath .; if ($target.Path.StartsWith($root.Path)) { Remove-Item -LiteralPath $target.Path -Recurse -Force } else { throw "Refusing to remove outside workspace: $($target.Path)" }
```

## Route Smoke Check

Run this after changing account, settings, admin, or form-handling code. It creates a temporary SQLite database, signs up a test admin, saves Deputy login details through Settings, saves another user's Deputy login through Admin, and submits an error report.

```powershell
python scripts\smoke_route_flows.py
python scripts\smoke_love_racing.py
```

## Manual App Checks

After Portainer redeploy:

- Open `/month`.
- Open a normal race day.
- Confirm Race Day strip includes start/on-track/first race/last race where present.
- Confirm Deputy Schedule excludes Out of Region noise.
- Confirm timing-only crew changes do not badge every row.
- Confirm `SVT` displays as `Sound VT` when it is the only audio/replay assignment, and as `Sound` when another employee has an overlapping `VT` assignment at the same location.
- Confirm a removed Deputy schedule assignment disappears after the next successful complete schedule-window sync, while a failed/partial capture retains the previous crew list.
- Confirm empty RTS/FM areas do not create TBC rows, while assigned RTS/FM people still appear.
- On a known Thoroughbred track, confirm the cached 2D map appears at the bottom of the day page without an outbound link. Confirm Harness/Greyhound days do not borrow the Cambridge Thoroughbred map.
- Open `/settings`.
- Run Sync and Update.
- Confirm spinner/progress appears and then hides.
- Confirm a failed sync shows a useful message below the status.
- Open `/admin`.
- Open `/admin/roster-days/new`, save a draft, confirm it remains private, publish it, and verify only an assigned user's month/day views show it.
- Expand Travel day and hotels, assign two crew to different hotels, and confirm each published user sees only their own hotel.
- Confirm FCR context, H-Cambridge, and Travel then Overnighter are absent from production positions.
- In Settings, confirm weekday cards explicitly show rostered hours and shift counts, today is excluded from completed totals, and Recent Completed Roster Days matches those totals.
- Confirm each user shows next planned sync and last sync status.
- If using the temporary tunnel stack, confirm its `cloudflared` container logs show a `trycloudflare.com` URL.

The route smoke also verifies that Office/Clow Place travel defaults collapse to one base, named hotels stay separate, `G Cambridge` aliases merge, generic contractor context is excluded, duplicate user copies count as one race-day sample, a preceding overnight travel shift can teach the next day's office-to-track duration, and admin diagnostics load on demand.

## Known Test Gaps

- No automated unit tests yet.
- Deputy web capture is hard to test without live credentials.
- Hotel selection and hotel-to-track travel remain manual until real stay details are known.
