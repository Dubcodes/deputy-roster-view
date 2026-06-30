from __future__ import annotations

from datetime import datetime

from .config import Settings, get_settings
from .database import (
    get_app_setting,
    list_known_racecourse_names,
    save_love_racing_meetings,
    update_app_settings,
)
from .love_racing import fetch_love_racing_meetings


def refresh_planning_calendar(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    now = datetime.now(settings.timezone)
    checked_at = now.isoformat(timespec="seconds")
    known_locations = list_known_racecourse_names()
    try:
        result = fetch_love_racing_meetings(known_locations, today=now.date())
        saved = save_love_racing_meetings(result.meetings, checked_at)
        status = "ok" if saved else "empty"
        message = result.message if saved else (
            "The planning calendar was checked, but no future race days matched known worked locations. "
            f"It contained {result.fetched_rows} thoroughbred meetings."
        )
        update_app_settings(
            {
                "love_racing_last_status": status,
                "love_racing_last_sync_at": checked_at if saved else get_app_setting("love_racing_last_sync_at", ""),
                "love_racing_last_checked_at": checked_at,
                "love_racing_last_message": message,
                "love_racing_last_error": "",
                "love_racing_last_fetched_rows": str(result.fetched_rows),
                "love_racing_last_matched_rows": str(result.matched_rows),
                "love_racing_last_saved_rows": str(saved),
                "love_racing_last_known_locations": str(len(known_locations)),
                "love_racing_last_source_url": result.source_url,
                "love_racing_last_status_code": str(result.status_code),
                "love_racing_last_content_length": str(result.content_length),
                "love_racing_last_attempts": " | ".join(result.attempts),
            }
        )
        return {"status": status, "message": message, "saved": saved}
    except Exception as exc:
        error_detail = f"{type(exc).__name__}: {str(exc) or '(no message)'}"
        update_app_settings(
            {
                "love_racing_last_status": "error",
                "love_racing_last_checked_at": checked_at,
                "love_racing_last_message": "Planning calendar scan failed.",
                "love_racing_last_error": error_detail[:500],
                "love_racing_last_fetched_rows": "0",
                "love_racing_last_matched_rows": "0",
                "love_racing_last_saved_rows": "0",
                "love_racing_last_known_locations": str(len(known_locations)),
                "love_racing_last_source_url": "",
                "love_racing_last_status_code": "",
                "love_racing_last_content_length": "",
                "love_racing_last_attempts": " | ".join(getattr(exc, "attempts", ()) or ()),
            }
        )
        return {
            "status": "error",
            "message": "Planning calendar scan failed. See Planning Race Days for details.",
            "error": error_detail,
            "saved": 0,
        }
