"""Direct-from-employer job boards (Greenhouse, Lever, Ashby).

Most companies host their careers page on one of a few applicant-tracking
systems, and those systems publish every open role through a public JSON feed.
Jobs read from there are posted by the employer itself - they are real, current
openings, not aggregator reposts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.http import request_with_retry
from core.logging_config import get_logger
from scrapers.base_scraper import ScrapedJob, html_to_text, normalise_key, parse_timestamp

logger = get_logger("ats")

SUPPORTED_ATS = ("greenhouse", "lever", "ashby")

_FEEDS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
}

# Board URLs as they appear in links, iframes and embed scripts on careers pages.
_URL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/|embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)", re.I)),
]

# Path segments that the URL patterns can capture but are never a board slug.
_NOT_SLUGS = {"embed", "v1", "boards", "js", "job_board", "jobs", "api", "static", "assets"}


@dataclass(frozen=True)
class BoardRef:
    ats: str
    slug: str

    @property
    def careers_url(self) -> str:
        return {
            "greenhouse": f"https://job-boards.greenhouse.io/{self.slug}",
            "lever": f"https://jobs.lever.co/{self.slug}",
            "ashby": f"https://jobs.ashbyhq.com/{self.slug}",
        }[self.ats]


class BoardNotFound(Exception):
    pass


def find_board_in_text(text: str) -> BoardRef | None:
    """Spot a Greenhouse/Lever/Ashby board reference in a URL or HTML page."""
    for ats, pattern in _URL_PATTERNS:
        for match in pattern.finditer(text or ""):
            slug = match.group(1).strip(".")
            if slug and slug.lower() not in _NOT_SLUGS:
                return BoardRef(ats, slug)
    return None


async def fetch_board(ref: BoardRef, company_name: str | None = None) -> list[ScrapedJob]:
    """Every open role on one company board. Raises BoardNotFound on 404."""
    url = _FEEDS[ref.ats].format(slug=ref.slug)
    response = await request_with_retry("GET", url, max_retries=2, headers={"Accept": "application/json"})
    if response is None:
        raise ConnectionError(f"Could not reach {ref.ats}")
    if response.status_code == 404:
        raise BoardNotFound(f"No {ref.ats} board named '{ref.slug}'")
    if response.status_code != 200:
        raise ConnectionError(f"{ref.ats} returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectionError(f"{ref.ats} returned a non-JSON response") from exc

    parser = {"greenhouse": _parse_greenhouse, "lever": _parse_lever, "ashby": _parse_ashby}[ref.ats]
    jobs = parser(payload, ref, company_name or ref.slug)
    logger.info("%s/%s: %d open roles", ref.ats, ref.slug, len(jobs))
    return jobs


async def detect_board(name: str = "", url: str = "") -> BoardRef:
    """Work out which ATS board belongs to a company, from a URL and/or its name.

    1. The URL is itself a board link (jobs.lever.co/acme ...).
    2. The URL is the company's own careers page that embeds or links a board.
    3. Guess the board slug from the company name and probe each ATS.
    """
    if url:
        ref = find_board_in_text(url)
        if ref:
            return ref

        page_url = url if re.match(r"^https?://", url, re.I) else f"https://{url}"
        response = await request_with_retry("GET", page_url, max_retries=1)
        if response is not None and response.status_code < 400:
            ref = find_board_in_text(str(response.url)) or find_board_in_text(response.text)
            if ref:
                return ref

    for slug in _slug_candidates(name or _domain_name(url)):
        for ats in SUPPORTED_ATS:
            ref = BoardRef(ats, slug)
            try:
                jobs_url = _FEEDS[ats].format(slug=slug)
                response = await request_with_retry("GET", jobs_url, max_retries=0)
                if response is not None and response.status_code == 200 and _has_board_shape(ats, response):
                    return ref
            except Exception:  # pragma: no cover - network noise
                continue

    raise BoardNotFound(
        "Could not find a Greenhouse, Lever or Ashby job board for this company. "
        "Paste the exact careers-page URL, or the company may use another system."
    )


def _has_board_shape(ats: str, response) -> bool:
    try:
        data = response.json()
    except ValueError:
        return False
    if ats == "lever":
        return isinstance(data, list)
    return isinstance(data, dict) and isinstance(data.get("jobs"), list)


def _domain_name(url: str) -> str:
    match = re.search(r"(?:https?://)?(?:www\.|careers\.|jobs\.)?([A-Za-z0-9-]+)\.", url or "")
    return match.group(1) if match else ""


def _slug_candidates(name: str) -> list[str]:
    base = (name or "").strip().lower()
    if not base:
        return []
    base = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|technologies|pvt|private)\b\.?", "", base).strip()
    words = re.findall(r"[a-z0-9]+", base)
    candidates = ["".join(words), "-".join(words), words[0] if words else ""]
    seen: list[str] = []
    for slug in candidates:
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def greenhouse_form_url(slug: str, job_id: Any) -> str:
    return f"https://job-boards.greenhouse.io/{slug}/jobs/{job_id}"


# -- parsers -----------------------------------------------------------------


def _parse_greenhouse(payload: dict[str, Any], ref: BoardRef, company: str) -> list[ScrapedJob]:
    jobs: list[ScrapedJob] = []
    for row in payload.get("jobs") or []:
        url = row.get("absolute_url")
        if not row.get("title") or not url:
            continue
        description_html = row.get("content") or ""
        jobs.append(ScrapedJob(
            title=row["title"],
            company=row.get("company_name") or company,
            location=(row.get("location") or {}).get("name") or "",
            description=html_to_text(description_html),
            description_html=description_html,
            url=url,
            # Companies often point absolute_url at their own careers site with an
            # embedded widget; the hosted page always has the application form inline.
            apply_url=greenhouse_form_url(ref.slug, row.get("id")) if row.get("id") else url,
            platform="greenhouse",
            posted_date=parse_timestamp(row.get("first_published") or row.get("updated_at")),
            tags=[d.get("name") for d in row.get("departments") or [] if d.get("name")],
            external_id=f"{ref.slug}:{row.get('id')}",
            raw={"source": "company_site", "ats": "greenhouse", "board": ref.slug},
        ))
    return jobs


def _parse_lever(payload: list[dict[str, Any]], ref: BoardRef, company: str) -> list[ScrapedJob]:
    jobs: list[ScrapedJob] = []
    for row in payload or []:
        url = row.get("hostedUrl")
        if not row.get("text") or not url:
            continue
        categories = row.get("categories") or {}
        sections = "\n\n".join(
            f"{section.get('text', '')}\n{html_to_text(section.get('content', ''))}"
            for section in row.get("lists") or []
        )
        description = "\n\n".join(
            part for part in (row.get("descriptionPlain"), sections, row.get("additionalPlain")) if part
        )
        salary = row.get("salaryRange") or {}
        jobs.append(ScrapedJob(
            title=row["text"],
            company=company,
            location=categories.get("location") or "",
            description=description.strip(),
            description_html=row.get("description"),
            url=url,
            apply_url=row.get("applyUrl") or url,
            platform="lever",
            salary_min=salary.get("min"),
            salary_max=salary.get("max"),
            salary_currency=salary.get("currency"),
            posted_date=parse_timestamp(row.get("createdAt")),
            is_remote=(row.get("workplaceType") == "remote"),
            job_type=_employment_type(categories.get("commitment")),
            tags=[t for t in (categories.get("team"), categories.get("department")) if t],
            external_id=f"{ref.slug}:{row.get('id')}",
            raw={"source": "company_site", "ats": "lever", "board": ref.slug},
        ))
    return jobs


def _parse_ashby(payload: dict[str, Any], ref: BoardRef, company: str) -> list[ScrapedJob]:
    jobs: list[ScrapedJob] = []
    for row in payload.get("jobs") or []:
        url = row.get("jobUrl")
        if not row.get("title") or not url or row.get("isListed") is False:
            continue
        jobs.append(ScrapedJob(
            title=row["title"],
            company=company,
            location=row.get("location") or "",
            description=row.get("descriptionPlain") or html_to_text(row.get("descriptionHtml")),
            description_html=row.get("descriptionHtml"),
            url=url,
            apply_url=row.get("applyUrl") or url,
            platform="ashby",
            posted_date=parse_timestamp(row.get("publishedAt")),
            is_remote=bool(row.get("isRemote")),
            job_type=_employment_type(row.get("employmentType")),
            tags=[t for t in (row.get("department"), row.get("team")) if t],
            external_id=f"{ref.slug}:{row.get('id')}",
            raw={"source": "company_site", "ats": "ashby", "board": ref.slug},
        ))
    return jobs


def _employment_type(value: str | None) -> str:
    key = normalise_key(value or "")
    if "intern" in key:
        return "internship"
    if "contract" in key or "temporary" in key:
        return "contract"
    if "parttime" in key:
        return "part_time"
    return "full_time"
