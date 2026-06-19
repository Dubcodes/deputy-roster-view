from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def configure_test_environment() -> Path:
    sys.path.insert(0, str(ROOT_DIR))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-route-smoke-"))
    os.environ["DATA_DIR"] = str(temp_dir)
    os.environ["DB_PATH"] = str(temp_dir / "route_smoke.sqlite3")
    os.environ["APP_SECRET_KEY"] = "route-smoke-secret"
    os.environ["SIGNUP_ENABLED"] = "true"
    os.environ["COOKIE_SECURE"] = "false"
    return temp_dir


def assert_redirect(response, expected_fragment: str) -> None:
    location = response.headers.get("location", "")
    if response.status_code != 303 or expected_fragment not in location:
        raise AssertionError(f"Expected redirect containing {expected_fragment!r}, got {response.status_code} {location!r}")


def main() -> None:
    configure_test_environment()

    from fastapi.testclient import TestClient

    from app.database import create_app_user, get_app_user_by_email, init_db
    from app.main import app
    from app.security import encrypt_text, hash_pin

    init_db()
    client = TestClient(app)

    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://bb12c621103108.au.deputy.com/#/",
            "deputy_email": "admin@example.com",
            "deputy_password": "initial-password",
            "pin": "1234",
            "pin_confirm": "1234",
            "next_url": "/settings",
        },
        follow_redirects=False,
    )
    assert_redirect(signup, "/settings")

    settings_save = client.post(
        "/settings/deputy-login",
        data={
            "deputy_web_url": "https://bb12c621103108.au.deputy.com/#/",
            "deputy_email": "admin@example.com",
            "deputy_password": "changed-password",
        },
        follow_redirects=False,
    )
    assert_redirect(settings_save, "Deputy+login+updated")

    report = client.post(
        "/settings/error-report",
        data={"report_text": "Route smoke report", "page_url": "/month"},
        follow_redirects=False,
    )
    assert_redirect(report, "Error+report+saved")

    other = create_app_user(
        deputy_email="crew@example.com",
        display_name="Crew User",
        pin_hash=hash_pin("1111"),
        deputy_web_url="https://bb12c621103108.au.deputy.com/#/",
        encrypted_email=encrypt_text("crew@example.com"),
        encrypted_password=encrypt_text("old-password"),
    )
    admin_save = client.post(
        f"/admin/users/{int(other['id'])}/deputy-login",
        data={
            "deputy_web_url": "https://bb12c621103108.au.deputy.com/#/",
            "deputy_email": "crew@example.com",
            "deputy_password": "new-password",
        },
        follow_redirects=False,
    )
    assert_redirect(admin_save, "Deputy+login+updated")

    if get_app_user_by_email("crew@example.com") is None:
        raise AssertionError("Expected admin-created crew user to remain queryable.")

    print("route smoke flows ok")


if __name__ == "__main__":
    main()
