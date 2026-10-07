"""LinkedIn Easy Apply handler.

Easy Apply is a multi-step modal rather than a single form, so this handler
walks the Next/Review/Submit sequence, filling whatever each step exposes.
"""
from __future__ import annotations

from typing import Any

from automation.platform_handlers.base_handler import BaseApplicationHandler, SubmissionResult
from core.logging_config import get_logger

logger = get_logger("automation.linkedin")

_MAX_STEPS = 8


class LinkedInHandler(BaseApplicationHandler):
    platform_name = "linkedin"
    display_name = "LinkedIn Easy Apply"
    requires_login = True

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
        result = SubmissionResult(success=False, status="failed")

        try:
            await page.goto(apply_url, timeout=45_000, wait_until="domcontentloaded")
            await self.human_delay()
            result.add("navigate", apply_url, screenshot=await self.screenshot(page, application_id, "job"))

            current = page.url.lower()
            if "login" in current or "authwall" in current:
                result.status = "needs_manual"
                result.message = "LinkedIn session expired. Reconnect it on the Sessions page."
                result.add("auth", result.message, "warning")
                return result

            easy_apply = page.locator("button.jobs-apply-button, button:has-text('Easy Apply')")
            if await easy_apply.count() == 0:
                result.status = "needs_manual"
                result.message = (
                    "This role does not offer Easy Apply - it sends applicants to the "
                    "company site. Use the prepared documents to apply manually."
                )
                result.add("detect_easy_apply", result.message, "warning")
                return result

            await easy_apply.first.click()
            await self.human_delay(1.5, 3.0)
            result.add("open_modal", "Easy Apply modal opened")

            answers = self.build_answers(applicant, cover_letter)
            total_filled = 0

            for step in range(_MAX_STEPS):
                modal = page.locator("div.jobs-easy-apply-modal, div[role='dialog']")
                if await modal.count() == 0:
                    break

                total_filled += await self.fill_form(page, answers, result)
                await self.upload_resume(page, resume_path, result)
                await self._answer_selects(page, result)

                submit_button = page.locator(
                    "button[aria-label*='Submit application'], button:has-text('Submit application')"
                )
                if await submit_button.count() > 0:
                    shot = await self.screenshot(page, application_id, "review")
                    if shot:
                        result.screenshots.append(shot)

                    if dry_run:
                        result.success = True
                        result.status = "dry_run"
                        result.message = (
                            f"Dry run: completed {step + 1} Easy Apply steps, "
                            f"{total_filled} fields filled. Nothing was submitted."
                        )
                        result.add("dry_run", result.message)
                        return result

                    await submit_button.first.click()
                    await self.human_delay(2.5, 4.0)
                    result.success = True
                    result.status = "submitted"
                    result.message = f"Easy Apply submitted after {step + 1} steps."
                    result.external_reference = apply_url[:255]
                    result.add(
                        "submitted",
                        result.message,
                        screenshot=await self.screenshot(page, application_id, "confirmation"),
                    )
                    return result

                next_button = page.locator(
                    "button[aria-label*='Continue to next step'], "
                    "button:has-text('Next'), button:has-text('Review')"
                )
                if await next_button.count() == 0:
                    break
                await next_button.first.click()
                await self.human_delay(1.2, 2.5)
                result.add("next_step", f"Advanced to step {step + 2}")

            result.status = "needs_manual"
            result.message = (
                "Easy Apply asked questions this agent could not answer confidently. "
                "Finish it manually - your tailored documents are ready."
            )
            result.add(
                "incomplete",
                result.message,
                "warning",
                await self.screenshot(page, application_id, "incomplete"),
            )
            return result

        except Exception as exc:
            logger.warning("LinkedIn Easy Apply failed: %s", exc)
            result.status = "failed"
            result.message = str(exc)[:400]
            result.add("error", result.message, "error", await self.screenshot(page, application_id, "error"))
            return result

    async def _answer_selects(self, page, result: SubmissionResult) -> None:
        """Pick an affirmative option for dropdowns still on their placeholder.

        Easy Apply gates progress on required selects (work authorisation and
        similar); already-answered ones are never touched.
        """
        try:
            selects = page.locator("select:visible")
            for index in range(min(await selects.count(), 10)):
                element = selects.nth(index)
                current = (await element.input_value() or "").strip().lower()
                if current and current not in {"select an option", "-", "choose"}:
                    continue
                options = await element.locator("option").all_text_contents()
                preferred = next(
                    (o for o in options if o.strip().lower() in {"yes", "y"}),
                    next(iter(options[1:]), None),
                )
                if preferred:
                    await element.select_option(label=preferred.strip())
                    result.add("select_answered", preferred.strip()[:60])
        except Exception as exc:
            logger.debug("Select handling skipped: %s", exc)
