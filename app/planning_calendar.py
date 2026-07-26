from __future__ import annotations

import re
from datetime import datetime, timedelta

from .config import Settings, get_settings
from .database import (
    calendar_location_key,
    claim_love_racing_detail_jobs,
    fetch_love_racing_details_between,
    fetch_shifts_between,
    finish_love_racing_detail_job,
    get_app_setting,
    get_love_racing_meeting_detail,
    list_known_racecourse_names,
    mark_love_racing_detail_fetch_failed,
    merge_love_racing_meeting_identities,
    merge_love_racing_programme,
    queue_love_racing_detail_job,
    save_love_racing_meetings,
    update_app_settings,
)
from .love_racing import fetch_love_racing_meetings, parse_love_racing_events
from .love_racing_details import (
    capture_calendar_months,
    capture_meeting_pages,
    failure_backoff,
    parse_meeting_programme,
    programme_refresh_due,
)


DETAIL_REFRESH_WINDOW_DAYS = 7


def refresh_planning_calendar(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    now = datetime.now(settings.timezone)
    checked_at = now.isoformat(timespec="seconds")
    known_locations = list_known_racecourse_names()
    try:
        result = fetch_love_racing_meetings(known_locations, today=now.date())
        for meeting in result.meetings:
            if meeting.get("meeting_id"):
                meeting["discovered_at"] = checked_at
        saved = save_love_racing_meetings(result.meetings, checked_at)
        identity_status = _discover_meeting_identities(
            result.meetings,
            known_locations,
            now,
        )
        queued = queue_due_love_racing_details(
            settings,
            now=now,
            reason="planning refresh",
            horizon_days=DETAIL_REFRESH_WINDOW_DAYS,
        )
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
                "love_racing_identity_message": identity_status["message"],
                "love_racing_detail_jobs_queued": str(queued["queued"]),
            }
        )
        return {
            "status": status,
            "message": message,
            "saved": saved,
            "identities": identity_status,
            "detail_jobs": queued,
        }
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


def _discover_meeting_identities(
    planning_meetings: list[dict[str, object]],
    known_locations: list[str],
    now: datetime,
) -> dict[str, object]:
    months = sorted(
        {
            (meeting_day.year, meeting_day.month)
            for meeting in planning_meetings
            if (meeting_day := _date_from_text(meeting.get("date"))) is not None
        }
    )
    if not months:
        return {"matched": 0, "ambiguous": 0, "message": "No meeting months required identity discovery."}
    try:
        events = capture_calendar_months(months)
        discovered = parse_love_racing_events(events, known_locations, today=now.date())
        for meeting in discovered:
            meeting["discovered_at"] = now.isoformat(timespec="seconds")
        counts = merge_love_racing_meeting_identities(
            discovered,
            now.isoformat(timespec="seconds"),
        )
        return {
            **counts,
            "message": (
                f"Resolved {counts['matched']} official meeting IDs"
                + (f"; {counts['ambiguous']} ambiguous." if counts["ambiguous"] else ".")
            ),
        }
    except Exception as exc:
        return {
            "matched": 0,
            "ambiguous": 0,
            "message": f"Meeting ID discovery deferred: {type(exc).__name__}: {str(exc)[:240]}",
        }


