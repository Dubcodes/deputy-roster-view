# Deputy Roster View Agent Notes

Start here when picking up work on this repo.

## Project

Re-Deputy is a private FastAPI/Jinja/SQLite multi-user roster and workday planning app with trusted devices, contractor views, staggered Deputy syncs, and an explicit OAuth-backed trial-write safety gate.

## Read First

- `docs/PROJECT_BRIEF.md` - product goal and current scope.
- `docs/DOMAIN_RULES.md` - roster/timing/domain rules learned from the user.
- `docs/ARCHITECTURE.md` - important modules and data flow.
- `docs/DECISIONS.md` - decisions and why they were made.
- `docs/MULTI_USER_PLAN.md` - planned shared multi-user expansion.
- `docs/TESTING.md` - local checks before committing.
- `docs/AI_TASK_TEMPLATE.md` - prompt template for future AI handovers.

## Safety

- Do not commit `.env`, `data/`, database files, Deputy credentials, calendar URLs, browser session data, or captured secrets.
- Do not print the Deputy calendar URL or login password.
- Redact long Deputy URLs and tokens in diagnostics where practical.
- Treat Deputy as read-only everywhere except the existing, explicit OAuth-backed trial workflow. That workflow is limited to assigned production shifts on an exact allowlisted tenant and must preserve permission, ownership, overlap, drift, read-back and audit safeguards. Never extend it to travel, vehicles, Open/TBC, contractors or production-wide writes without explicit review.
- Deputy write mode must remain off by default. Never contact a live Deputy tenant or test a live Deputy write without explicit task authorization, and never substitute another user's OAuth connection or a global token.

## Useful Commands

```powershell
python -m py_compile app\main.py app\database.py app\deputy_web.py app\scheduler.py app\sync_ics.py app\config.py app\auth.py app\security.py app\user_credentials.py
git -C \\192.168.0.238\storage\projects\deputy-recalender diff --check
```

Template compile check, if Jinja is not installed locally:

```powershell
python -m pip install --target .codex_tmp_jinja jinja2==3.1.5
$env:PYTHONPATH='.codex_tmp_jinja'; python -c "from jinja2 import Environment, FileSystemLoader; env=Environment(loader=FileSystemLoader('app/templates')); env.filters.update(datetime=lambda v, fmt='%a %d %b %H:%M': str(v), time=str, day_short=str, hours=str, urlencode=str); [env.get_template(t) for t in ['admin.html','base.html','login.html','month.html','day.html','settings.html','signup.html','timesheet.html']]; print('templates ok')"
$env:PYTHONPATH='.codex_tmp_jinja'; python scripts\smoke_render_templates.py
```

Remove `.codex_tmp_jinja` after the check.

## Working Style

- Keep the app small and boring: FastAPI, Jinja, SQLite, APScheduler.
- Prefer clear domain helpers over clever abstractions.
- The phone day view matters most after the month calendar.
- If Deputy data is confusing, preserve raw diagnostics behind collapsed/copyable debug sections and make the main UI calm.
