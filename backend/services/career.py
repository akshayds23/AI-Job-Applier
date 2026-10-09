"""Career profile: everything the user has done beyond their resume.

Facts come from GitHub repositories, a portfolio page, an uploaded document or
the user typing them in. An LLM turns raw text into candidate facts:

* ``project``   - a named project with a description and technologies
* ``highlight`` - a statement of work, attached to one of the user's jobs
* ``skill``     - a skill or tool

Candidates start as ``pending``; only ``approved`` ones are used. Approved facts
are merged into the same experience / project / skill lists that scoring,
tailoring, the truthfulness check and the resume generator read, so the AI can
pick them for a job and every line still traces back to something the user
confirmed. They are stored apart from the resume, so re-uploading it keeps them.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.http import request_with_retry
from core.logging_config import get_logger
from database.models import CareerFact, Experience, Project, Skill
from scrapers.base_scraper import html_to_text

logger = get_logger("career")

MAX_TEXT_CHARS = 12000        # per AI call
MAX_DOCUMENT_CHUNKS = 3       # long documents are read in up to 3 parts
GITHUB_REPOS = 12             # most recently pushed, non-fork repositories
GITHUB_README_CHARS = 2800
GITHUB_BATCH = 4              # repositories per AI call

SYSTEM_PROMPT = (
    "You extract a candidate's verifiable work from documents for their resume. "
    "You only report what the text states. You never invent technologies, numbers or outcomes."
)

EXTRACT_PROMPT = """Extract the candidate's work from this {source_kind}.

{context}

Return JSON:
{{
  "projects": [{{"name": "", "description": "", "technologies": [""], "url": ""}}],
  "highlights": [{{"text": "", "skills": [""]}}],
  "skills": [""]
}}

Rules:
- projects: things the candidate built. description = 2-4 plain sentences on what it does and how
  (architecture, notable techniques), each ending with a period. technologies = languages,
  frameworks, services named in the text.
- highlights: accomplishments in a job - systems built, set up, integrated or shipped - as resume
  statements starting with a verb, e.g. "Set up daily backups with offsite copies on Cloudflare R2."
  Keep the technologies. At most 8, most significant first. NOT routine procedures or runbook steps
  (checking logs, running health checks, resetting passwords, verifying with curl).
- skills: tools, platforms, languages and techniques used (short names, e.g. "Docker", "Traefik").
- Write generically: never include file paths, container, bucket, database or server names,
  usernames, IP addresses, credentials, internal URLs, people's names, customer names or prices.
- Use [] for anything not present. Do not repeat the same item in two lists.

TEXT:
{text}
"""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


# Models sometimes copy internal identifiers despite the rules; scrub the common shapes.
_SCRUB = [
    (re.compile(r"\s*(?:under|at|in|from|to)?\s*`?(?:~|/)[\w.\-]+(?:/[\w.\-]+)+`?"), ""),        # file paths
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), ""),                                            # IP addresses
    (re.compile(r"\s*(?:bucket|container|containers|database|server|host)\s+`?[a-z0-9]+(?:[-_][a-z0-9]+)+`?", re.I),
     lambda m: " " + m.group(0).split()[0]),                                                  # "bucket foo-bar"
    (re.compile(r"`?\b[a-z0-9]+(?:[-_][a-z0-9]+){2,}\b`?"), ""),                                 # foo-bar-baz ids
    (re.compile(r"\S+@\S+\.\w+"), ""),                                                          # e-mail addresses
]


def scrub(text: str) -> str:
    for pattern, repl in _SCRUB:
        text = pattern.sub(repl, text)
    text = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()
    return text


def _clean_list(values: Any, limit: int = 15, length: int = 60) -> list[str]:
    out: list[str] = []
    for value in values or []:
        text = re.sub(r"\s+", " ", str(value or "")).strip()[:length]
        if text and text.lower() not in {v.lower() for v in out}:
            out.append(text)
    return out[:limit]


async def extract_facts(llm, text: str, source_kind: str, context: str = "") -> dict[str, list]:
    """One AI call: raw text -> candidate projects, highlights and skills."""
    data = await llm.generate_json(
        EXTRACT_PROMPT.format(source_kind=source_kind, context=context, text=text[:MAX_TEXT_CHARS]),
        SYSTEM_PROMPT,
        max_tokens=2500,
    )
    projects = []
    for p in data.get("projects") or []:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            projects.append({
                "name": str(p["name"]).strip()[:255],
                "description": scrub(re.sub(r"\s+", " ", str(p.get("description") or "")))[:2000],
                "technologies": _clean_list(p.get("technologies")),
                "url": str(p.get("url") or "").strip()[:500],
            })
    highlights = []
    for h in data.get("highlights") or []:
        text_h = scrub(re.sub(r"\s+", " ", str((h or {}).get("text") if isinstance(h, dict) else h or "")))
        if len(text_h.split()) >= 5:
            highlights.append({"text": text_h[:400], "skills": _clean_list(h.get("skills") if isinstance(h, dict) else [])})
    return {"projects": projects, "highlights": highlights[:10], "skills": _clean_list(data.get("skills"), 40)}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


async def _known(db: AsyncSession, user_id: str) -> dict[str, set[str]]:
    """Names already in the profile or the career profile, so imports don't add duplicates."""
    projects = {_norm(n) for n in (await db.execute(select(Project.name).where(Project.user_id == user_id))).scalars()}
    skills = {s.lower() for s in (await db.execute(select(Skill.name).where(Skill.user_id == user_id))).scalars()}
    texts: set[str] = set()
    for fact in (await db.execute(select(CareerFact).where(CareerFact.user_id == user_id))).scalars():
        if fact.kind == "project":
            projects.add(_norm(fact.title))
        elif fact.kind == "skill":
            skills.add(fact.title.lower())
        else:
            texts.add(_norm(fact.text or fact.title))
    return {"projects": projects, "skills": skills, "highlights": texts}


