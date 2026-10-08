"""Discovery pipeline: scrape -> dedupe -> persist -> match -> draft applications.

This is the heart of the product. It is driven both by the manual "Run
discovery" button and by the background scheduler, and it streams progress
events so the dashboard can show what is happening live.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agents.orchestrator import JobApplicationOrchestrator
from config import settings
from core.events import event_bus
from core.logging_config import get_logger
from database.database import AsyncSessionLocal
from database.models import (
    Application,
    Company,
    Education,
    Experience,
    JobListing,
    Project,
    ScrapeRun,
    Skill,
    User,
    UserJobMatch,
    UserProfile,
    utcnow,
)
from agents.skill_matcher import analyse_job_description, score_match as heuristic_score
from scrapers.base_scraper import ScrapedJob, has_description, tokenize_query
from scrapers.registry import ScraperRegistry
from services.ats import BoardRef, fetch_board
from services.job_verifier import JobVerifier, TrustResult, ranking_key
from utils.llm_keys import DeferJob, current_keyring

logger = get_logger("pipeline")

_REMOTE_WORDS = {"remote", "anywhere", "worldwide", "hybrid", "work from home", "wfh"}

DEFAULT_PLATFORMS = ["remoteok", "remotive", "jobicy", "himalayas", "linkedin", "arbeitnow"]
DEFAULT_QUERIES = ["Software Engineer"]
# Only these boards search by place; the others return the same global/remote
# list whatever the location, so they are queried once per run, not per place.
LOCATION_AWARE_PLATFORMS = {"linkedin"}
# Places searched per run (every target location is still used for filtering).
MAX_SEARCH_LOCATIONS = 6
# Jobs without a description are listed as "title only": never above this score,
# never sent to the AI, never auto-queued (the user can paste the description).
TITLE_ONLY_SCORE_CAP = 60.0
TITLE_ONLY_REASON = ("Matched on the job title only: the posting came without a description. "
                     "Add the description to get a real AI match score.")
# Time budgets inside the collect phase (a serverless step is killed at 300s).
COMPANY_FETCH_SECONDS = 60.0
BOARD_SEARCH_SECONDS = 90.0

# The LLM pipeline is the slow part; cap how many jobs one run analyses so a
# manual click returns in a predictable time and token spend stays bounded.
MAX_ANALYSED_PER_RUN = 25
# Jobs whose free rule-based score is this far below the user's minimum are not sent to the AI.
PREFILTER_MARGIN = 22
# Worst-case seconds for one job's AI analysis; no new job starts closer than this to a step's deadline.
ANALYSIS_STEP_SECONDS = 45.0
_ANALYSIS_CONCURRENCY = 3


class JobDiscoveryPipeline:
    """Runs discovery + AI matching for one user."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    # -- phases ------------------------------------------------------------
    # A run is split into bounded phases so it can execute as background-job
    # steps on serverless hosts (each step must finish within the platform's
    # time limit). Progress lives in the database between steps.

    async def start_run(
        self, *, trigger: str = "manual", platforms: list[str] | None = None,
        queries: list[str] | None = None, location: str = "",
    ) -> str:
        async with AsyncSessionLocal() as session:
            context = await self._load_context(session, platforms, queries, location)
            run = ScrapeRun(
                user_id=self.user_id,
                trigger=trigger,
                platforms=context["platforms"],
                queries=context["queries"],
                status="running",
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            return run.id

    async def collect(
        self,
        run_id: str,
        *,
        platforms: list[str] | None = None,
        queries: list[str] | None = None,
        location: str = "",
        limit_per_query: int = 15,
        max_analysed: int = MAX_ANALYSED_PER_RUN,
        deadline: float | None = None,
        skip_platforms: set[str] | None = None,
    ) -> list[str]:
        """Scrape, filter, verify, store and pre-filter. Returns job ids worth AI analysis.

        ``deadline`` (time.monotonic) bounds the whole phase: on a serverless host
        a step that overruns is killed and its work lost, so slow boards are cut
        off and verification is skipped rather than overrunning.
        """
        start = time.monotonic()
        deadline = deadline or start + 240
        async with AsyncSessionLocal() as session:
            context = await self._load_context(session, platforms, queries, location)
        if skip_platforms:
            context["platforms"] = [p for p in context["platforms"] if p not in skip_platforms]
        summary: dict[str, Any] = {"jobs_found": 0, "jobs_new": 0, "verified": 0, "suspicious": 0}
        await self._emit("discovery_started", {"run_id": run_id, "platforms": context["platforms"], "queries": context["queries"]})

        # Company career pages first: they are verified, employer-posted
        # openings, so they get first claim on the per-run analysis budget.
        try:
            company_jobs = await asyncio.wait_for(
                self._scrape_watched_companies(context["queries"]),
                timeout=max(10.0, min(COMPANY_FETCH_SECONDS, deadline - start - 120)),
            )
        except asyncio.TimeoutError:
            logger.warning("Watched-company boards took too long; continuing without them this run")
            company_jobs = []

        # Job boards share one time budget and run concurrently; whatever is
        # still running when it expires is cut off.
        board_budget = max(20.0, min(BOARD_SEARCH_SECONDS, deadline - time.monotonic() - 100))
        global_boards = [p for p in context["platforms"] if p not in LOCATION_AWARE_PLATFORMS]
        place_boards = [p for p in context["platforms"] if p in LOCATION_AWARE_PLATFORMS]
        searches = []
        if global_boards:
            searches.append(ScraperRegistry.search_all(
                platforms=global_boards, queries=context["queries"], location="",
                limit_per_query=limit_per_query, timeout=board_budget,
            ))
        for place in context["locations"] if place_boards else []:
            searches.append(ScraperRegistry.search_all(
                platforms=place_boards, queries=context["queries"], location=place,
                limit_per_query=limit_per_query, timeout=board_budget,
            ))
        board_jobs: list[ScrapedJob] = [job for found in await asyncio.gather(*searches) for job in found]
        seen = {job.dedup_hash for job in company_jobs}
        scraped = company_jobs + [job for job in board_jobs if job.dedup_hash not in seen]
        scraped = self._apply_user_filters(scraped, context)
        summary["jobs_found"] = len(scraped)
        if not scraped:
            await self._save_progress(run_id, summary)
            return []

        # Real-job check, then analyse the most trustworthy postings first.
        # It probes employers' career sites, so it gets only the time that is left.
        trust: dict = {}
        try:
            async with AsyncSessionLocal() as session:
                trust = await asyncio.wait_for(
                    JobVerifier(session).score_all(scraped), timeout=max(10.0, deadline - time.monotonic() - 60)
                )
        except asyncio.TimeoutError:
            logger.warning("Verification ran out of time; saving %d jobs without trust checks", len(scraped))
        scraped.sort(key=lambda job: ranking_key(job, trust.get(job.dedup_hash)))
        summary["verified"] = sum(1 for t in trust.values() if t.label == "verified")
        summary["suspicious"] = sum(1 for t in trust.values() if t.label in ("suspicious", "stale"))

        async with AsyncSessionLocal() as session:
            stored, new_count = await self._persist_jobs(session, scraped, trust)
            summary["jobs_new"] = new_count
            unmatched = await self._filter_unmatched(session, stored)

        # Without a description an AI "match score" is a guess from the title, so
        # those jobs are listed as title-only (no AI tokens) until a description exists.
        described = [job for job in unmatched if has_description(job.get("description_text"))]
        title_only = [job for job in unmatched if not has_description(job.get("description_text"))]
        analysed, prefiltered = self._prefilter(described, context, max_analysed)
        if prefiltered or title_only:
            async with AsyncSessionLocal() as session:
                await self._save_prefiltered(session, prefiltered)
                await self._save_title_only(session, title_only, context)
        summary["title_only"] = len(title_only)
        await self._save_progress(run_id, summary)
        return [job["id"] for job in analysed]

    async def analyse_ids(self, run_id: str, job_ids: list[str], deadline: float) -> tuple[list[str], float | None]:
        """Analyse as many jobs as fit before ``deadline`` (time.monotonic).

        Returns (job ids still pending, defer-until epoch or None). A job whose
        AI call is deferred by rate limits stays pending for the next step.
        """
        if not job_ids:
            return [], None
        async with AsyncSessionLocal() as session:
            context = await self._load_context(session, None, None, "")
            rows = (await session.execute(select(JobListing).where(JobListing.id.in_(job_ids)))).scalars().all()
        by_id = {
            row.id: {"id": row.id, "title": row.title, "company": row.company, "platform": row.platform,
                     "description_text": row.description_text}
            for row in rows
        }
        jobs = [by_id[i] for i in job_ids if i in by_id]
        counters, finished, defer_until = await self._analyse(jobs, context, run_id, deadline)
        await self._save_progress(run_id, counters, accumulate=True)
        pending = [i for i in job_ids if i in by_id and i not in finished]
        return pending, defer_until

    async def finish(self, run_id: str, status: str = "completed", error: str | None = None) -> None:
        async with AsyncSessionLocal() as session:
            run = await session.get(ScrapeRun, run_id)
            if run is None:
                return
            run.status = status
            run.error_message = error
            run.finished_at = utcnow()
            await session.commit()
        await self._emit("discovery_complete" if status == "completed" else "discovery_failed", {"run_id": run_id})

    async def _save_progress(self, run_id: str, values: dict[str, Any], accumulate: bool = False) -> None:
        columns = {
            "jobs_found": "jobs_found", "jobs_new": "jobs_new", "verified": "jobs_verified",
            "suspicious": "jobs_suspicious", "matches_created": "matches_created",
            "applications_drafted": "applications_drafted",
        }
        async with AsyncSessionLocal() as session:
            run = await session.get(ScrapeRun, run_id)
            if run is None:
                return
            for key, column in columns.items():
                if key in values:
                    current = getattr(run, column) or 0
                    setattr(run, column, current + values[key] if accumulate else values[key])
            await session.commit()

    async def run(
        self,
        *,
        trigger: str = "manual",
        platforms: list[str] | None = None,
        queries: list[str] | None = None,
        location: str = "",
        limit_per_query: int = 15,
        max_analysed: int = MAX_ANALYSED_PER_RUN,
    ) -> dict[str, Any]:
        """Whole run in one call (scripts and tests). Production runs go through background jobs."""
        run_id = await self.start_run(trigger=trigger, platforms=platforms, queries=queries, location=location)
        try:
            pending = await self.collect(run_id, platforms=platforms, queries=queries, location=location,
                                         limit_per_query=limit_per_query, max_analysed=max_analysed)
            while pending:
                pending, defer_until = await self.analyse_ids(run_id, pending, deadline=float("inf"))
                if pending and defer_until:
                    await asyncio.sleep(max(1.0, defer_until - time.time()))
            await self.finish(run_id)
        except Exception as exc:
            logger.exception("Discovery run %s failed", run_id)
            await self.finish(run_id, "failed", str(exc)[:500])
        return await run_summary(run_id)

    # -- context -----------------------------------------------------------

    async def _load_context(
        self,
        session: AsyncSession,
        platforms: list[str] | None,
        queries: list[str] | None,
        location: str,
    ) -> dict[str, Any]:
        user = await session.get(User, self.user_id)
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.user_id == self.user_id))
        ).scalar_one_or_none()

        skills = list(
            (await session.execute(select(Skill).where(Skill.user_id == self.user_id))).scalars()
        )
        experiences = list(
            (
                await session.execute(
                    select(Experience)
                    .where(Experience.user_id == self.user_id)
                    .order_by(Experience.display_order)
                )
            ).scalars()
        )
        projects = list(
            (await session.execute(select(Project).where(Project.user_id == self.user_id))).scalars()
        )
        education = list(
            (await session.execute(select(Education).where(Education.user_id == self.user_id))).scalars()
        )

        available = set(ScraperRegistry.list_available_platforms())
        chosen = platforms or (profile.enabled_platforms if profile else None) or DEFAULT_PLATFORMS
        chosen = [p for p in chosen if p in available] or [p for p in DEFAULT_PLATFORMS if p in available]

        chosen_queries = queries or (profile.target_roles if profile else None) or DEFAULT_QUERIES
        chosen_queries = [q for q in chosen_queries if q][:4]

        target_locations = [l.strip() for l in ((profile.target_locations if profile else None) or []) if l and l.strip()]
        # Up to MAX_SEARCH_LOCATIONS places are searched; "Remote" is a preference, not a place.
        place_locations = [l for l in target_locations if l.lower() not in _REMOTE_WORDS]
        search_locations = ([location] if location else (place_locations[:MAX_SEARCH_LOCATIONS] or target_locations[:1])) or [""]

        return {
            "user": user,
            "user_name": (user.name if user else "") or "Applicant",
            "profile": profile,
            "profile_dict": {
                "email": user.email if user else "",
                "phone": profile.phone if profile else "",
                "location": profile.location if profile else "",
                "linkedin_url": profile.linkedin_url if profile else "",
                "github_url": profile.github_url if profile else "",
                "portfolio_url": profile.portfolio_url if profile else "",
                "professional_summary": profile.professional_summary if profile else "",
                "headline": (profile.headline if profile else "") or "",
                "achievements": (profile.achievements if profile else None) or [],
                "target_roles": (profile.target_roles if profile else None) or [],
                "experience_years": profile.experience_years if profile else None,
            },
            "skills": [{"name": s.name, "category": s.category} for s in skills],
            "experiences": [
                {
                    "id": e.id,
                    "title": e.title,
                    "company": e.company,
                    "location": e.location,
                    "start_date": e.start_date,
                    "end_date": e.end_date,
                    "is_current": e.is_current,
                    "bullets": e.bullets or [],
                    "technologies": e.technologies or [],
                }
                for e in experiences
            ],
            "projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "technologies": p.technologies or [],
                    "url": p.url,
                }
                for p in projects
            ],
            "education": [
                {
                    "id": e.id,
                    "institution": e.institution,
                    "degree": e.degree,
                    "field": e.field,
                    "start_date": e.start_date,
                    "end_date": e.end_date,
                    "gpa": e.gpa,
                }
                for e in education
            ],
            "platforms": chosen,
            "queries": chosen_queries,
            "locations": search_locations,
            "place_locations": [l.lower() for l in place_locations],
            "remote_preference": ((profile.remote_preference if profile else None) or "any"),
            "max_job_age_days": (profile.max_job_age_days if profile and profile.max_job_age_days else None),
            "min_score": (profile.auto_apply_min_score if profile else None) or settings.MIN_MATCH_SCORE_TO_TAILOR,
            "excluded_companies": {c.lower() for c in ((profile.excluded_companies if profile else None) or [])},
            "excluded_keywords": [k.lower() for k in ((profile.keywords_exclude if profile else None) or [])],
            "llm_provider": profile.llm_provider if profile else None,
            "template": (profile.preferred_template if profile else None) or "classic",
        }

    async def _scrape_watched_companies(self, queries: list[str]) -> list[ScrapedJob]:
        """Open roles from the user's watched company boards that fit a target role."""
        async with AsyncSessionLocal() as session:
            companies = list(
                (
                    await session.execute(
                        select(Company).where(Company.user_id == self.user_id, Company.is_active.is_(True))
                    )
                ).scalars()
            )
        if not companies:
            return []

        semaphore = asyncio.Semaphore(settings.SCRAPER_CONCURRENCY)

        async def fetch_one(company: Company) -> tuple[Company, list[ScrapedJob], str | None]:
            async with semaphore:
                try:
                    jobs = await fetch_board(BoardRef(company.ats, company.ats_slug), company.name)
                    return company, jobs, None
                except Exception as exc:
                    return company, [], str(exc)[:300]

        results = await asyncio.gather(*(fetch_one(c) for c in companies))

        relevant: list[ScrapedJob] = []
        async with AsyncSessionLocal() as session:
            for company, jobs, error in results:
                row = await session.get(Company, company.id)
                if row is not None:
                    row.last_checked_at = utcnow()
                    row.last_error = error
                    if error is None:
                        row.last_job_count = len(jobs)
                relevant.extend(job for job in jobs if title_matches_roles(job.title, queries))
            await session.commit()

        # Newest first, so fresh openings are analysed before stale ones.
        relevant.sort(key=lambda job: job.posted_date or datetime.min, reverse=True)
        logger.info(
            "Watched companies: %d boards checked, %d roles match target titles",
            len(companies),
            len(relevant),
        )
        await self._emit("companies_checked", {"companies": len(companies), "matching_jobs": len(relevant)})
        return relevant

    @staticmethod
    def _apply_user_filters(jobs: list[ScrapedJob], context: dict[str, Any]) -> list[ScrapedJob]:
        excluded_companies = context["excluded_companies"]
        excluded_keywords = context["excluded_keywords"]
        places = context.get("place_locations") or []
        preference = context.get("remote_preference") or "any"
        max_age = context.get("max_job_age_days")
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=max_age) if max_age else None

        def location_ok(job: ScrapedJob) -> bool:
            if preference == "remote":
                return job.is_remote
            here = (job.location or "").lower()
            in_place = any(place.split(",")[0].strip() in here for place in places) if places else True
            if preference == "onsite":
                return in_place and not (job.is_remote and not places)
            return in_place or job.is_remote

        kept: list[ScrapedJob] = []
        seen: set[str] = set()
        for job in jobs:
            if job.dedup_hash in seen:  # the same posting found via several locations
                continue
            seen.add(job.dedup_hash)
            if cutoff and job.posted_date and job.posted_date < cutoff:
                continue
            if not location_ok(job):
                continue
            if job.company.lower() in excluded_companies:
                continue
            haystack = f"{job.title} {job.description[:1000]}".lower()
            if any(keyword in haystack for keyword in excluded_keywords):
                continue
            kept.append(job)

        if len(kept) != len(jobs):
            logger.info("User filters removed %d of %d postings", len(jobs) - len(kept), len(jobs))
        return kept

    # -- persistence -------------------------------------------------------

    async def _persist_jobs(
        self, session: AsyncSession, scraped: list[ScrapedJob], trust: dict[str, TrustResult]
    ) -> tuple[list[JobListing], int]:
        hashes = [job.dedup_hash for job in scraped]
        existing_rows = (
            await session.execute(select(JobListing).where(JobListing.dedup_hash.in_(hashes)))
        ).scalars()
        existing = {row.dedup_hash: row for row in existing_rows}

        stored: list[JobListing] = []
        new_count = 0

        for job in scraped:
            found = existing.get(job.dedup_hash)
            if found is not None:
                # Refresh volatile fields so a re-listed job stays current.
                found.is_active = True
                found.scraped_at = utcnow()
                if job.description and len(job.description) > len(found.description_text or ""):
                    found.description_text = job.description
                self._apply_trust(found, trust.get(job.dedup_hash))
                stored.append(found)
                continue

            listing = JobListing(
                platform=job.platform,
                external_id=job.external_id,
                title=job.title,
                company=job.company,
                company_logo_url=job.company_logo_url,
                location=job.location,
                is_remote=job.is_remote,
                job_type=job.job_type,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                salary_currency=job.salary_currency,
                description_text=job.description,
                description_html=job.description_html,
                url=job.url,
                apply_url=job.apply_url,
                posted_date=job.posted_date,
                tags=job.tags,
                seniority_level=job.seniority_level,
                raw_payload=job.raw,
                dedup_hash=job.dedup_hash,
            )
            self._apply_trust(listing, trust.get(job.dedup_hash))
            session.add(listing)
            try:
                await session.flush()
                stored.append(listing)
                existing[job.dedup_hash] = listing
                new_count += 1
            except IntegrityError:
                # Another concurrent run inserted the same posting.
                await session.rollback()
                duplicate = (
                    await session.execute(
                        select(JobListing).where(JobListing.dedup_hash == job.dedup_hash)
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    stored.append(duplicate)

        await session.commit()
        return stored, new_count

    @staticmethod
    def _apply_trust(listing: JobListing, result: TrustResult | None) -> None:
        if result is None:
            return
        listing.trust_score = result.score
        listing.trust_label = result.label
        listing.trust_flags = result.flags
        listing.verified_url = result.verified_url

    async def _filter_unmatched(
        self, session: AsyncSession, jobs: list[JobListing]
    ) -> list[dict[str, Any]]:
        """Return only jobs this user has not been scored against yet."""
        if not jobs:
            return []
        job_ids = [job.id for job in jobs]
        matched_ids = set(
            (
                await session.execute(
                    select(UserJobMatch.job_id).where(
                        UserJobMatch.user_id == self.user_id,
                        UserJobMatch.job_id.in_(job_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "platform": job.platform,
                "description_text": job.description_text,
            }
            for job in jobs
            # Suspicious or stale postings never reach the AI or the review queue.
            if job.id not in matched_ids and job.trust_label not in ("suspicious", "stale")
        ]

    # -- analysis ----------------------------------------------------------

    def _prefilter(
        self, jobs: list[dict[str, Any]], context: dict[str, Any], limit: int
    ) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], Any]]]:
        """Rank jobs with the free rule-based scorer; only plausible ones reach the AI.

        The AI score is clamped to within 20 points of this baseline, so a job
        whose baseline is far below the threshold can never pass - analysing it
        would only spend the user's tokens.
        """
        skill_names = [s["name"] for s in context["skills"]]
        titles = [e["title"] for e in context["experiences"] if e.get("title")]
        floor = context["min_score"] - PREFILTER_MARGIN
        scored = []
        for job in jobs:
            analysis = analyse_job_description(job["title"], job.get("description_text") or "")
            baseline = heuristic_score(
                candidate_skills=skill_names,
                candidate_years=context["profile_dict"].get("experience_years"),
                candidate_titles=titles,
                target_roles=context["queries"],
                job_title=job["title"],
                jd_analysis=analysis,
            )
            scored.append((job, baseline))
        scored.sort(key=lambda pair: pair[1].score, reverse=True)
        keep = [job for job, baseline in scored if baseline.score >= floor][:limit]
        kept_ids = {job["id"] for job in keep}
        # Below the floor: recorded as skipped so they are never re-analysed.
        # Above the floor but over this run's limit: left for the next run.
        dropped = [(job, b) for job, b in scored if job["id"] not in kept_ids and b.score < floor]
        logger.info("Pre-filter: %d of %d jobs worth AI analysis", len(keep), len(jobs))
        return keep, dropped

    async def _save_prefiltered(self, session: AsyncSession, dropped: list) -> None:
        for job, baseline in dropped:
            session.add(UserJobMatch(
                user_id=self.user_id,
                job_id=job["id"],
                match_score=float(baseline.score),
                matched_skills=baseline.matched_skills,
                gap_skills=baseline.gap_skills,
                reasoning="Skipped before AI analysis: far below your minimum match score.",
                scoring_method="prefilter",
                status="skipped",
            ))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()

    def _title_baseline(self, job: dict[str, Any], context: dict[str, Any]):
        return heuristic_score(
            candidate_skills=[s["name"] for s in context["skills"]],
            candidate_years=context["profile_dict"].get("experience_years"),
            candidate_titles=[e["title"] for e in context["experiences"] if e.get("title")],
            target_roles=context["queries"],
            job_title=job["title"],
            jd_analysis=analyse_job_description(job["title"], ""),
        )

    async def _save_title_only(self, session: AsyncSession, jobs: list[dict[str, Any]], context: dict[str, Any]) -> None:
        for job in jobs:
            baseline = self._title_baseline(job, context)
            session.add(UserJobMatch(
                user_id=self.user_id,
                job_id=job["id"],
                match_score=min(float(baseline.score), TITLE_ONLY_SCORE_CAP),
                matched_skills=[],
                gap_skills=[],
                reasoning=TITLE_ONLY_REASON,
                scoring_method="title_only",
                status="new",
            ))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()

    async def rescore_with_description(self, job_id: str, description: str) -> dict[str, Any]:
        """The user pasted a job's description: store it and analyse that one job with AI.

        Replaces the title-only match; creates a review-queue entry if it now meets
        the user's minimum score. Returns the new match score and status.
        """
        description = description.strip()
        async with AsyncSessionLocal() as session:
            listing = await session.get(JobListing, job_id)
            if listing is None:
                raise ValueError("Job not found")
            listing.description_text = description[:20000]
            existing = (await session.execute(
                select(UserJobMatch).where(UserJobMatch.user_id == self.user_id, UserJobMatch.job_id == job_id)
            )).scalars().all()
            for row in existing:
                await session.delete(row)
            await session.commit()
            context = await self._load_context(session, None, None, "")
        job = {"id": job_id, "title": listing.title, "company": listing.company, "platform": listing.platform,
               "description_text": listing.description_text}
        counters, finished, defer_until = await self._analyse([job], context, "", time.monotonic() + 600)
        async with AsyncSessionLocal() as session:
            match = (await session.execute(
                select(UserJobMatch).where(UserJobMatch.user_id == self.user_id, UserJobMatch.job_id == job_id)
            )).scalars().first()
        if match is None:
            raise RuntimeError("AI is busy (rate limit) - try again in a minute" if defer_until else "Analysis failed")
        return {"match_score": match.match_score, "status": match.status, "scoring_method": match.scoring_method}

    async def _analyse(
        self, jobs: list[dict[str, Any]], context: dict[str, Any], run_id: str, deadline: float = float("inf")
    ) -> tuple[dict[str, int], set[str], float | None]:
        orchestrator = JobApplicationOrchestrator(context["llm_provider"])
        # One concurrent analysis per usable API key: more keys, more parallelism.
        keyring = current_keyring()
        semaphore = asyncio.Semaphore(keyring.parallelism if keyring else _ANALYSIS_CONCURRENCY)
        counters = {"matches_created": 0, "applications_drafted": 0, "skipped_low_score": 0}
        finished: set[str] = set()
        defers: list[float] = []
        lock = asyncio.Lock()

        async def analyse_one(job: dict[str, Any]) -> None:
            async with semaphore:
                # Leave headroom: a job started too close to the deadline would be cut off.
                if defers or time.monotonic() > deadline - ANALYSIS_STEP_SECONDS:
                    return
                try:
                    result = await orchestrator.process_job_application(
                        user_name=context["user_name"],
                        user_profile=context["profile_dict"],
                        skills=context["skills"],
                        experiences=context["experiences"],
                        projects=context["projects"],
                        job_listing=job,
                        min_score=context["min_score"],
                        tailor=False,  # tailored resume + cover letter are made on approval
                    )
                except DeferJob as exc:
                    defers.append(exc.until)
                    return
                except Exception as exc:
                    logger.warning("Analysis failed for %s: %s", job.get("title"), exc)
                    finished.add(job["id"])  # don't retry a job that errors outright
                    return

                async with lock:
                    async with AsyncSessionLocal() as session:
                        await self._save_result(session, job, result, context, counters)
                    finished.add(job["id"])

        await asyncio.gather(*(analyse_one(job) for job in jobs))
        return counters, finished, (min(defers) if defers else None)

    async def _save_result(
        self,
        session: AsyncSession,
        job: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
        counters: dict[str, int],
    ) -> None:
        should_apply = bool(result.get("should_apply"))
        match = UserJobMatch(
            user_id=self.user_id,
            job_id=job["id"],
            match_score=float(result.get("match_score", 0.0)),
            matched_skills=result.get("matched_skills", []),
            gap_skills=result.get("gap_skills", []),
            relevant_projects=(result.get("tailored_data") or {}).get("selected_project_ids", []),
            jd_analysis=result.get("jd_analysis", {}),
            reasoning=result.get("reasoning", "")[:2000],
            scoring_method=result.get("scoring_method", "heuristic"),
            status="queued" if should_apply else "skipped",
        )
        session.add(match)

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return  # already scored by a concurrent run

        counters["matches_created"] += 1

        if not should_apply:
            counters["skipped_low_score"] += 1
            await session.commit()
            return

        # Re-scored job that is already in the review queue: point it at the new score.
        existing_app = (await session.execute(
            select(Application).where(Application.user_id == self.user_id, Application.job_id == job["id"])
        )).scalars().first()
        if existing_app is not None:
            existing_app.match_id = match.id
            await session.commit()
            return

        tailored = result.get("tailored_data") or {}
        application = Application(
            user_id=self.user_id,
            job_id=job["id"],
            match_id=match.id,
            tailored_summary=tailored.get("tailored_summary"),
            tailored_headline=tailored.get("headline"),
            tailored_skill_groups=tailored.get("skill_groups"),
            tailored_bullets=tailored.get("tailored_bullets", {}),
            tailored_skills_order=tailored.get("skills_order", []),
            selected_project_ids=tailored.get("selected_project_ids", []),
            achievements=tailored.get("achievements", []),
            cover_letter=result.get("cover_letter"),
            status="pending",
        )
        session.add(application)
        try:
            await session.flush()
            counters["applications_drafted"] += 1
        except IntegrityError:
            await session.rollback()
            return

        await session.commit()

    # -- bookkeeping -------------------------------------------------------

    async def _finish_run(
        self, run_id: str, summary: dict[str, Any], status: str, error: str | None = None
    ) -> None:
        async with AsyncSessionLocal() as session:
            run = await session.get(ScrapeRun, run_id)
            if run is None:
                return
            run.jobs_found = summary["jobs_found"]
            run.jobs_new = summary["jobs_new"]
            run.matches_created = summary["matches_created"]
            run.applications_drafted = summary["applications_drafted"]
            run.jobs_verified = summary.get("verified", 0)
            run.jobs_suspicious = summary.get("suspicious", 0)
            run.status = status
            run.error_message = error
            run.finished_at = utcnow()
            await session.commit()

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await event_bus.publish(self.user_id, event, payload)


async def run_discovery_for_user(user_id: str, **kwargs: Any) -> dict[str, Any]:
    return await JobDiscoveryPipeline(user_id).run(**kwargs)


async def run_summary(run_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        run = await session.get(ScrapeRun, run_id)
        if run is None:
            return {"run_id": run_id}
        return {
            "run_id": run.id, "status": run.status, "platforms": run.platforms, "queries": run.queries,
            "jobs_found": run.jobs_found, "jobs_new": run.jobs_new, "matches_created": run.matches_created,
            "applications_drafted": run.applications_drafted, "verified": run.jobs_verified,
            "suspicious": run.jobs_suspicious, "errors": [run.error_message] if run.error_message else [],
        }


async def users_due_for_scrape(session: AsyncSession) -> list[str]:
    """User ids whose configured scrape interval has elapsed."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    profiles = (
        await session.execute(
            select(UserProfile).where(UserProfile.is_onboarded.is_(True))
        )
    ).scalars()

    due: list[str] = []
    for profile in profiles:
        interval = max(1, profile.scrape_frequency_hours or 6)
        last = (
            await session.execute(
                # Any recent search counts - a manual run an hour ago makes a scheduled one wasteful.
                select(func.max(ScrapeRun.started_at)).where(ScrapeRun.user_id == profile.user_id)
            )
        ).scalar()
        if last is None or last <= now - timedelta(hours=interval):
            due.append(profile.user_id)
    return due


def title_matches_roles(title: str, roles: list[str]) -> bool:
    """True when the title contains every meaningful word of at least one target role.

    Company boards list every opening (sales, legal, ...), so a loose any-word
    match would flood the queue; "Software Engineer" should match "Senior
    Software Engineer, Payments" but not "Sales Engineer".
    """
    lowered = (title or "").lower()
    for role in roles or []:
        terms = tokenize_query(role)
        if terms and all(term in lowered for term in terms):
            return True
    return not any(tokenize_query(role) for role in roles or [])
