from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database.database import get_db
from database.models import User, UserProfile, Skill, Experience, Project, Education, MasterResume
from api.auth import get_current_user
from services.resume_parser import ResumeParser
from services.resume_import import apply_parsed_resume, latest_master, parsed_with_ai
from services.profile_scraper import ProfileScraper
from pydantic import BaseModel
from typing import List, Optional
import os
import shutil

router = APIRouter(prefix="/profile", tags=["profile"])

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    professional_summary: Optional[str] = None
    target_roles: Optional[List[str]] = None
    target_locations: Optional[List[str]] = None
    min_salary: Optional[int] = None
    max_salary: Optional[int] = None
    preferred_template: Optional[str] = "classic"
    approval_mode: Optional[str] = "semi_auto"
    is_onboarded: Optional[bool] = None
    headline: Optional[str] = None
    pdf_engine: Optional[str] = None
    contact_email: Optional[str] = None
    achievements: Optional[List[str]] = None
    scrape_frequency_hours: Optional[int] = None
    remote_preference: Optional[str] = None
    max_job_age_days: Optional[int] = None
    auto_apply_min_score: Optional[float] = None
    excluded_companies: Optional[List[str]] = None
    keywords_exclude: Optional[List[str]] = None
    enabled_platforms: Optional[List[str]] = None

class AutoImportRequest(BaseModel):
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None

@router.get("/")
async def get_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)

    # Fetch skills, experience, projects, education
    skills_res = await db.execute(select(Skill).where(Skill.user_id == user.id))
    skills = [s.name for s in skills_res.scalars().all()]

    exp_res = await db.execute(select(Experience).where(Experience.user_id == user.id))
    exps = [
        {
            "id": e.id,
            "title": e.title,
            "company": e.company,
            "dates": f"{e.start_date or ''} - {e.end_date or 'Present'}",
            "bullets": e.bullets or []
        }
        for e in exp_res.scalars().all()
    ]

    proj_res = await db.execute(select(Project).where(Project.user_id == user.id))
    projs = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "technologies": p.technologies or [],
            "url": p.url or ""
        }
        for p in proj_res.scalars().all()
    ]

    return {
        "id": profile.id,
        "name": user.name or "",
        "email": user.email,
        "phone": profile.phone or "",
        "location": profile.location or "",
        "linkedin_url": profile.linkedin_url or "",
        "github_url": profile.github_url or "",
        "portfolio_url": profile.portfolio_url or "",
        "professional_summary": profile.professional_summary or "",
        "headline": profile.headline or "",
        "pdf_engine": profile.pdf_engine or "latex",
        "contact_email": profile.contact_email or "",
        "achievements": profile.achievements or [],
        # Empty means "not set yet" - discovery falls back to a generic role, the UI asks the user.
        "target_roles": profile.target_roles or [],
        "target_locations": profile.target_locations or [],
        "preferred_template": profile.preferred_template or "classic",
        "approval_mode": profile.approval_mode or "semi_auto",
        "is_onboarded": profile.is_onboarded,
        "scrape_frequency_hours": profile.scrape_frequency_hours or 24,
        "remote_preference": profile.remote_preference or "any",
        "max_job_age_days": profile.max_job_age_days or 30,
        "education": [
            {"institution": e.institution, "degree": e.degree, "field": e.field, "start_date": e.start_date,
             "end_date": e.end_date, "gpa": e.gpa}
            for e in (await db.execute(select(Education).where(Education.user_id == user.id))).scalars()
        ],
        "auto_apply_min_score": profile.auto_apply_min_score if profile.auto_apply_min_score is not None else 55,
        "excluded_companies": profile.excluded_companies or [],
        "keywords_exclude": profile.keywords_exclude or [],
        "enabled_platforms": profile.enabled_platforms or [],
        "skills": skills,
        "experiences": exps,
        "projects": projs
    }

