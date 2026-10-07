"""Resume ingestion: PDF/DOCX/TXT -> raw text -> structured profile data.

The regex layer runs first and always produces usable contact details and
skills. The LLM layer then enriches it with experience/project structure. If no
LLM is configured the regex result still populates a working profile.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.skill_matcher import extract_skills
from core.logging_config import get_logger
from utils.llm_client import LLMUnavailable, get_llm_client

logger = get_logger("resume_parser")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b")
_LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", re.I)
_GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w-]+/?", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_GPA_RE = re.compile(r"\b(?:GPA|CGPA)[:\s]*([0-9]\.?[0-9]{0,2})\s*(?:/\s*([0-9]{1,2}))?", re.I)

_SECTION_ALIASES = {
    "summary": ("summary", "profile", "objective", "about me", "professional summary"),
    "experience": ("experience", "employment", "work history", "professional experience"),
    "education": ("education", "academic", "qualifications"),
    "skills": ("skills", "technical skills", "technologies", "competencies"),
    "projects": ("projects", "featured projects", "personal projects", "portfolio"),
    "certifications": ("certifications", "certificates", "achievements", "awards"),
}

PARSE_PROMPT = """Extract structured data from this resume text.

RESUME TEXT:
{raw_text}

Return JSON with exactly this shape:
{{
  "contact": {{
    "name": "", "email": "", "phone": "", "location": "",
    "linkedin_url": "", "github_url": "", "portfolio_url": ""
  }},
  "headline": "",
  "professional_summary": "",
  "experience_years": <integer total years of professional experience, or null>,
  "skills": [{{"name": "Python", "category": "language|framework|tool|cloud|ai|database|domain|leadership|soft"}}],
  "experiences": [{{
    "company": "", "title": "", "location": "",
    "start_date": "Mon YYYY", "end_date": "Mon YYYY or Present",
    "is_current": false, "bullets": [""], "technologies": [""]
  }}],
  "projects": [{{"name": "", "description": "", "technologies": [""], "url": ""}}],
  "education": [{{
    "institution": "", "degree": "", "field": "",
    "start_date": "", "end_date": "", "gpa": ""
  }}],
  "certifications": [""],
  "achievements": [""]
}}

Rules:
- Copy facts verbatim. Never invent employers, dates, degrees, numbers or skills.
- Use "" for missing strings and [] for missing lists.
- headline: the tagline under the name (e.g. "AI Engineer | Generative AI"), or "" if there is none.
- bullets: every sentence or line describing a role is a bullet, even if the resume writes it as a
  plain paragraph rather than a bulleted list. Split paragraphs into one bullet per sentence.
- A company with several titles (promotions) becomes one experience entry per title; give each the
  bullets written under that company.
- Short lines like "Company - Title | 2016-2020" (e.g. under "Earlier experience") are experiences too:
  fill company, title, start_date and end_date from them, with empty bullets.
- achievements: quantified highlights, awards and rankings stated anywhere in the resume
  (e.g. "Scaled programs to 300+ schools"), rewritten as short standalone lines with the exact numbers.
