# Deputy Roster View Agent Notes

Start here when picking up work on this repo.

## Project

Deputy Roster View is a private FastAPI/Jinja/SQLite app that re-presents a Deputy roster in a clearer calendar/day view. It is designed for a single-user homelab deployment through Docker Compose / Portainer.

## Read First

- `docs/PROJECT_BRIEF.md` - product goal and current scope.
- `docs/DOMAIN_RULES.md` - roster/timing/domain rules learned from the user.
- `docs/ARCHITECTURE.md` - important modules and data flow.
- `docs/DECISIONS.md` - decisions and why they were made.
- `docs/TESTING.md` - local checks before committing.
- `docs/AI_TASK_TEMPLATE.md` - prompt template for future AI handovers.

## Safety

- Do not commit `.env`, `data/`, database files, Deputy credentials, calendar URLs, browser session data, or captured secrets.
- Do not print the Deputy calendar URL or login password.
- Redact long Deputy URLs and tokens in diagnostics where practical.
- Deputy is read-only from this app. Do not add write-back actions unless explicitly requested and carefully reviewed.

## Useful Commands

```powershell
python -m py_compile app\main.py app\database.py app\deputy_web.py app\scheduler.py app\sync_ics.py app\config.py
git -C \\192.168.0.238\storage\projects\deputy-recalender diff --check
```

Template compile check, if Jinja is not installed locally:

```powershell
python -m pip install --target .codex_tmp_jinja jinja2==3.1.5
$env:PYTHONPATH='.codex_tmp_jinja'; python -c "from jinja2 import Environment, FileSystemLoader; env=Environment(loader=FileSystemLoader('app/templates')); env.filters.update(datetime=lambda v, fmt='%a %d %b %H:%M': str(v), time=str, day_short=str, hours=str, urlencode=str); [env.get_template(t) for t in ['base.html','month.html','day.html','settings.html','timesheet.html']]; print('templates ok')"
$env:PYTHONPATH='.codex_tmp_jinja'; python scripts\smoke_render_templates.py
```

Remove `.codex_tmp_jinja` after the check.

## Working Style

- Keep the app small and boring: FastAPI, Jinja, SQLite, APScheduler.
- Prefer clear domain helpers over clever abstractions.
- The phone day view matters most after the month calendar.
- If Deputy data is confusing, preserve raw diagnostics behind collapsed/copyable debug sections and make the main UI calm.
