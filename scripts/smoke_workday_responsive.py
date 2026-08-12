from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-workday-responsive-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "responsive.sqlite3"),
        APP_SECRET_KEY="responsive-smoke-secret",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
    )

    import uvicorn
    from playwright.sync_api import sync_playwright

    from app.database import get_connection, get_default_team_id, init_db, save_crew_vehicle, set_crew_person_team
    from app.main import app

    init_db()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise AssertionError("Responsive smoke server did not start.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/signup")
            page.locator('[name="deputy_email"]').fill("responsive@example.com")
            page.locator('[name="deputy_password"]').fill("password")
            page.locator('[name="pin"]').fill("1234")
            page.locator('[name="pin_confirm"]').fill("1234")
            page.locator('button[type="submit"]').click()
            page.wait_for_url("**/month")
            for width in (375, 320):
                page.set_viewport_size({"width": width, "height": 900})
                for month, label in ((8, "August 2026"), (9, "September 2026"), (11, "November 2026")):
                    page.goto(f"http://127.0.0.1:{port}/month?year=2026&month={month}")
                    page.wait_for_selector(".month-nav")
                    if page.locator(".month-nav strong").inner_text().strip() != label:
                        raise AssertionError(f"Long month name was abbreviated at {width}px.")
                    if page.locator(".brand-meta").count():
                        raise AssertionError("User/sync metadata remained duplicated in the site header.")
                    if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                        raise AssertionError(f"{label} header overflowed at {width}px.")
                    brand_box = page.locator(".brand").bounding_box()
                    actions_box = page.locator(".site-nav").bounding_box()
                    month_box = page.locator(".month-nav").bounding_box()
                    if not brand_box or not actions_box or not month_box:
                        raise AssertionError(f"{label} header controls were missing at {width}px.")
                    if abs(brand_box["y"] - actions_box["y"]) > 12 or actions_box["x"] <= brand_box["x"]:
                        raise AssertionError(f"{label} brand/actions did not share the first row at {width}px.")
                    if month_box["y"] < max(brand_box["y"] + brand_box["height"], actions_box["y"] + actions_box["height"]) - 2:
                        raise AssertionError(f"{label} month navigation overlapped the first row at {width}px.")
                    if month_box["x"] > width * .35:
                        raise AssertionError(f"{label} month navigation remained rigidly viewport-centred at {width}px.")
            page.set_viewport_size({"width": 1280, "height": 900})
            init_db()
            with get_connection() as conn:
                admin = conn.execute("SELECT id FROM app_users WHERE deputy_email='responsive@example.com'").fetchone()
                person = conn.execute("SELECT id FROM crew_people WHERE app_user_id=?", (int(admin["id"]),)).fetchone()
                conn.execute("UPDATE crew_people SET canonical_display_name='Campbell Stephens',current_deputy_name='Cambo' WHERE id=?", (int(person["id"]),))
                conn.execute(
                    "INSERT INTO crew_aliases(person_id,alias,normalized_alias,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (int(person["id"]), "Cambo", "cambo", "2026-08-10T09:00:00+12:00", "2026-08-10T09:00:00+12:00"),
                )
            northern_team = get_default_team_id()
            set_crew_person_team(
                person_id=int(person["id"]),
                team_id=int(northern_team),
                active=True,
                is_primary=True,
                actor_user_id=int(admin["id"]),
            )
            for order, label in enumerate(("684", "685"), start=10):
                save_crew_vehicle(
                    vehicle_id=None,
                    display_label=label,
                    aliases=[],
                    active=True,
                    sort_order=order,
                    team_id=northern_team,
                    notes="",
                    actor_user_id=int(admin["id"]),
                )
            save_crew_vehicle(
                vehicle_id=None,
                display_label="Rav91",
                aliases=["Rav4"],
                active=True,
                sort_order=30,
                team_id=get_default_team_id(),
                notes="",
                actor_user_id=int(admin["id"]),
            )
            console_errors = []
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/admin/roster-days/new")
            if "Build a work day" not in page.locator("body").inner_text():
                raise AssertionError("Workday builder did not render in browser smoke.")
            first_row = page.locator("[data-assignment-row]").first
            person_picker = first_row.locator('[data-picker-kind="person"] [data-picker-input]')
            person_picker.click()
            person_options = first_row.locator('[data-picker-kind="person"] [data-picker-option]')
            if person_options.nth(0).inner_text().splitlines()[0] != "Open position" or person_options.nth(1).inner_text().splitlines()[0] != "TBC / not offered":
                raise AssertionError("Open and TBC choices were not first in the Person picker.")
            if "Northern Team" not in first_row.locator('[data-team-group]').inner_text() or "Campbell Stephens" not in first_row.locator('[data-team-group]').inner_text():
                raise AssertionError("Northern Team picker grouping did not use existing membership.")
            person_picker.fill("tbc")
            person_picker.press("Enter")
            if first_row.locator('[name="assignment_state"]').input_value() != "tbc":
                raise AssertionError("TBC picker choice did not set TBC state.")
            person_picker.fill("open position")
            person_picker.press("Enter")
            if first_row.locator('[name="assignment_state"]').input_value() != "open":
                raise AssertionError("Open picker choice did not set open state.")
            person_picker.fill("cambo")
            person_picker.press("Enter")
            if first_row.locator('[name="assignee"]').input_value() != f"person:{int(person['id'])}" or person_picker.input_value() != "Campbell Stephens" or first_row.locator('[name="assignment_state"]').input_value() != "assigned":
                raise AssertionError("Keyboard alias search did not select canonical Campbell Stephens.")
            vehicle_picker = first_row.locator('[data-picker-kind="vehicle"] [data-picker-input]')
            vehicle_picker.click()
            if vehicle_picker.input_value() != "":
                raise AssertionError("Transport picker did not separate the selected label from its blank search query.")
            vehicle_picker.fill("68")
            visible_vehicle_text = first_row.locator('[data-picker-kind="vehicle"] [data-picker-menu]').inner_text()
            if "684" not in visible_vehicle_text or "685" not in visible_vehicle_text:
                raise AssertionError("Typing 68 did not immediately find 684 and 685.")
            first_row.locator('[data-picker-kind="vehicle"] [data-picker-option][data-label="685"]').click()
            if vehicle_picker.input_value() != "685" or first_row.locator('[name="vehicle_label"]').input_value() != "685":
                raise AssertionError("Selecting 685 did not display and store 685.")
            vehicle_picker.click()
            if vehicle_picker.input_value() != "":
                raise AssertionError("Previously selected transport was not cleared from the next search query.")
            vehicle_picker.fill("Rav4")
            vehicle_picker.press("Enter")
            if first_row.locator('[name="transport_mode"]').input_value() != "vehicle" or first_row.locator('[name="vehicle_label"]').input_value() != "Rav91":
                raise AssertionError("Keyboard vehicle alias search did not select canonical Rav91.")

            page.locator('[name="day_type"]').select_option("office_day")
            if page.locator('[data-general-field]:visible').count() == 0 or page.locator('[data-race-field]:visible').count() != 0:
                raise AssertionError("Day-type controls did not hide race-only fields without removing general fields.")

            initial_rows = page.locator("[data-assignment-row]").count()
            page.locator("[data-add-position]").click()
            page.locator("[data-add-attendee]").click()
            if page.locator("[data-assignment-row]").count() != initial_rows + 2:
                raise AssertionError("Compact Add position/Add attendee controls did not create editable rows.")
            last_row = page.locator("[data-assignment-row]").last
            last_row.locator("[data-assignment-advanced]").evaluate("element => { element.open = true; }")
            last_row.locator("[data-remove-row]").click()
            if page.locator("[data-assignment-row]").count() != initial_rows + 1:
                raise AssertionError("Advanced remove-position control did not remove its row.")

            for width in (375, 320):
                page.set_viewport_size({"width": width, "height": 900})
                page.reload()
                page.wait_for_selector("[data-workday-form]")
                overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
                if overflow:
                    raise AssertionError(f"Workday builder overflowed horizontally at {width}px.")
                first_row = page.locator("[data-assignment-row]").first
                box = first_row.bounding_box()
                if not box or box["x"] < 0 or box["x"] + box["width"] > width + 1:
                    raise AssertionError(f"Assignment editor did not fit the {width}px viewport: {box!r}")
                if first_row.locator("[data-assignment-advanced]").get_attribute("open") is not None:
                    raise AssertionError("Advanced assignment fields were not collapsed by default.")
                picker_input = first_row.locator('[data-picker-kind="person"] [data-picker-input]')
                picker_input.click()
                picker_menu = first_row.locator('[data-picker-kind="person"] [data-picker-menu]')
                menu_box = picker_menu.bounding_box()
                if not menu_box or menu_box["x"] < 0 or menu_box["x"] + menu_box["width"] > width + 1:
                    raise AssertionError(f"Person picker menu overflowed at {width}px: {menu_box!r}")
                picker_input.press("Escape")
                if width <= 375:
                    columns = first_row.locator(".workday-assignment-grid").evaluate(
                        "element => getComputedStyle(element).gridTemplateColumns"
                    )
                    if " " in columns.strip():
                        raise AssertionError(f"Assignment fields did not stack at {width}px: {columns!r}")
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/admin")
            page.locator(".crew-directory-panel").evaluate("element => { element.open = true; }")
            page.locator("[data-crew-search]").fill("cambo")
            visible_crew = page.locator("[data-crew-row]:visible")
            if visible_crew.count() != 1 or "Campbell Stephens" not in visible_crew.first.inner_text():
                raise AssertionError("Crew alias search did not return one canonical crew row.")
            page.locator("[data-crew-search]").fill("")
            page.locator("[data-crew-team-filter]").select_option(str(northern_team))
            if page.locator("[data-crew-row]:visible").count() != 1:
                raise AssertionError("Crew team filter did not restrict rows to Northern Team.")
            team_form = visible_crew.first.locator("[data-person-team-form]")
            team_form.locator("[data-team-input]").fill("Special Events")
            team_form.locator("[data-team-submit]").click()
            if not page.locator("[data-create-team-dialog]").is_visible():
                raise AssertionError("Inline new-team choice did not require confirmation.")
            original_url = page.url
            page.evaluate("window.scrollTo(0, 500)")
            original_scroll = page.evaluate("window.scrollY")
            page.locator("[data-confirm-create-team]").click()
            row_status = visible_crew.first.locator("[data-team-status]")
            try:
                row_status.get_by_text("Team created and assigned.", exact=True).wait_for(timeout=5_000)
            except Exception as exc:
                raise AssertionError(f"In-place team creation failed: {row_status.inner_text()!r}") from exc
            if page.url != original_url or page.locator("[data-crew-search]").input_value() or page.locator("[data-crew-team-filter]").input_value() != str(northern_team):
                raise AssertionError("In-place team creation navigated or reset Crew controls.")
            if abs(page.evaluate("window.scrollY") - original_scroll) > 2:
                raise AssertionError("In-place team creation changed Admin scroll position.")
            special_chip = visible_crew.first.locator(".team-chip", has_text="Special Events")
            if special_chip.count() != 1:
                raise AssertionError("In-place team creation did not add its chip.")
            page.once("dialog", lambda dialog: dialog.accept())
            special_chip.click()
            page.wait_for_function("![...document.querySelectorAll('.team-chip')].some((item) => item.textContent.includes('Special Events'))")
            if page.url != original_url:
                raise AssertionError("In-place team removal navigated away from Admin.")
            for width in (375, 320):
                page.set_viewport_size({"width": width, "height": 900})
                if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                    raise AssertionError(f"Compact Crew management overflowed horizontally at {width}px.")
            browser.close()
            if console_errors:
                raise AssertionError(f"Builder browser errors: {console_errors!r}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print("workday and Crew responsive smoke ok (375px, 320px)")


if __name__ == "__main__":
    main()
