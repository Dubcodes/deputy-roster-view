# Testing

## Canonical release gate

Run the deterministic offline suite locally and in CI with:

```powershell
python scripts\release_gate.py
```

The runner compiles Python, validates and executes the service-worker navigation smoke, renders templates, runs the Deputy/security/integration gates and all committed offline application fixtures, rehearses migration twice, checks SQLite integrity and foreign keys, and audits assignment/link collisions. It never uses live Deputy credentials or a live tenant.

`scripts/smoke_account_onboarding.py` covers the ASCII-only 4–32 digit PIN contract, Re-Deputy login copy, URL-free signup, hash-only/revocable/expiring/single-use and replacement ordinary invitations, concurrent activation, transactional activation/credential rollback, raw-token-free failure responses, no-crew/no-Deputy management accounts, global-view access, sync exclusion, later credential connection, configured-URL fallback, independent Re-Deputy/Deputy emails, and Admin-only on-demand credential-email disclosure.

`scripts/smoke_service_worker.js` executes notification clicks with same-origin and cross-origin windows, rejected navigation, null navigation, rejected focus, unsafe targets, and no-window fallback. A null `WindowClient.navigate()` result must open the safe target rather than focusing the old page.

The 320px/375px Playwright gate is intentionally local because CI does not install browser binaries solely for this check:

```powershell
python scripts\release_gate.py --responsive
```

GitHub Actions additionally runs `pip check`, `pip-audit`, builds the actual Dockerfile, and boots/restarts a disposable container for unauthenticated HTTP, persistent-data, write-mode-off, and zero-mutation assertions.

## Required Local Checks

Run before committing:

```powershell
python -m py_compile app\main.py app\database.py app\deputy_web.py app\scheduler.py app\sync_ics.py app\config.py app\auth.py app\security.py app\user_credentials.py app\track_maps.py app\public_holidays.py
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
python scripts\smoke_love_racing_details.py
python scripts\smoke_admin_overrides.py
python scripts\smoke_extended_features.py
python scripts\smoke_roster_integrity.py
python scripts\smoke_track_map_classification.py
python scripts\smoke_note_interpretation.py
python scripts\smoke_workday_builder.py
python scripts\smoke_workday_responsive.py
python scripts\smoke_identity_reconciliation.py
python scripts\smoke_self_travel.py
python scripts\smoke_crew_teams_applications.py
```

The Admin override smoke covers legacy-row migration, field/value normalization, active precedence, historical days, immediate recalculation, replacement, disable fallback, canonical venue scope, and Changed-badge isolation. For deployed-data migration confidence, run `init_db()` against a disposable database copy only; never point migration tests at the live file.

The identity reconciliation smoke covers employee-ID account linking, conservative conflicting-evidence review, Jayden/Alf/Nate duplicate retirement, already-correct links, trusted-device preservation, historical redirects, the 6 August Office Day across personal/global/day/Next Up/timesheet views, Admin transfer/merge confirmation, merge audit/idempotency, builder deduplication, and visibility gained by linking an account after publication.

The crew/team/application smoke covers Campbell/Cambo and Otm685/Alf canonical search, team priority and cross-team search, Open versus TBC, team eligibility, idempotent self-only applications, overlap and unknown-time conflicts, local acceptance/visibility, vehicle aliases, and zero Deputy writes. The responsive builder smoke exercises keyboard person/vehicle search and picker bounds at 1280, 430, 375, and 320 pixels.

## Manual App Checks

After Portainer redeploy:

