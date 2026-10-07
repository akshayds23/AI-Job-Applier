"""Build the tailored resume + cover letter files for one application."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import Application, Education, Experience, JobListing, Project, Skill, User, UserProfile
from services.resume_generator import ResumeGenerator


async def applicant_profile(db: AsyncSession, user: User) -> dict[str, Any]:
    """Contact details used both on the resume and to fill application forms."""
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    return {
        "name": user.name or "",
        # The address on the resume can differ from the login (e.g. a dedicated job-search inbox).
        "email": (profile.contact_email if profile else None) or user.email,
        "phone": (profile.phone if profile else "") or "",
        "location": (profile.location if profile else "") or "",
        "linkedin_url": (profile.linkedin_url if profile else "") or "",
        "github_url": (profile.github_url if profile else "") or "",
        "portfolio_url": (profile.portfolio_url if profile else "") or "",
        "template": (profile.preferred_template if profile else None) or "classic",
        "headline": (profile.headline if profile else None) or "",
        "professional_summary": (profile.professional_summary if profile else None) or "",
        "achievements": (profile.achievements if profile else None) or [],
        "certifications": (profile.certifications if profile else None) or [],
        "pdf_engine": (profile.pdf_engine if profile else None) or "latex",
    }


async def build_application_documents(db: AsyncSession, application: Application) -> dict[str, str | None]:
    """Render resume (DOCX + PDF) and cover letter (PDF); store the paths on the application."""
    user = await db.get(User, application.user_id)
    job = await db.get(JobListing, application.job_id)
    contact = await applicant_profile(db, user)

    experiences = [
        {
            "id": e.id, "title": e.title, "company": e.company, "location": e.location,
            "start_date": e.start_date, "end_date": e.end_date, "is_current": e.is_current,
            "bullets": e.bullets or [], "technologies": e.technologies or [],
        }
        for e in (
            await db.execute(
                select(Experience).where(Experience.user_id == user.id).order_by(Experience.display_order)
            )
        ).scalars()
    ]
    projects = [
        {"id": p.id, "name": p.name, "description": p.description, "technologies": p.technologies or [], "url": p.url}
        for p in (await db.execute(select(Project).where(Project.user_id == user.id))).scalars()
    ]
    education = [
        {
            "id": e.id, "institution": e.institution, "degree": e.degree, "field": e.field,
            "start_date": e.start_date, "end_date": e.end_date, "gpa": e.gpa,
        }
        for e in (await db.execute(select(Education).where(Education.user_id == user.id))).scalars()
    ]

    skills = list((await db.execute(select(Skill).where(Skill.user_id == user.id))).scalars())
    skill_categories = {s.name: (s.category or "general") for s in skills}
    skills_order = application.tailored_skills_order or [s.name for s in skills]

    output_dir = settings.generated_path / application.id
    generator = ResumeGenerator()
    docx_path, pdf_path = await generator.generate(
        output_dir=output_dir,
        job_id=application.id,
        user_profile=contact,
        user_name=contact["name"] or "Applicant",
        tailored_summary=application.tailored_summary,
        tailored_bullets=application.tailored_bullets or {},
        skills_order=skills_order,
        experiences=experiences,
        projects=projects,
        education=education,
        template_name=contact["template"],
        achievements=application.achievements or contact["achievements"],
        selected_project_ids=application.selected_project_ids or [],
        headline=application.tailored_headline or contact["headline"],
        skill_groups=application.tailored_skill_groups,
        skill_categories=skill_categories,
        certifications=contact["certifications"],
        pdf_engine=contact["pdf_engine"],
    )
    latex_pdf = Path(pdf_path).with_name(Path(pdf_path).stem + "_latex.pdf")
    chosen_pdf = str(latex_pdf) if contact["pdf_engine"] == "latex" and latex_pdf.exists() else pdf_path

    cover_letter_path = None
    if application.cover_letter:
        cover_letter_path = await generator.generate_cover_letter(
            output_dir=output_dir,
            job_id=application.id,
            user_name=contact["name"] or "Applicant",
            user_profile=contact,
            company=job.company if job else "",
            job_title=job.title if job else "",
            body=application.cover_letter,
            template_name=contact["template"],
        )

    application.resume_docx_path = docx_path
    application.resume_pdf_path = chosen_pdf
    application.cover_letter_path = cover_letter_path
    await db.commit()
    return {
        "docx": docx_path,
        "pdf": chosen_pdf,                 # what people see: LaTeX when available
        "ats_pdf": pdf_path,               # plain-text-clean PDF for applicant-tracking systems
        "latex_pdf": str(latex_pdf) if latex_pdf.exists() else None,
        "tex": str(Path(pdf_path).with_suffix(".tex")),
        "cover_letter": cover_letter_path,
    }
