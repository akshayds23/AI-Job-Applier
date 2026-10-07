from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database.database import get_db
from database.models import User, JobListing, UserJobMatch, ScrapeRun
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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
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

    matches = []
    for match, job in rows:
        matches.append({
            "match_id": match.id,
            "match_score": match.match_score,
            "matched_skills": match.matched_skills,
            "gap_skills": match.gap_skills,
            "status": match.status,
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


@router.post("/work")
async def work(user: User = Depends(get_current_user)):
    """Advance this user's due background jobs (search, document preparation) for up to ~4 minutes.

    The browser calls this without waiting for the answer while something is in
    progress. On a normal server the in-process loop does the same; on Vercel
    this (plus cron) is what moves work forward. Leases stop double processing.
    """
    steps = await run_due_jobs(budget_seconds=220, user_id=user.id)
    return {"steps": steps, "more": await has_due_work(user.id)}
