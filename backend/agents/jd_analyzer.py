"""JD analysis agent: LLM extraction with a deterministic fallback."""
from __future__ import annotations

from typing import Any

from agents.skill_matcher import analyse_job_description
from core.logging_config import get_logger
from utils.llm_client import LLMUnavailable, get_llm_client

logger = get_logger("agent.jd")

SYSTEM_PROMPT = (
    "You are an expert technical recruiter. You read job descriptions and extract "
    "precise, structured facts. You never invent requirements that are not stated."
)

PROMPT = """Analyse this job posting and extract structured information.

JOB TITLE: {title}
COMPANY: {company}

JOB DESCRIPTION:
{jd_text}

Return JSON with exactly these keys:
{{
  "required_skills": ["hard skills explicitly required, max 15"],
  "preferred_skills": ["nice-to-have skills, max 10"],
  "responsibilities": ["what the person will actually do, max 8"],
  "tech_stack": ["concrete technologies named, max 15"],
  "seniority_level": "junior|mid|senior|lead|principal",
  "years_experience_required": <integer or null>,
  "education_required": "bachelors|masters|phd|none",
  "job_type": "full_time|part_time|contract|internship",
  "key_qualifications": ["the 5 most important qualifications"],
  "company_context": "one sentence on what the company does, or empty string"
}}

Rules:
- Only include skills explicitly mentioned or unambiguously implied.
- Use null for years_experience_required when the posting does not state one.
- Keep every string under 150 characters.
"""

_MAX_JD_CHARS = 9000


class JDAnalyzerAgent:
    """Turns raw JD text into structured requirements."""

    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client or get_llm_client()

    async def analyze(self, title: str, company: str, jd_text: str) -> dict[str, Any]:
        fallback = analyse_job_description(title, jd_text or "")

        if not (jd_text or "").strip():
            logger.debug("Empty JD for %r - using title-only heuristics", title)
            return fallback

        if not self.llm.is_available:
            return fallback

        try:
            result = await self.llm.generate_json(
                PROMPT.format(
                    title=title or "Unknown",
                    company=company or "Unknown",
                    jd_text=(jd_text or "")[:_MAX_JD_CHARS],
                ),
                SYSTEM_PROMPT,
                max_tokens=2000,
            )
        except LLMUnavailable as exc:
            logger.warning("JD analysis fell back to heuristics: %s", exc)
            return fallback

        return self._normalise(result, fallback)

    @staticmethod
    def _normalise(result: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        """Coerce the model's output into the shape the pipeline expects."""

        def as_list(key: str, limit: int) -> list[str]:
            value = result.get(key)
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                return fallback.get(key, [])[:limit]
            cleaned = [str(item).strip()[:150] for item in value if str(item).strip()]
            return cleaned[:limit] or fallback.get(key, [])[:limit]

        years = result.get("years_experience_required")
        if isinstance(years, str):
            years = int(years) if years.isdigit() else None
        if not isinstance(years, int) or not 0 < years <= 30:
            years = fallback.get("years_experience_required")

        seniority = str(result.get("seniority_level") or "").lower()
        if seniority not in {"junior", "mid", "senior", "lead", "principal"}:
            seniority = fallback["seniority_level"]

        job_type = str(result.get("job_type") or "").lower()
        if job_type not in {"full_time", "part_time", "contract", "internship"}:
            job_type = "full_time"

        education = str(result.get("education_required") or "").lower()
        if education not in {"bachelors", "masters", "phd", "none"}:
            education = fallback["education_required"]

        return {
            "required_skills": as_list("required_skills", 15),
            "preferred_skills": as_list("preferred_skills", 10),
            "responsibilities": as_list("responsibilities", 8),
            "tech_stack": as_list("tech_stack", 15),
            "seniority_level": seniority,
            "years_experience_required": years,
            "education_required": education,
            "job_type": job_type,
            "key_qualifications": as_list("key_qualifications", 5),
            "company_context": str(result.get("company_context") or "")[:300],
            "source": "llm",
        }