- skills: include items from skills/core-strengths sections; do not split one item into several.
"""


class ResumeParser:
    def __init__(self, llm_client=None) -> None:
        self.llm = llm_client or get_llm_client(interactive=True)

    # -- text extraction ---------------------------------------------------

    def extract_raw_text(self, file_path: str | Path) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Resume not found: {path}")

        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type {suffix!r}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("Resume exceeds the 10 MB limit")

        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix in {".docx", ".doc"}:
            return self._extract_docx(path)
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _extract_pdf(path: Path) -> str:
        import pymupdf

        chunks: list[str] = []
        with pymupdf.open(str(path)) as document:
            for page in document:
                chunks.append(page.get_text("text"))
        text = "\n".join(chunks)
        if len(text.strip()) < 50:
            raise ValueError(
                "No selectable text found in this PDF. It looks like a scan - "
                "upload a text-based PDF or a DOCX instead."
            )
        return text

    @staticmethod
    def _extract_docx(path: Path) -> str:
        import docx

        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        # Many resumes lay out contact details or skills inside tables.
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts)

    # -- parsing -----------------------------------------------------------

    async def parse(self, file_path: str | Path) -> dict[str, Any]:
        return await self.parse_text(self.extract_raw_text(file_path))

    async def parse_text(self, raw_text: str) -> dict[str, Any]:
        baseline = self.parse_heuristic(raw_text)

        if not self.llm.is_available:
            logger.info("Parsed resume with regex only (no LLM configured)")
            return baseline

        try:
            result = await self.llm.generate_json(
                PARSE_PROMPT.format(raw_text=raw_text[:14000]),
                "You are a precise resume parser. You extract only what is written.",
                max_tokens=4000,
            )
        except LLMUnavailable as exc:
            logger.warning("Resume LLM parse failed, using regex result: %s", exc)
            return baseline

        return self._merge(result, baseline, raw_text)

    def parse_heuristic(self, raw_text: str) -> dict[str, Any]:
        """Deterministic extraction that always yields a usable profile."""
        text = raw_text or ""
        sections = self._split_sections(text)

        email = (_EMAIL_RE.search(text) or [None]) and _EMAIL_RE.search(text)
        phone = _PHONE_RE.search(text)
        linkedin = _LINKEDIN_RE.search(text)
        github = _GITHUB_RE.search(text)

        portfolio = ""
        for url in _URL_RE.findall(text):
            if "linkedin.com" in url.lower() or "github.com" in url.lower():
                continue
            portfolio = url.rstrip(".,);")
            break

        summary = sections.get("summary", "")
        if not summary:
            # Fall back to the first substantial paragraph after the header.
            for block in text.split("\n\n"):
                cleaned = " ".join(block.split())
                if 80 < len(cleaned) < 700 and "@" not in cleaned:
                    summary = cleaned
                    break

        return {
            "contact": {
                "name": self._guess_name(text),
                "email": email.group(0) if email else "",
                "phone": phone.group(0).strip() if phone else "",
                "location": self._guess_location(text),
                "linkedin_url": _normalise_url(linkedin.group(0)) if linkedin else "",
                "github_url": _normalise_url(github.group(0)) if github else "",
                "portfolio_url": portfolio,
            },
            "headline": "",
            "achievements": [],
            "professional_summary": summary[:1200],
            "experience_years": self._estimate_years(text),
            "skills": [{"name": s, "category": "general"} for s in extract_skills(text)],
            "experiences": [],
            "projects": [],
            "education": [],
            "certifications": [],
            "raw_text": text,
            "source": "heuristic",
        }

    # -- merge -------------------------------------------------------------

    def _merge(self, result: dict[str, Any], baseline: dict[str, Any], raw_text: str) -> dict[str, Any]:
        contact = result.get("contact") or {}
        base_contact = baseline["contact"]
        merged_contact = {
            key: (str(contact.get(key) or "").strip() or base_contact.get(key, ""))
            for key in base_contact
        }
        # Regex beats the model on exact strings it can match verbatim.
        for key in ("email", "phone", "linkedin_url", "github_url"):
            if base_contact.get(key):
                merged_contact[key] = base_contact[key]

        skills: list[dict[str, str]] = []
        seen: set[str] = set()
        for entry in result.get("skills") or []:
            name = (entry.get("name") if isinstance(entry, dict) else str(entry) or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            category = (entry.get("category") if isinstance(entry, dict) else "") or "general"
            skills.append({"name": name[:120], "category": str(category)[:50]})
        for entry in baseline["skills"]:
            if entry["name"].lower() not in seen:
                seen.add(entry["name"].lower())
                skills.append(entry)

        experiences = [
            {
                "company": str(e.get("company") or "").strip()[:255],
                "title": str(e.get("title") or "").strip()[:255],
                "location": str(e.get("location") or "").strip()[:255],
                "start_date": str(e.get("start_date") or "").strip()[:50],
                "end_date": str(e.get("end_date") or "").strip()[:50],
                "is_current": bool(e.get("is_current"))
                or str(e.get("end_date") or "").strip().lower() in {"present", "current"},
                "bullets": [str(b).strip()[:500] for b in (e.get("bullets") or []) if str(b).strip()],
                "technologies": [str(t).strip()[:60] for t in (e.get("technologies") or []) if str(t).strip()],
            }
            for e in (result.get("experiences") or [])
            if isinstance(e, dict) and (e.get("company") or e.get("title"))
        ]

        projects = [
            {
                "name": str(p.get("name") or "").strip()[:255],
                "description": str(p.get("description") or "").strip()[:2000],
                "technologies": [str(t).strip()[:60] for t in (p.get("technologies") or []) if str(t).strip()],
                "url": str(p.get("url") or "").strip()[:500],
            }
            for p in (result.get("projects") or [])
            if isinstance(p, dict) and p.get("name")
        ]

        education = [
            {
                "institution": str(e.get("institution") or "").strip()[:255],
                "degree": str(e.get("degree") or "").strip()[:120],
                "field": str(e.get("field") or "").strip()[:255],
                "start_date": str(e.get("start_date") or "").strip()[:50],
                "end_date": str(e.get("end_date") or "").strip()[:50],
                "gpa": str(e.get("gpa") or "").strip()[:10],
            }
            for e in (result.get("education") or [])
            if isinstance(e, dict) and (e.get("institution") or e.get("degree"))
        ]

        years = result.get("experience_years")
        if not isinstance(years, int) or not 0 < years <= 60:
            years = baseline["experience_years"]

        return {
            "contact": merged_contact,
            "professional_summary": (
                str(result.get("professional_summary") or "").strip() or baseline["professional_summary"]
            )[:1500],
            "experience_years": years,
            "skills": skills[:60],
            "experiences": experiences[:12],
            "projects": projects[:12],
            "education": education[:6],
            "certifications": [
                str(c).strip()[:200] for c in (result.get("certifications") or []) if str(c).strip()
            ][:10],
            "headline": str(result.get("headline") or "").strip()[:200],
            "achievements": [
                str(a).strip()[:220] for a in (result.get("achievements") or []) if str(a).strip()
            ][:8],
            "raw_text": raw_text,
            "source": "llm",
        }

    # -- heuristics --------------------------------------------------------

    @staticmethod
    def _split_sections(text: str) -> dict[str, str]:
        """Bucket the document by its section headings."""
        lines = text.splitlines()
        sections: dict[str, list[str]] = {}
        current: str | None = None

        for line in lines:
            stripped = line.strip()
            if not stripped or len(stripped) > 60:
                if current:
                    sections.setdefault(current, []).append(line)
                continue

            probe = re.sub(r"[^a-z ]", "", stripped.lower()).strip()
            matched = next(
                (key for key, aliases in _SECTION_ALIASES.items() if probe in aliases),
                None,
            )
            if matched:
                current = matched
                sections.setdefault(current, [])
            elif current:
                sections[current].append(line)

        return {key: "\n".join(value).strip() for key, value in sections.items()}

    @staticmethod
    def _guess_name(text: str) -> str:
        """The name is almost always the first short, title-cased line."""
        for line in text.splitlines()[:8]:
            candidate = line.strip()
            if not 4 <= len(candidate) <= 50:
                continue
            if any(ch in candidate for ch in "@|:/\\") or any(ch.isdigit() for ch in candidate):
                continue
            words = candidate.split()
            if 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w):
                return candidate
        return ""

    @staticmethod
    def _guess_location(text: str) -> str:
        head = "\n".join(text.splitlines()[:12])
        match = re.search(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?,\s*(?:[A-Z]{2}|[A-Z][a-z]+))\b", head)
        return match.group(1) if match else ""

    @staticmethod
    def _estimate_years(text: str) -> int | None:
        explicit = re.search(r"(\d{1,2})\+?\s*years?\s+(?:of\s+)?experience", text, re.I)
        if explicit:
            value = int(explicit.group(1))
            if 0 < value <= 50:
                return value

        # Otherwise infer from the earliest employment year mentioned.
        years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", text)]
        if not years:
            return None
        from datetime import datetime

        span = datetime.now().year - min(years)
        return span if 0 < span <= 50 else None


def _normalise_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith("http"):
        url = f"https://{url}"
    return url
