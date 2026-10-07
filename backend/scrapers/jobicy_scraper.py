"""Jobicy scraper - public remote-jobs API (v2) with tag/geo filtering."""
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

logger = get_logger("scraper.jobicy")

API_URL = "https://jobicy.com/api/v2/remote-jobs"

_JOB_TYPE_MAP = {
    "full-time": "full_time",
    "part-time": "part_time",
    "contract": "contract",
    "internship": "internship",
    "temporary": "contract",
}


class JobicyScraper(BaseScraper):
    platform_name = "jobicy"
    display_name = "Jobicy"
    requires_login = False
    supports_auto_apply = False

    async def search_jobs(
        self,
        query: str,
        location: str = "",
        limit: int = 25,
        filters: dict[str, Any] | None = None,
    ) -> list[ScrapedJob]:
        params: dict[str, Any] = {"count": max(1, min(limit, 50))}
        if query:
            params["tag"] = query
        geo = (filters or {}).get("geo")
        if geo:
            params["geo"] = geo

        response = await request_with_retry(
            "GET", API_URL, params=params, headers={"Accept": "application/json"}
        )
        if response is None or response.status_code != 200:
            logger.warning("Jobicy unavailable (%s)", getattr(response, "status_code", "no response"))
            return []

        try:
            payload = response.json()
        except ValueError:
            logger.warning("Jobicy returned non-JSON payload")
            return []

        rows = payload.get("jobs") or []
        jobs = [job for job in (self._to_job(row) for row in rows) if job is not None]
        logger.info("Jobicy: %d postings for %r", len(jobs), query)
        return jobs[:limit]

    def _to_job(self, row: dict[str, Any]) -> ScrapedJob | None:
        title = row.get("jobTitle")
        url = row.get("url")
        if not title or not url:
            return None

        description_html = row.get("jobDescription") or row.get("jobExcerpt") or ""
        description = html_to_text(description_html)
        salary_min, salary_max, currency = parse_salary(description[:600])
        job_types = coerce_list(row.get("jobType"))
        raw_type = job_types[0].lower() if job_types else ""

        return ScrapedJob(
            title=title,
            company=row.get("companyName") or "Unknown",
            location=row.get("jobGeo") or "Remote",
            description=description,
            description_html=description_html,
            url=url,
            apply_url=url,
            platform=self.platform_name,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            posted_date=parse_timestamp(row.get("pubDate")),
            is_remote=True,
            job_type=_JOB_TYPE_MAP.get(raw_type, "full_time"),
            seniority_level=(row.get("jobLevel") or "").lower() or None,
            tags=coerce_list(row.get("jobIndustry")),
            external_id=str(row.get("id")) if row.get("id") else None,
            company_logo_url=row.get("companyLogo"),
            raw={"jobSlug": row.get("jobSlug")},
        )