async def save_candidates(db: AsyncSession, user_id: str, found: dict[str, list], source: str,
                          source_ref: str, role_company: str | None) -> int:
    """Store new candidates as pending (anything already known is skipped)."""
    known = await _known(db, user_id)
    added = 0
    for p in found["projects"]:
        if _norm(p["name"]) in known["projects"]:
            continue
        known["projects"].add(_norm(p["name"]))
        db.add(CareerFact(user_id=user_id, kind="project", title=p["name"], text=p["description"],
                          skills=p["technologies"], url=p["url"] or None, source=source, source_ref=source_ref))
        added += 1
    for h in found["highlights"]:
        if _norm(h["text"]) in known["highlights"]:
            continue
        known["highlights"].add(_norm(h["text"]))
        db.add(CareerFact(user_id=user_id, kind="highlight", title=h["text"][:255], text=h["text"],
                          skills=h["skills"], role_company=role_company, source=source, source_ref=source_ref))
        added += 1
    for s in found["skills"]:
        if s.lower() in known["skills"]:
            continue
        known["skills"].add(s.lower())
        db.add(CareerFact(user_id=user_id, kind="skill", title=s, source=source, source_ref=source_ref))
        added += 1
    await db.commit()
    return added


async def current_role_company(db: AsyncSession, user_id: str) -> str | None:
    rows = (await db.execute(
        select(Experience).where(Experience.user_id == user_id).order_by(Experience.display_order)
    )).scalars().all()
    current = next((e for e in rows if e.is_current or (e.end_date or "").lower() in {"present", "current"}), None)
    return (current or (rows[0] if rows else None)).company if rows else None


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


async def import_github(db: AsyncSession, user_id: str, username: str, llm) -> dict[str, Any]:
    username = username.strip().strip("/").split("/")[-1]
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        raise ValueError("Enter a GitHub username, e.g. akshayds23")
    response = await request_with_retry(
        "GET", f"https://api.github.com/users/{username}/repos?per_page=100&sort=pushed",
        headers={"Accept": "application/vnd.github+json"}, max_retries=1,
    )
    if response is None or response.status_code != 200:
        raise ValueError(f"Could not read GitHub user '{username}'"
                         + (" (GitHub rate limit - try again in an hour)" if response is not None and response.status_code == 403 else ""))
    repos = [r for r in response.json() if not r.get("fork") and not r.get("archived")]
    repos = [r for r in repos if r.get("name", "").lower() != username.lower()][:GITHUB_REPOS]

    known = await _known(db, user_id)
    blocks: list[str] = []
    for repo in repos:
        if _norm(repo["name"]) in known["projects"]:
            continue
        readme = await request_with_retry(
            "GET", f"https://raw.githubusercontent.com/{username}/{repo['name']}/HEAD/README.md", max_retries=1,
        )
        body = readme.text[:GITHUB_README_CHARS] if readme is not None and readme.status_code == 200 else ""
        if not body and not repo.get("description"):
            continue  # nothing to describe
        blocks.append(
            f"### Repository: {repo['name']}\nURL: {repo['html_url']}\nLanguage: {repo.get('language') or ''}\n"
            f"Description: {repo.get('description') or ''}\nREADME:\n{body}\n"
        )

    added = 0
    for start in range(0, len(blocks), GITHUB_BATCH):
        found = await extract_facts(
            llm, "\n".join(blocks[start:start + GITHUB_BATCH]), "set of GitHub repositories",
            "Each repository is a project by the candidate. Name each project after the repository "
            "(a readable name) and use the repository URL as url. Do not output highlights.",
        )
        found["highlights"] = []
        added += await save_candidates(db, user_id, found, "github", f"https://github.com/{username}", None)
    return {"added": added, "repositories_read": len(blocks)}


