"""Durable background jobs that run in bounded steps.

Why: on serverless hosts (Vercel) there is no long-lived process - every piece
of work must finish inside one invocation (300s on Hobby). So long work is a
row in ``background_jobs`` that any process can advance a step at a time:

* the request that enqueued it (FastAPI background task, right after the response),
* the frontend's progress polling (which kicks a step when one is due),
* a cron call (``/api/internal/cron``), or
* the in-process loop when running as a normal server (local / Docker).

Each step holds a short lease on the row, so two processes never run the
same job at once. When every API key is rate-limited the job is *deferred*
(``run_after`` = when the first key frees up) instead of a process sleeping;
once the user's maximum wait has passed, it continues with the non-AI fallback.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, or_, select, update

from core.logging_config import get_logger
from database.database import AsyncSessionLocal
from database.models import Application, BackgroundJob, utcnow
from utils.llm_keys import DeferJob, JobWaitPolicy, reset_job_policy, set_job_policy, use_user_keys

logger = get_logger("jobs")

# Each step must finish well inside the platform limit (Vercel Hobby: 300s).
STEP_BUDGET_SECONDS = float(os.environ.get("JOB_STEP_BUDGET_SECONDS", "240"))
LEASE_SECONDS = STEP_BUDGET_SECONDS + 90
MAX_ATTEMPTS = 4
ACTIVE = ("queued", "running", "waiting")


@dataclass
class StepResult:
    status: str                       # "done" | "continue" | "defer"
    payload: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    run_after: float | None = None    # epoch, for "defer"


Handler = Callable[[BackgroundJob, dict[str, Any], float], Awaitable[StepResult]]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str):
    def register(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn
    return register


def _naive(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enqueue / inspect
# ---------------------------------------------------------------------------


async def enqueue(user_id: str, kind: str, payload: dict[str, Any], message: str = "Queued", dedupe_key: str | None = None) -> BackgroundJob:
    """Add a job; an active job with the same ``dedupe_key`` is returned instead of a duplicate."""
    async with AsyncSessionLocal() as db:
        if dedupe_key:
            existing = (
                await db.execute(
                    select(BackgroundJob).where(
                        BackgroundJob.user_id == user_id, BackgroundJob.kind == kind,
                        BackgroundJob.status.in_(ACTIVE),
                    )
                )
            ).scalars().all()
            for job in existing:
                if (job.payload or {}).get("dedupe_key") == dedupe_key:
                    return job
        job = BackgroundJob(user_id=user_id, kind=kind, payload={**payload, "dedupe_key": dedupe_key},
                            message=message, run_after=utcnow())
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job


async def active_job(user_id: str, kind: str, dedupe_key: str | None = None) -> BackgroundJob | None:
    async with AsyncSessionLocal() as db:
        jobs = (
            await db.execute(
                select(BackgroundJob)
                .where(BackgroundJob.user_id == user_id, BackgroundJob.kind == kind, BackgroundJob.status.in_(ACTIVE))
                .order_by(BackgroundJob.created_at.desc())
            )
        ).scalars().all()
    for job in jobs:
        if dedupe_key is None or (job.payload or {}).get("dedupe_key") == dedupe_key:
            return job
    return None


def job_view(job: BackgroundJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    wait = 0
    if job.status == "waiting" and job.run_after:
        wait = max(0, round((job.run_after.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()))
    return {"id": job.id, "kind": job.kind, "status": job.status, "message": job.message, "waiting_seconds": wait}


async def has_due_work(user_id: str | None = None) -> bool:
    now = utcnow()
    async with AsyncSessionLocal() as db:
        query = select(BackgroundJob.id).where(_due_clause(now)).limit(1)
        if user_id:
            query = query.where(BackgroundJob.user_id == user_id)
        return (await db.execute(query)).first() is not None


def _due_clause(now: datetime):
    claimable = or_(BackgroundJob.locked_until.is_(None), BackgroundJob.locked_until < now)
    return and_(
        claimable,
        or_(
            and_(BackgroundJob.status.in_(("queued", "waiting")), BackgroundJob.run_after <= now),
            BackgroundJob.status == "running",  # a step whose process died: lease expired
        ),
    )


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


async def _claim(user_id: str | None) -> BackgroundJob | None:
    now = utcnow()
    async with AsyncSessionLocal() as db:
        query = select(BackgroundJob.id).where(_due_clause(now)).order_by(BackgroundJob.run_after).limit(5)
        if user_id:
            query = query.where(BackgroundJob.user_id == user_id)
        candidates = [row[0] for row in (await db.execute(query)).all()]
        for job_id in candidates:
            # Compare-and-set on the lease: only one process wins each job.
            result = await db.execute(
                update(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    or_(BackgroundJob.locked_until.is_(None), BackgroundJob.locked_until < now),
                )
                .values(locked_until=now + timedelta(seconds=LEASE_SECONDS), status="running",
                        attempts=BackgroundJob.attempts + 1)
            )
            await db.commit()
            if result.rowcount == 1:
                return await db.get(BackgroundJob, job_id)
    return None


async def run_due_jobs(budget_seconds: float = STEP_BUDGET_SECONDS, user_id: str | None = None) -> int:
    """Run due job steps until the time budget is spent. Returns steps run."""
    deadline = time.monotonic() + budget_seconds
    steps = 0
    while time.monotonic() < deadline - 20:
        job = await _claim(user_id)
        if job is None:
            break
        await _run_step(job, deadline)
        steps += 1
    return steps


async def _run_step(job: BackgroundJob, deadline: float) -> None:
    fn = _HANDLERS.get(job.kind)
    payload = dict(job.payload or {})
    status, message, run_after, error = "failed", f"Unknown job kind {job.kind}", None, None

    if fn is not None:
        try:
            async with use_user_keys(job.user_id) as keyring:
                token = set_job_policy(JobWaitPolicy(allow_fallback=bool(payload.get("allow_fallback"))))
                try:
                    result = await fn(job, payload, deadline)
                finally:
                    reset_job_policy(token)
                payload = result.payload or payload
                message = result.message
                if result.status == "done":
                    status = "done"
                elif result.status == "continue":
                    status, run_after = "queued", time.time()
                else:  # defer: every key rate-limited
                    until = result.run_after or time.time() + 30
                    max_wait = NO_FALLBACK_WAIT_SECONDS if job.kind in _NO_FALLBACK else keyring.max_wait_seconds
                    status, run_after = _defer(payload, max_wait, until)
                    if status == "queued":
                        message = _fallback_message(payload, keyring, until, max_wait)
                        logger.warning("Job %s (%s): %s", job.id, job.kind, message)
        except DeferJob as exc:  # raised outside a handler's own handling
            status, run_after = _defer(payload, 900, exc.until)
            message = f"Waiting for your API rate limit ({exc.reason})"
        except Exception as exc:
            logger.exception("Job %s (%s) step failed", job.id, job.kind)
            error = str(exc)[:500]
            if job.attempts < MAX_ATTEMPTS:
                status, run_after, message = "queued", time.time() + 30 * job.attempts, f"Retrying after an error: {error[:120]}"
            else:
                status, message = "failed", f"Failed: {error[:200]}"

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(BackgroundJob).where(BackgroundJob.id == job.id).values(
                status=status, payload=payload, message=message[:500] if message else None, error=error,
                run_after=_naive(run_after) if run_after else utcnow(), locked_until=None, updated_at=utcnow(),
            )
        )
        await db.commit()
    if status == "failed" and job.kind in _FAILURE_HOOKS:
        await _FAILURE_HOOKS[job.kind](job, message)


def _defer(payload: dict[str, Any], max_wait_seconds: float, until: float) -> tuple[str, float]:
    """Reschedule for when a key frees up; after the user's max wait, allow the fallback."""
    first = payload.setdefault("first_wait_at", time.time())
    if time.time() - first + (until - time.time()) > max_wait_seconds:
        payload["allow_fallback"] = True
        return "queued", time.time()
    return "waiting", until


