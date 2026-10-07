"""Base application handler: shared form-filling and audit-trail machinery.

Every submission produces screenshots and a step log, so a user can always see
exactly what was done on their behalf.
"""
from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from core.logging_config import get_logger

logger = get_logger("automation.handler")


@dataclass
class SubmissionStep:
    action: str
    detail: str = ""
    level: str = "info"
    screenshot: str | None = None
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "detail": self.detail,
            "level": self.level,
            "screenshot": self.screenshot,
            "at": self.at,
        }


@dataclass
class SubmissionResult:
    success: bool
    status: str  # submitted | needs_manual | failed | dry_run
    message: str = ""
    external_reference: str | None = None
    screenshots: list[str] = field(default_factory=list)
    steps: list[SubmissionStep] = field(default_factory=list)

    def add(self, action: str, detail: str = "", level: str = "info", screenshot: str | None = None) -> None:
        self.steps.append(SubmissionStep(action, detail, level, screenshot))
        if screenshot:
            self.screenshots.append(screenshot)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "message": self.message,
            "external_reference": self.external_reference,
            "screenshots": self.screenshots,
            "steps": [s.to_dict() for s in self.steps],
        }


# Field-matching patterns, ordered most specific first so that "first name"
# never falls through to the generic "name" rule.
FIELD_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("first_name", ("first name", "firstname", "given name", "fname")),
    ("last_name", ("last name", "lastname", "surname", "family name", "lname")),
    ("full_name", ("full name", "your name", "candidate name", "name")),
    ("email", ("email", "e-mail")),
    ("phone", ("phone", "mobile", "telephone", "contact number")),
    ("location", ("location", "city", "address", "where are you based")),
    ("linkedin", ("linkedin",)),
    ("github", ("github",)),
    ("portfolio", ("portfolio", "website", "personal site")),
    ("cover_letter", ("cover letter", "why do you want", "tell us about", "message", "additional information")),
    ("salary", ("salary", "compensation", "expected pay", "rate")),
    ("notice", ("notice period", "when can you start", "availability", "start date")),
]


_FOREIGN_FIELD_WORDS = (
    "company", "employer", "school", "university", "college", "reference", "referr",
    "manager", "hiring", "recruiter", "emergency", "pronoun", "preferred name",
    # Voluntary self-identification is always the applicant's own choice.
    "gender", "hispanic", "latino", "race", "ethnic", "veteran", "disabilit", "sexual orientation",
)