- Open `/month`.
- Open a normal race day.
- Confirm Race Day strip includes start/on-track/first race/last race where present.
- Confirm Deputy Schedule excludes Out of Region noise.
- Confirm timing-only crew changes do not badge every row.
- Confirm immediate notification tests with no active device return a friendly message and do not leave a queued event.
- Confirm an incomplete draft can be reviewed and explicitly published with warnings.
- Confirm canonical vehicles conflict across separate same-date workdays, but not between assignments on one workday.
- Confirm Admin team chips add/create/remove in place without resetting search, filters, or scroll position.
- Inspect `Server-Timing` on month, day, Settings, and Admin responses when comparing page performance.
- Capture an authoritative crew roster, then replace it with the Ruakaka-style Side 2/CCU2/VT/Sound change chain. Confirm Change History records both moves, the Sound/VT merge, and any evidenced same-position replacement exactly once.
- Confirm `SVT` displays as `Sound/VT` when it is the only audio/replay assignment, and as `Sound` when another employee has an overlapping `VT` assignment at the same location.
- Confirm a removed Deputy schedule assignment disappears after the next successful complete schedule-window sync, while a failed/partial capture retains the previous crew list.
- Confirm a personal assignment fills only its matching TBC crew position and a conflicting named shared assignment remains visible with a warning.
- Confirm one complete personal-capture absence warns, the second may retire, and partial/failed captures do not advance the count.
- Confirm completed events no longer show active Changed badges and later captures cannot prune or replace their populated history.
- Expand Admin Roster integrity and inspect any partial upcoming events, evidence fills, conflicts, locked events, and archive recovery counts.
- Confirm empty RTS/FM areas do not create TBC rows, while assigned RTS/FM people still appear.
- On a known Thoroughbred track, confirm the cached 2D map appears at the bottom of the day page without an outbound link. Confirm Harness/Greyhound days do not borrow the Cambridge Thoroughbred map.
- Open `/settings`.
- Run Sync and Update.
- Confirm spinner/progress appears and then hides.
- Confirm a failed sync shows a useful message below the status.
- Open `/admin`.
- Open `/admin/roster-days/new`, create an office day with a roleless attendee, save it privately, publish it, and confirm calendar, Next Up, day, weekly total, global view, and timesheet all agree.
- Apply the Thoroughbred preset, remove RTS, add Gimbal and an explicit open Gimbal Assist row, then reopen the draft and confirm removed roles stay removed.
- Confirm the new transport picker omits No transport required while a historical saved selection still renders; Making own way, an assigned vehicle, and transport TBC remain visually distinct.
- On an 08:30 race day with truck early start enabled, confirm Tender and OB assignments show 08:15 and include the extra 15 minutes in personal hours, while 684 and other crew remain at 08:30. Disable the setting and confirm everyone returns to 08:30.
- In Settings at 375px and 320px, enable Web Push only through the explicit button, register more than one device, send immediate and scheduled tests, then disable the current device. Confirm reminders and changes deep-link locally and do not repeat after scheduler restart.
- Search `cambo` in Person and confirm only Campbell Stephens is shown. Select Open position and confirm it appears in Available/Open Shifts while a TBC row does not.
- Apply from an eligible linked account, withdraw it, and have an admin accept a fresh application. Confirm conflicts block Apply and Deputy data is unchanged.
- In Admin, add/remove a team member, change team order, add a vehicle alias, and confirm the builder still displays canonical person and vehicle labels.
- Expand Travel day and hotels, assign two crew to different hotels, and confirm each published user sees only their own hotel.
- Confirm FCR context, H-Cambridge, and Travel then Overnighter are absent from production positions.
- In Settings, expand Your roster stats and confirm weekday cards show rostered hours and shift counts, today is excluded from completed totals, and the location-hours summary remains readable.
- In the crew calendar, open two locations on the same date and confirm each day shows only that location's crew. Confirm Back to month and the calendar icon both retain crew view.
- In Admin, confirm Locations starts collapsed and that each location can save separate office and hotel travel times.
- Expand Travel-route matrix and confirm opposite directions can keep different values. On an overnight race day, confirm the morning hotel route and evening office route are labelled separately.
- Expand Crew directory, confirm Deputy-only people appear, and confirm an alias cannot be active for both Gary records.
- On Waitangi Day or another national holiday, confirm one keyboard-focusable star appears beside the date in personal month, shared month, list, day, and timesheet views.
- At approximately 320px and 375px width, confirm the holiday star has reserved heading space and its popover stays inside the viewport without covering the date, weekday, shifts, or a neighbouring cell.
- Refresh Track Maps and inspect the recorded dimensions/result. At phone width, confirm the map remains inside the page, keeps its aspect ratio, and is not enlarged beyond its natural width.
- In Admin Track maps, confirm trial aliases appear only beneath their canonical venue, operational locations are absent, and uncertain locations stay in the collapsed classification subsection. Check the controls around 320px and 375px wide.
- Confirm each user shows next planned sync and last sync status.
- In Admin, use Refresh upcoming race times and confirm the action returns immediately while discovered meetings show queued, awaiting, partial, complete, or failed status.
- In Admin, preview meeting `55034` with expected date `2026-07-26` and venue `Te Aroha`; confirm it shows 9 races and 11:34-16:35 without saving a cache row.
- Run Refresh unresolved race times across a recent range. Confirm the result distinguishes preserved Deputy values from Love Racing-filled fields and reports an unmatched date/venue without guessing.
- On a Thoroughbred day with only a Deputy race count, confirm cached Love Racing first/last race times fill the missing fields and the source note stays compact. Confirm Harness/Greyhound and non-racing shifts are unchanged.
- If using the temporary tunnel stack, confirm its `cloudflared` container logs show a `trycloudflare.com` URL.

