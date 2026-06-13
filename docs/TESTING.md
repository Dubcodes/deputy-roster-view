# Testing

## Required Local Checks

Run before committing:

```powershell
python -m py_compile app\main.py app\database.py app\deputy_web.py app\scheduler.py app\sync_ics.py app\config.py app\auth.py app\security.py app\user_credentials.py
git -C \\192.168.0.238\storage\projects\deputy-recalender diff --check
```

## Template Compile Check

If local Python does not have Jinja:

```powershell
python -m pip install --target .codex_tmp_jinja jinja2==3.1.5
$env:PYTHONPATH='.codex_tmp_jinja'; python -c "from jinja2 import Environment, FileSystemLoader; env=Environment(loader=FileSystemLoader('app/templates')); env.filters.update(datetime=lambda v, fmt='%a %d %b %H:%M': str(v), time=str, day_short=str, hours=str, urlencode=str); [env.get_template(t) for t in ['admin.html','base.html','login.html','month.html','day.html','settings.html','signup.html','timesheet.html']]; print('templates ok')"
$env:PYTHONPATH='.codex_tmp_jinja'; python scripts\smoke_render_templates.py
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
- Confirm a failed sync shows a useful message below the status.
- Open `/admin`.
- Confirm each user shows next planned sync and last sync status.
- If using the temporary tunnel, confirm the `cloudflared` container logs show a `trycloudflare.com` URL.

## Known Test Gaps

- No automated unit tests yet.
- Deputy web capture is hard to test without live credentials.
- Overnight travel rules are intentionally incomplete until better examples exist.
