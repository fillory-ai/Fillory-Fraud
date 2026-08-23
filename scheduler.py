"""Scheduled scanning and scan-health monitoring.

v0 only scanned when a human clicked a button, which means the product's core
promise — "we watch for fraud" — was only true while someone was watching. M1
makes scanning autonomous, and with that comes the obligation to notice when it
silently stops working.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import SessionLocal
from models import ScanLog
from config import (
    SCHEDULER_ENABLED,
    SCAN_INTERVAL_HOURS,
    SCAN_JITTER_MINUTES,
    SCAN_STALE_HOURS,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_last_health: dict = {"checked_at": None, "stale": False, "detail": "not yet checked"}
# Guards against a slow scan overlapping the next tick.
_scan_lock = asyncio.Lock()


def last_successful_scan(session) -> ScanLog | None:
    return (
        session.query(ScanLog)
        .filter(ScanLog.status == "completed")
        .order_by(ScanLog.started_at.desc())
        .first()
    )


def scan_health() -> dict:
    """Are we actually seeing the market right now?

    Silent failure is the worst outcome in a monitoring product: the customer
    reads "0 fraud found" as good news when it may mean the scraper is blocked.
    This is deliberately reported as a first-class status, not a log line.
    """
    session = SessionLocal()
    try:
        last_ok = last_successful_scan(session)
        last_any = (
            session.query(ScanLog).order_by(ScanLog.started_at.desc()).first()
        )
        now = datetime.now(timezone.utc)
        if last_ok is None:
            return {
                "healthy": False,
                "reason": "no successful scan on record",
                "last_success_at": None,
                "last_scan_status": last_any.status if last_any else None,
                "hours_since_success": None,
                "stale_after_hours": SCAN_STALE_HOURS,
                "enrichment_rate": None,
            }
        age_hours = (now - last_ok.started_at).total_seconds() / 3600
        healthy = age_hours <= SCAN_STALE_HOURS
        return {
            "healthy": healthy,
            "reason": "ok" if healthy else f"no successful scan in {age_hours:.1f}h",
            "last_success_at": last_ok.started_at.isoformat(),
            "last_scan_status": last_any.status if last_any else None,
            "hours_since_success": round(age_hours, 2),
            "stale_after_hours": SCAN_STALE_HOURS,
            "enrichment_rate": last_ok.enrichment_rate,
        }
    finally:
        session.close()


async def _run_scheduled_scan():
    from pipeline import run_scan  # imported late to avoid a circular import

    if _scan_lock.locked():
        logger.warning("Scheduled scan skipped: previous scan still running")
        return
    async with _scan_lock:
        jitter = random.uniform(0, SCAN_JITTER_MINUTES * 60)
        logger.info("Scheduled scan starting in %.0fs (jitter)", jitter)
        await asyncio.sleep(jitter)
        try:
            result = await run_scan(source="all", trigger="scheduled")
            logger.info("Scheduled scan finished: %s", result)
        except Exception:
            # A crash here must not kill the scheduler thread.
            logger.exception("Scheduled scan raised")


async def _health_check():
    """Escalate a stale scanner to whoever runs Fillory — never to the customer."""
    global _last_health
    health = scan_health()
    health["checked_at"] = datetime.now(timezone.utc).isoformat()
    _last_health = health
    if not health["healthy"]:
        # Loud on purpose. When there is an ops channel this becomes a page;
        # until then a persistent ERROR plus the dashboard banner is the alert.
        logger.error(
            "SCAN HEALTH DEGRADED: %s (last success %s)",
            health["reason"],
            health["last_success_at"],
        )


def scheduler_status() -> dict:
    jobs = []
    if _scheduler is not None:
        for job in _scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
            })
    return {
        "enabled": SCHEDULER_ENABLED,
        "running": bool(_scheduler and _scheduler.running),
        "interval_hours": SCAN_INTERVAL_HOURS,
        "jitter_minutes": SCAN_JITTER_MINUTES,
        "jobs": jobs,
        "last_health_check": _last_health,
    }


def start_scheduler():
    """Start background jobs. Scanning is opt-in; health checking is not.

    SCHEDULER_ENABLED defaults to false because scans spend Apify credits and a
    developer machine left running overnight should not spend them. Health
    checks run regardless — they are free and their absence is what lets a
    broken scanner go unnoticed.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _health_check,
        "interval",
        hours=1,
        id="scan_health",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    if SCHEDULER_ENABLED:
        _scheduler.add_job(
            _run_scheduled_scan,
            "interval",
            hours=SCAN_INTERVAL_HOURS,
            id="scheduled_scan",
            max_instances=1,
            coalesce=True,          # missed runs collapse into one, no stampede
            misfire_grace_time=3600,
        )
        logger.info("Scan scheduler enabled: every %sh", SCAN_INTERVAL_HOURS)
    else:
        logger.info("Scan scheduler disabled (SCHEDULER_ENABLED not set)")

    _scheduler.start()
    return _scheduler


def shutdown_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
