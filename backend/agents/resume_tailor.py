"""Resume tailoring agent - rewrites existing content for a specific JD.

The agent never adds experience the candidate does not have. It reorders,
re-emphasises and rephrases what is already in the profile: a role-specific
headline, a focused summary, rewritten bullets for the most relevant roles,
skills grouped the way the target job thinks about them, and the candidate's
own achievements.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.skill_matcher import canonicalise, rank_projects
from core.logging_config import get_logger
from utils.llm_client import LLMUnavailable, get_llm_client

logger = get_logger("agent.tailor")

# Content budget for the layout in services/resume_generator.py (A4, 0.4in margins).
MAX_SUMMARY_CHARS = 600
MAX_HEADLINE_CHARS = 110
MAX_BULLETS_PER_ROLE = 4
MAX_BULLET_CHARS = 230
MAX_ROLES = 4            # roles that get full bullets; the rest are listed on one line
MAX_PROJECTS = 3
MAX_SKILLS = 32
MAX_SKILL_GROUPS = 6
MAX_ACHIEVEMENTS = 4

SYSTEM_PROMPT = (
    "You are an elite ATS resume writer. You rewrite a candidate's real experience "
    "to foreground what a specific job needs. You never fabricate employers, dates, "
    "metrics, or skills the candidate has not claimed."
)

PROMPT = """Tailor this candidate's resume for the target role.

CANDIDATE HEADLINE: {headline}
CANDIDATE SUMMARY: {summary}
CANDIDATE SKILLS: {skills}
CANDIDATE ACHIEVEMENTS: {achievements}

EXPERIENCE (keyed by id, most recent first):
{experiences_json}

PROJECTS (keyed by id):
{projects_json}

TARGET ROLE: {job_title} at {company}
REQUIRED SKILLS: {required_skills}
RESPONSIBILITIES: {responsibilities}

HARD RULES
1. Never invent skills, employers, titles, dates or numbers. Every number you write must appear
   in the candidate material above.
2. headline: 2-4 short phrases separated by " | " (max {max_headline} chars) describing the candidate
   in this job's vocabulary, e.g. "Technical Program Manager | AI & Robotics | Cross-functional Delivery".
   Only phrases the candidate's experience supports.
3. tailored_summary: 3-4 complete sentences, max {max_summary_chars} characters, ending with a full stop.
   Built only from the candidate's summary, roles and achievements - lead with the parts this job
   needs most. Never restate the job's requirements as the candidate's experience.
4. tailored_bullets: choose the {max_roles} roles most relevant to this job. For each, 2-{max_bullets}
   bullets, each a REPHRASING of one of that role's own bullets: keep its facts and most of its words,
   keep every number it states (e.g. "110+ commits", "8 releases") - numbers are the strongest part,
   start with a strong verb, and use the job's vocabulary only where it describes the same thing.
   Do NOT add outcomes, results, collaborators or activities that the source line does not state
   (no "reduced time-to-market", "improved efficiency", etc. unless written). Max {max_bullet_chars} chars.
5. skill_groups: 4-{max_groups} groups named for this job (e.g. "Program Management", "AI & Robotics",
   "Tools"), each listing the candidate's existing skills only, most relevant group first.
6. achievements: up to {max_achievements} of the candidate's own achievements or quantified highlights,
   most relevant first, same numbers.

