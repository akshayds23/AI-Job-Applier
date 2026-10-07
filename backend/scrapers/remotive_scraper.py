"""Remotive scraper - public JSON API with server-side search."""
from __future__ import annotations

from typing import Any

from core.http import request_with_retry
from core.logging_config import get_logger
from scrapers.base_scraper import (
    BaseScraper,
    ScrapedJob,
    coerce_list,
    html_to_text,
    parse_salary,
    parse_timestamp,
)

logger = get_logger("scraper.remotive")

API_URL = "https://remotive.com/api/remote-jobs"

_JOB_TYPE_MAP = {
    "full_time": "full_time",
    "part_time": "part_time",
    "contract": "contract",
    "freelance": "contract",
    "internship": "internship",
    "temporary": "contract",
}


class RemotiveScraper(BaseScraper):
    platform_name = "remotive"
    display_name = "Remotive"
    requires_login = False
    supports_auto_apply = False

    async def search_jobs(
        self,
        query: str,
        location: str = "",
        limit: int = 25,
        filters: dict[str, Any] | None = None,
    ) -> list[ScrapedJob]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if query:
            params["search"] = query
        category = (filters or {}).get("category")
        if category:
            params["category"] = category

        response = await request_with_retry(
            "GET", API_URL, params=params, headers={"Accept": "application/json"}
        )
        if response is None or response.status_code != 200:
            logger.warning("Remotive unavailable (%s)", getattr(response, "status_code", "no response"))
            return []

        try:
            payload = response.json()
        except ValueError:
            logger.warning("Remotive returned non-JSON payload")
            return []

        rows = payload.get("jobs") or []
        jobs = [job for job in (self._to_job(row) for row in rows) if job is not None]
        logger.info("Remotive: %d postings for %r", len(jobs), query)
        return jobs[:limit]

    def _to_job(self, row: dict[str, Any]) -> ScrapedJob | None:
        title = row.get("title")
        url = row.get("url")
        if not title or not url:
            return None

        description_html = row.get("description") or ""
        salary_min, salary_max, currency = parse_salary(row.get("salary"))
        raw_type = (row.get("job_type") or "").lower()

        return ScrapedJob(
            title=title,
            company=row.get("company_name") or "Unknown",
            location=row.get("candidate_required_location") or "Remote",
            description=html_to_text(description_html),
            description_html=description_html,
            url=url,
            apply_url=url,
            platform=self.platform_name,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            posted_date=parse_timestamp(row.get("publication_date")),
            is_remote=True,
            job_type=_JOB_TYPE_MAP.get(raw_type, "full_time"),
            tags=coerce_list(row.get("tags")),
            external_id=str(row.get("id")) if row.get("id") else None,
            company_logo_url=row.get("company_logo_url") or row.get("company_logo"),
            raw={"category": row.get("category")},
        )
