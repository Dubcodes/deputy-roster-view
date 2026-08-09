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

    from app.database import init_db
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
            page.goto(f"http://127.0.0.1:{port}/admin/roster-days/new")
            if "Build a work day" not in page.locator("body").inner_text():
                raise AssertionError("Workday builder did not render in browser smoke.")

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

            for width in (1280, 430, 375, 320):
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
                if width <= 375:
                    columns = first_row.locator(".workday-assignment-grid").evaluate(
                        "element => getComputedStyle(element).gridTemplateColumns"
                    )
                    if " " in columns.strip():
                        raise AssertionError(f"Assignment fields did not stack at {width}px: {columns!r}")
            for width in (1280, 430, 375, 320):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"http://127.0.0.1:{port}/month")
                if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                    raise AssertionError(f"Month/header layout overflowed horizontally at {width}px.")
                if width <= 430:
                    if page.locator(".brand-meta-name-full").is_visible() or not page.locator(".brand-meta-name-short").is_visible():
                        raise AssertionError(f"Compact mobile identity did not replace the full header name at {width}px.")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print("workday responsive smoke ok (1280px, 430px, 375px, 320px)")


if __name__ == "__main__":
    main()
