from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VISUAL_DIR = ROOT / ".codex_tmp_054_visual"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temp_dir = Path(tempfile.mkdtemp(prefix="redeputy-054-responsive-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "responsive.sqlite3"),
        APP_SECRET_KEY="responsive-054-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
        TRUSTED_DEVICE_LIMIT="20",
    )

    import uvicorn
    from playwright.sync_api import sync_playwright

    from app.account_invitations import create_account_invite
    from app.database import (
        create_admin_override,
        create_app_user,
        create_trusted_device,
        get_connection,
        init_db,
        save_roster_day,
        upsert_travel_time_default,
    )
    from app.main import app
    from app.security import SESSION_COOKIE_NAME, hash_pin, hash_session_token

    init_db()
    admin = create_app_user(
        deputy_email="responsive-admin@example.invalid",
        display_name="Responsive Admin",
        pin_hash=hash_pin("2468"),
        deputy_web_url="",
        encrypted_email="",
        encrypted_password="",
    )
    admin_id = int(admin["id"])
    session_token = "responsive-054-admin-device"
    expires = (datetime.now().astimezone() + timedelta(days=30)).isoformat(timespec="seconds")
    create_trusted_device(
        user_id=admin_id,
        token_hash=hash_session_token(session_token),
        expires_at=expires,
        label="Current browser",
        user_agent="Playwright release fixture",
    )
    for index in range(3):
        create_trusted_device(
            user_id=admin_id,
            token_hash=hash_session_token(f"responsive-active-{index}"),
            expires_at=expires,
            label=f"Active fixture {index + 1}",
            user_agent=f"Fixture browser active/{index + 1}",
        )
    with get_connection() as conn:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for index in range(9):
            conn.execute(
                """INSERT INTO trusted_devices(
                       user_id,token_hash,label,user_agent,created_at,last_seen_at,expires_at,revoked_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    admin_id, hash_session_token(f"responsive-revoked-{index}"),
                    f"Previous fixture {index + 1}", f"Fixture browser revoked/{index + 1}",
                    now, now, expires, now,
                ),
            )
        for index, (key, label, enabled) in enumerate((
            ("avondale", "Avondale", 1),
            ("te-rapa", "Te Rapa", 0),
        )):
            conn.execute(
                """INSERT INTO love_racing_meetings(
                       meeting_date,racecourse_key,racecourse,club_name,meeting_id,meeting_url,
                       discovery_source,discovered_at,source_url,source_hash,raw_text,
                       first_seen_at,last_seen_at,last_synced_at,is_active
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    f"2026-09-{12 + index:02d}", key, label, f"{label} Racing Club", f"054-{index}",
                    f"https://fixture.invalid/{key}", "fixture", now, "https://fixture.invalid/calendar",
                    f"fixture-{key}", "", now, now, now,
                ),
            )
            conn.execute(
                "INSERT INTO planning_location_preferences(location_key,display_name,is_enabled,updated_at) VALUES(?,?,?,?)",
                (key, label, enabled, now),
            )
    upsert_travel_time_default(
        track_key="avondale", track_label="Avondale", base_label="Office / Clow Place",
        travel_minutes=35, source="manual", note="Normal route",
    )
    upsert_travel_time_default(
        track_key="avondale", track_label="Avondale", base_label="Harbour View Hotel",
        travel_minutes=18, source="manual", note="Overnight base",
    )
    upsert_travel_time_default(
        track_key="studio", track_label="Studio", base_label="Office / Clow Place",
        travel_minutes=22, source="manual", note="Travel-only fixture",
    )
    create_admin_override(
        created_by_user_id=admin_id,
        target_date="2026-09-12",
        target_track="Avondale",
        override_type="timing",
        label="first_race",
        value="12:35",
        note="Responsive fixture",
    )
    save_roster_day(
        roster_day_id=None, roster_date="2026-09-20", track_key="studio", track_label="Studio",
        race_type="", day_type="office_day", start_origin="Office / Clow Place",
        finish_destination="Office / Clow Place", office_start="09:00", on_track_time="",
        first_race_time="", last_race_time="", race_count=None, notes="", hotel_assignments="[]",
        title="Studio preparation", custom_location="Studio", end_time="16:00", updated_by_user_id=admin_id,
        assignments=[{"assignment_key": "responsive-open", "role_label": "Camera", "assignment_state": "open"}],
    )
    for index in range(14):
        create_account_invite(
            f"fixture-{index}@example.invalid", f"Invitation Fixture {index + 1}", admin_id
        )

    VISUAL_DIR.mkdir(exist_ok=True)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise AssertionError("0.5.4 responsive fixture server did not start")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies([{"name": SESSION_COOKIE_NAME, "value": session_token, "url": base_url}])
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            page.goto(f"{base_url}/admin")
            accounts = page.locator('[data-admin-disclosure-key="accounts"]')
            locations = page.locator('[data-admin-disclosure-key="locations"]')
            overrides = page.locator('[data-admin-disclosure-key="manual-overrides"]')
            assert not accounts.get_attribute("open") and not locations.get_attribute("open") and not overrides.get_attribute("open")
            page.screenshot(path=str(VISUAL_DIR / "admin-collapsed-1280.png"), full_page=True)

            accounts.locator("summary").first.click()
            invites = page.locator('[data-admin-disclosure-key="account-invitations"]')
            invites.locator("summary").first.click()
            invite_count = page.locator('[data-invite-kind="account"]').count()
            form = invites.locator('form[action="/admin/account-invitations"]')
            form.locator('[name="account_email"]').fill("browser-refresh@example.invalid")
            form.locator('[name="display_name"]').fill("Browser Refresh Fixture")
            form.locator('button[type="submit"]').click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_function("location.hash === '' && !new URL(location.href).searchParams.has('notice')")
            assert accounts.get_attribute("open") is not None and invites.get_attribute("open") is not None
            created_row = page.locator('[data-invite-kind="account"]').first
            activation_link = created_row.locator('[data-invite-link-input]').input_value()
            assert activation_link.startswith(f"{base_url}/account/invite/")
            assert page.locator('[data-invite-kind="account"]').count() == invite_count + 1
            page.reload()
            assert page.locator('[data-invite-kind="account"]').count() == invite_count + 1
            assert page.locator('[data-invite-kind="account"]').first.locator('[data-invite-link-input]').input_value() == activation_link

            accounts.evaluate("element => { element.open = true; }")
            invites.evaluate("element => { element.open = true; }")
            target_row = page.locator('[data-invite-kind="account"]').last
            target_row.scroll_into_view_if_needed()
            prior_scroll = page.evaluate("scrollY")
            target_row.locator("button", has_text="Revoke").click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_function("!new URL(location.href).searchParams.has('notice')")
            assert accounts.get_attribute("open") is not None and invites.get_attribute("open") is not None
            assert abs(page.evaluate("scrollY") - prior_scroll) < 180
            page.screenshot(path=str(VISUAL_DIR / "accounts-restored-1280.png"), full_page=True)
            page.goto(f"{base_url}/month")
            page.goto(f"{base_url}/admin")
            assert accounts.get_attribute("open") is None, "consumed context unexpectedly persisted into a fresh visit"

            locations.evaluate("element => { element.open = true; element.scrollIntoView(); }")
            page.wait_for_timeout(100)
            location_rows = page.locator(".location-management-row")
            assert location_rows.count() >= 3
            columns = location_rows.first.evaluate("element => getComputedStyle(element).gridTemplateColumns.split(' ').filter(Boolean).length")
            assert columns == 5, columns
            avondale = location_rows.filter(has_text="Avondale").first
            assert "Harbour View Hotel" in avondale.inner_text() and "Normal route" in avondale.inner_text()
            page.screenshot(path=str(VISUAL_DIR / "locations-1280.png"), full_page=True)
            location_scroll = page.evaluate("scrollY")
            avondale.locator('.planning-location-toggle input').click()
            page.wait_for_load_state("domcontentloaded")
            assert locations.get_attribute("open") is not None
            assert abs(page.evaluate("scrollY") - location_scroll) < 220

            users = page.locator('[data-admin-disclosure-key="users"]')
            users.evaluate("element => { element.open = true; element.scrollIntoView(); }")
            user = users.locator(".admin-user-row").first
            user.evaluate("element => { element.open = true; }")
            assert user.locator(".admin-device-row:not(.is-revoked)").count() >= 4
            previous = user.locator(".previous-devices")
            assert previous.count() == 1 and previous.get_attribute("open") is None
            assert "Previously trusted devices · 9" in previous.locator("summary").inner_text()
            previous.locator("summary").click()
            assert previous.locator(".admin-device-row.is-revoked").count() == 9
            user_scroll = page.evaluate("scrollY")
            user.locator(".admin-device-row", has_text="Active fixture 2").locator("button", has_text="Untrust").click()
            page.wait_for_load_state("domcontentloaded")
            assert users.get_attribute("open") is not None and user.get_attribute("open") is not None
            assert abs(page.evaluate("scrollY") - user_scroll) < 220
            page.screenshot(path=str(VISUAL_DIR / "trusted-devices-1280.png"), full_page=True)

            overrides = page.locator('[data-admin-disclosure-key="manual-overrides"]')
            assert overrides.get_attribute("open") is None
            overrides.locator("summary").first.click()
            assert overrides.locator('form[action="/admin/overrides"]').count() >= 1
            assert "1 active" in overrides.locator("summary").first.inner_text()
            page.screenshot(path=str(VISUAL_DIR / "manual-overrides-1280.png"), full_page=True)

            page.goto(f"{base_url}/help")
            assert page.locator(".help-term svg").count() >= 5
            assert page.locator(".help-heading-term svg").count() >= 2
            page.screenshot(path=str(VISUAL_DIR / "help-1280.png"), full_page=True)

            for width in (375, 320):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"{base_url}/admin")
                locations = page.locator('[data-admin-disclosure-key="locations"]')
                locations.locator("summary").first.click()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
                mobile_columns = page.locator(".location-management-row").first.evaluate("element => getComputedStyle(element).gridTemplateColumns.split(' ').filter(Boolean).length")
                assert mobile_columns == 1
                page.screenshot(path=str(VISUAL_DIR / f"locations-{width}.png"), full_page=True)
                page.goto(f"{base_url}/help")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
                page.screenshot(path=str(VISUAL_DIR / f"help-{width}.png"), full_page=True)

            browser.close()
            if page_errors:
                raise AssertionError(f"0.5.4 browser errors: {page_errors!r}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print(f"0.5.4 Admin/Help responsive browser smoke ok; screenshots: {VISUAL_DIR}")


if __name__ == "__main__":
    main()
