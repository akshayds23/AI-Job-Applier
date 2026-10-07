"""Multi-agent pipeline: JD analysis -> scoring -> tailoring -> guardrails -> letter.

Stages after scoring are skipped when a job is clearly not worth the tokens,
which is what keeps a 200-job scrape affordable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agents.cover_letter import CoverLetterAgent
from agents.guardrails import GuardrailsAgent
from agents.jd_analyzer import JDAnalyzerAgent
from agents.match_scorer import MatchScorerAgent
from agents.resume_tailor import ResumeTailorAgent
from config import settings
from core.logging_config import get_logger
from utils.llm_client import get_llm_client

logger = get_logger("orchestrator")


@dataclass
class PipelineResult:
    should_apply: bool
    match_score: float
    matched_skills: list[str] = field(default_factory=list)
    gap_skills: list[str] = field(default_factory=list)
    reasoning: str = ""
    scoring_method: str = "heuristic"
    jd_analysis: dict[str, Any] = field(default_factory=dict)
    tailored_data: dict[str, Any] | None = None
    cover_letter: str | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    skipped_reason: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_apply": self.should_apply,
            "match_score": self.match_score,
            "matched_skills": self.matched_skills,
            "gap_skills": self.gap_skills,
            "reasoning": self.reasoning,
            "scoring_method": self.scoring_method,
            "jd_analysis": self.jd_analysis,
            "tailored_data": self.tailored_data,
            "cover_letter": self.cover_letter,
            "verification": self.verification,
            "skipped_reason": self.skipped_reason,
            "duration_ms": self.duration_ms,
        }


class JobApplicationOrchestrator:
    """Coordinates the agents for a single job."""

    def __init__(self, llm_provider: str | None = None) -> None:
        llm = get_llm_client(llm_provider)
        self.jd_analyzer = JDAnalyzerAgent(llm)
        self.match_scorer = MatchScorerAgent(llm)
        self.resume_tailor = ResumeTailorAgent(llm)
        self.cover_letter_gen = CoverLetterAgent(llm)
        self.guardrails = GuardrailsAgent()

    async def process_job_application(
        self,
        user_name: str,
        user_profile: dict[str, Any],
        skills: list[dict | str],
        experiences: list[dict],
        projects: list[dict],
        job_listing: dict[str, Any],
        *,
        min_score: float | None = None,
        generate_cover_letter: bool = True,
        tailor: bool = True,
    ) -> dict[str, Any]:
        started = time.monotonic()
        threshold = settings.MIN_MATCH_SCORE_TO_TAILOR if min_score is None else min_score

        job_title = job_listing.get("title", "")
        company = job_listing.get("company", "")
        jd_text = job_listing.get("description_text", "") or ""

        logger.info("Pipeline start: %s at %s", job_title, company)

        # 1. Understand the job
        jd_analysis = await self.jd_analyzer.analyze(job_title, company, jd_text)

        # 2. Score the fit
        match = await self.match_scorer.score_match(
            user_profile=user_profile,
            skills=skills,
            experiences=experiences,
            projects=projects,
            job_title=job_title,
            jd_analysis=jd_analysis,
        )
        score = float(match.get("score", 0.0))

        result = PipelineResult(
            should_apply=False,
            match_score=score,
            matched_skills=match.get("matched_skills", []),
            gap_skills=match.get("gap_skills", []),
            reasoning=match.get("reasoning", ""),
            scoring_method=match.get("method", "heuristic"),
            jd_analysis=jd_analysis,
        )

        # 3. Gate on score before spending tokens on tailoring
        if score < threshold:
            result.skipped_reason = f"Match score {score:.0f} is below the {threshold:.0f} threshold"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("Pipeline skip: %s at %s (%.0f < %.0f)", job_title, company, score, threshold)
            return result.to_dict()

        if not tailor:
            # Discovery only scores; tailoring runs when the user approves the job.
            result.should_apply = True
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result.to_dict()

        # 4. Tailor the resume content
        tailored = await self.resume_tailor.tailor(
            user_profile=user_profile,
            skills=skills,
            experiences=experiences,
            projects=projects,
            job_title=job_title,
            company=company,
            jd_analysis=jd_analysis,
        )

        # 5. Enforce truthfulness (mutates `tailored` in place)
        verification = self.guardrails.enforce(
            skills,
            experiences,
            tailored,
            extra_sources=[user_profile.get("professional_summary") or "", *(user_profile.get("achievements") or [])],
        )

        # 6. Cover letter, then scrub any claim the profile does not support
        cover_letter = None
        if generate_cover_letter:
            cover_letter = await self.cover_letter_gen.generate_cover_letter(
                user_name=user_name,
                user_profile=user_profile,
                matched_skills=result.matched_skills,
                job_title=job_title,
                company_name=company,
                jd_analysis=jd_analysis,
                highlights=tailored.get("achievements"),
                gap_skills=result.gap_skills,
            )
            cover_letter, letter_report = self.guardrails.check_cover_letter(
                cover_letter,
                candidate_years=user_profile.get("experience_years"),
                gap_skills=result.gap_skills,
            )
            verification.violations.extend(letter_report.violations)
            verification.corrections.extend(letter_report.corrections)
            verification.is_valid = verification.is_valid and letter_report.is_valid

        result.should_apply = True
        result.tailored_data = tailored
        result.cover_letter = cover_letter
        result.verification = verification.to_dict()
        result.duration_ms = int((time.monotonic() - started) * 1000)

        logger.info(
            "Pipeline done: %s at %s -> %.0f%% in %dms (%d guardrail corrections)",
            job_title,
            company,
            score,
            result.duration_ms,
            len(verification.violations),
        )
        return result.to_dict()
