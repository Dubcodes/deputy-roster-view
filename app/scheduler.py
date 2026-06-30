from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings, get_settings
from .database import (
    get_calendar_url,
    get_due_user_syncs,
    get_next_upcoming_shift,
    has_deputy_schedule_changes_for_date,
    list_syncable_app_users,
    mark_user_sync_finished,
    mark_user_sync_started,
    set_user_next_sync,
    write_sync_log,
)
from .deputy_web import sync_deputy_web_schedule
from .planning_calendar import refresh_planning_calendar
from .sync_ics import sync_deputy_calendar
from .user_credentials import settings_for_user


_scheduler: BackgroundScheduler | None = None
_last_pre_shift_keys: set[str] = set()
_user_sync_runner_lock = threading.Lock()


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(microsecond=0)


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler:
    global _scheduler
    settings = settings or get_settings()

    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        daily_sync_dispatch,
        trigger=CronTrigger(hour=settings.sync_at_hour, minute=0, timezone=settings.timezone),
        id="daily_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        check_pre_shift_sync,
        trigger=IntervalTrigger(minutes=10, timezone=settings.timezone),
        id="pre_shift_sync_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_due_user_syncs,
        trigger=IntervalTrigger(minutes=5, timezone=settings.timezone),
        id="staggered_user_sync_runner",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        refresh_planning_calendar,
        trigger=CronTrigger(day_of_week="mon", hour=4, minute=30, timezone=settings.timezone),
        id="weekly_planning_calendar",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    plan_staggered_user_syncs(settings, reason="startup", start_at=_now(settings) + timedelta(minutes=1))
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def check_pre_shift_sync(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    status = get_pre_shift_status(settings)
    due_windows = [
        window
        for window in status["sync_windows"]
        if window["should_sync"] and window["sync_key"] not in _last_pre_shift_keys
    ]
    if not due_windows:
        return {"ran": False, "reason": status["reason"]}

    if list_syncable_app_users():
        planned = plan_staggered_user_syncs(settings, reason=status["reason"], start_at=_now(settings))
        for window in due_windows:
            _last_pre_shift_keys.add(str(window["sync_key"]))
        return {"ran": True, "planned": planned, "windows": due_windows}

    result = sync_roster_sources(settings)
    calendar_result = result.get("calendar") if isinstance(result.get("calendar"), dict) else {}
    if calendar_result.get("status") == "ok":
        for window in due_windows:
            _last_pre_shift_keys.add(str(window["sync_key"]))
    return {"ran": True, "windows": due_windows, "result": result}


def daily_sync_dispatch(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    if list_syncable_app_users():
        return plan_staggered_user_syncs(settings, reason="daily")
    return sync_roster_sources(settings)


def plan_staggered_user_syncs(
    settings: Settings | None = None,
    *,
    reason: str,
    start_at: datetime | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    users = list_syncable_app_users()
    if not users:
        return {"planned": 0, "reason": "no users with saved Deputy credentials"}

    start_at = (start_at or _now(settings)).replace(microsecond=0)
    stagger_minutes = max(1, settings.user_sync_stagger_minutes)
    jitter_minutes = max(0, settings.user_sync_jitter_minutes)
    planned_times: list[str] = []

    for index, user in enumerate(users):
        jitter = _stable_jitter_minutes(int(user["id"]), start_at.date().isoformat(), reason, jitter_minutes)
        next_sync = start_at + timedelta(minutes=(index * stagger_minutes) + jitter)
        next_sync_text = next_sync.isoformat()
        set_user_next_sync(int(user["id"]), next_sync_text, reason)
        planned_times.append(next_sync_text)

    return {
        "planned": len(users),
        "reason": reason,
        "first_at": planned_times[0],
        "last_at": planned_times[-1],
        "stagger_minutes": stagger_minutes,
        "jitter_minutes": jitter_minutes,
    }


def run_due_user_syncs(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    if not _user_sync_runner_lock.acquire(blocking=False):
        return {"ran": False, "reason": "user sync runner already active"}

    try:
        now = _now(settings).isoformat()
        due_users = get_due_user_syncs(now, limit=max(1, settings.user_sync_batch_size))
        results = []
        for user in due_users:
            user_id = int(user["id"])
            started_at = _now(settings).isoformat()
            if not mark_user_sync_started(user_id, started_at):
                continue
            try:
                summary = sync_roster_sources(settings, user_id=user_id)
                status = "ok" if summary.get("status") == "ok" else "error"
                message = sync_summary_message(summary)
            except Exception as exc:
                status = "error"
                message = f"User sync failed: {exc.__class__.__name__}."
            finished_at = _now(settings).isoformat()
            mark_user_sync_finished(
                user_id,
                finished_at=finished_at,
                status=status,
                message=message,
            )
            results.append({"user_id": user_id, "status": status, "message": message})
        return {"ran": bool(results), "count": len(results), "results": results}
    finally:
        _user_sync_runner_lock.release()


def sync_roster_sources(settings: Settings | None = None, user_id: int | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    runtime_settings = settings
    credential_error = ""
    if user_id is not None:
        try:
            user_settings = settings_for_user(user_id, settings)
            if user_settings is None:
                credential_error = "Saved Deputy login is missing for this user."
            else:
                runtime_settings = user_settings
        except Exception as exc:
            credential_error = f"Saved Deputy login could not be decrypted: {exc.__class__.__name__}."

    started_at = _now(settings).isoformat()
    if credential_error:
        web_result = {
            "status": "error",
            "message": credential_error,
            "saved_own_shift_rows": 0,
            "saved_schedule_rows": 0,
            "payload": {},
        }
    else:
        web_result = sync_deputy_web_schedule(runtime_settings, owner_user_id=user_id)

    calendar_result = _skipped_calendar_result("iCal backup feed is not configured.")
    calendar_settings = runtime_settings if user_id is not None else settings
    calendar_url = calendar_settings.deputy_ical_url.strip() if user_id is not None else get_calendar_url(settings)
    if calendar_url:
        calendar_result = sync_deputy_calendar(
            calendar_settings,
            owner_user_id=user_id,
            calendar_url=calendar_url,
        )

    status = _combined_sync_status(calendar_result, web_result)
    finished_at = _now(settings).isoformat()
    if calendar_result.get("status") == "skipped":
        write_sync_log(
            {
                "started_at": started_at,
                "finished_at": finished_at,
                "status": status,
                "message": sync_summary_message({"status": status, "calendar": calendar_result, "web": web_result}),
                "events_seen": 0,
                "events_created": 0,
                "events_updated": 0,
                "events_marked_deleted": 0,
            }
        )

    return {
        "status": status,
        "calendar": calendar_result,
        "web": web_result,
        "user_id": user_id,
    }


def sync_summary_message(summary: dict[str, object]) -> str:
    calendar_result = summary.get("calendar") if isinstance(summary.get("calendar"), dict) else {}
    web_result = summary.get("web") if isinstance(summary.get("web"), dict) else {}
    parts = []
    if calendar_result.get("status") == "ok":
        parts.append(
            "iCal roster: "
            f"{calendar_result.get('events_created', 0)} new, "
            f"{calendar_result.get('events_updated', 0)} changed, "
            f"{calendar_result.get('events_marked_deleted', 0)} cancelled."
        )
    elif calendar_result.get("status") == "skipped":
        parts.append(str(calendar_result.get("message") or "iCal skipped."))
    elif calendar_result:
        parts.append(str(calendar_result.get("message") or "iCal sync failed."))

    if web_result.get("status") == "ok":
        parts.append(
            "Deputy web capture saved "
            f"{web_result.get('saved_own_shift_rows', 0)} roster rows and "
            f"{web_result.get('saved_schedule_rows', 0)} schedule rows."
        )
    elif web_result.get("status") == "skipped":
        parts.append(str(web_result.get("message") or "Deputy web capture skipped."))
    elif web_result:
        parts.append(str(web_result.get("message") or "Deputy web capture failed."))

    message = " ".join(part for part in parts if part).strip()
    if message:
        return message
    return "No sync source ran. Add a Deputy login or backup iCal URL."


def _combined_sync_status(calendar_result: dict[str, object], web_result: dict[str, object]) -> str:
    if calendar_result.get("status") == "ok" or web_result.get("status") == "ok":
        return "ok"
    return "error"


def _skipped_calendar_result(message: str) -> dict[str, object]:
    return {
        "status": "skipped",
        "message": message,
        "events_seen": 0,
        "events_created": 0,
        "events_updated": 0,
        "events_marked_deleted": 0,
    }


def _shift_schedule_location_ids(shift: object) -> list[int]:
    try:
        source_payload = str(shift["source_payload"] or "")  # type: ignore[index]
    except (KeyError, TypeError):
        return []
    if not source_payload:
        return []
    try:
        payload = json.loads(source_payload)
    except (TypeError, ValueError):
        return []
    normalised = payload.get("normalised", {}) if isinstance(payload, dict) else {}
    if not isinstance(normalised, dict):
        return []
    location_id = normalised.get("area_location_id")
    try:
        return [int(location_id)] if location_id not in (None, "") else []
    except (TypeError, ValueError):
        return []


def _stable_jitter_minutes(user_id: int, date_seed: str, reason: str, jitter_minutes: int) -> int:
    if jitter_minutes <= 0:
        return 0
    digest = hashlib.sha256(f"{date_seed}:{reason}:{user_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (jitter_minutes + 1)


def get_pre_shift_status(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    now = _now(settings)
    shift = get_next_upcoming_shift(now.isoformat())
    if shift is None:
        return {
            "shift": None,
            "target_at": None,
            "followup_target_at": None,
            "should_sync": False,
            "sync_key": None,
            "sync_windows": [],
            "reason": "no upcoming shift",
        }

    start_at = datetime.fromisoformat(shift["start_at"])
    early_target_at = start_at - timedelta(hours=settings.early_pre_shift_sync_hours)
    target_at = start_at - timedelta(minutes=settings.pre_shift_sync_minutes)
    followup_target_at = start_at - timedelta(minutes=settings.changed_followup_sync_minutes)
    early_should_sync = early_target_at <= now <= start_at
    primary_should_sync = target_at <= now <= start_at
    crew_schedule_changed = has_deputy_schedule_changes_for_date(
        str(shift["date"]),
        location_ids=_shift_schedule_location_ids(shift),
    )
    followup_should_sync = (
        (bool(int(shift["changed_since_viewed"] or 0)) or crew_schedule_changed)
        and followup_target_at <= now <= start_at
    )
    early_key = f"{shift['id']}:{shift['start_at']}:early:{early_target_at.isoformat()}"
    primary_key = f"{shift['id']}:{shift['start_at']}:primary:{target_at.isoformat()}"
    followup_key = f"{shift['id']}:{shift['start_at']}:changed-followup:{followup_target_at.isoformat()}"
    sync_windows = [
        {
            "name": "12-hour pre-shift",
            "target_at": early_target_at.isoformat(),
            "should_sync": early_should_sync,
            "sync_key": early_key,
        },
        {
            "name": "pre-shift",
            "target_at": target_at.isoformat(),
            "should_sync": primary_should_sync,
            "sync_key": primary_key,
        },
        {
            "name": "changed follow-up",
            "target_at": followup_target_at.isoformat(),
            "should_sync": followup_should_sync,
            "sync_key": followup_key,
        },
    ]
    if followup_should_sync:
        reason = "inside changed follow-up sync window"
    elif primary_should_sync:
        reason = "inside pre-shift sync window"
    elif early_should_sync:
        reason = "inside 12-hour pre-shift sync window"
    else:
        reason = "waiting for pre-shift window"

    return {
        "shift": dict(shift),
        "early_target_at": early_target_at.isoformat(),
        "target_at": target_at.isoformat(),
        "followup_target_at": followup_target_at.isoformat(),
        "crew_schedule_changed": crew_schedule_changed,
        "should_sync": early_should_sync or primary_should_sync or followup_should_sync,
        "sync_key": early_key,
        "sync_windows": sync_windows,
        "reason": reason,
    }