async def import_url(db: AsyncSession, user_id: str, url: str, llm) -> dict[str, Any]:
    url = url.strip()
    if not re.match(r"https?://", url):
        url = "https://" + url
    response = await request_with_retry("GET", url, max_retries=1, headers={"Accept": "text/html"})
    if response is None or response.status_code >= 400:
        raise ValueError("Could not open that page")
    text = html_to_text(response.text)
    if len(text) < 200:
        raise ValueError("That page has almost no readable text (it may load its content with JavaScript)")
    found = await extract_facts(llm, text, "personal portfolio website",
                                "Projects listed are the candidate's own. Work at a named employer is a highlight.")
    added = await save_candidates(db, user_id, found, "portfolio", url, await current_role_company(db, user_id))
    return {"added": added}


async def import_document(db: AsyncSession, user_id: str, file_name: str, text: str, llm,
                          role_company: str | None = None) -> dict[str, Any]:
    if len(text.strip()) < 200:
        raise ValueError("The document has almost no readable text")
    role = role_company or await current_role_company(db, user_id)
    context = (f"The document describes work the candidate did at {role}: report it as highlights, "
               "and as a project only when it is a distinct named product or system they built.") if role else ""
    added = 0
    chunks = [text[i:i + MAX_TEXT_CHARS] for i in range(0, len(text), MAX_TEXT_CHARS)][:MAX_DOCUMENT_CHUNKS]
    for chunk in chunks:
        found = await extract_facts(llm, chunk, "work document", context)
        added += await save_candidates(db, user_id, found, "document", file_name, role)
    return {"added": added, "parts_read": len(chunks)}


def document_text(file_name: str, raw: bytes) -> str:
    """Plain text from an uploaded file (HTML, Markdown, text, PDF or Word)."""
    lower = file_name.lower()
    if lower.endswith((".html", ".htm")):
        return html_to_text(raw.decode("utf-8", errors="ignore"))
    if lower.endswith((".md", ".txt")):
        return raw.decode("utf-8", errors="ignore")
    if lower.endswith((".pdf", ".docx", ".doc")):
        import tempfile
        from pathlib import Path

        from services.resume_parser import ResumeParser

        suffix = Path(lower).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(raw)
            path = handle.name
        try:
            return ResumeParser.__new__(ResumeParser).extract_raw_text(path)
        finally:
            Path(path).unlink(missing_ok=True)
    raise ValueError("Upload an HTML, Markdown, text, PDF or Word file")


# ---------------------------------------------------------------------------
# Merging approved facts into the profile used by scoring and tailoring
# ---------------------------------------------------------------------------


async def approved_facts(db: AsyncSession, user_id: str) -> list[CareerFact]:
    return list((await db.execute(
        select(CareerFact).where(CareerFact.user_id == user_id, CareerFact.status == "approved")
        .order_by(CareerFact.created_at)
    )).scalars())


def merge_into_profile(facts: list[CareerFact], experiences: list[dict], projects: list[dict],
                       skills: list[dict]) -> None:
    """Add approved facts to the profile lists in place.

    Highlights join the bullets of the job they belong to (matched by company),
    projects join the project list (unless the name is already there) and skills
    join the skill list - so everything downstream treats them as the user's own.
    """
    by_company = {(e.get("company") or "").strip().lower(): e for e in experiences}
    project_names = {_norm(p.get("name") or "") for p in projects}
    skill_names = {(s.get("name") or "").lower() for s in skills}

    for fact in facts:
        if fact.kind == "highlight":
            target = by_company.get((fact.role_company or "").strip().lower())
            if target is None:
                target = next((e for c, e in by_company.items()
                               if fact.role_company and (fact.role_company.lower() in c or c in fact.role_company.lower())), None)
            if target is None and not fact.role_company and experiences:
                target = experiences[0]  # no job given: the most recent one
            if target is not None and fact.text not in (target.get("bullets") or []):
                target["bullets"] = list(target.get("bullets") or []) + [fact.text]
        elif fact.kind == "project" and _norm(fact.title) not in project_names:
            project_names.add(_norm(fact.title))
            projects.append({"id": f"fact-{fact.id}", "name": fact.title, "description": fact.text or "",
                             "technologies": list(fact.skills or []), "url": fact.url or ""})
        if fact.kind == "skill" or fact.kind in {"project", "highlight"}:
            names = [fact.title] if fact.kind == "skill" else list(fact.skills or [])
            for name in names:
                if name and name.lower() not in skill_names:
                    skill_names.add(name.lower())
                    skills.append({"name": name, "category": "career"})


async def enrich_profile(db: AsyncSession, user_id: str, experiences: list[dict], projects: list[dict],
                         skills: list[dict]) -> None:
    facts = await approved_facts(db, user_id)
    if facts:
        merge_into_profile(facts, experiences, projects, skills)
