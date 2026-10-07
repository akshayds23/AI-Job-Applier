"""Scraper plugin contract plus the shared helpers every plugin needs."""
from __future__ import annotations

import hashlib
import html as html_module
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")
_BLOCK_RE = re.compile(r"</(p|div|li|ul|ol|h[1-6]|tr|table|section|article)>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_LI_RE = re.compile(r"<li[^>]*>", re.I)

_SALARY_RE = re.compile(
    r"(?P<cur>[$€£₹]|USD|EUR|GBP|INR)?\s*"
    r"(?P<num>\d{1,3}(?:[,.]\d{3})+|\d{2,7})\s*(?P<k>k\b)?",
    re.I,
)
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}

_REMOTE_HINTS = ("remote", "anywhere", "worldwide", "work from home", "distributed", "wfh")

_SENIORITY_PATTERNS = [
    ("principal", ("principal", "staff engineer", "distinguished")),
    ("lead", ("lead", "head of", "manager", "director", "vp ")),
    ("senior", ("senior", "sr.", "sr ", "iii")),
    ("junior", ("junior", "jr.", "jr ", "entry level", "entry-level", "graduate", "intern")),
]

_QUERY_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "in", "at", "to", "with", "job", "jobs", "role",
}


@dataclass
class ScrapedJob:
    """Normalised job posting produced by every scraper plugin."""

    title: str
    company: str
    location: str
    description: str
    url: str
    platform: str
    apply_url: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = "USD"
    posted_date: datetime | None = None
    is_remote: bool = False
    job_type: str | None = "full_time"
    seniority_level: str | None = None
    tags: list[str] = field(default_factory=list)
    external_id: str | None = None
    company_logo_url: str | None = None
    description_html: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = clean_text(self.title)[:500]
        self.company = clean_text(self.company)[:255] or "Unknown"
        self.location = clean_text(self.location)[:255]
        self.tags = [clean_text(t)[:60] for t in (self.tags or []) if t][:20]
        if not self.apply_url:
            self.apply_url = self.url
        if not self.is_remote:
            self.is_remote = looks_remote(f"{self.title} {self.location}")
        if not self.seniority_level:
            self.seniority_level = infer_seniority(self.title)

    @property
    def dedup_hash(self) -> str:
        """Stable identity across runs, so re-scraping never duplicates rows."""
        identity = self.external_id or self.url
        basis = "|".join(
            [self.platform, normalise_key(self.company), normalise_key(self.title), identity]
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dedup_hash"] = self.dedup_hash
        return data


@runtime_checkable
class JobScraperPlugin(Protocol):
    """Contract every job-board plugin must satisfy.

    Drop a module in ``backend/scrapers/`` exposing a class with these members
    and the registry auto-discovers it on startup - no core code changes.
    """

    platform_name: str
    requires_login: bool
    supports_auto_apply: bool

    async def initialize(self, config: dict[str, Any], browser_context: Any = None) -> None: ...

    async def search_jobs(
        self,
        query: str,
        location: str = "",
        limit: int = 25,
        filters: dict[str, Any] | None = None,
    ) -> list[ScrapedJob]: ...

    async def get_job_details(self, job_url: str) -> ScrapedJob | None: ...

    async def is_job_active(self, job_url: str) -> bool: ...


class BaseScraper:
    """Convenience base giving plugins sane no-op lifecycle hooks."""

    platform_name = "base"
    requires_login = False
    supports_auto_apply = False

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.browser_context: Any = None

    async def initialize(self, config: dict[str, Any], browser_context: Any = None) -> None:
        self.config = config or {}
        self.browser_context = browser_context

    async def get_job_details(self, job_url: str) -> ScrapedJob | None:
        return None

    async def is_job_active(self, job_url: str) -> bool:
        from core.http import request_with_retry

        response = await request_with_retry("HEAD", job_url, max_retries=1)
        return bool(response and response.status_code < 400)


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------


def html_to_text(raw: str | None) -> str:
    """Convert a JD HTML blob into readable plain text, preserving structure."""
    if not raw:
        return ""
    text = raw
    # Several feeds double-encode their HTML, so unescape until it stabilises.
    for _ in range(3):
        unescaped = html_module.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = _BR_RE.sub("\n", text)
    text = _LI_RE.sub("\n- ", text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    text = text.replace("\xa0", " ")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_RE.sub("\n\n", text).strip()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = _TAG_RE.sub(" ", text)
    text = html_module.unescape(text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def normalise_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def looks_remote(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _REMOTE_HINTS)


def infer_seniority(title: str) -> str:
    lowered = f" {(title or '').lower()} "
    for level, needles in _SENIORITY_PATTERNS:
        if any(needle in lowered for needle in needles):
            return level
    return "mid"


def parse_int(value: Any) -> int | None:
    """Best-effort integer coercion; treats 0 and junk as 'not provided'."""
    if value in (None, "", "None"):
        return None
    try:
        number = int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_salary(text: str | None) -> tuple[int | None, int | None, str | None]:
    """Extract (min, max, currency) from free-form salary text."""
    if not text:
        return None, None, None
    values: list[int] = []
    currency: str | None = None
    for match in _SALARY_RE.finditer(text):
        raw_currency = match.group("cur")
        if raw_currency and not currency:
            currency = _CURRENCY_SYMBOLS.get(raw_currency, raw_currency.upper())
        digits = match.group("num").replace(",", "").replace(".", "")
        try:
            amount = int(digits)
        except ValueError:
            continue
        has_k = bool(match.group("k"))
        if has_k:
            amount *= 1000
        # A bare number needs to be large to be pay - otherwise "founded in
        # 2019" or "team of 50" would be read as a salary.
        floor = 1000 if (raw_currency or has_k) else 10_000
        if floor <= amount <= 10_000_000:
            values.append(amount)
    if not values:
        return None, None, currency
    values.sort()
    if len(values) == 1:
        return values[0], None, currency or "USD"
    return values[0], values[-1], currency or "USD"


def parse_timestamp(value: Any) -> datetime | None:
    """Accept epoch seconds/millis or an ISO-8601 string; return naive UTC."""
    if value in (None, "", "None"):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            epoch = int(float(value))
            if epoch > 10**12:  # milliseconds
                epoch //= 1000
            return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (ValueError, OSError, OverflowError):
        return None


def coerce_list(value: Any) -> list[str]:
    """Feeds return tags as a list, a JSON-ish string, or a delimited string."""
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_text(v) for v in value if v]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        import ast

        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return [clean_text(v) for v in parsed if v]
        except (ValueError, SyntaxError):
            pass
    return [part.strip() for part in re.split(r"[,;|]", text) if part.strip()]


def tokenize_query(query: str) -> list[str]:
    tokens = re.split(r"[^a-z0-9+#.]+", (query or "").lower())
    return [t for t in tokens if t and t not in _QUERY_STOPWORDS and len(t) > 1]


def matches_query(job: ScrapedJob, terms: list[str]) -> bool:
    """Loose relevance filter for feeds without server-side search."""
    if not terms:
        return True
    haystack = " ".join(
        [job.title, job.company, " ".join(job.tags), job.description[:1500]]
    ).lower()
    return any(term in haystack for term in terms)
