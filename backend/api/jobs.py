import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database.database import get_db
from database.models import Application, User, JobListing, UserJobMatch, ScrapeRun
from services.jobs import active_job, has_due_work, job_view, kick, run_due_jobs, start_discovery
from utils.llm_client import NO_KEYS_MESSAGE
from utils.llm_keys import current_keyring
from api.auth import get_current_user
from typing import List, Optional

router = APIRouter(prefix="/jobs", tags=["jobs"])

@router.get("/matched")
async def get_matched_jobs(
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    platform: Optional[str] = None,
    include_skipped: bool = False,
    include_done: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """The job feed. Jobs the user is done with (applied, skipped, hidden) are left out
    unless ``include_done``; the feed shows what still needs a decision."""
    query = (
        select(UserJobMatch, JobListing)
        .join(JobListing, UserJobMatch.job_id == JobListing.id)
        .where(UserJobMatch.user_id == user.id)
        .where(UserJobMatch.match_score >= min_score)
        .order_by(desc(UserJobMatch.match_score))
    )
    if platform:
        query = query.where(JobListing.platform == platform)
    if not include_skipped:
        query = query.where(UserJobMatch.status != "skipped")

    result = await db.execute(query)
    rows = result.all()

    from services.job_pipeline import listing_allowed, load_filter_prefs

    prefs = await load_filter_prefs(db, user.id)  # settings may have changed since these were found
    app_status = {
        job_id: status for job_id, status in (await db.execute(
            select(Application.job_id, Application.status).where(Application.user_id == user.id)
        )).all()
    }
    matches = []
    for match, job in rows:
        if not listing_allowed(job, prefs):
            continue
        done = match.status == "dismissed" or app_status.get(job.id) in DONE_STATUSES
        if done and not include_done:
            continue
        matches.append({
            "match_id": match.id,
            "match_score": match.match_score,
            "matched_skills": match.matched_skills,
            "gap_skills": match.gap_skills,
            "status": match.status,
            "application_status": app_status.get(job.id),
            "scoring_method": match.scoring_method,
            "reasoning": match.reasoning,
            "matched_at": match.matched_at,
            "job": {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "is_remote": job.is_remote,
                "platform": job.platform,
                "url": job.url,
                "apply_url": job.apply_url,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_currency": job.salary_currency,
                "description_text": job.description_text,
                "posted_date": job.posted_date,
                "tags": job.tags,
                "trust_score": job.trust_score,
                "trust_label": job.trust_label,
                "trust_flags": job.trust_flags or [],
                "verified_url": job.verified_url,
                "job_type": job.job_type,
                "seniority_level": job.seniority_level
            }
        })

    return matches

def _run_to_dict(run: ScrapeRun, job: dict | None = None) -> dict:
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "platforms": run.platforms,
        "queries": run.queries,
        "jobs_found": run.jobs_found,
        "jobs_new": run.jobs_new,
        "matches_created": run.matches_created,
        "applications_drafted": run.applications_drafted,
        "jobs_verified": run.jobs_verified,
        "jobs_suspicious": run.jobs_suspicious,
        "error_message": run.error_message,
        "progress": job["message"] if job else None,
        "llm_waiting_seconds": job["waiting_seconds"] if job else 0,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


@router.post("/scrape", status_code=202)
async def trigger_live_scrape(
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    """Start a discovery run. It runs as a background job in bounded steps."""
    keyring = current_keyring()
    if keyring is None or not any(k.usable for k in keyring.keys):
        raise HTTPException(status_code=400, detail=NO_KEYS_MESSAGE)
    job = await start_discovery(user.id)
    kick(background_tasks, user.id)
    return {"status": "started", "job": job_view(job)}


@router.get("/runs")
async def list_scrape_runs(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    runs = (
        await db.execute(
            select(ScrapeRun)
            .where(ScrapeRun.user_id == user.id)
            .order_by(desc(ScrapeRun.started_at))
            .limit(limit)
        )
    ).scalars().all()
    job = job_view(await active_job(user.id, "discovery"))
    return [_run_to_dict(run, job if run.status == "running" else None) for run in runs]


# Application states that mean the user has dealt with a job.
DONE_STATUSES = {"submitted", "viewed", "interview", "offer", "rejected", "skipped"}


async def _set_match_status(db: AsyncSession, user_id: str, job_id: str, status: str) -> None:
    match = (await db.execute(
        select(UserJobMatch).where(UserJobMatch.user_id == user_id, UserJobMatch.job_id == job_id)
    )).scalars().first()
    if match is None:
        raise HTTPException(status_code=404, detail="Job not found in your feed")
    match.status = status
    await db.commit()


@router.post("/{job_id}/hide")
async def hide_job(job_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Not interested: drop it from the feed. It stays remembered, so searches never bring it back."""
    await _set_match_status(db, user.id, job_id, "dismissed")
    return {"status": "dismissed"}


@router.post("/{job_id}/unhide")
async def unhide_job(job_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    queued = (await db.execute(
        select(Application.id).where(Application.user_id == user.id, Application.job_id == job_id)
    )).first()
    await _set_match_status(db, user.id, job_id, "queued" if queued else "new")
    return {"status": "restored"}


@router.post("/clear")
async def clear_feed(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Hide every feed job the user has not acted on. Jobs in the review queue or applied to
    are kept; hidden jobs are remembered, so new searches only bring genuinely new postings."""
    in_progress = {
        job_id for (job_id,) in (await db.execute(
            select(Application.job_id).where(Application.user_id == user.id)
        )).all()
    }
    rows = (await db.execute(
        select(UserJobMatch).where(UserJobMatch.user_id == user.id, UserJobMatch.status != "dismissed")
    )).scalars().all()
    cleared = 0
    for match in rows:
        if match.job_id not in in_progress:
            match.status = "dismissed"
            cleared += 1
    await db.commit()
    return {"cleared": cleared}


class DescriptionIn(BaseModel):
    description: str


@router.post("/{job_id}/description")
async def add_description(job_id: str, data: DescriptionIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """The user pasted a posting's description: store it and AI-score that one job (one call)."""
    from services.job_pipeline import JobDiscoveryPipeline
    from utils.llm_keys import JobWaitPolicy, reset_job_policy, set_job_policy

    text_in = (data.description or "").strip()
    if len(text_in) < 200:
        raise HTTPException(status_code=400, detail="Paste the full job description (at least a few paragraphs).")
    owned = (await db.execute(
        select(UserJobMatch.id).where(UserJobMatch.user_id == user.id, UserJobMatch.job_id == job_id)
    )).first()
    if owned is None:
        raise HTTPException(status_code=404, detail="Job not found in your feed")
    if current_keyring() is None or not current_keyring().keys:
        raise HTTPException(status_code=400, detail=NO_KEYS_MESSAGE)

    # Short waits only: a rate-limited key answers "try again" instead of holding the request.
    token = set_job_policy(JobWaitPolicy(allow_fallback=False, inline_wait_seconds=20))
    try:
        return await asyncio.wait_for(JobDiscoveryPipeline(user.id).rescore_with_description(job_id, text_in), 120)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="The AI took too long - try again in a minute")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        reset_job_policy(token)


@router.post("/work")
async def work(user: User = Depends(get_current_user)):
    """Advance this user's due background jobs (search, document preparation) for up to ~4 minutes.

    The browser calls this without waiting for the answer while something is in
    progress. On a normal server the in-process loop does the same; on Vercel
    this (plus cron) is what moves work forward. Leases stop double processing.
    """
    steps = await run_due_jobs(budget_seconds=220, user_id=user.id)
    return {"steps": steps, "more": await has_due_work(user.id)}
