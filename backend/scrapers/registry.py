"""Auto-discovering plugin registry for job scrapers."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Any

from config import settings
from core.logging_config import get_logger
from scrapers.base_scraper import ScrapedJob

logger = get_logger("scraper.registry")

_EXCLUDED_MODULES = {"base_scraper", "registry", "__init__"}
_EXCLUDED_CLASSES = {"BaseScraper", "JobScraperPlugin"}


class ScraperRegistry:
    """Discovers scraper plugins in ``backend/scrapers/`` and runs them."""

    _scrapers: dict[str, type] = {}
    _discovered = False

    @classmethod
    def discover(cls, force: bool = False) -> dict[str, type]:
        if cls._discovered and not force:
            return cls._scrapers

        cls._scrapers = {}
        package_dir = Path(__file__).parent

        for _finder, module_name, _is_pkg in pkgutil.iter_modules([str(package_dir)]):
            if module_name in _EXCLUDED_MODULES:
                continue
            try:
                module = importlib.import_module(f"scrapers.{module_name}")
            except Exception as exc:
                logger.warning("Skipping scraper module %r: %s", module_name, exc)
                continue

            for attr_name, attr in vars(module).items():
                if attr_name in _EXCLUDED_CLASSES or not inspect.isclass(attr):
                    continue
                # Only register classes defined in this module, so imported
                # helpers are not double-registered under another platform.
                if attr.__module__ != module.__name__:
                    continue
                platform = getattr(attr, "platform_name", None)
                if not platform or platform == "base":
                    continue
                if not callable(getattr(attr, "search_jobs", None)):
                    logger.warning("%s has platform_name but no search_jobs()", attr_name)
                    continue
                cls._scrapers[platform.lower()] = attr
                logger.info("Registered scraper plugin: %s -> %s", platform, attr_name)

        cls._discovered = True
        return cls._scrapers

    @classmethod
    def get_scraper_class(cls, platform: str) -> type | None:
        cls.discover()
        return cls._scrapers.get((platform or "").lower())

    @classmethod
    def create(cls, platform: str) -> Any | None:
        scraper_cls = cls.get_scraper_class(platform)
        return scraper_cls() if scraper_cls else None

    @classmethod
    def list_available_platforms(cls) -> list[str]:
        cls.discover()
        return sorted(cls._scrapers)

    @classmethod
    def describe(cls) -> list[dict[str, Any]]:
        cls.discover()
        return [
            {
                "platform": platform,
                "display_name": getattr(scraper_cls, "display_name", platform.title()),
                "requires_login": bool(getattr(scraper_cls, "requires_login", False)),
                "supports_auto_apply": bool(getattr(scraper_cls, "supports_auto_apply", False)),
            }
            for platform, scraper_cls in sorted(cls._scrapers.items())
        ]

    @classmethod
    async def search_all(
        cls,
        platforms: list[str],
        queries: list[str],
        location: str = "",
        limit_per_query: int = 15,
        filters: dict[str, Any] | None = None,
    ) -> list[ScrapedJob]:
        """Run every (platform, query) pair concurrently and merge the results.

        A failing plugin never takes the run down - it just contributes nothing.
        """
        cls.discover()
        semaphore = asyncio.Semaphore(settings.SCRAPER_CONCURRENCY)

        async def run_one(platform: str, query: str) -> list[ScrapedJob]:
            scraper_cls = cls._scrapers.get(platform.lower())
            if scraper_cls is None:
                logger.warning("Unknown platform requested: %s", platform)
                return []
            async with semaphore:
                try:
                    scraper = scraper_cls()
                    await scraper.initialize({}, None)
                    return await scraper.search_jobs(
                        query=query, location=location, limit=limit_per_query, filters=filters
                    )
                except Exception as exc:
                    logger.warning("Scraper %s failed for %r: %s", platform, query, exc)
                    return []

        tasks = [run_one(p, q) for p in platforms for q in (queries or [""])]
        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: dict[str, ScrapedJob] = {}
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Scraper task raised: %s", result)
                continue
            for job in result:
                merged.setdefault(job.dedup_hash, job)

        logger.info(
            "Discovery complete: %d unique postings from %d platform/query tasks",
            len(merged),
            len(tasks),
        )
        return list(merged.values())
