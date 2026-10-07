"""Apply a parsed resume to a user's profile, and re-import it with AI later.

A resume uploaded before the user has added an AI key is parsed by the regex
fallback, which cannot split work history, projects or education into entries -
so generated resumes would only contain a summary and skills. ``reimport_with_ai``
re-parses the stored resume *text* (the uploaded file itself may be gone on
serverless hosts) once a key exists, and fills in what the first pass missed.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from database.models import Education, Experience, MasterResume, Project, Skill, User, UserProfile

logger = get_logger("resume_import")


def parsed_with_ai(master: MasterResume | None) -> bool:
    return bool(master and (master.structured_data or {}).get("source") == "llm")


async def latest_master(db: AsyncSession, user_id: str) -> MasterResume | None:
    return (
        await db.execute(
            select(MasterResume).where(MasterResume.user_id == user_id)
            .order_by(MasterResume.uploaded_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def apply_parsed_resume(db: AsyncSession, user: User, parsed: dict[str, Any], *, fill_only: bool = False) -> None:
    """Write parsed resume data into the profile tables (caller commits).

    ``fill_only`` (re-import) keeps anything the user already has or edited:
    contact fields are only filled when empty, skills are merged, and a section
    (experience / projects / education) is only written when it is empty.
    """
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)

    def put(obj: Any, attr: str, value: Any) -> None:
        if value and not (fill_only and getattr(obj, attr)):
            setattr(obj, attr, value)

    contact = parsed.get("contact") or {}
    put(user, "name", contact.get("name"))
    put(profile, "phone", contact.get("phone"))
    put(profile, "location", contact.get("location"))
    put(profile, "linkedin_url", contact.get("linkedin_url"))
    put(profile, "github_url", contact.get("github_url"))
    put(profile, "contact_email", contact.get("email"))
    put(profile, "professional_summary", parsed.get("professional_summary"))
    put(profile, "experience_years", parsed.get("experience_years"))
    put(profile, "headline", parsed.get("headline"))
    if fill_only:
        put(profile, "achievements", parsed.get("achievements"))
        put(profile, "certifications", parsed.get("certifications"))
    else:
        profile.achievements = parsed.get("achievements") or []
        profile.certifications = parsed.get("certifications") or []
    profile.title_suggestions = None  # recomputed from the new work history
    profile.is_onboarded = True

    # Skills are unique per user; "REST APIs" and "REST API" are the same skill.
    seen_skills: set[str] = set()
    if fill_only:
        seen_skills = {n.lower().rstrip("s") for n in (await db.execute(select(Skill.name).where(Skill.user_id == user.id))).scalars()}
    else:
        await db.execute(delete(Skill).where(Skill.user_id == user.id))
    for s in parsed.get("skills", []):
        skill_name = (s.get("name") if isinstance(s, dict) else str(s) or "").strip()[:100]
        key = skill_name.lower().rstrip("s")
        if skill_name and key not in seen_skills:
            seen_skills.add(key)
            db.add(Skill(user_id=user.id, name=skill_name, category=s.get("category", "general") if isinstance(s, dict) else "general"))

    async def replace(model, rows: list[dict[str, Any]], build) -> None:
        if not rows:
            return
        if fill_only:
            if (await db.execute(select(model.id).where(model.user_id == user.id).limit(1))).first():
                return
        else:
            await db.execute(delete(model).where(model.user_id == user.id))
        for order, row in enumerate(rows):
            db.add(build(order, row))

    if not fill_only:
        # A fresh upload replaces the old history even when the new parse found none.
        for model in (Experience, Project, Education):
            await db.execute(delete(model).where(model.user_id == user.id))

    await replace(Experience, parsed.get("experiences") or [], lambda order, exp: Experience(
        display_order=order, user_id=user.id,
        company=exp.get("company") or "Company", title=exp.get("title") or "Role",
        location=exp.get("location", ""), start_date=exp.get("start_date", ""),
        end_date=exp.get("end_date") or "Present",
        bullets=exp.get("bullets", []), technologies=exp.get("technologies", []),
    ))
    await replace(Project, parsed.get("projects") or [], lambda order, proj: Project(
        user_id=user.id, name=proj.get("name") or "Project", description=proj.get("description", ""),
        technologies=proj.get("technologies", []), url=proj.get("url", ""),
    ))
    await replace(Education, parsed.get("education") or [], lambda order, edu: Education(
        user_id=user.id, institution=edu.get("institution") or "Institution", degree=edu.get("degree") or "Degree",
        field=edu.get("field") or None, start_date=edu.get("start_date") or None,
        end_date=edu.get("end_date") or None, gpa=edu.get("gpa") or None, display_order=order,
    ))


async def reimport_with_ai(db: AsyncSession, user_id: str, llm_client=None, force: bool = False) -> dict[str, Any]:
    """Re-parse the latest uploaded resume with AI if the first pass was regex-only.

    Returns ``{"status": "imported" | "already" | "no_resume" | "no_ai", ...}``.
    ``DeferJob`` from a rate-limited key propagates so a background job can wait.
    """
    from services.resume_parser import ResumeParser

    master = await latest_master(db, user_id)
    if master is None or not (master.parsed_text or "").strip():
        return {"status": "no_resume"}
    if parsed_with_ai(master) and not force:
        return {"status": "already"}

    parser = ResumeParser(llm_client)
    parsed = await parser.parse_text(master.parsed_text)
    if parsed.get("source") != "llm":
        return {"status": "no_ai"}

    user = await db.get(User, user_id)
    master.structured_data = parsed
    await apply_parsed_resume(db, user, parsed, fill_only=True)
    await db.commit()
    counts = {k: len(parsed.get(k) or []) for k in ("experiences", "projects", "education")}
    logger.info("Re-imported resume with AI for %s: %s", user_id, counts)
    return {"status": "imported", **counts}
