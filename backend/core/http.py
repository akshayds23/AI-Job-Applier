"""Shared async HTTP client with retry/backoff and polite rate limiting."""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from config import settings
from core.logging_config import get_logger

logger = get_logger("http")

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class DomainRateLimiter:
    """Minimum spacing between requests to the same host."""

    def __init__(self, min_interval: float = 1.0) -> None:
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last.get(host, 0.0)
            wait = self.min_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait + random.uniform(0, 0.25))
            self._last[host] = time.monotonic()


rate_limiter = DomainRateLimiter(min_interval=1.0)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.HTTP_TIMEOUT_SECONDS),
            follow_redirects=True,
            headers={
                "User-Agent": settings.USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int | None = None,
    respect_rate_limit: bool = True,
    retry_on_429: bool = True,
    **kwargs: Any,
) -> httpx.Response | None:
    """Perform a request, retrying transient failures with exponential backoff.

    Returns ``None`` when every attempt failed, so callers can degrade instead
    of raising into a user-facing request.
    """
    retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries
    client = get_client()
    host = httpx.URL(url).host or "unknown"

    for attempt in range(retries + 1):
        if respect_rate_limit:
            await rate_limiter.acquire(host)
        try:
            response = await client.request(method, url, **kwargs)
            retryable = response.status_code in _RETRYABLE_STATUS and (retry_on_429 or response.status_code != 429)
            if retryable and attempt < retries:
                delay = _backoff(attempt, response)
                logger.warning("%s %s -> %s, retrying in %.1fs", method, host, response.status_code, delay)
                await asyncio.sleep(delay)
                continue
            return response
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt >= retries:
                logger.warning("%s %s failed after %d attempts: %s", method, host, attempt + 1, exc)
                return None
            delay = _backoff(attempt, None)
            logger.debug("%s %s errored (%s), retrying in %.1fs", method, host, exc, delay)
            await asyncio.sleep(delay)
    return None


def _backoff(attempt: int, response: httpx.Response | None) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
    return min(2.0**attempt, 16.0) + random.uniform(0, 0.75)
