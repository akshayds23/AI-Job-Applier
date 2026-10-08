"""LinkedIn scraper built on the public guest job-search endpoints.

LinkedIn exposes unauthenticated HTML fragments for job search and for
individual postings. That is enough for discovery, so no login is needed to
populate the feed. A Playwright session (see ``automation/login_manager``) is
only required to *apply* through LinkedIn Easy Apply.

LinkedIn rate-limits aggressively; the shared ``DomainRateLimiter`` spaces
requests out and detail fetches are capped per run.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from core.http import request_with_retry
from core.logging_config import get_logger
from scrapers.base_scraper import (
    BaseScraper,
    ScrapedJob,
    clean_text,
    html_to_text,
    looks_remote,
    parse_salary,
)

logger = get_logger("scraper.linkedin")

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

_PAGE_SIZE = 10
_MAX_PAGES = 4
_MAX_DETAIL_FETCHES = 12  # keep the run polite and fast

# Once LinkedIn answers 429 it keeps doing so for a while (hosting IPs especially);
# retrying with back-off only burns the run's time budget, so skip it for a cool-off.
_BLOCK_SECONDS = 15 * 60
_blocked_until = 0.0


def linkedin_blocked() -> bool:
    return time.time() < _blocked_until


def _block() -> None:
    global _blocked_until
    _blocked_until = time.time() + _BLOCK_SECONDS

_JOB_ID_RE = re.compile(r"urn:li:jobPosting:(\d+)")
_RELATIVE_TIME_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", re.I)

_EMPLOYMENT_TYPE_MAP = {
    "full-time": "full_time",
    "part-time": "part_time",
    "contract": "contract",
    "temporary": "contract",
    "internship": "internship",
    "volunteer": "part_time",
}


class LinkedInScraper(BaseScraper):
    platform_name = "linkedin"
    display_name = "LinkedIn Jobs"
    requires_login = False  # discovery is public; applying needs a session
    supports_auto_apply = True

    async def search_jobs(
        self,
        query: str,
        location: str = "",
        limit: int = 25,
        filters: dict[str, Any] | None = None,
    ) -> list[ScrapedJob]:
        filters = filters or {}
        cards: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        if linkedin_blocked():
            return []

        for page in range(_MAX_PAGES):
            params = {
                "keywords": query or "",
                "location": location or "Worldwide",
                "start": page * _PAGE_SIZE,
            }
            if filters.get("remote_only"):
                params["f_WT"] = "2"  # LinkedIn's remote work-type filter
            if filters.get("posted_within_hours"):
                params["f_TPR"] = f"r{int(filters['posted_within_hours']) * 3600}"

            if linkedin_blocked():
                break
            response = await request_with_retry(
                "GET", SEARCH_URL, params=params, headers={"Accept": "text/html"}, retry_on_429=False
            )
            if response is None or response.status_code != 200:
                if response is not None and response.status_code == 429:
                    _block()
                    logger.warning("LinkedIn rate-limited; skipping it for %d min", _BLOCK_SECONDS // 60)
                else:
                    logger.warning(
                        "LinkedIn search unavailable (%s)",
                        getattr(response, "status_code", "no response"),
                    )
                break

            page_cards = self._parse_search_page(response.text)
            if not page_cards:
                break

            for card in page_cards:
                if card["external_id"] not in seen_ids:
                    seen_ids.add(card["external_id"])
                    cards.append(card)

            if len(cards) >= limit:
                break

        cards = cards[:limit]
        if not cards:
            logger.info("LinkedIn: no cards for %r", query)
            return []

        # Enrich the top results with full JD text (needed for real matching).
        detail_budget = min(len(cards), _MAX_DETAIL_FETCHES)
        details = await asyncio.gather(
            *(self._fetch_detail(card["external_id"]) for card in cards[:detail_budget]),
            return_exceptions=True,
        )
        for card, detail in zip(cards, details):
            if isinstance(detail, dict):
                card.update({k: v for k, v in detail.items() if v})

        jobs = [self._to_job(card) for card in cards]
        jobs = [job for job in jobs if job is not None]
        logger.info("LinkedIn: %d postings for %r (%d enriched)", len(jobs), query, detail_budget)
        return jobs

    # -- parsing ----------------------------------------------------------

    def _parse_search_page(self, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, Any]] = []

        for card in soup.select("div.base-card, div.job-search-card"):
            urn = card.get("data-entity-urn") or ""
            match = _JOB_ID_RE.search(urn)
            if not match:
                continue
            job_id = match.group(1)

            title_el = card.select_one("h3.base-search-card__title")
            company_el = card.select_one("h4.base-search-card__subtitle a, h4.base-search-card__subtitle")
            location_el = card.select_one(".job-search-card__location")
            link_el = card.select_one("a.base-card__full-link")
            logo_el = card.select_one("img.artdeco-entity-image")
            time_el = card.select_one("time")

            title = clean_text(title_el.get_text() if title_el else "")
            if not title:
                continue

            url = (link_el.get("href") if link_el else "") or f"https://www.linkedin.com/jobs/view/{job_id}"
            results.append(
                {
                    "external_id": job_id,
                    "title": title,
                    "company": clean_text(company_el.get_text() if company_el else ""),
                    "location": clean_text(location_el.get_text() if location_el else ""),
                    "url": url.split("?")[0],
                    "company_logo_url": (logo_el.get("data-delayed-url") if logo_el else None),
                    "posted_label": (time_el.get("datetime") if time_el else None),
                }
            )
        return results

    async def _fetch_detail(self, job_id: str) -> dict[str, Any]:
        if linkedin_blocked():
            return {}
        response = await request_with_retry(
            "GET", DETAIL_URL.format(job_id=job_id), headers={"Accept": "text/html"}, max_retries=1,
            retry_on_429=False,
        )
        if response is not None and response.status_code == 429:
            _block()
        if response is None or response.status_code != 200:
            return {}

        soup = BeautifulSoup(response.text, "html.parser")
        body = soup.select_one(".description__text, .show-more-less-html__markup")
        description_html = body.decode_contents() if body else ""

        detail: dict[str, Any] = {}
        if description_html:
            detail["description_html"] = description_html
            detail["description"] = html_to_text(description_html)

        for item in soup.select(".description__job-criteria-item"):
            header = item.select_one(".description__job-criteria-subheader")
            value = item.select_one(".description__job-criteria-text")
            if not header or not value:
                continue
            key = clean_text(header.get_text()).lower()
            val = clean_text(value.get_text())
            if key.startswith("seniority"):
                detail["seniority_label"] = val
            elif key.startswith("employment"):
                detail["employment_type"] = val

        apply_link = soup.select_one("a.topcard__link, a[data-tracking-control-name*='topcard']")
        if apply_link and apply_link.get("href"):
            detail["apply_url"] = apply_link["href"].split("?")[0]

        return detail

    def _to_job(self, card: dict[str, Any]) -> ScrapedJob | None:
        title = card.get("title")
        if not title:
            return None

        description = card.get("description") or ""
        salary_min, salary_max, currency = parse_salary(description[:800])
        employment = (card.get("employment_type") or "").strip().lower()
        seniority = (card.get("seniority_label") or "").strip().lower()
        location = card.get("location") or ""

        return ScrapedJob(
            title=title,
            company=card.get("company") or "Unknown",
            location=location,
            description=description,
            description_html=card.get("description_html"),
            url=card["url"],
            apply_url=card.get("apply_url") or card["url"],
            platform=self.platform_name,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            posted_date=self._parse_posted(card.get("posted_label")),
            is_remote=looks_remote(f"{title} {location}"),
            job_type=_EMPLOYMENT_TYPE_MAP.get(employment, "full_time"),
            seniority_level=seniority if seniority and seniority != "not applicable" else None,
            external_id=card.get("external_id"),
            company_logo_url=card.get("company_logo_url"),
            raw={"job_id": card.get("external_id")},
        )

    @staticmethod
    def _parse_posted(label: str | None):
        from scrapers.base_scraper import parse_timestamp

        return parse_timestamp(label) if label else None

    async def get_job_details(self, job_url: str) -> ScrapedJob | None:
        match = re.search(r"(\d{6,})", job_url)
        if not match:
            return None
        job_id = match.group(1)
        detail = await self._fetch_detail(job_id)
        if not detail:
            return None
        detail.update({"external_id": job_id, "url": job_url, "title": detail.get("title") or "LinkedIn Role"})
        return self._to_job(detail)
