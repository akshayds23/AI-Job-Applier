"""Re-run resume tailoring for an existing application with the current profile."""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.cover_letter import CoverLetterAgent
from agents.guardrails import GuardrailsAgent
from agents.jd_analyzer import JDAnalyzerAgent
from agents.resume_tailor import ResumeTailorAgent
from database.models import Application, Experience, JobListing, Project, Skill, User, UserJobMatch, UserProfile
from utils.llm_client import get_llm_client


async def retailor_application(db: AsyncSession, application: Application, cover_letter: bool = False) -> dict:
    job = await db.get(JobListing, application.job_id)
    profile = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == application.user_id))
    ).scalar_one_or_none()
    skills = [
        {"name": s.name, "category": s.category}
        for s in (await db.execute(select(Skill).where(Skill.user_id == application.user_id))).scalars()
    ]
    experiences = [
        {
            "id": e.id, "title": e.title, "company": e.company, "start_date": e.start_date,
            "end_date": e.end_date, "is_current": e.is_current, "bullets": e.bullets or [],
            "technologies": e.technologies or [],
        }
        for e in (
            await db.execute(
                select(Experience).where(Experience.user_id == application.user_id).order_by(Experience.display_order)
            )
        ).scalars()
    ]
    projects = [
        {"id": p.id, "name": p.name, "description": p.description, "technologies": p.technologies or []}
        for p in (await db.execute(select(Project).where(Project.user_id == application.user_id))).scalars()
    ]
    # Approved career-profile items join the profile, so tailoring can use them and the
    # truthfulness check accepts them as the user's own.
    from services.career import enrich_profile

    achievements = list((profile.achievements if profile else None) or [])
    await enrich_profile(db, application.user_id, experiences, projects, skills, achievements)

    user_profile = {
        "professional_summary": (profile.professional_summary if profile else "") or "",
        "headline": (profile.headline if profile else "") or "",
        "achievements": achievements,
        "experience_years": profile.experience_years if profile else None,
    }

    match = await db.get(UserJobMatch, application.match_id) if application.match_id else None
    llm = get_llm_client(profile.llm_provider if profile else None)
    # Re-read the job description on every prepare: analyses stored at discovery time
    # may come from an older or rule-based pass, and the documents depend on them.
    jd_analysis = await JDAnalyzerAgent(llm).analyze(job.title, job.company, job.description_text or "")
    if match is not None:
        match.jd_analysis = jd_analysis

    tailored = await ResumeTailorAgent(llm).tailor(
        user_profile=user_profile,
        skills=skills,
        experiences=experiences,
        projects=projects,
        job_title=job.title,
        company=job.company,
        jd_analysis=jd_analysis,
    )
    report = GuardrailsAgent().enforce(
        skills, experiences, tailored,
        extra_sources=[user_profile["professional_summary"], *user_profile["achievements"]],
    )

    application.tailored_headline = tailored.get("headline")
    application.tailored_summary = tailored.get("tailored_summary")
    application.tailored_bullets = tailored.get("tailored_bullets") or {}
    application.tailored_skills_order = tailored.get("skills_order") or []
    application.tailored_skill_groups = tailored.get("skill_groups")
    application.selected_project_ids = tailored.get("selected_project_ids") or []
    application.achievements = tailored.get("achievements") or []

    if cover_letter or not application.cover_letter:
        user = await db.get(User, application.user_id)
        guardrails = GuardrailsAgent()
        letter = await CoverLetterAgent(llm).generate_cover_letter(
            user_name=(user.name if user else "") or "Applicant",
            user_profile={**user_profile, "email": user.email if user else ""},
            matched_skills=(match.matched_skills if match else None) or [],
            job_title=job.title,
            company_name=job.company,
            jd_analysis=jd_analysis,
            highlights=tailored.get("achievements"),
            gap_skills=(match.gap_skills if match else None) or [],
        )
        letter, letter_report = guardrails.check_cover_letter(
            letter, candidate_years=profile.experience_years if profile else None,
            gap_skills=(match.gap_skills if match else None) or [],
        )
        application.cover_letter = letter
        report.violations.extend(letter_report.violations)

    await db.commit()
    return {"source": tailored.get("source"), "guardrail_corrections": report.violations}