def queue_due_love_racing_details(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    reason: str = "scheduled refresh",
    horizon_days: int = DETAIL_REFRESH_WINDOW_DAYS,
    manual: bool = False,
    meeting_ids: set[str] | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    now = (now or datetime.now(settings.timezone)).replace(microsecond=0)
    rows = fetch_love_racing_details_between(
        now.date().isoformat(),
        (now.date() + timedelta(days=max(1, horizon_days))).isoformat(),
    )
    queued = 0
    eligible = 0
    for row in rows:
        detail = dict(row)
        meeting_id = str(detail.get("meeting_id") or "")
        if meeting_ids is not None and meeting_id not in meeting_ids:
            continue
        due, due_reason = programme_refresh_due(detail, now, manual=manual)
        if not due:
            continue
        eligible += 1
        priority = _detail_priority(detail, now, manual)
        if queue_love_racing_detail_job(
            meeting_id,
            reason=f"{reason}: {due_reason}",
            priority=priority,
            requested_at=now.isoformat(timespec="seconds"),
        ):
            queued += 1
    return {"eligible": eligible, "queued": queued, "manual": manual}


def queue_love_racing_after_roster_sync(
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    now = (now or datetime.now(settings.timezone)).replace(microsecond=0)
    end = now + timedelta(hours=72)
    shift_keys: set[tuple[str, str]] = set()
    for row in fetch_shifts_between(now.date().isoformat(), end.date().isoformat(), owner_user_id=None):
        title = str(row["title"] or "")
        match = re.match(r"^\[T-([^\]]+)\]", title, re.IGNORECASE)
        if not match:
            continue
        label = match.group(1).strip()
        if calendar_location_key(label) == "cambridge":
            label = "Cambridge Synthetic"
        shift_keys.add((str(row["date"]), calendar_location_key(label)))
    if not shift_keys:
        return {"eligible": 0, "queued": 0}
    meeting_ids = {
        str(row["meeting_id"])
        for row in fetch_love_racing_details_between(
            now.date().isoformat(),
            end.date().isoformat(),
        )
        if (str(row["meeting_date"]), str(row["canonical_venue_key"])) in shift_keys
    }
    return queue_due_love_racing_details(
        settings,
        now=now,
        reason="roster sync",
        horizon_days=3,
        meeting_ids=meeting_ids,
    )


def run_love_racing_detail_jobs(
    settings: Settings | None = None,
    *,
    limit: int = 3,
) -> dict[str, object]:
    settings = settings or get_settings()
    now = datetime.now(settings.timezone).replace(microsecond=0)
    jobs = claim_love_racing_detail_jobs(now.isoformat(timespec="seconds"), limit=limit)
    if not jobs:
        return {"processed": 0, "completed": 0, "failed": 0}
    try:
        captured_pages = capture_meeting_pages(jobs)
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        captured_pages = [
            {
                "meeting_id": str(job["meeting_id"]),
                "html": "",
                "error": error,
            }
            for job in jobs
        ]
    pages = {
        str(result["meeting_id"]): result
        for result in captured_pages
    }
    completed = 0
    failed = 0
    for job in jobs:
        meeting_id = str(job["meeting_id"])
        page = pages.get(meeting_id, {})
        error = str(page.get("error") or "")
        finished_at = datetime.now(settings.timezone).replace(microsecond=0)
        if error or not page.get("html"):
            detail = get_love_racing_meeting_detail(meeting_id)
            failure_count = int(detail["failure_count"] or 0) + 1 if detail else 1
            next_retry = finished_at + failure_backoff(failure_count)
            mark_love_racing_detail_fetch_failed(
                meeting_id,
                failed_at=finished_at.isoformat(timespec="seconds"),
                error_summary=error or "Meeting page returned no HTML.",
                next_retry_at=next_retry.isoformat(timespec="seconds"),
            )
            finish_love_racing_detail_job(
                int(job["id"]),
                status="failed",
                completed_at=finished_at.isoformat(timespec="seconds"),
                last_error=error or "Meeting page returned no HTML.",
                next_attempt_at=next_retry.isoformat(timespec="seconds"),
            )
            failed += 1
            continue
        parsed = parse_meeting_programme(str(page["html"]))
        merge_love_racing_programme(
            meeting_id,
            {
                "lifecycle_status": parsed.lifecycle_status,
                "races": parsed.races,
                "race_count": parsed.race_count,
                "first_race_time": parsed.first_race_time,
                "last_race_time": parsed.last_race_time,
                "diagnostics": parsed.diagnostics,
                "content_hash": parsed.content_hash,
            },
            finished_at.isoformat(timespec="seconds"),
        )
        finish_love_racing_detail_job(
            int(job["id"]),
            status="completed",
            completed_at=finished_at.isoformat(timespec="seconds"),
        )
        completed += 1
    return {"processed": len(jobs), "completed": completed, "failed": failed}


def refresh_upcoming_race_times(
    settings: Settings | None = None,
    *,
    manual: bool = False,
) -> dict[str, object]:
    settings = settings or get_settings()
    queued = queue_due_love_racing_details(
        settings,
        reason="admin refresh" if manual else "scheduled refresh",
        manual=manual,
    )
    processed = run_love_racing_detail_jobs(settings)
    return {**queued, **processed}


def _detail_priority(detail: dict[str, object], now: datetime, manual: bool) -> int:
    if manual:
        return 100
    meeting_day = _date_from_text(detail.get("meeting_date"))
    if meeting_day is None:
        return 0
    days = (meeting_day - now.date()).days
    if days <= 0:
        return 80
    if days == 1:
        return 60
    if days <= 3:
        return 40
    return 10


def _date_from_text(value: object):
    try:
        return datetime.fromisoformat(str(value or "")[:10]).date()
    except ValueError:
        return None