def _fallback_message(payload: dict[str, Any], keyring, until: float, max_wait: float) -> str:
    """Say *why* AI is being skipped - a daily quota reset hours away looks very different from a short wait."""
    waited = time.time() - payload.get("first_wait_at", time.time())
    reasons = sorted({k.cooldown_reason for k in keyring.keys if k.usable and k.cooldown_reason})
    why = f" ({', '.join(reasons)})" if reasons else ""
    if waited < 60:
        return (f"Your API keys are rate-limited for {_human(until - time.time())} more{why} - longer than your "
                f"{_human(max_wait)} wait limit, so continuing without AI. Add another key or raise the limit in Settings.")
    return f"Waited {_human(waited)} for your API keys{why} - continuing without AI"


def _human(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


# Jobs that are useless without AI: they wait for the key however long it takes.
_NO_FALLBACK = {"resume_import"}
NO_FALLBACK_WAIT_SECONDS = 36 * 3600

_FAILURE_HOOKS: dict[str, Callable[[BackgroundJob, str], Awaitable[None]]] = {}


# ---------------------------------------------------------------------------
# Job kinds
# ---------------------------------------------------------------------------


@handler("discovery")
async def _discovery_step(job: BackgroundJob, payload: dict[str, Any], deadline: float) -> StepResult:
    from services.job_pipeline import JobDiscoveryPipeline

    pipeline = JobDiscoveryPipeline(job.user_id)
    run_id = payload["run_id"]
    if payload.get("phase", "collect") == "collect":
        payload["pending"] = await pipeline.collect(run_id)
        payload["phase"] = "analyse"
        if not payload["pending"]:
            await pipeline.finish(run_id)
            return StepResult("done", payload, "Search finished - nothing new worth analysing")
        if time.monotonic() > deadline - 60:
            return StepResult("continue", payload, f"Found jobs - analysing {len(payload['pending'])}")

    pending, defer_until = await pipeline.analyse_ids(run_id, payload.get("pending") or [], deadline)
    payload["pending"] = pending
    if not pending:
        await pipeline.finish(run_id)
        return StepResult("done", payload, "Search finished")
    if defer_until:
        wait = max(0, round(defer_until - time.time()))
        return StepResult("defer", payload, f"Waiting {wait}s for your API rate limit - {len(pending)} jobs left", defer_until)
    return StepResult("continue", payload, f"Analysing - {len(pending)} jobs left")


async def _discovery_failed(job: BackgroundJob, message: str) -> None:
    from services.job_pipeline import JobDiscoveryPipeline

    run_id = (job.payload or {}).get("run_id")
    if run_id:
        await JobDiscoveryPipeline(job.user_id).finish(run_id, "failed", message[:500])


_FAILURE_HOOKS["discovery"] = _discovery_failed


@handler("prepare")
async def _prepare_step(job: BackgroundJob, payload: dict[str, Any], deadline: float) -> StepResult:
    from services.tailoring import retailor_application

    from services.resume_import import reimport_with_ai

    app_id = payload["application_id"]
    await _set_prepare_state(app_id, {"state": "preparing", "message": "Tailoring your resume and writing the cover letter..."})
    try:
        async with AsyncSessionLocal() as db:
            if not payload.get("resume_checked"):
                # A resume uploaded before any AI key was added has no parsed work history;
                # read it properly first, or the tailored resume would be summary + skills only.
                imported = await reimport_with_ai(db, job.user_id)
                payload["resume_checked"] = True
                if imported["status"] == "imported":
                    logger.info("Re-imported resume before preparing %s", app_id)
            app = await db.get(Application, app_id)
            if app is None:
                return StepResult("done", payload, "Application no longer exists")
            result = await retailor_application(db, app, cover_letter=True)
    except DeferJob as exc:
        wait = max(0, round(exc.until - time.time()))
        await _set_prepare_state(app_id, {"state": "preparing", "message": f"Waiting {wait}s for your API rate limit ({exc.reason})"})
        return StepResult("defer", payload, f"Waiting for your API rate limit ({exc.reason})", exc.until)

    fixes = len(result["guardrail_corrections"])
    message = ("Tailored with AI." if result["source"] == "llm"
               else "AI was unavailable, so your own CV wording was reordered for this job.")
    if fixes:
        message += f" Truthfulness check removed {fixes} unsupported line(s)."
    await _set_prepare_state(app_id, {"state": "done", "message": message, "source": result["source"]})
    return StepResult("done", payload, message)


async def _prepare_failed(job: BackgroundJob, message: str) -> None:
    await _set_prepare_state((job.payload or {}).get("application_id"), {"state": "failed", "message": message})


_FAILURE_HOOKS["prepare"] = _prepare_failed


async def _set_prepare_state(app_id: str | None, state: dict[str, Any]) -> None:
    if not app_id:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(update(Application).where(Application.id == app_id).values(prepare_state=state))
        await db.commit()


@handler("resume_import")
async def _resume_import_step(job: BackgroundJob, payload: dict[str, Any], deadline: float) -> StepResult:
    from services.resume_import import reimport_with_ai
    from utils.llm_client import get_llm_client

    try:
        async with AsyncSessionLocal() as db:
            result = await reimport_with_ai(db, job.user_id, get_llm_client(), force=bool(payload.get("force")))
    except DeferJob as exc:
        wait = max(0, round(exc.until - time.time()))
        return StepResult("defer", payload, f"Waiting {_human(wait)} for your API rate limit ({exc.reason})", exc.until)
    messages = {
        "imported": "Resume re-read with AI: {experiences} roles, {projects} projects, {education} education entries",
        "already": "Your resume was already read with AI",
        "no_resume": "Upload your resume first",
        "no_ai": "Could not reach your AI keys - check Settings -> AI keys and try again",
    }
    return StepResult("done", payload, messages[result["status"]].format(**result))


@handler("inbox_sync")
async def _inbox_step(job: BackgroundJob, payload: dict[str, Any], deadline: float) -> StepResult:
    from services.mailer import sync_inbox

    async with AsyncSessionLocal() as db:
        stats = await sync_inbox(db, job.user_id)
    return StepResult("done", payload, f"Checked {stats['scanned']} emails, {stats['status_updates']} status update(s)")


# ---------------------------------------------------------------------------
# Convenience starters
# ---------------------------------------------------------------------------


async def start_discovery(user_id: str, trigger: str = "manual") -> BackgroundJob:
    from services.job_pipeline import JobDiscoveryPipeline

    existing = await active_job(user_id, "discovery")
    if existing is not None:
        return existing
    run_id = await JobDiscoveryPipeline(user_id).start_run(trigger=trigger)
    return await enqueue(user_id, "discovery", {"run_id": run_id, "phase": "collect"}, "Searching company sites and job boards")


async def start_prepare(user_id: str, application_id: str) -> BackgroundJob:
    job = await enqueue(user_id, "prepare", {"application_id": application_id}, "Preparing documents",
                        dedupe_key=application_id)
    await _set_prepare_state(application_id, {"state": "preparing", "message": "Queued - starting shortly..."})
    return job


async def start_resume_import(user_id: str, force: bool = False) -> BackgroundJob | None:
    """Queue an AI re-read of the uploaded resume (only if it still needs one, unless forced)."""
    from services.resume_import import latest_master, parsed_with_ai

    async with AsyncSessionLocal() as db:
        master = await latest_master(db, user_id)
    if master is None or (parsed_with_ai(master) and not force):
        return None
    return await enqueue(user_id, "resume_import", {"force": force}, "Reading your resume with AI", dedupe_key="resume")


def kick(background_tasks=None, user_id: str | None = None, budget: float = STEP_BUDGET_SECONDS) -> None:
    """Advance due jobs right after the current response is sent (serverless-safe)."""
    if background_tasks is not None:
        background_tasks.add_task(run_due_jobs, budget, user_id)
    else:
        asyncio.get_running_loop().create_task(run_due_jobs(budget, user_id))
