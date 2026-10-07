"""Suggest job titles to search for, from the candidate's own history.

Two layers:
* past titles, cleaned into the form job boards use
  ("Program Consultant – AI & Robotics" -> "AI & Robotics Program Consultant");
* with the user's AI key, adjacent titles their actual work supports
  (e.g. curriculum + lab + program delivery -> "Technical Program Manager"),
  each with a one-line reason. Never a seniority above what the history shows.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from core.logging_config import get_logger
from utils.llm_client import LLMUnavailable, get_llm_client

logger = get_logger("titles")

MAX_SUGGESTIONS = 10

_SPLIT_RE = re.compile(r"\s+[–—-]\s+|\s*,\s*|\s*\(\s*")
_SENIORITY_RE = re.compile(r"^(senior|sr\.?|junior|jr\.?|lead|principal|staff|associate|assistant)\s+", re.I)

PROMPT = """Suggest job titles this candidate should search for on job boards.

HEADLINE: {headline}
SUMMARY: {summary}
SKILLS: {skills}
WORK HISTORY (most recent first):
{history}

Return up to {limit} titles that recruiters actually post, most suitable first:
- include the candidate's most recent titles in their standard job-board form;
- add adjacent titles that the work described clearly supports (similar responsibilities, a
  different common name), but never a level above what the history shows
  (no "Director"/"VP"/"Head of" unless they held one);
- no duplicates or near-duplicates, no company names.

Return JSON: {{"suggestions": [{{"title": "...", "why": "one short line citing their experience", "kind": "past_title" | "adjacent"}}]}}
"""


def clean_title(title: str) -> str:
    """'Program Consultant – AI & Robotics' -> 'AI & Robotics Program Consultant'."""
    raw = re.sub(r"\s+", " ", title or "").strip()
    if not raw:
        return ""
    parts = [p.strip(" )") for p in _SPLIT_RE.split(raw) if p.strip(" )")]
    if len(parts) >= 2 and len(parts[1].split()) <= 4 and "/" not in parts[1]:
        # Trailing specialisation becomes a prefix: "<seniority> <area> <role>".
        seniority = _SENIORITY_RE.match(parts[0])
        role = parts[0][seniority.end():] if seniority else parts[0]
        return f"{seniority.group(0) if seniority else ''}{parts[1]} {role}".strip()
    return parts[0]


def _key(title: str) -> frozenset:
    """Word-set identity: 'Senior AI & Robotics Program Manager' == 'Senior Program Manager, AI & Robotics'."""
    return frozenset(w for w in re.findall(r"[a-z0-9+#/]+", title.lower()) if w not in {"and", "of", "the"})


def past_titles(experiences: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for exp in experiences:
        cleaned = clean_title(exp.get("title") or "")
        key = _key(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        company = exp.get("company") or ""
        out.append({"title": cleaned, "why": f"Your title at {company}" if company else "One of your past titles", "kind": "past_title"})
    return out


async def suggest_titles(profile: dict[str, Any], experiences: list[dict[str, Any]], skills: list[str]) -> dict[str, Any]:
    base = past_titles(experiences)
    result = {"suggestions": base[:MAX_SUGGESTIONS], "source": "history", "generated_at": datetime.now(timezone.utc).isoformat()}

    llm = get_llm_client(interactive=True)
    if not llm.is_available or not experiences:
        return result

    history = "\n".join(
        f"- {e.get('title')} at {e.get('company')} ({e.get('start_date') or ''} - {e.get('end_date') or ''}): "
        + " ".join(str(b) for b in (e.get("bullets") or [])[:3])
        for e in experiences[:10]
    )
    try:
        data = await llm.generate_json(
            PROMPT.format(
                headline=profile.get("headline") or "n/a",
                summary=(profile.get("professional_summary") or "")[:800] or "n/a",
                skills=", ".join(skills[:30]) or "n/a",
                history=history[:4000],
                limit=MAX_SUGGESTIONS,
            ),
            "You are an experienced technical recruiter who knows how job titles are written on job boards.",
            temperature=0.3,
            max_tokens=1500,
        )
    except LLMUnavailable as exc:
        logger.info("Title suggestions fell back to past titles: %s", exc)
        return result

    held_senior = any(re.search(r"\b(director|vp|vice president|head of|chief|cto|ceo)\b", e.get("title") or "", re.I) for e in experiences)
    # Past titles always come from the real history; the AI only adds related titles.
    suggestions: list[dict[str, str]] = list(base[:6])
    seen: set = {_key(item["title"]) for item in suggestions}
    for item in data.get("suggestions") or []:
        if not isinstance(item, dict) or item.get("kind") == "past_title":
            continue
        title = clean_title(str(item.get("title") or ""))[:80]
        if not title or _key(title) in seen:
            continue
        # Guard against inflated seniority the history doesn't support.
        if not held_senior and re.search(r"\b(director|vp|vice president|head of|chief|cto)\b", title, re.I):
            continue
        seen.add(_key(title))
        suggestions.append({"title": title, "why": str(item.get("why") or "").strip()[:160], "kind": "adjacent"})

    result.update(suggestions=suggestions[:MAX_SUGGESTIONS + 4], source="ai")
    return result


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
