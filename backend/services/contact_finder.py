"""Find who to email about an application.

Only addresses that are actually published are returned as "found": in the job
description, or on the company's own website. Common role inboxes
(careers@, hr@ ...) are offered separately and clearly marked as guesses.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

from core.http import request_with_retry
from core.logging_config import get_logger
from database.models import JobListing
from scrapers.base_scraper import normalise_key

logger = get_logger("contact_finder")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
_IGNORED_LOCAL = ("noreply", "no-reply", "donotreply", "do-not-reply", "privacy", "abuse", "security",
                  "legal", "press", "media", "support", "help", "billing", "sales", "info@example")
_IGNORED_DOMAINS = ("example.com", "sentry.io", "wixpress.com", "domain.com", "email.com", "yourcompany.com")
_HIRING_LOCAL = ("career", "job", "hr", "recruit", "talent", "hiring", "people", "apply", "resume", "cv")

# Hosts that are job boards / ATS vendors, never the employer's own domain.
_NON_EMPLOYER_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "linkedin.com", "remoteok.com", "remotive.com", "jobicy.com",
    "himalayas.app", "arbeitnow.com", "indeed.com", "naukri.com", "workable.com", "smartrecruiters.com",
    "myworkdayjobs.com", "wellfound.com", "bamboohr.com", "recruitee.com",
)

_CONTACT_PATHS = ("", "/careers", "/jobs", "/contact", "/contact-us", "/about")

GUESSED_INBOXES = ("careers", "jobs", "hr")


def _clean(emails: set[str]) -> list[str]:
    kept = []
    for email in emails:
        email = email.strip(".").lower()
        local, _, domain = email.partition("@")
        if any(tag in email for tag in _IGNORED_LOCAL) or domain in _IGNORED_DOMAINS:
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", email):
            continue
        kept.append(email)
    # Hiring inboxes first.
    return sorted(set(kept), key=lambda e: (not any(tag in e.split("@")[0] for tag in _HIRING_LOCAL), e))


def _employer_host(url: str | None) -> str | None:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    if not host or any(host.endswith(h) for h in _NON_EMPLOYER_HOSTS):
        return None
    # careers.acme.com -> acme.com
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 and len(parts[-1]) > 2 else host


async def _guess_domain(company: str) -> str | None:
    """Try acme.com / acme.io / acme.ai and accept one whose page mentions the company."""
    key = normalise_key(company)
    if not key:
        return None
    for tld in ("com", "io", "ai", "co", "in"):
        domain = f"{key}.{tld}"
        response = await request_with_retry("GET", f"https://{domain}", max_retries=0)
        if response is None or response.status_code >= 400:
            continue
        final_host = urlparse(str(response.url)).netloc.lower().removeprefix("www.")
        title = re.search(r"<title[^>]*>(.*?)</title>", response.text or "", re.I | re.S)
        if company.lower().split()[0] in (title.group(1).lower() if title else "") or key in normalise_key(final_host):
            return _employer_host(str(response.url)) or domain
    return None


async def find_company_domain(job: JobListing) -> str | None:
    for url in (job.verified_url, job.apply_url, job.url):
        host = _employer_host(url)
        if host:
            return host
    return await _guess_domain(job.company or "")


async def _scrape_site(domain: str) -> set[str]:
    found: set[str] = set()

    async def fetch(path: str) -> None:
        response = await request_with_retry("GET", urljoin(f"https://{domain}", path), max_retries=0)
        if response is not None and response.status_code < 400 and "html" in response.headers.get("content-type", ""):
            found.update(_EMAIL_RE.findall(response.text or ""))

    await asyncio.gather(*(fetch(path) for path in _CONTACT_PATHS))
    # Only addresses on the company's own domain count.
    return {email for email in found if email.lower().endswith("@" + domain) or email.lower().endswith("." + domain)}


async def find_contacts(job: JobListing) -> dict:
    contacts: list[dict] = []

    for email in _clean(set(_EMAIL_RE.findall(job.description_text or ""))):
        contacts.append({"email": email, "source": "Job description", "confidence": "high"})

    domain = None
    try:
        domain = await find_company_domain(job)
        if domain:
            listed = {c["email"] for c in contacts}
            for email in _clean(await _scrape_site(domain)):
                if email not in listed:
                    contacts.append({"email": email, "source": f"Published on {domain}", "confidence": "medium"})
    except Exception as exc:
        logger.debug("Website contact scan failed for %s: %s", job.company, exc)

    guesses = [
        {"email": f"{local}@{domain}", "source": "Common inbox - guessed, may bounce", "confidence": "guess"}
        for local in GUESSED_INBOXES
    ] if domain else []

    return {"domain": domain, "contacts": contacts, "guesses": guesses}
