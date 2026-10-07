"""Scheduling: queue recurring work, and (on a normal server) advance jobs.

* ``enqueue_due_work`` queues scheduled job searches and inbox syncs. It runs
  from the in-process scheduler on a normal server, and from
  ``/api/internal/cron`` on serverless hosts.
* On a normal server a short loop also advances background jobs. On
  serverless hosts that is done by requests (right after enqueueing, and on
  progress polls) and by the cron call instead.
"""
from __future__ import annotations

from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from config import settings
from core.logging_config import get_logger
from database.database import AsyncSessionLocal
from database.models import AIKey, EmailAccount, utcnow
from services.job_pipeline import users_due_for_scrape
from services.jobs import enqueue, run_due_jobs, start_discovery

logger = get_logger("scheduler")

INBOX_SYNC_EVERY = timedelta(minutes=60)
_JOB_LOOP_SECONDS = 5
_ENQUEUE_EVERY_MINUTES = 5

_scheduler: AsyncIOScheduler | None = None


async def enqueue_due_work() -> dict[str, int]:
    """Queue scheduled searches (users with an AI key) and inbox syncs that are due."""
    queued = {"discovery": 0, "inbox_sync": 0}
    async with AsyncSessionLocal() as session:
        due = await users_due_for_scrape(session)
        with_keys = {
            row[0] for row in (
                await session.execute(select(AIKey.user_id).where(AIKey.is_enabled.is_(True), AIKey.status == "active"))
            ).all()
        }
        accounts = (await session.execute(select(EmailAccount))).scalars().all()

    for user_id in due:
        if user_id in with_keys:
            await start_discovery(user_id, trigger="scheduled")
            queued["discovery"] += 1
    cutoff = utcnow() - INBOX_SYNC_EVERY
    for account in accounts:
        if account.last_sync_at is None or account.last_sync_at < cutoff:
            await enqueue(account.user_id, "inbox_sync", {}, "Checking inbox", dedupe_key="inbox")
            queued["inbox_sync"] += 1
    if any(queued.values()):
        logger.info("Queued scheduled work: %s", queued)
    return queued


async def _advance_jobs() -> None:
    await run_due_jobs(budget_seconds=240)


def start_scheduler() -> None:
    """Normal (always-on) server only. Serverless hosts use /api/internal/cron instead."""
    global _scheduler
    if not settings.ENABLE_SCHEDULER or settings.serverless or _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(_advance_jobs, "interval", seconds=_JOB_LOOP_SECONDS, id="jobs",
                       max_instances=1, coalesce=True)
    _scheduler.add_job(enqueue_due_work, "interval", minutes=_ENQUEUE_EVERY_MINUTES, id="enqueue",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("Scheduler started (jobs every %ds, scheduled work every %d min)", _JOB_LOOP_SECONDS, _ENQUEUE_EVERY_MINUTES)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
