from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings, get_settings
from .database import get_next_upcoming_shift
from .sync_ics import sync_deputy_calendar


_scheduler: BackgroundScheduler | None = None
_last_pre_shift_key: str | None = None


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
    global _last_pre_shift_key
    settings = settings or get_settings()
    status = get_pre_shift_status(settings)
    if not status["should_sync"]:
        return {"ran": False, "reason": status["reason"]}

    key = str(status["sync_key"])
    if _last_pre_shift_key == key:
        return {"ran": False, "reason": "already synced for this shift window"}

    result = sync_deputy_calendar(settings)
    if result.get("status") == "ok":
        _last_pre_shift_key = key
    return {"ran": True, "result": result}


def get_pre_shift_status(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    now = _now(settings)
    shift = get_next_upcoming_shift(now.isoformat())
    if shift is None:
        return {
            "shift": None,
            "target_at": None,
            "should_sync": False,
            "sync_key": None,
            "reason": "no upcoming shift",
        }

    start_at = datetime.fromisoformat(shift["start_at"])
    target_at = start_at - timedelta(minutes=settings.pre_shift_sync_minutes)
    should_sync = target_at <= now <= start_at
    return {
        "shift": dict(shift),
        "target_at": target_at.isoformat(),
        "should_sync": should_sync,
        "sync_key": f"{shift['id']}:{shift['start_at']}:{target_at.isoformat()}",
        "reason": "inside pre-shift window" if should_sync else "waiting for pre-shift window",
    }
