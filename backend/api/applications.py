import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from database.database import get_db
from database.models import User, Application, JobListing, ApplicationLog, UserJobMatch, utcnow
from api.auth import get_current_user
from pydantic import BaseModel
from typing import Optional

from services.apply_assistant import choose_apply_url, session_state, start_assisted_apply
from services.documents import build_application_documents

router = APIRouter(prefix="/applications", tags=["applications"])




# pending -> approved -> applying -> submitted | awaiting_confirmation
# then (from replies / manually) viewed -> interview -> offer, or rejected / skipped
VALID_STATUSES = {
    "pending", "approved", "applying", "awaiting_confirmation", "submitted",
    "viewed", "interview", "offer", "rejected", "skipped",
}

class ApplicationStatusUpdate(BaseModel):
    status: str


class ApplicationEdit(BaseModel):
    tailored_summary: Optional[str] = None
    cover_letter: Optional[str] = None


async def _get_owned(db: AsyncSession, user: User, app_id: str) -> tuple[Application, JobListing]:
    row = (
        await db.execute(
            select(Application, JobListing)
            .join(JobListing, Application.job_id == JobListing.id)
            .where(Application.id == app_id, Application.user_id == user.id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row[0], row[1]


@router.get("/")
async def get_applications(
    status: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    query = (
        select(Application, JobListing, UserJobMatch.match_score)
        .join(JobListing, Application.job_id == JobListing.id)
        .outerjoin(UserJobMatch, Application.match_id == UserJobMatch.id)
        .where(Application.user_id == user.id)
        .order_by(desc(Application.created_at))
    )
    if status:
        query = query.where(Application.status.in_(status.split(",")))

    rows = (await db.execute(query)).all()

    from services.job_pipeline import listing_allowed, load_filter_prefs

    prefs = await load_filter_prefs(db, user.id)
    apps = []
    for app, job, match_score in rows:
        # Untouched suggestions follow the current search settings; anything the
        # user has prepared or applied to always stays visible.
        if app.status == "pending" and not app.prepare_state and not listing_allowed(job, prefs):
            continue
        apps.append({
            "id": app.id,
            "status": app.status,
            "match_score": match_score,
            "tailored_summary": app.tailored_summary,
            "tailored_bullets": app.tailored_bullets,
            "cover_letter": app.cover_letter,
            "resume_docx_path": app.resume_docx_path,
            "resume_pdf_path": app.resume_pdf_path,
            "submission_method": app.submission_method,
            "submitted_at": app.submitted_at,
            "error_message": app.error_message,
            "created_at": app.created_at,
            "updated_at": app.updated_at,
            "apply_session": session_state(app.id),
            "prepared": bool(app.tailored_summary),
            "prepare": app.prepare_state,
            "tailored_headline": app.tailored_headline,
            "job": {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "platform": job.platform,
                "url": job.url,
                "apply_url": choose_apply_url(job),
                "verified_url": job.verified_url,
                "trust_label": job.trust_label,
                "trust_score": job.trust_score,
            }
        })

    return apps


@router.put("/{app_id}/status")
async def update_application_status(
    app_id: str,
    data: ApplicationStatusUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if data.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status '{data.status}'")
    app, _job = await _get_owned(db, user, app_id)

    app.status = data.status
    if data.status == "submitted" and app.submitted_at is None:
        app.submitted_at = utcnow()
        app.submission_method = app.submission_method or "manual"
    db.add(ApplicationLog(application_id=app.id, action="status_updated", details=f"Status changed to {data.status}"))

    await db.commit()
    await db.refresh(app)
    return {"id": app.id, "status": app.status, "submitted_at": app.submitted_at}


@router.put("/{app_id}")
async def edit_application(
    app_id: str,
    data: ApplicationEdit,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Let the user correct the AI drafts before applying."""
    app, _job = await _get_owned(db, user, app_id)
    if data.tailored_summary is not None:
        app.tailored_summary = data.tailored_summary
    if data.cover_letter is not None:
        app.cover_letter = data.cover_letter
    db.add(ApplicationLog(application_id=app.id, action="edited", details="Drafts edited by user"))
    await db.commit()
    return {"id": app.id, "tailored_summary": app.tailored_summary, "cover_letter": app.cover_letter}


@router.post("/{app_id}/prepare", status_code=202)
async def prepare_documents(
    app_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Tailor the resume and write the cover letter for this job (a background job)."""
    from services.jobs import kick, start_prepare

    app, _job = await _get_owned(db, user, app_id)
    await start_prepare(user.id, app.id)
    kick(background_tasks, user.id)
    return {"state": "preparing", "message": "Queued - starting shortly..."}


@router.post("/{app_id}/assist-apply", status_code=202)
async def assisted_apply(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Open the real application form in a visible browser and pre-fill it."""
    from config import settings

    if not settings.ASSISTED_APPLY_ENABLED:
        raise HTTPException(status_code=400, detail="Auto-fill runs only when AutoApplier is installed on your own computer. Use 'Open application page'.")
    app, _job = await _get_owned(db, user, app_id)
    if app.status in ("submitted", "interview", "offer"):
        raise HTTPException(status_code=409, detail="This application was already submitted")
    app.error_message = None
    await db.commit()
    return start_assisted_apply(user.id, app.id)


@router.get("/{app_id}/apply-status")
async def assisted_apply_status(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    app, _job = await _get_owned(db, user, app_id)
    from services.jobs import active_job, job_view

    job = job_view(await active_job(user.id, "prepare", dedupe_key=app.id))
    return {
        "status": app.status,
        "error_message": app.error_message,
        "session": session_state(app.id),
        "prepare": app.prepare_state,
        "prepared": bool(app.tailored_summary),
        "llm_wait": {"seconds": job["waiting_seconds"], "reason": job["message"] or ""} if job else {"seconds": 0, "reason": ""},
    }


@router.get("/{app_id}/logs")
async def application_logs(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    app, _job = await _get_owned(db, user, app_id)
    logs = (
        await db.execute(
            select(ApplicationLog).where(ApplicationLog.application_id == app.id).order_by(ApplicationLog.created_at)
        )
    ).scalars().all()
    return [{"action": l.action, "details": l.details, "created_at": l.created_at} for l in logs]


@router.get("/{app_id}/download-resume")
async def download_tailored_resume(
    app_id: str,
    format: str = Query("pdf", pattern="^(pdf|ats_pdf|docx|tex|cover_letter)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Download the tailored 1-page resume (PDF or DOCX) or the cover letter PDF."""
    app, job = await _get_owned(db, user, app_id)
    paths = await build_application_documents(db, app)

    path = paths[format]
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Document could not be generated")

    safe = "".join(c if c.isalnum() else "_" for c in f"{job.company}_{job.title}")[:80]
    prefix = {"cover_letter": "Cover_Letter", "ats_pdf": "Resume_ATS"}.get(format, "Resume")
    extension = os.path.splitext(path)[1]
    return FileResponse(path=path, filename=f"{prefix}_{safe}{extension}", media_type="application/octet-stream")
