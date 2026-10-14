"""Suggest companies to watch, for the user's own field and places.

The AI proposes employers that hire for the user's target roles; each one is then
checked against the real careers board (Greenhouse, Lever or Ashby). Only companies
whose board AutoApplier can read are returned, ranked by how many open roles match
the user's titles and locations right now. A name the AI made up simply fails the check.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from core.logging_config import get_logger
from services.ats import BoardNotFound, detect_board, fetch_board
from services.job_pipeline import _REMOTE_WORDS, location_allowed, title_matches_roles
from utils.llm_client import LLMUnavailable, get_llm_client

logger = get_logger("company_suggester")

ASK_FOR = 30            # names requested from the AI; many fail the board check
MAX_RESULTS = 15
CHECK_CONCURRENCY = 6
CHECK_TIMEOUT_SECONDS = 15
TOTAL_BUDGET_SECONDS = 75

NO_KEY_NOTE = "Add an AI key in Settings to get company suggestions for your field."
NO_ROLES_NOTE = "Add the job titles you want in Settings first, so suggestions match your field."

PROMPT = """Suggest employers this candidate should watch for job openings.

TARGET ROLES: {roles}
PLACES: {places}
REMOTE PREFERENCE: {remote}
HEADLINE: {headline}
SKILLS: {skills}
RECENT WORK: {history}
ALREADY WATCHING (do not repeat): {watching}
EXCLUDED BY THE CANDIDATE (never suggest): {excluded}

Return up to {limit} real, currently operating companies that regularly hire for these roles
in these places (or remotely, if allowed), in the candidate's own industry and adjacent ones.
Strongly prefer companies whose careers page runs on Greenhouse, Lever or Ashby; skip
companies that only hire through Workday, Taleo, SuccessFactors or their own portal.
Mix well-known employers with growing mid-size ones. No recruitment agencies.

Return JSON: {{"companies": [{{"name": "...", "careers_url": "board or careers page URL if you know it, else empty", "why": "one short line on why it fits this candidate"}}]}}
"""

async def _check(entry: dict[str, str], roles: list[str], places: list[str], preference: str) -> dict[str, Any] | None:
    name = entry["name"]
    try:
        ref = await detect_board(name, entry.get("careers_url") or "")
        jobs = await fetch_board(ref, name)
    except (BoardNotFound, ConnectionError, ValueError) as exc:
        logger.debug("No readable board for %s: %s", name, exc)
        return None
    except Exception as exc:  # one odd board must not break the whole list
        logger.info("Board check failed for %s: %s", name, exc)
        return None
    if not jobs:
        return None

    # Same title and location rules as a real search, so the count is what a search would find.
    matching = [
        job for job in jobs
        if title_matches_roles(job.title, roles)
        and location_allowed(job.location, bool(getattr(job, "is_remote", False)), places, preference)
    ]
    return {
        "name": name,
        "careers_url": ref.careers_url,
        "ats": ref.ats,
        "slug": ref.slug,
        "why": entry.get("why", ""),
        "open_roles": len(jobs),
        "matching_roles": len(matching),
        "sample_titles": [job.title for job in matching[:3]],
    }


async def suggest_companies(
    *,
    roles: list[str],
    places: list[str],
    remote_preference: str,
    headline: str | None,
    skills: list[str],
    history: list[str],
    watching: list[str],
    excluded: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "suggestions": [],
        "source": "none",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not roles:
        result["note"] = NO_ROLES_NOTE
        return result

    llm = get_llm_client(interactive=True)
    if not llm.is_available:
        result["note"] = NO_KEY_NOTE
        return result

    try:
        data = await llm.generate_json(
            PROMPT.format(
                roles=", ".join(roles[:8]),
                places=", ".join(places[:6]) or "any",
                remote={"remote": "remote only", "onsite": "on-site or hybrid only"}.get(remote_preference, "remote or on-site"),
                headline=headline or "n/a",
                skills=", ".join(skills[:25]) or "n/a",
                history="; ".join(history[:4]) or "n/a",
                watching=", ".join(watching[:60]) or "none",
                excluded=", ".join(excluded[:40]) or "none",
                limit=ASK_FOR,
            ),
            "You are a recruiter who knows which companies hire for which roles and which applicant-tracking system each one uses.",
            temperature=0.4,
            max_tokens=2500,
        )
    except LLMUnavailable as exc:
        logger.info("Company suggestions unavailable: %s", exc)
        result["note"] = str(exc) or NO_KEY_NOTE
        return result

    skip = {n.strip().lower() for n in watching + excluded if n}
    entries: list[dict[str, str]] = []
    for item in data.get("companies") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:80]
        if not name or name.lower() in skip:
            continue
        skip.add(name.lower())
        entries.append({
            "name": name,
            "careers_url": str(item.get("careers_url") or "").strip()[:300],
            "why": str(item.get("why") or "").strip()[:160],
        })

    filter_places = [p.lower() for p in places if p.lower() not in _REMOTE_WORDS]
    gate = asyncio.Semaphore(CHECK_CONCURRENCY)

    async def bounded(entry: dict[str, str]) -> dict[str, Any] | None:
        async with gate:
            try:
                return await asyncio.wait_for(_check(entry, roles, filter_places, remote_preference), CHECK_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                return None

    tasks = [asyncio.create_task(bounded(e)) for e in entries[:ASK_FOR]]
    done, pending = await asyncio.wait(tasks, timeout=TOTAL_BUDGET_SECONDS) if tasks else (set(), set())
    for task in pending:
        task.cancel()

    found = [t.result() for t in done if not t.cancelled() and t.exception() is None and t.result()]
    # Companies with matching roles first; others with open roles fill the list.
    found.sort(key=lambda c: (c["matching_roles"] > 0, c["matching_roles"], c["open_roles"]), reverse=True)
    seen_boards: set[tuple[str, str]] = set()
    unique = []
    for company in found:
        key = (company["ats"], company["slug"].lower())
        if key not in seen_boards:
            seen_boards.add(key)
            unique.append(company)

    result.update(suggestions=unique[:MAX_RESULTS], source="ai", checked=len(entries))
    if not unique:
        result["note"] = "None of the suggested companies had a careers page AutoApplier can read. Try again, or add companies by name."
    logger.info("Company suggestions: %d asked, %d with readable boards", len(entries), len(unique))
    return result
