from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database.database import get_db
from database.models import User, UserProfile, Skill, Experience, Project, Education, MasterResume
from api.auth import get_current_user
from services.resume_parser import ResumeParser
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

    # Update User Profile
    contact = parsed.get("contact", {})
    prof_res = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = prof_res.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    if contact.get("name"):
        user.name = contact.get("name")
    if contact.get("phone"):
        profile.phone = contact.get("phone")
    if contact.get("location"):
        profile.location = contact.get("location")
    if contact.get("linkedin_url"):
        profile.linkedin_url = contact.get("linkedin_url")
    if contact.get("github_url"):
        profile.github_url = contact.get("github_url")
    if parsed.get("professional_summary"):
        profile.professional_summary = parsed.get("professional_summary")
    if parsed.get("experience_years"):
        profile.experience_years = parsed.get("experience_years")
    if parsed.get("headline"):
        profile.headline = parsed["headline"]
    if contact.get("email"):
        profile.contact_email = contact["email"]
    profile.achievements = parsed.get("achievements") or []
    profile.title_suggestions = None
    profile.certifications = parsed.get("certifications") or []

    profile.is_onboarded = True

    # Clear old skills and insert parsed skills
    await db.execute(delete(Skill).where(Skill.user_id == user.id))
    seen_skills: set[str] = set()
    for s in parsed.get("skills", []):
        skill_name = (s.get("name") if isinstance(s, dict) else str(s) or "").strip()[:100]
        # Skills are unique per user; "REST APIs" and "REST API" are the same skill.
        key = skill_name.lower().rstrip("s")
        if skill_name and key not in seen_skills:
            seen_skills.add(key)
            db.add(Skill(user_id=user.id, name=skill_name, category=s.get("category", "general") if isinstance(s, dict) else "general"))

    # Clear old experiences and insert parsed experiences
    await db.execute(delete(Experience).where(Experience.user_id == user.id))
    for order, exp in enumerate(parsed.get("experiences", [])):
        db.add(Experience(
            display_order=order,
            user_id=user.id,
            company=exp.get("company", "Company"),
            title=exp.get("title", "Role"),
            location=exp.get("location", ""),
            start_date=exp.get("start_date", ""),
            end_date=exp.get("end_date", "Present"),
            bullets=exp.get("bullets", []),
            technologies=exp.get("technologies", [])
        ))

    # Clear old projects and insert parsed projects
    await db.execute(delete(Project).where(Project.user_id == user.id))
    for proj in parsed.get("projects", []):
        db.add(Project(
            user_id=user.id,
            name=proj.get("name", "Project"),
            description=proj.get("description", ""),
            technologies=proj.get("technologies", []),
            url=proj.get("url", "")
        ))

    await db.execute(delete(Education).where(Education.user_id == user.id))
    for order, edu in enumerate(parsed.get("education", [])):
        db.add(Education(
            user_id=user.id,
            institution=edu.get("institution") or "Institution",
            degree=edu.get("degree") or "Degree",
            field=edu.get("field") or None,
            start_date=edu.get("start_date") or None,
            end_date=edu.get("end_date") or None,
            gpa=edu.get("gpa") or None,
            display_order=order,
        ))

    await db.commit()
    return {"status": "success", "parsed_summary": parsed.get("professional_summary", "")}

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