Return JSON:
{{
  "headline": "...",
  "tailored_summary": "...",
  "tailored_bullets": {{"<experience_id>": ["bullet", "bullet"]}},
  "skill_groups": {{"Group name": ["Skill", "Skill"]}},
  "selected_project_ids": ["<project_id>"],
  "achievements": ["..."]
}}
"""

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


_TYPOGRAPHY = {"‑": "-", "‐": "-", " ": " ", " ": " "}


def clean_text(text: str) -> str:
    """Normalise LLM typography (non-breaking hyphens/spaces) and whitespace."""
    text = str(text or "")
    for source, target in _TYPOGRAPHY.items():
        text = text.replace(source, target)
    # Words split across lines in the source PDF ("Ex- perienced") - rejoin them.
    # ("pre- and post-sales" is a real phrase, so "and/or/to" never get joined.)
    text = re.sub(r"\b([A-Za-z]{2,})-\s+(?!(?:and|or|to)\b)([a-z]{2,})\b", r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()


def trim_to_sentence(text: str, limit: int) -> str:
    """Shorten to whole sentences within ``limit`` - never cut mid-word."""
    text = clean_text(text)
    if len(text) <= limit:
        return text
    kept = ""
    for sentence in _SENTENCE_END_RE.split(text):
        candidate = f"{kept} {sentence}".strip()
        if len(candidate) > limit:
            break
        kept = candidate
    if kept:
        return kept
    # A single overlong sentence: cut at a word boundary and close it cleanly.
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "."


class ResumeTailorAgent:
    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client or get_llm_client()

    async def tailor(
        self,
        user_profile: dict[str, Any],
        skills: list[dict | str],
        experiences: list[dict],
        projects: list[dict],
        job_title: str,
        company: str,
        jd_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        skill_names = [s.get("name") if isinstance(s, dict) else str(s) for s in skills]
        skill_names = [s for s in skill_names if s]

        fallback = self._deterministic(user_profile, skill_names, experiences, projects, jd_analysis)
        if not self.llm.is_available:
            return fallback

        experience_input = {
            str(exp.get("id", index)): {
                "title": exp.get("title"),
                "company": exp.get("company"),
                "dates": f"{exp.get('start_date') or ''} - {exp.get('end_date') or ''}".strip(" -"),
                "bullets": (exp.get("bullets") or [])[:6],
            }
            for index, exp in enumerate(experiences[:12])
        }
        project_input = {
            str(project.get("id", index)): {
                "name": project.get("name"),
                "description": (project.get("description") or "")[:250],
                "technologies": project.get("technologies") or [],
            }
            for index, project in enumerate(projects[:8])
        }

        try:
            result = await self.llm.generate_json(
                PROMPT.format(
                    headline=user_profile.get("headline") or "not provided",
                    summary=(user_profile.get("professional_summary") or "")[:1200] or "not provided",
                    skills=", ".join(skill_names[:50]),
                    achievements="; ".join(user_profile.get("achievements") or []) or "none listed",
                    experiences_json=json.dumps(experience_input, ensure_ascii=False)[:6000],
                    projects_json=json.dumps(project_input, ensure_ascii=False)[:2500],
                    job_title=job_title,
                    company=company or "the company",
                    required_skills=", ".join(jd_analysis.get("required_skills", [])) or "not listed",
                    responsibilities="; ".join(jd_analysis.get("responsibilities", [])[:6]) or "not listed",
                    max_headline=MAX_HEADLINE_CHARS,
                    max_summary_chars=MAX_SUMMARY_CHARS,
                    max_roles=MAX_ROLES,
                    max_bullets=MAX_BULLETS_PER_ROLE,
                    max_bullet_chars=MAX_BULLET_CHARS,
                    max_groups=MAX_SKILL_GROUPS,
                    max_achievements=MAX_ACHIEVEMENTS,
                ),
                SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=3500,
            )
        except LLMUnavailable as exc:
            logger.warning("Resume tailoring fell back to deterministic ordering: %s", exc)
            return fallback

        return self._normalise(result, fallback, skill_names, experiences, projects)

    # -- deterministic path ------------------------------------------------

    def _deterministic(
        self,
        user_profile: dict[str, Any],
        skill_names: list[str],
        experiences: list[dict],
        projects: list[dict],
        jd_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Real tailoring without an LLM: relevance ordering and clean trimming."""
        wanted = [canonicalise(s) for s in jd_analysis.get("required_skills", [])]
        wanted += [canonicalise(s) for s in jd_analysis.get("tech_stack", [])]
        wanted_set = {s for s in wanted if s}

        ranked_skills = sorted(
            skill_names,
            key=lambda s: (canonicalise(s) in wanted_set, len(s)),
            reverse=True,
        )

        bullets: dict[str, list[str]] = {}
        for index, exp in enumerate(experiences):
            if len(bullets) >= MAX_ROLES:
                break
            source = [str(b) for b in (exp.get("bullets") or []) if b]
            if not source:
                continue
            # Surface bullets mentioning the JD's stack first.
            source.sort(
                key=lambda b: len({canonicalise(w) for w in b.split()} & wanted_set),
                reverse=True,
            )
            bullets[str(exp.get("id", index))] = [
                trim_to_sentence(b, MAX_BULLET_CHARS) for b in source[:MAX_BULLETS_PER_ROLE]
            ]

        relevant_projects = rank_projects(projects, list(wanted_set), limit=MAX_PROJECTS)

        return {
            "headline": str(user_profile.get("headline") or "").strip()[:MAX_HEADLINE_CHARS],
            "tailored_summary": trim_to_sentence(user_profile.get("professional_summary") or "", MAX_SUMMARY_CHARS),
            "tailored_bullets": bullets,
            "skills_order": ranked_skills[:MAX_SKILLS],
            "skill_groups": None,  # the generator groups by each skill's category
            "selected_project_ids": [
                str(p.get("id")) for p in relevant_projects if p.get("id") is not None
            ],
            "achievements": [str(a) for a in (user_profile.get("achievements") or [])][:MAX_ACHIEVEMENTS],
            "source": "heuristic",
        }

    # -- normalisation -----------------------------------------------------

    @staticmethod
    def _normalise(
        result: dict[str, Any],
        fallback: dict[str, Any],
        skill_names: list[str],
        experiences: list[dict],
        projects: list[dict],
    ) -> dict[str, Any]:
        summary = str(result.get("tailored_summary") or "").strip()
        if len(summary) < 40:
            summary = fallback["tailored_summary"]
        summary = trim_to_sentence(summary, MAX_SUMMARY_CHARS)

        headline = clean_text(result.get("headline")).strip(" |")
        if not headline or len(headline) > MAX_HEADLINE_CHARS:
            headline = fallback["headline"]

        valid_experience_ids = {str(e.get("id", i)) for i, e in enumerate(experiences)}
        raw_bullets = result.get("tailored_bullets")
        bullets: dict[str, list[str]] = {}
        if isinstance(raw_bullets, dict):
            for key, value in raw_bullets.items():
                key = str(key)
                if key not in valid_experience_ids or len(bullets) >= MAX_ROLES:
                    continue
                if isinstance(value, str):
                    value = [value]
                if not isinstance(value, list):
                    continue
                cleaned = [
                    trim_to_sentence(str(item).strip().lstrip("-*• "), MAX_BULLET_CHARS)
                    for item in value
                    if str(item).strip()
                ]
                if cleaned:
                    bullets[key] = cleaned[:MAX_BULLETS_PER_ROLE]
        if not bullets:
            bullets = fallback["tailored_bullets"]

        # Skills may only be reordered/grouped, never added.
        owned_lookup = {s.lower(): s for s in skill_names}
        owned_lookup.update({canonicalise(s).lower(): s for s in skill_names})

        def owned(name: Any) -> str | None:
            return owned_lookup.get(str(name).strip().lower()) or owned_lookup.get(canonicalise(str(name)).lower())

        groups: dict[str, list[str]] = {}
        used: set[str] = set()
        if isinstance(result.get("skill_groups"), dict):
            for group, members in result["skill_groups"].items():
                if len(groups) >= MAX_SKILL_GROUPS or not isinstance(members, list):
                    continue
                kept = []
                for member in members:
                    match = owned(member)
                    if match and match not in used:
                        kept.append(match)
                        used.add(match)
                if kept:
                    groups[str(group).strip()[:40]] = kept[:10]

        ordered = [s for group in groups.values() for s in group]
        for skill in fallback["skills_order"]:
            if skill not in ordered:
                ordered.append(skill)

        valid_project_ids = {str(p.get("id", i)) for i, p in enumerate(projects)}
        selected = [
            str(pid) for pid in (result.get("selected_project_ids") or []) if str(pid) in valid_project_ids
        ][:MAX_PROJECTS]
        if not selected:
            selected = fallback["selected_project_ids"]

        achievements = [
            clean_text(a)[:200] for a in (result.get("achievements") or []) if str(a).strip()
        ][:MAX_ACHIEVEMENTS] or fallback["achievements"]

        return {
            "headline": headline,
            "tailored_summary": summary,
            "tailored_bullets": bullets,
            "skills_order": ordered[:MAX_SKILLS],
            "skill_groups": groups or None,
            "selected_project_ids": selected,
            "achievements": achievements,
            "source": "llm",
        }
