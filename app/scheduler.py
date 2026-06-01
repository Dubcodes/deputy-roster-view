from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings, get_settings
from .database import get_next_upcoming_shift
from .sync_ics import sync_deputy_calendar


_scheduler: BackgroundScheduler | None = None
_last_pre_shift_keys: set[str] = set()


def _now(settings: Settings) -> datetime:
    return datetime.now(settings.timezone).replace(microsecond=0)


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler:
    global _scheduler
    settings = settings or get_settings()

    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        sync_deputy_calendar,
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
    scheduler.start()
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

    result = sync_deputy_calendar(settings)
    if result.get("status") == "ok":
        for window in due_windows:
            _last_pre_shift_keys.add(str(window["sync_key"]))
    return {"ran": True, "windows": due_windows, "result": result}


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
    target_at = start_at - timedelta(minutes=settings.pre_shift_sync_minutes)
    followup_target_at = start_at - timedelta(minutes=settings.changed_followup_sync_minutes)
    primary_should_sync = target_at <= now <= start_at
    followup_should_sync = (
        bool(int(shift["changed_since_viewed"] or 0))
        and followup_target_at <= now <= start_at
    )
    primary_key = f"{shift['id']}:{shift['start_at']}:primary:{target_at.isoformat()}"
    followup_key = f"{shift['id']}:{shift['start_at']}:changed-followup:{followup_target_at.isoformat()}"
    sync_windows = [
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
    if primary_should_sync:
        reason = "inside pre-shift sync window"
    elif followup_should_sync:
        reason = "inside changed follow-up sync window"
    else:
        reason = "waiting for pre-shift window"

    return {
        "shift": dict(shift),
        "target_at": target_at.isoformat(),
        "followup_target_at": followup_target_at.isoformat(),
        "should_sync": primary_should_sync or followup_should_sync,
        "sync_key": primary_key,
        "sync_windows": sync_windows,
        "reason": reason,
    }
