"""Match scoring agent.

The deterministic scorer always runs. When an LLM is available its judgement is
blended in, but it is clamped to the heuristic's neighbourhood so a
hallucinating model cannot push an irrelevant job to the top of the queue.
"""
from __future__ import annotations

from typing import Any

from agents.skill_matcher import MatchResult, canonicalise, score_match
from core.logging_config import get_logger
from utils.llm_client import LLMUnavailable, get_llm_client

logger = get_logger("agent.match")

SYSTEM_PROMPT = (
    "You are a precise hiring analyst. You judge how well a candidate fits a role "
    "based only on the evidence given. You are sceptical and never inflate scores."
)

PROMPT = """Score how well this candidate matches this job.

CANDIDATE
- Skills: {skills}
- Years of experience: {years}
- Recent titles: {titles}
- Target roles: {target_roles}
- Summary: {summary}
- Projects: {projects}

JOB
- Title: {job_title}
- Seniority: {seniority}
- Required skills: {required_skills}
- Preferred skills: {preferred_skills}
- Key qualifications: {qualifications}

A rule-based scorer independently rated this match {heuristic_score}/100
(matched: {heuristic_matched}; gaps: {heuristic_gaps}).
Use that as an anchor. Only depart from it if the evidence clearly justifies it.

Return JSON:
{{
  "score": <number 0-100>,
  "matched_skills": ["skills the candidate genuinely has that the job wants"],
  "gap_skills": ["required skills the candidate lacks"],
  "relevant_projects": ["names of the candidate's most relevant projects"],
  "reasoning": "two sentences explaining the score"
}}

Scoring guide: 90-100 exceptional fit, 70-89 strong, 60-69 transferable, below 60 weak.
"""

# How far the LLM may move the score away from the deterministic anchor.
_MAX_DEVIATION = 20.0


class MatchScorerAgent:
    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client or get_llm_client()

    async def score_match(
        self,
        user_profile: dict[str, Any],
        skills: list[dict | str],
        experiences: list[dict],
        projects: list[dict],
        job_title: str,
        jd_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        skill_names = [s.get("name") if isinstance(s, dict) else str(s) for s in skills]
        skill_names = [s for s in skill_names if s]
        titles = [e.get("title", "") for e in experiences if e.get("title")]
        years = user_profile.get("experience_years")
        target_roles = user_profile.get("target_roles") or []

        baseline = score_match(
            candidate_skills=skill_names,
            candidate_years=years,
            candidate_titles=titles,
            target_roles=target_roles,
            job_title=job_title,
            jd_analysis=jd_analysis,
        )

        if not self.llm.is_available:
            return baseline.to_dict()

        try:
            result = await self.llm.generate_json(
                PROMPT.format(
                    skills=", ".join(skill_names[:40]) or "not provided",
                    years=years if years is not None else "not stated",
                    titles=", ".join(titles[:5]) or "not provided",
                    target_roles=", ".join(target_roles) or "not specified",
                    summary=(user_profile.get("professional_summary") or "not provided")[:600],
                    projects="; ".join(
                        f"{p.get('name')}: {(p.get('description') or '')[:120]}" for p in projects[:5]
                    )
                    or "none",
                    job_title=job_title,
                    seniority=jd_analysis.get("seniority_level", "mid"),
                    required_skills=", ".join(jd_analysis.get("required_skills", [])) or "not listed",
                    preferred_skills=", ".join(jd_analysis.get("preferred_skills", [])) or "none",
                    qualifications="; ".join(jd_analysis.get("key_qualifications", [])[:5]) or "none",
                    heuristic_score=round(baseline.score),
                    heuristic_matched=", ".join(baseline.matched_skills[:8]) or "none",
                    heuristic_gaps=", ".join(baseline.gap_skills[:6]) or "none",
                ),
                SYSTEM_PROMPT,
                max_tokens=1200,
            )
        except LLMUnavailable as exc:
            logger.warning("Match scoring fell back to heuristics: %s", exc)
            return baseline.to_dict()

        return self._blend(result, baseline, skill_names).to_dict()

    @staticmethod
    def _blend(result: dict[str, Any], baseline: MatchResult, candidate_skills: list[str]) -> MatchResult:
        raw_score = result.get("score")
        try:
            llm_score = float(raw_score)
        except (TypeError, ValueError):
            llm_score = baseline.score

        llm_score = max(0.0, min(100.0, llm_score))
        # Clamp to the heuristic's neighbourhood, then average the two views.
        clamped = max(baseline.score - _MAX_DEVIATION, min(baseline.score + _MAX_DEVIATION, llm_score))
        final = round((clamped * 0.6) + (baseline.score * 0.4), 1)

        owned = {canonicalise(s).lower() for s in candidate_skills}
        owned |= {s.lower() for s in candidate_skills}

        # The candidate cannot "match" a skill they never claimed.
        matched = [
            str(s).strip()
            for s in (result.get("matched_skills") or [])
            if str(s).strip() and (canonicalise(str(s)).lower() in owned or str(s).lower() in owned)
        ]
        if not matched:
            matched = baseline.matched_skills

        gaps = [str(s).strip() for s in (result.get("gap_skills") or []) if str(s).strip()]
        reasoning = str(result.get("reasoning") or baseline.reasoning)[:600]

        return MatchResult(
            score=final,
            matched_skills=list(dict.fromkeys(matched))[:15],
            gap_skills=list(dict.fromkeys(gaps or baseline.gap_skills))[:10],
            reasoning=reasoning,
            method="llm+heuristic",
        )