The route smoke also verifies that Office/Clow Place travel defaults collapse to one base, named hotels stay separate, `G Cambridge` aliases merge, generic contractor context is excluded, duplicate user copies count as one race-day sample, a preceding overnight travel shift can teach the next day's office-to-track duration, and admin diagnostics load on demand.

## Known Test Gaps

- Integrity behavior is covered by a sanitized database smoke fixture rather than a live Deputy account.
- Deputy web capture is hard to test without live credentials.
- Live Deputy capture remains difficult to reproduce without credentials; parsing and database behavior are covered with sanitized fixtures.
- Live Love Racing browser access can change independently of the app. Meeting discovery, programme parsing, cadence, monotonic cache merging, queue deduplication, and source precedence are covered by local HTML/SQLite smoke fixtures.

The self-travel smoke confirms owner-only today/future authorization, stable identity linkage, reversible local overlay, preservation of a later roster vehicle update, append-only preference audit, and isolation from Deputy evidence and Changed state. The responsive builder smoke covers 1280px, 430px, 375px, and 320px widths.

`python scripts\smoke_notifications.py` covers truck-time payloads, multiple devices, owner isolation, reminders, published-manual changes and cancellation, scheduled tests, deduplication, draft exclusion, open-position push filtering, and safe service-worker links.
# 2026.08.13.1 focused checks

`python scripts/smoke_release_integration.py` uses a disposable SQLite database and mocked Deputy HTTP. It covers idempotent migration, personal start/finish and notification payloads, hash-only contractor invites, activation/replay/expiry/revocation/PIN login, contractor route and own-day authorization, OAuth state hashing/replay, encrypted tokens/secrets, own-user readiness, one POST plus GET verification, durable Roster linking, and duplicate-create prevention.

`python scripts/smoke_workday_responsive.py` checks August, September, and November 2026 at 375px and 320px, including first-row brand/actions, left-fluid second-row month navigation, overlap, and horizontal overflow.

Automated tests never contact a live Deputy tenant. A real disposable trial smoke must be initiated by an Admin from a published workday, reviewed in the dry preview, and confirmed by typing `CONFIRM`; verify tenant, operation short ID, Roster ID, and read-back result in the popup and Admin audit.

# 2026.08.13.2 Deputy readiness checks

`python scripts/smoke_release_integration.py` also covers ordinary-user read readiness,
the write-readiness trial/host matrix, same-identity permission loss and gain, EmployeeId
mismatch, independent reference-resource denial, token refresh, James/Sarah OAuth-token
isolation, cross-owner prepared-operation rejection, disconnected-user non-fallback,
contractor OAuth-route denial, independent personal start/finish behavior, and a
second idempotent migration pass with row-count preservation. All Deputy HTTP remains
mocked; the suite performs zero live Deputy writes.

# 2026.08.13.3 final Deputy release gate

`python scripts/smoke_deputy_release_gate.py` uses only sanitized Lab Round 2 and official-documentation response fixtures. It covers the v2 envelope/normalizer, Resource QUERY syntax, Taupo and Ruakaka full intervals, truck early start, duration-only breaks, incomplete timing, duplicate full-duration roles, bounded create preflight/reconciliation, malformed success bodies, strict Deputy endpoint validation, partial-batch publication blocking, and idempotent database initialization.

`python scripts/smoke_release_integration.py` additionally covers general OAuth at `once.deputy.com`, configured callback origin, returned endpoint rejection, long-life refresh scope, rotating refresh token fields, OAuth replay/cross-user isolation, per-user references/readiness, contractor and Admin boundaries, operation ownership, Timesheet locks, publish idempotency, and unknown-create recovery. Neither suite contacts Deputy.
