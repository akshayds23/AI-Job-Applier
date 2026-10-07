"""Assisted apply: open the real application form, pre-fill it, let the user submit.

The browser is visible and stays open. The user reviews what was filled,
answers any company-specific questions, and clicks Submit themselves. While the
window is open we watch for the employer's confirmation page and mark the
application submitted automatically when it appears.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from automation.login_manager import LoginManager, PlaywrightNotInstalled
from automation.platform_handlers.base_handler import SubmissionResult
from automation.platform_handlers.generic_handler import GenericHandler
from core.events import event_bus
from core.logging_config import get_logger
from database.database import AsyncSessionLocal
from database.models import Application, ApplicationLog, JobListing, User, utcnow
from services.ats import greenhouse_form_url
from services.documents import applicant_profile, build_application_documents

logger = get_logger("apply_assistant")

# How long the assisted browser waits for the user before giving up.
SESSION_TIMEOUT_SECONDS = 45 * 60
_POLL_SECONDS = 2.0

_CONFIRMATION_RE = re.compile(
    r"thank(s| you) for (applying|your application|your interest)"
    r"|application (has been |was )?(submitted|received)"
    r"|we('ve| have) received your application"
    r"|your application (is|has been) (complete|submitted|received)",
    re.I,
)

# Sites where we only open the page: automating them risks the user's account.
_OPEN_ONLY_HOSTS = ("linkedin.com", "naukri.com", "indeed.com")

# Greenhouse renders the form further down the job page; Lever/Ashby use a separate URL.
_APPLY_PATH_HINTS = {
    "jobs.lever.co": lambda url: url if url.rstrip("/").endswith("/apply") else url.rstrip("/") + "/apply",
    "jobs.ashbyhq.com": lambda url: url if url.rstrip("/").endswith("/application") else url.rstrip("/") + "/application",
}

# application_id -> live session state (in-process; one browser per application)
_sessions: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task] = set()


def session_state(application_id: str) -> dict[str, Any] | None:
    return _sessions.get(application_id)


def choose_apply_url(job: JobListing) -> str:
    """Prefer the employer's own application form over an aggregator copy."""
    if job.platform == "greenhouse" and job.external_id and ":" in job.external_id:
        slug, _, job_id = job.external_id.partition(":")
        return greenhouse_form_url(slug, job_id)
    url = job.verified_url or job.apply_url or job.url
    host = urlparse(url).netloc.lower()
    for hint_host, transform in _APPLY_PATH_HINTS.items():
        if host.endswith(hint_host):
            return transform(url)
    return url


def start_assisted_apply(user_id: str, application_id: str) -> dict[str, Any]:
    existing = _sessions.get(application_id)
    if existing and existing["state"] in ("preparing", "filling", "waiting_for_user"):
        return existing
    state = {"state": "preparing", "message": "Generating your tailored resume...", "filled": 0, "url": None}
    _sessions[application_id] = state
    task = asyncio.create_task(_run(user_id, application_id, state))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return state


async def _set(user_id: str, application_id: str, state: dict[str, Any], **changes: Any) -> None:
    state.update(changes)
    await event_bus.publish(user_id, "apply_session", {"application_id": application_id, **state})


