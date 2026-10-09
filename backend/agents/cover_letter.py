"""Cover letter agent."""
from __future__ import annotations

from typing import Any

from core.logging_config import get_logger
from utils.llm_client import LLMUnavailable, get_llm_client

logger = get_logger("agent.cover_letter")

SYSTEM_PROMPT = (
    "You are an experienced career writer. You write specific, grounded cover "
    "letters that a hiring manager would actually finish reading. You never use "
    "filler openings and never claim experience the candidate has not stated."
)

PROMPT = """Write a cover letter for this application.

CANDIDATE
- Name: {candidate_name}
- Background: {summary}
- Total years of professional experience: {years}
- Skills relevant to this role: {matched_skills}
- Notable work: {highlights}

ROLE
- Title: {job_title}
- Company: {company_name}
- What they need: {requirements}
{company_context}

FORBIDDEN CLAIMS - the candidate does NOT have these. Never state or imply
experience with any of them: {gap_skills}

REQUIREMENTS
- 3 to 4 paragraphs, 220-330 words total.
- Open with a specific reason for interest in {company_name} - never "I am writing
  to express my interest" or "I am a highly motivated professional".
- Paragraph 2-3: connect concrete candidate experience to their stated needs,
  drawing only on the skills and background listed above.
- Close with a direct, confident call to action.
- Sound like a real person writing to a real team: plain words, varied sentence length, no
  em or en dashes, no stock phrases ("I am excited to", "leverage", "passionate", "dynamic",
  "seamless", "synergy", "I believe I would be a great fit").
- Never invent employers, metrics, credentials, or a different number of years of
  experience. If you mention duration at all, it must be {years}.
- It is fine to acknowledge eagerness to learn something, but never claim to have
  already done it.
- Output only the letter body. No date, no address block, no subject line.
"""


class CoverLetterAgent:
    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client or get_llm_client()

    async def generate_cover_letter(
        self,
        user_name: str,
        user_profile: dict[str, Any],
        matched_skills: list[str],
        job_title: str,
        company_name: str,
        jd_analysis: dict[str, Any],
        highlights: list[str] | None = None,
        gap_skills: list[str] | None = None,
    ) -> str:
        company = company_name or "your team"
        years = user_profile.get("experience_years")

        if not self.llm.is_available:
            return self._template(user_name, user_profile, matched_skills, job_title, company)

        context = jd_analysis.get("company_context") or ""
        requirements = "; ".join(
            (jd_analysis.get("key_qualifications") or [])[:4]
            + (jd_analysis.get("required_skills") or [])[:6]
        )

        try:
            letter = await self.llm.generate_text(
                PROMPT.format(
                    candidate_name=user_name or "the candidate",
                    summary=(user_profile.get("professional_summary") or "not provided")[:700],
                    years=f"{years} years" if years else "not stated - do not mention a duration",
                    matched_skills=", ".join(matched_skills[:10]) or "not specified",
                    highlights="; ".join((highlights or [])[:3]) or "not provided",
                    job_title=job_title,
                    company_name=company,
                    requirements=requirements or "not stated",
                    gap_skills=", ".join((gap_skills or [])[:12]) or "none",
                    company_context=f"- About them: {context}" if context else "",
                ),
                SYSTEM_PROMPT,
                temperature=0.6,
                max_tokens=1400,
            )
        except LLMUnavailable as exc:
            logger.warning("Cover letter fell back to template: %s", exc)
            return self._template(user_name, user_profile, matched_skills, job_title, company)

        from services.resume_generator import plain_text

        paragraphs = self._clean(letter, user_name).split("\n\n")
        return "\n\n".join(plain_text(part) for part in paragraphs)

    @staticmethod
    def _clean(letter: str, user_name: str) -> str:
        text = (letter or "").strip()
        # Models sometimes wrap the letter in a code fence or add a subject line.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith(("text", "markdown")):
                text = text.split("\n", 1)[-1]
        lines = [ln for ln in text.splitlines() if not ln.lower().startswith(("subject:", "re:", "date:"))]
        text = "\n".join(lines).strip()

        if user_name and user_name.lower() not in text[-160:].lower():
            text = f"{text}\n\nSincerely,\n{user_name}"
        return text

    @staticmethod
    def _template(
        user_name: str,
        user_profile: dict[str, Any],
        matched_skills: list[str],
        job_title: str,
        company: str,
    ) -> str:
        # Used when no AI is available: built only from the candidate's own words,
        # so it can never claim anything their profile does not say.
        summary = (user_profile.get("professional_summary") or "").strip()
        headline = (user_profile.get("headline") or "").strip()
        achievements = [str(a).strip().rstrip(".") for a in (user_profile.get("achievements") or []) if str(a).strip()][:3]
        skills = ", ".join(matched_skills[:4])

        paragraphs = [f"Dear Hiring Team at {company},"]
        opening = f"I am writing to apply for the {job_title} role."
        if headline:
            opening += f" My background is in {headline.replace(' | ', ', ')}."
        paragraphs.append(opening)
        if summary:
            paragraphs.append(summary)
        if achievements:
            paragraphs.append("A few highlights: " + "; ".join(achievements) + ".")
        if skills:
            paragraphs.append(f"Skills from my experience that match this role include {skills}.")
        paragraphs.append(
            f"I would welcome the chance to discuss how my experience fits {company}'s needs. "
            "Thank you for your consideration."
        )
        paragraphs.append(f"Sincerely,\n{user_name or 'The candidate'}")
        return "\n\n".join(paragraphs)
