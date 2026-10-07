"""Arbeitnow scraper - public EU-focused job board feed.

The feed returns the whole board (paginated) with no text search, so we page
until enough postings match the query locally.
"""
from __future__ import annotations

from typing import Any

from core.http import request_with_retry
from core.logging_config import get_logger
from scrapers.base_scraper import (
    BaseScraper,
    ScrapedJob,
    coerce_list,
    html_to_text,
    matches_query,
    parse_salary,
    parse_timestamp,
    tokenize_query,
)

logger = get_logger("scraper.arbeitnow")

API_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 3

_JOB_TYPE_MAP = {
    "full_time": "full_time",
    "full-time": "full_time",
    "part_time": "part_time",
    "part-time": "part_time",
    "contract": "contract",
    "internship": "internship",
}


class ArbeitnowScraper(BaseScraper):
    platform_name = "arbeitnow"
    display_name = "Arbeitnow (EU)"
    requires_login = False
    supports_auto_apply = False

    async def search_jobs(
        self,
        query: str,
        location: str = "",
        limit: int = 25,
        filters: dict[str, Any] | None = None,
    ) -> list[ScrapedJob]:
        terms = tokenize_query(query)
        location_needle = (location or "").strip().lower()
        collected: list[ScrapedJob] = []

        for page in range(1, _MAX_PAGES + 1):
            response = await request_with_retry(
                "GET", API_URL, params={"page": page}, headers={"Accept": "application/json"}
            )
            if response is None or response.status_code != 200:
                logger.warning(
                    "Arbeitnow unavailable (%s)", getattr(response, "status_code", "no response")
                )
                break

            try:
                payload = response.json()
            except ValueError:
                logger.warning("Arbeitnow returned non-JSON payload")
                break

            rows = payload.get("data") or []
            if not rows:
                break

            for row in rows:
                job = self._to_job(row)
                if job is None or not matches_query(job, terms):
                    continue
                if location_needle and location_needle not in job.location.lower() and not job.is_remote:
                    continue
                collected.append(job)
                if len(collected) >= limit:
                    break

            if len(collected) >= limit:
                break

        logger.info("Arbeitnow: %d postings for %r", len(collected), query)
        return collected[:limit]

    def _to_job(self, row: dict[str, Any]) -> ScrapedJob | None:
        title = row.get("title")
        url = row.get("url")
        if not title or not url:
            return None

        description_html = row.get("description") or ""
        description = html_to_text(description_html)
        salary_min, salary_max, currency = parse_salary(description[:800])
        job_types = coerce_list(row.get("job_types"))
        raw_type = job_types[0].lower() if job_types else ""

        return ScrapedJob(
            title=title,
            company=row.get("company_name") or "Unknown",
            location=row.get("location") or "",
            description=description,
            description_html=description_html,
            url=url,
            apply_url=url,
            platform=self.platform_name,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency or "EUR",
            posted_date=parse_timestamp(row.get("created_at")),
            is_remote=bool(row.get("remote")),
            job_type=_JOB_TYPE_MAP.get(raw_type, "full_time"),
            tags=coerce_list(row.get("tags")),
            external_id=row.get("slug"),
            raw={"slug": row.get("slug")},
        )
