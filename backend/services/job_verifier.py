"""Real-job check: is a posting genuine, current, and on the employer's own site?

Job boards are full of reposts, long-closed roles kept up for "pipeline", and
outright scams. Each posting gets a 0-100 trust score built from signals that
can actually be checked:

* Cross-check against the company's own careers board (Greenhouse/Lever/Ashby).
  Found there -> verified, and the direct employer link is kept so the user can
  apply on the company site. Board exists but role absent -> likely closed.
* Posting age, and how often the same role has been re-posted.
* Scam markers: fees, deposits, chat-app hiring, missing company.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from database.models import AtsLookup, JobListing, utcnow
from scrapers.base_scraper import ScrapedJob, normalise_key
from services.ats import BoardNotFound, BoardRef, detect_board, fetch_board, find_board_in_text

logger = get_logger("verifier")

# New company-board lookups per run. Results are cached, so coverage grows run
# by run without hammering the ATS APIs in any single run.
MAX_NEW_LOOKUPS_PER_RUN = 15
LOOKUP_TTL = timedelta(days=30)

SUSPICIOUS_BELOW = 35

_SCAM_PATTERNS = [
    (re.compile(r"\b(registration|training|joining|processing|application)\s+(fee|charges?)\b", re.I), "Asks for a fee"),
    (re.compile(r"\b(security|refundable)\s+deposit\b", re.I), "Asks for a deposit"),
    (re.compile(r"\b(whats\s?app|telegram)\b", re.I), "Hiring via WhatsApp/Telegram"),
    (re.compile(r"\b(earn|make)\s+(?:up\s+to\s+)?[$₹£€]?\s?\d[\d,]*\s*(?:per|a|/)\s*(day|week)\b", re.I), "'Earn per day' pitch"),
    (re.compile(r"\b(no\s+(interview|experience)\s+(needed|required))\b.*\b(immediate|instant)\b", re.I | re.S), "No-interview instant hire"),
    (re.compile(r"\b(gmail|yahoo|hotmail|outlook)\.com\b", re.I), "Contact is a free email address"),
]

# Hosts of the boards we scrape: an apply link pointing here says nothing about the employer.
_AGGREGATOR_HOSTS = ("linkedin.", "remoteok.", "remotive.", "jobicy.", "himalayas.", "arbeitnow.", "indeed.", "naukri.")


@dataclass
class TrustResult:
    score: float
    label: str
    flags: list[str] = field(default_factory=list)
    verified_url: str | None = None


def _is_company_site(job: ScrapedJob) -> bool:
    return (job.raw or {}).get("source") == "company_site"


def _title_key(title: str) -> str:
    # Drop seniority/req-id noise so "Sr. Backend Engineer (R1234)" ~ "Senior Backend Engineer".
    lowered = re.sub(r"\(.*?\)|\[.*?\]", " ", (title or "").lower())
    lowered = re.sub(r"\b(sr|senior|jr|junior|i{1,3}|iv|level \d)\b\.?", " ", lowered)
    return " ".join(re.findall(r"[a-z0-9+#]+", lowered))


def find_matching_role(title: str, board_jobs: list[ScrapedJob]) -> ScrapedJob | None:
    target = _title_key(title)
    if not target:
        return None
    best, best_ratio = None, 0.0
    for candidate in board_jobs:
        key = _title_key(candidate.title)
        if key == target:
            return candidate
        ratio = SequenceMatcher(None, target, key).ratio()
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best if best_ratio >= 0.85 else None


class JobVerifier:
    """Scores one discovery run's postings. Create one per run (it caches boards)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._boards: dict[tuple[str, str], list[ScrapedJob] | None] = {}
        self._lookups_left = MAX_NEW_LOOKUPS_PER_RUN

    async def score_all(self, jobs: list[ScrapedJob]) -> dict[str, TrustResult]:
        refs = await self._resolve_boards(jobs)
        await self._prefetch_boards({ref for ref in refs.values() if ref is not None})
        repost_counts = await self._repost_counts(jobs)

        results: dict[str, TrustResult] = {}
        for job in jobs:
            key = normalise_key(job.company)
            results[job.dedup_hash] = self._score(job, refs.get(key), repost_counts.get((key, normalise_key(job.title)), 0))
        return results

    # -- signals -------------------------------------------------------------

    def _score(self, job: ScrapedJob, ref: BoardRef | None, reposts: int) -> TrustResult:
        if _is_company_site(job):
            return TrustResult(100.0, "verified", ["Posted on the company's own careers page"], job.url)

        score = 60.0
        flags: list[str] = []
        verified_url: str | None = None
        not_on_board = False

        # 1. Cross-check with the employer's own board.
        board_jobs = self._boards.get((ref.ats, ref.slug)) if ref else None
        if board_jobs is not None:
            match = find_matching_role(job.title, board_jobs)
            if match is not None:
                score += 35
                verified_url = match.apply_url or match.url
                flags.append(f"Also listed on {job.company}'s careers page")
            else:
                score -= 20
                not_on_board = True
                flags.append(f"Not on {job.company}'s careers page - may be closed or a repost")

        # 2. Freshness.
        if job.posted_date:
            age_days = max(0, (datetime.utcnow() - job.posted_date).days)
            if age_days <= 3:
                score += 10
                flags.append("Fresh: posted in the last 3 days")
            elif age_days > 60:
                score -= 25
                flags.append(f"Old: posted {age_days} days ago")
            elif age_days > 30:
                score -= 12
                flags.append(f"Posted {age_days} days ago")
        else:
            score -= 5
            flags.append("No posting date")

        # 3. Repeated reposting of the same role.
        if reposts >= 3:
            score -= 10
            flags.append(f"Same role posted {reposts} times - possibly evergreen/pipeline")

        # 4. Content red flags.
        text = f"{job.title}\n{job.description or ''}"
        scam_hits = [label for pattern, label in _SCAM_PATTERNS if pattern.search(text)]
        if scam_hits:
            score -= 30 + 15 * (len(scam_hits) - 1)
            flags.extend(scam_hits)
        if not job.company or job.company == "Unknown":
            score -= 25
            flags.append("Company name missing")
        if len(job.description or "") < 300:
            score -= 10
            flags.append("Very short description")

        score = max(0.0, min(100.0, score))
        if verified_url:
            label = "verified"
        elif scam_hits and (score < SUSPICIOUS_BELOW or len(scam_hits) >= 2):
            label = "suspicious"
        elif score < SUSPICIOUS_BELOW:
            label = "stale" if any(f.startswith(("Old:", "Same role", "Posted ")) for f in flags) else "suspicious"
        elif not_on_board:
            label = "unconfirmed"
        else:
            label = "likely_real"
        return TrustResult(round(score, 1), label, flags, verified_url)

    # -- company boards ------------------------------------------------------

    async def _resolve_boards(self, jobs: list[ScrapedJob]) -> dict[str, BoardRef | None]:
        """Company key -> careers board, using links in the posting, the cache, then probing."""
        refs: dict[str, BoardRef | None] = {}
        names: dict[str, str] = {}
        for job in jobs:
            if _is_company_site(job) or not job.company or job.company == "Unknown":
                continue
            key = normalise_key(job.company)
            if not key or refs.get(key):
                continue
            # The apply link often points straight at the employer's ATS.
            ref = None
            for link in (job.apply_url, job.url):
                if link and not any(host in link for host in _AGGREGATOR_HOSTS):
                    ref = find_board_in_text(link) or ref
            refs[key] = ref
            names.setdefault(key, job.company)

        unknown = [key for key, ref in refs.items() if ref is None]
        if unknown:
            cached = {
                row.company_key: row
                for row in (
                    await self.session.execute(select(AtsLookup).where(AtsLookup.company_key.in_(unknown)))
                ).scalars()
            }
            fresh_cutoff = utcnow() - LOOKUP_TTL
            to_probe: list[str] = []
            for key in unknown:
                row = cached.get(key)
                if row is not None and row.checked_at and row.checked_at >= fresh_cutoff:
                    refs[key] = BoardRef(row.ats, row.ats_slug) if row.found else None
                else:
                    to_probe.append(key)

            to_probe = to_probe[: self._lookups_left]
            self._lookups_left -= len(to_probe)
            probed = await asyncio.gather(*(self._probe(names[key]) for key in to_probe))
            for key, ref in zip(to_probe, probed):
                refs[key] = ref
                row = cached.get(key) or AtsLookup(company_key=key)
                row.found = ref is not None
                row.ats = ref.ats if ref else None
                row.ats_slug = ref.slug if ref else None
                row.checked_at = utcnow()
                self.session.add(row)
            await self.session.commit()
        return refs

    @staticmethod
    async def _probe(company: str) -> BoardRef | None:
        try:
            return await detect_board(company, "")
        except BoardNotFound:
            return None
        except Exception as exc:
            logger.debug("Board probe failed for %s: %s", company, exc)
            return None

    async def _prefetch_boards(self, refs: set[BoardRef]) -> None:
        async def load(ref: BoardRef) -> None:
            try:
                self._boards[(ref.ats, ref.slug)] = await fetch_board(ref)
            except Exception as exc:
                logger.debug("Could not load board %s/%s: %s", ref.ats, ref.slug, exc)
                self._boards[(ref.ats, ref.slug)] = None

        semaphore = asyncio.Semaphore(4)

        async def guarded(ref: BoardRef) -> None:
            async with semaphore:
                await load(ref)

        await asyncio.gather(*(guarded(ref) for ref in refs))

    async def _repost_counts(self, jobs: list[ScrapedJob]) -> dict[tuple[str, str], int]:
        """How many distinct listings exist for each (company, title) pair."""
        companies = {job.company for job in jobs if job.company}
        if not companies:
            return {}
        rows = (
            await self.session.execute(
                select(JobListing.company, JobListing.title, func.count(JobListing.id))
                .where(JobListing.company.in_(companies))
                .group_by(JobListing.company, JobListing.title)
            )
        ).all()
        return {(normalise_key(c), normalise_key(t)): n for c, t, n in rows}


def ranking_key(job: ScrapedJob, trust: TrustResult | None) -> tuple:
    """Analysis order: employer-posted first, then verified, then most trustworthy and newest."""
    label_rank = {"verified": 0, "likely_real": 1, "unconfirmed": 2, "stale": 3, "suspicious": 4}
    return (
        0 if _is_company_site(job) else 1,
        label_rank.get(trust.label if trust else "", 2),
        -(trust.score if trust else 50.0),
        -(job.posted_date.timestamp() if job.posted_date else 0.0),
    )