@router.get("/title-suggestions")
async def title_suggestions(
    refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Job titles to search for, from past titles and (with an AI key) the work behind them.

    Cached on the profile; refresh=true regenerates (one AI call). Uploading a new resume clears the cache.
    """
    from services.title_suggester import suggest_titles

    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.title_suggestions and not refresh:
        return profile.title_suggestions

    experiences = [
        {"title": e.title, "company": e.company, "start_date": e.start_date, "end_date": e.end_date, "bullets": e.bullets or []}
        for e in (await db.execute(
            select(Experience).where(Experience.user_id == user.id).order_by(Experience.display_order)
        )).scalars()
    ]
    skills = [s.name for s in (await db.execute(select(Skill).where(Skill.user_id == user.id))).scalars()]
    result = await suggest_titles(
        {"headline": profile.headline, "professional_summary": profile.professional_summary}, experiences, skills
    )
    profile.title_suggestions = result
    await db.commit()
    return result


@router.put("/")
async def update_profile(
    data: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    update_dict = data.model_dump(exclude_unset=True)
    if "name" in update_dict and update_dict["name"]:
        user.name = update_dict.pop("name")

    for field, value in update_dict.items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)
    await db.refresh(user)

    return {
        "id": profile.id,
        "name": user.name,
        "email": user.email,
        "phone": profile.phone,
        "location": profile.location,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "professional_summary": profile.professional_summary,
        "preferred_template": profile.preferred_template
    }

@router.post("/upload-resume")
async def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload PDF/DOCX resume file -> parse text & structured JSON -> populate profile."""
    from config import settings

    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    # Never trust the client's filename as a path.
    safe_name = "".join(c for c in os.path.basename(file.filename or "resume") if c.isalnum() or c in "._- ")[:120] or "resume"
    file_path = str(settings.uploads_path / f"{user.id}_{safe_name.lstrip('.')}")
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    parser = ResumeParser()
    parsed = await parser.parse(file_path)

    # Save to master resume
    master = MasterResume(
        user_id=user.id,
        file_name=safe_name,
        file_path=file_path,
        file_type=safe_name.rsplit(".", 1)[-1].lower(),
        parsed_text=parsed.get("raw_text", ""),
        structured_data=parsed
    )
    db.add(master)

    await apply_parsed_resume(db, user, parsed)

    await db.commit()
    return {
        "status": "success",
        "parsed_summary": parsed.get("professional_summary", ""),
        "parsed_with_ai": parsed.get("source") == "llm",
        "counts": {k: len(parsed.get(k) or []) for k in ("experiences", "projects", "education")},
    }


@router.get("/resume-status")
async def resume_status(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Whether the uploaded resume was read with AI (regex-only misses work history)."""
    from services.jobs import active_job

    master = await latest_master(db, user.id)
    job = await active_job(user.id, "resume_import")
    return {
        "has_resume": master is not None,
        "file_name": master.file_name if master else None,
        "parsed_with_ai": parsed_with_ai(master),
        "importing": job is not None,
        "message": job.message if job else None,
    }


@router.post("/reimport-resume", status_code=202)
async def reimport_resume(background_tasks: BackgroundTasks, user: User = Depends(get_current_user)):
    """Re-read the uploaded resume with AI (runs as a background job; waits out rate limits)."""
    from services.jobs import kick, start_resume_import

    job = await start_resume_import(user.id, force=True)
    if job is None:
        raise HTTPException(status_code=400, detail="Upload your resume first")
    kick(background_tasks, user.id)
    return {"status": "queued"}

@router.post("/auto-import")
async def auto_import_from_links(
    req: AutoImportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Scrapes user's GitHub / Portfolio links -> extracts skills & projects -> updates UserProfile table."""
    scraper = ProfileScraper()
    gh_data = await scraper.scrape_github(req.github_url) if req.github_url else {}
    port_data = await scraper.scrape_portfolio(req.portfolio_url) if req.portfolio_url else {}

    # Save URLs and Bio to UserProfile
    prof_res = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = prof_res.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    if req.github_url:
        profile.github_url = req.github_url
    if req.portfolio_url:
        profile.portfolio_url = req.portfolio_url
    if gh_data.get("bio") and not profile.professional_summary:
        profile.professional_summary = gh_data.get("bio")

    added_skills = set(gh_data.get("skills", []) + port_data.get("skills", []))

    for sk_name in added_skills:
        existing = await db.execute(select(Skill).where(Skill.user_id == user.id, Skill.name == sk_name))
        if not existing.scalar_one_or_none():
            db.add(Skill(user_id=user.id, name=sk_name, category="auto-imported"))

    for proj in gh_data.get("projects", []):
        existing_proj = await db.execute(select(Project).where(Project.user_id == user.id, Project.name == proj["name"]))
        if not existing_proj.scalar_one_or_none():
            db.add(Project(
                user_id=user.id,
                name=proj["name"],
                description=proj["description"],
                technologies=proj.get("technologies", []),
                url=proj.get("url", "")
            ))

    await db.commit()
    return {
        "status": "success",
        "github_url": profile.github_url,
        "portfolio_url": profile.portfolio_url,
        "skills_imported": list(added_skills),
        "projects_imported": len(gh_data.get("projects", []))
    }
