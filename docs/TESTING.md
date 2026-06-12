# Testing

## Required Local Checks

Run before committing:

```powershell
python -m py_compile app\main.py app\database.py app\deputy_web.py app\scheduler.py app\sync_ics.py app\config.py
git -C \\192.168.0.238\storage\projects\deputy-recalender diff --check
```

## Template Compile Check

If local Python does not have Jinja:

```powershell
python -m pip install --target .codex_tmp_jinja jinja2==3.1.5
$env:PYTHONPATH='.codex_tmp_jinja'; python -c "from jinja2 import Environment, FileSystemLoader; env=Environment(loader=FileSystemLoader('app/templates')); env.filters.update(datetime=lambda v, fmt='%a %d %b %H:%M': str(v), time=str, day_short=str, hours=str, urlencode=str); [env.get_template(t) for t in ['base.html','month.html','day.html','settings.html','timesheet.html']]; print('templates ok')"
```

Remove `.codex_tmp_jinja` after:

```powershell
$target = Resolve-Path -LiteralPath .codex_tmp_jinja; $root = Resolve-Path -LiteralPath .; if ($target.Path.StartsWith($root.Path)) { Remove-Item -LiteralPath $target.Path -Recurse -Force } else { throw "Refusing to remove outside workspace: $($target.Path)" }
```

## Manual App Checks

After Portainer redeploy:

- Open `/month`.
- Open a normal race day.
- Confirm Race Day strip includes start/on-track/first race/last race where present.
- Confirm Deputy Schedule excludes Out of Region noise.
- Confirm timing-only crew changes do not badge every row.
- Open `/settings`.
- Run Sync and Update.
- Confirm spinner/progress appears and then hides.

## Known Test Gaps

- No automated unit tests yet.
- Deputy web capture is hard to test without live credentials.
- Overnight travel rules are intentionally incomplete until better examples exist.
