"""Himalayas scraper - public remote-jobs JSON feed with cursor pagination."""
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
    parse_int,
    parse_timestamp,
    tokenize_query,
)

logger = get_logger("scraper.himalayas")

API_URL = "https://himalayas.app/jobs/api"
_PAGE_SIZE = 50
_MAX_PAGES = 4

_EMPLOYMENT_TYPE_MAP = {
    "full time": "full_time",
    "part time": "part_time",
    "contract": "contract",
    "internship": "internship",
    "temporary": "contract",
}


class HimalayasScraper(BaseScraper):
    platform_name = "himalayas"
    display_name = "Himalayas"
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
        collected: list[ScrapedJob] = []
        cursor: str | None = None

        # The feed has no text search, so page until we have enough matches.
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {"limit": _PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor

            response = await request_with_retry(
                "GET", API_URL, params=params, headers={"Accept": "application/json"}
            )
            if response is None or response.status_code != 200:
                logger.warning(
                    "Himalayas unavailable (%s)", getattr(response, "status_code", "no response")
                )
                break

            try:
                payload = response.json()
            except ValueError:
                logger.warning("Himalayas returned non-JSON payload")
                break

            rows = payload.get("jobs") or []
            if not rows:
                break

            for row in rows:
                job = self._to_job(row)
                if job is not None and matches_query(job, terms):
                    collected.append(job)
                    if len(collected) >= limit:
                        break

            if len(collected) >= limit:
                break
            cursor = payload.get("nextCursor")
            if not cursor:
                break

        logger.info("Himalayas: %d postings for %r", len(collected), query)
        return collected[:limit]

    def _to_job(self, row: dict[str, Any]) -> ScrapedJob | None:
        title = row.get("title")
        url = row.get("applicationLink") or row.get("guid")
        if not title or not url:
            return None

        description_html = row.get("description") or row.get("excerpt") or ""
        locations = coerce_list(row.get("locationRestrictions"))
        seniority = coerce_list(row.get("seniority"))
        employment = (row.get("employmentType") or "").strip().lower()

        return ScrapedJob(
            title=title,
            company=row.get("companyName") or "Unknown",
            location=", ".join(locations) if locations else "Remote",
            description=html_to_text(description_html),
            description_html=description_html,
            url=url,
            apply_url=url,
            platform=self.platform_name,
            salary_min=parse_int(row.get("minSalary")),
            salary_max=parse_int(row.get("maxSalary")),
            salary_currency=row.get("currency") if row.get("currency") not in (None, "None") else "USD",
            posted_date=parse_timestamp(row.get("pubDate")),
            is_remote=True,
            job_type=_EMPLOYMENT_TYPE_MAP.get(employment, "full_time"),
            seniority_level=seniority[0].lower() if seniority else None,
            tags=coerce_list(row.get("categories"))[:12],
            external_id=str(row.get("guid")) if row.get("guid") else None,
            company_logo_url=row.get("companyLogo"),
            raw={"companySlug": row.get("companySlug"), "salaryPeriod": row.get("salaryPeriod")},
        )
