"""RemoteOK scraper - public JSON feed at https://remoteok.com/api.

The feed returns the full active board in one document (element 0 is a legal
notice, not a job), so we fetch once, cache briefly, and filter locally.
"""
from __future__ import annotations

import time
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

logger = get_logger("scraper.remoteok")

API_URL = "https://remoteok.com/api"
_CACHE_TTL_SECONDS = 300


class RemoteOKScraper(BaseScraper):
    platform_name = "remoteok"
    display_name = "RemoteOK"
    requires_login = False
    supports_auto_apply = False

    _cache: tuple[float, list[dict[str, Any]]] | None = None

    async def _fetch_feed(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = type(self)._cache
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

        response = await request_with_retry("GET", API_URL, headers={"Accept": "application/json"})
        if response is None or response.status_code != 200:
            logger.warning("RemoteOK feed unavailable (%s)", getattr(response, "status_code", "no response"))
            return cached[1] if cached else []

        try:
            payload = response.json()
        except ValueError:
            logger.warning("RemoteOK returned non-JSON payload")
            return cached[1] if cached else []

        if not isinstance(payload, list):
            return []
        # Index 0 is the API terms-of-service notice.
        rows = [row for row in payload[1:] if isinstance(row, dict) and row.get("position")]
        type(self)._cache = (now, rows)
        logger.info("RemoteOK feed: %d live postings", len(rows))
        return rows

    async def search_jobs(
        self,
        query: str,
        location: str = "",
        limit: int = 25,
        filters: dict[str, Any] | None = None,
    ) -> list[ScrapedJob]:
        rows = await self._fetch_feed()
        if not rows:
            return []

        terms = tokenize_query(query)
        jobs: list[ScrapedJob] = []
        for row in rows:
            job = self._to_job(row)
            if job is None:
                continue
            if matches_query(job, terms):
                jobs.append(job)
            if len(jobs) >= limit:
                break

        logger.info("RemoteOK: %d/%d postings matched %r", len(jobs), len(rows), query)
        return jobs

    def _to_job(self, row: dict[str, Any]) -> ScrapedJob | None:
        title = row.get("position") or ""
        if not title:
            return None

        description_html = row.get("description") or ""
        url = row.get("url") or f"https://remoteok.com/remote-jobs/{row.get('id')}"

        return ScrapedJob(
            title=title,
            company=row.get("company") or "Unknown",
            location=row.get("location") or "Remote",
            description=html_to_text(description_html),
            description_html=description_html,
            url=url,
            apply_url=row.get("apply_url") or url,
            platform=self.platform_name,
            salary_min=parse_int(row.get("salary_min")),
            salary_max=parse_int(row.get("salary_max")),
            salary_currency="USD",
            posted_date=parse_timestamp(row.get("epoch") or row.get("date")),
            is_remote=True,
            tags=coerce_list(row.get("tags")),
            external_id=str(row.get("id")) if row.get("id") else None,
            company_logo_url=row.get("company_logo") or row.get("logo") or None,
            raw={"slug": row.get("slug"), "id": row.get("id")},
        )

    async def get_job_details(self, job_url: str) -> ScrapedJob | None:
        for row in await self._fetch_feed():
            candidate = row.get("url") or ""
            if candidate.lower() == job_url.lower():
                return self._to_job(row)
        return None