async def _log(application_id: str, action: str, details: str, screenshot: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        db.add(ApplicationLog(application_id=application_id, action=action, details=details[:2000], screenshot_path=screenshot))
        await db.commit()


async def _set_status(application_id: str, status: str, **fields: Any) -> None:
    async with AsyncSessionLocal() as db:
        app = await db.get(Application, application_id)
        if app is None:
            return
        app.status = status
        for key, value in fields.items():
            setattr(app, key, value)
        await db.commit()


async def _run(user_id: str, application_id: str, state: dict[str, Any]) -> None:
    try:
        async with AsyncSessionLocal() as db:
            app = await db.get(Application, application_id)
            job = await db.get(JobListing, app.job_id)
            user = await db.get(User, user_id)
            applicant = await applicant_profile(db, user)
            documents = await build_application_documents(db, app)
            cover_letter = app.cover_letter
            url = choose_apply_url(job)
            app.status = "applying"
            await db.commit()

        open_only = any(host in urlparse(url).netloc.lower() for host in _OPEN_ONLY_HOSTS)
        await _set(user_id, application_id, state, state="filling", url=url,
                   message="Opening the application page...")
        await _log(application_id, "assisted_apply_started", url)

        manager = LoginManager()
        async with manager.browser_context(user_id, "apply", headless=False) as (context, page):
            handler = GenericHandler()
            result = SubmissionResult(success=False, status="needs_manual")
            await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            await asyncio.sleep(2.5)  # let client-rendered forms mount

            filled, uploaded = 0, False
            if not open_only:
                await _reveal_form(page)
                answers = handler.build_answers(applicant, cover_letter)
                resume = documents["ats_pdf"] or documents["pdf"] or documents["docx"]
                for frame in page.frames:  # main page first, then embedded ATS iframes
                    filled += await handler.fill_form(frame, answers, result)
                    if not uploaded:
                        uploaded = await handler.upload_resume(frame, resume, result)
                await page.bring_to_front()

            shot = await handler.screenshot(page, application_id, "prefilled")
            if open_only:
                message = ("Opened the job on a site we don't automate (to protect your account). "
                           "Apply there using the resume saved in data/generated.")
            elif filled or uploaded:
                message = (f"Filled {filled} field(s){' and attached your resume' if uploaded else ''}. "
                           "Check every field, answer any extra questions, then click Submit in the browser window.")
            else:
                message = ("Couldn't find the form fields on this page (it may need a click on 'Apply' first, "
                           "or redirect to another site). Fill it in the open browser window.")
            await _log(application_id, "form_prefilled", message, shot)
            await _set(user_id, application_id, state, state="waiting_for_user", filled=filled,
                       resume_attached=uploaded, message=message)
            await _set_status(application_id, "applying", submission_method="assisted")

            confirmed = await _wait_for_submission(page)

        if confirmed:
            await _set_status(application_id, "submitted", submitted_at=utcnow(), external_reference=confirmed[:255])
            await _log(application_id, "submitted", f"Confirmation page detected: {confirmed}")
            await _set(user_id, application_id, state, state="submitted",
                       message="Application submitted - confirmation page detected.")
        else:
            await _set_status(application_id, "awaiting_confirmation")
            await _set(user_id, application_id, state, state="closed",
                       message="Browser closed. If you submitted the application, mark it as submitted.")

    except PlaywrightNotInstalled as exc:
        await _fail(user_id, application_id, state, str(exc))
    except Exception as exc:
        logger.exception("Assisted apply failed for %s", application_id)
        await _fail(user_id, application_id, state, f"Could not open the application: {exc}")


async def _fail(user_id: str, application_id: str, state: dict[str, Any], message: str) -> None:
    await _set_status(application_id, "approved", error_message=message[:1000])
    await _log(application_id, "assisted_apply_failed", message)
    await _set(user_id, application_id, state, state="failed", message=message)


async def _reveal_form(page) -> None:
    """Some job pages hide the form behind an 'Apply' button on the same page."""
    try:
        if await page.locator("input[type='email']:visible, input[type='file']").count() > 0:
            return
        button = page.locator(
            "a:has-text('Apply for this job'):visible, button:has-text('Apply for this job'):visible, "
            "button:has-text('Apply now'):visible, a:has-text('Apply now'):visible, "
            "button:has-text('Apply'):visible"
        )
        if await button.count() > 0:
            await button.first.click()
            await asyncio.sleep(2.0)
    except Exception as exc:
        logger.debug("Could not reveal form: %s", exc)


async def _wait_for_submission(page) -> str | None:
    """Poll the open window until a confirmation page appears or the user closes it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SESSION_TIMEOUT_SECONDS
    while loop.time() < deadline:
        if page.is_closed():
            return None
        try:
            text = await page.inner_text("body", timeout=3000)
            match = _CONFIRMATION_RE.search(text or "")
            if match:
                await asyncio.sleep(1.5)  # leave the confirmation visible briefly
                return f"{page.url} ({match.group(0)})"
        except Exception:
            if page.is_closed():
                return None
        await asyncio.sleep(_POLL_SECONDS)
    return None