class BaseApplicationHandler:
    """Generic Playwright form handler; platform handlers refine the specifics."""

    platform_name = "generic"
    display_name = "Generic Application Form"
    requires_login = False

    def __init__(self) -> None:
        self.screenshot_dir = settings.screenshots_path
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    # -- helpers ----------------------------------------------------------

    async def human_delay(self, minimum: float | None = None, maximum: float | None = None) -> None:
        low = settings.AUTOMATION_MIN_DELAY if minimum is None else minimum
        high = settings.AUTOMATION_MAX_DELAY if maximum is None else maximum
        await asyncio.sleep(random.uniform(low, max(low, high)))

    async def screenshot(self, page, application_id: str, label: str) -> str | None:
        try:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", f"{application_id}_{label}")[:80]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            path = self.screenshot_dir / f"{safe}_{stamp}.png"
            await page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as exc:
            logger.debug("Screenshot failed (%s): %s", label, exc)
            return None

    @staticmethod
    def build_answers(applicant: dict[str, Any], cover_letter: str | None) -> dict[str, str]:
        """Map the applicant profile onto the logical field names above."""
        name = str(applicant.get("name") or "").strip()
        parts = name.split()
        return {
            "full_name": name,
            "first_name": parts[0] if parts else "",
            "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
            "email": str(applicant.get("email") or ""),
            "phone": str(applicant.get("phone") or ""),
            "location": str(applicant.get("location") or ""),
            "linkedin": str(applicant.get("linkedin_url") or ""),
            "github": str(applicant.get("github_url") or ""),
            "portfolio": str(applicant.get("portfolio_url") or ""),
            "cover_letter": (cover_letter or "").strip(),
            "salary": str(applicant.get("expected_salary") or ""),
            "notice": str(applicant.get("notice_period") or "Immediately available"),
        }

    @classmethod
    def classify_field(cls, *labels: str) -> str | None:
        """Decide which answer belongs in a field, from its label/name/placeholder."""
        blob = " ".join(label.lower() for label in labels if label)
        if not blob.strip():
            return None
        # Fields *about other people/organisations* must never get the applicant's details.
        if any(word in blob for word in _FOREIGN_FIELD_WORDS):
            return None
        for key, needles in FIELD_PATTERNS:
            if any(needle in blob for needle in needles):
                return key
        return None

    async def describe_field(self, page, element) -> str:
        """Collect every textual hint attached to an input."""
        hints: list[str] = []
        for attribute in ("aria-label", "name", "id", "placeholder", "data-test"):
            try:
                value = await element.get_attribute(attribute)
                if value:
                    hints.append(value)
            except Exception:
                continue
        try:
            element_id = await element.get_attribute("id")
            if element_id:
                label = page.locator(f"label[for='{element_id}']")
                if await label.count() > 0:
                    hints.append((await label.first.inner_text())[:120])
        except Exception:
            pass
        return " ".join(hints)

    async def fill_form(self, page, answers: dict[str, str], result: SubmissionResult) -> int:
        """Fill every recognised, empty, visible field. Returns the fill count."""
        filled = 0

        inputs = page.locator(
            "input[type='text'], input[type='email'], input[type='tel'], input[type='url'], "
            "input:not([type]), textarea"
        )
        count = min(await inputs.count(), 40)

        for index in range(count):
            element = inputs.nth(index)
            try:
                if not await element.is_visible() or not await element.is_editable():
                    continue
                if (await element.input_value() or "").strip():
                    continue  # never overwrite what the platform pre-filled
                # Searchable dropdowns (react-select etc.) need a real choice, not typed text.
                if await element.get_attribute("role") == "combobox" or await element.get_attribute("aria-autocomplete"):
                    continue

                key = self.classify_field(await self.describe_field(page, element))
                value = answers.get(key or "", "")
                if not value:
                    continue

                await element.fill(value[:5000])
                filled += 1
                result.add("field_filled", f"{key}", "info")
                await self.human_delay(0.2, 0.6)
            except Exception as exc:
                logger.debug("Could not fill field %d: %s", index, exc)

        return filled

    async def upload_resume(self, page, resume_path: str, result: SubmissionResult) -> bool:
        if not resume_path or not Path(resume_path).exists():
            result.add("resume_upload", "Resume file not found on disk", "warning")
            return False

        try:
            file_inputs = page.locator("input[type='file']")
            if await file_inputs.count() == 0:
                return False
            await file_inputs.first.set_input_files(resume_path)
            result.add("resume_upload", Path(resume_path).name)
            await self.human_delay(1.0, 2.0)
            return True
        except Exception as exc:
            result.add("resume_upload", f"Upload failed: {exc}", "warning")
            return False

    async def find_submit_button(self, page):
        """Locate the real submit control without tripping over 'Save' buttons."""
        candidates = [
            "button[type='submit']:visible",
            "button:has-text('Submit application'):visible",
            "button:has-text('Submit'):visible",
            "button:has-text('Apply now'):visible",
            "button:has-text('Send application'):visible",
            "input[type='submit']:visible",
        ]
        for selector in candidates:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0 and await locator.first.is_enabled():
                    return locator.first
            except Exception:
                continue
        return None

    # -- main entry point --------------------------------------------------

    async def submit(
        self,
        page,
        *,
        application_id: str,
        apply_url: str,
        applicant: dict[str, Any],
        resume_path: str,
        cover_letter: str | None,
        dry_run: bool = True,
    ) -> SubmissionResult:
        """Navigate, fill, and (unless ``dry_run``) submit the application."""
        result = SubmissionResult(success=False, status="failed")

        try:
            await page.goto(apply_url, timeout=45_000, wait_until="domcontentloaded")
            await self.human_delay()
            result.add("navigate", apply_url, screenshot=await self.screenshot(page, application_id, "loaded"))

            answers = self.build_answers(applicant, cover_letter)
            filled = await self.fill_form(page, answers, result)
            uploaded = await self.upload_resume(page, resume_path, result)

            if filled == 0 and not uploaded:
                result.status = "needs_manual"
                result.message = (
                    "No fillable application form was found on this page - it likely "
                    "redirects to an external ATS. Apply manually using the prepared documents."
                )
                result.add("detect_form", result.message, "warning")
                return result

            submit_button = await self.find_submit_button(page)
            if submit_button is None:
                result.status = "needs_manual"
                result.message = f"Filled {filled} field(s) but found no submit button."
                result.add("locate_submit", result.message, "warning")
                result.screenshots.append(await self.screenshot(page, application_id, "filled") or "")
                return result

            shot = await self.screenshot(page, application_id, "before_submit")
            if shot:
                result.screenshots.append(shot)

            if dry_run:
                result.success = True
                result.status = "dry_run"
                result.message = (
                    f"Dry run: filled {filled} field(s)"
                    f"{' and attached the resume' if uploaded else ''}. "
                    "Nothing was submitted. Enable auto-submit to send for real."
                )
                result.add("dry_run", result.message)
                return result

            await submit_button.click()
            await self.human_delay(2.5, 4.5)
            confirmation = await self.screenshot(page, application_id, "after_submit")

            result.success = True
            result.status = "submitted"
            result.message = f"Application submitted ({filled} field(s) filled)."
            result.external_reference = page.url[:255]
            result.add("submitted", page.url, screenshot=confirmation)
            return result

        except Exception as exc:
            logger.warning("Submission failed for %s: %s", apply_url, exc)
            result.status = "failed"
            result.message = str(exc)[:400]
            result.add("error", result.message, "error", await self.screenshot(page, application_id, "error"))
            return result
