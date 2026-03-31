"""
Background scheduler for daily pipeline execution.

Uses APScheduler's BackgroundScheduler (runs in a thread, no separate process).
max_instances=1 prevents overlapping runs in the same process.

Enabled/disabled and scheduled time are controlled by YAML config:
  scheduler:
    enabled: true
    daily_time_utc: "06:00"
    publish_telegram_by_default: false

If scheduler.enabled is false (the default), nothing is started.

Scheduled runs use run_date = today - 1 day (i.e. yesterday).
The job fires in the morning of the current day to summarise content
from the previous calendar day. Manual/admin-triggered runs always
use the explicitly requested date.

The scheduler is started from the FastAPI lifespan in app/main.py.
It must be shut down cleanly on app shutdown (also in lifespan).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_scheduled_job(daily_time_utc: str) -> None:
    """
    Job function called by APScheduler.
    Creates its own DB session and runs the daily pipeline for today.
    """
    from app.config import settings
    from app.database import SessionLocal
    from app.orchestration.service import run_daily_pipeline

    # Scheduled runs generate yesterday's digest — the job fires in the morning
    # of the current day, but the content being summarised is from the day before.
    run_date = date.today() - timedelta(days=1)
    logger.info("Scheduled daily pipeline starting for date=%s (yesterday)", run_date)
    db = SessionLocal()
    try:
        summary = run_daily_pipeline(
            db=db,
            run_date=run_date,
            trigger_type="scheduled",
            publish_telegram=None,  # use scheduler.publish_telegram_by_default
            cfg=settings,
        )
        logger.info(
            "Scheduled daily pipeline finished status=%s run_id=%s",
            summary["status"],
            summary["pipeline_run_id"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Scheduled daily pipeline error: %s", exc)
    finally:
        db.close()


def start_scheduler(cfg: Settings) -> None:
    """
    Start the background scheduler if scheduler.enabled=true.
    Safe to call multiple times — only starts once.
    """
    global _scheduler

    if not cfg.scheduler.enabled:
        logger.info("Scheduler disabled (scheduler.enabled=false)")
        return

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running; not starting again")
        return

    # Parse HH:MM
    try:
        hour_str, minute_str = cfg.scheduler.daily_time_utc.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
    except (ValueError, AttributeError):
        logger.error(
            "Invalid scheduler.daily_time_utc=%r; expected HH:MM format",
            cfg.scheduler.daily_time_utc,
        )
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_scheduled_job,
        trigger="cron",
        hour=hour,
        minute=minute,
        kwargs={"daily_time_utc": cfg.scheduler.daily_time_utc},
        id="daily_pipeline",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,  # allow up to 1h late firing (e.g. after restart)
    )
    _scheduler.start()
    logger.info(
        "Scheduler started; daily_pipeline scheduled at %02d:%02d UTC",
        hour, minute,
    )


def stop_scheduler() -> None:
    """Shut down the scheduler cleanly on app shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None
