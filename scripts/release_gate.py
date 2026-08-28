from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFLINE_SMOKES = (
    "smoke_render_templates.py",
    "smoke_deputy_release_gate.py",
    "smoke_security_closure.py",
    "smoke_closure_20260820_2.py",
    "smoke_auth_basics.py",
    "smoke_patch_052.py",
    "smoke_patch_053.py",
    "smoke_patch_054.py",
    "smoke_patch_055.py",
    "smoke_release_integration.py",
    "smoke_deployment_continuity.py",
    "smoke_admin_overrides.py",
    "smoke_crew_teams_applications.py",
    "smoke_extended_features.py",
    "smoke_identity_reconciliation.py",
    "smoke_love_racing.py",
    "smoke_love_racing_details.py",
    "smoke_note_interpretation.py",
    "smoke_vehicle_combined_rows.py",
    "smoke_diagnostic_privacy.py",
    "smoke_notifications.py",
    "smoke_roster_integrity.py",
    "smoke_route_flows.py",
    "smoke_account_onboarding.py",
    "smoke_self_travel.py",
    "smoke_track_map_classification.py",
    "smoke_workday_builder.py",
)


def run(label: str, command: list[str], env: dict[str, str]) -> None:
    print(f"\n=== {label} ===", flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Re-Deputy's deterministic offline release gate.")
    parser.add_argument("--responsive", action="store_true", help="Also run the local Playwright 320px/375px browser gate.")
    args = parser.parse_args()
    env = os.environ.copy()
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
        "DEPUTY_ICAL_URL": "",
        "DEPUTY_WEB_URL": "",
        "DEPUTY_LOGIN_EMAIL": "",
        "DEPUTY_LOGIN_PASSWORD": "",
    })
    run("Python compilation", [sys.executable, "-m", "compileall", "-q", "app", "scripts"], env)
    run("service worker syntax", ["node", "--check", "app/static/service-worker.js"], env)
    run("one-shot notice syntax", ["node", "--check", "app/static/one-shot-notice.js"], env)
    run("Admin context syntax", ["node", "--check", "app/static/admin-context.js"], env)
    run("Admin invitation syntax", ["node", "--check", "app/static/admin-invitations.js"], env)
    run("service worker navigation smoke", ["node", "scripts/smoke_service_worker.js"], env)
    run("one-shot notice cleanup smoke", ["node", "scripts/smoke_notice_cleanup.js"], env)
    run("Admin one-shot context smoke", ["node", "scripts/smoke_admin_context.js"], env)
    run("Admin invitation lifecycle smoke", ["node", "scripts/smoke_admin_invitations.js"], env)
    for script in OFFLINE_SMOKES:
        run(script, [sys.executable, str(Path("scripts") / script)], env)
    with tempfile.TemporaryDirectory(prefix="redeputy-release-migration-") as directory:
        database = str(Path(directory) / "migration.sqlite3")
        run("migration rehearsal", [sys.executable, "scripts/rehearse_migration.py", database], env)
        run("assignment/link collision audit", [sys.executable, "scripts/audit_assignment_keys.py", database], env)
    if args.responsive:
        run("responsive 320px/375px browser gate", [sys.executable, "scripts/smoke_workday_responsive.py"], env)
        run("0.5.4 Admin/Help responsive browser gate", [sys.executable, "scripts/smoke_patch_054_responsive.py"], env)
    else:
        print("\n=== responsive browser gate ===\nNOT RUN (use --responsive locally)", flush=True)
    print("\nRe-Deputy deterministic offline release gate passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
