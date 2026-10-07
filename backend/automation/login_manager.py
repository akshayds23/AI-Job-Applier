"""Persistent browser sessions, one profile directory per (user, platform).

The user logs into a platform once in a visible browser window; Chromium's
profile directory keeps the cookies, so later automated runs reuse that session
without ever handling the user's password.
"""
from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from core.logging_config import get_logger

logger = get_logger("login_manager")

# Where to land when verifying a session, and what proves we are logged in.
PLATFORM_CHECKS: dict[str, dict[str, Any]] = {
    "linkedin": {
        "url": "https://www.linkedin.com/feed/",
        "logged_in_selectors": ["nav.global-nav", "[data-control-name='identity_profile_photo']"],
        "logged_out_markers": ["/login", "/signup", "/authwall", "/uas/login"],
        "login_url": "https://www.linkedin.com/login",
    },
    "indeed": {
        "url": "https://www.indeed.com/",
        "logged_in_selectors": ["[data-gnav-element-name='AccountMenu']", "#gnav-main-menu"],
        "logged_out_markers": ["/account/login", "secure.indeed.com/auth"],
        "login_url": "https://secure.indeed.com/account/login",
    },
    "naukri": {
        "url": "https://www.naukri.com/mnjuser/homepage",
        "logged_in_selectors": [".nI-gNb-drawer__bars", ".view-profile-wrapper"],
        "logged_out_markers": ["/nlogin/login"],
        "login_url": "https://www.naukri.com/nlogin/login",
    },
    "wellfound": {
        "url": "https://wellfound.com/jobs",
        "logged_in_selectors": ["[data-test='UserMenu']", "nav[aria-label='Main']"],
        "logged_out_markers": ["/login", "/signup"],
        "login_url": "https://wellfound.com/login",
    },
}


class PlaywrightNotInstalled(RuntimeError):
    """Raised when the Playwright browser binaries have not been downloaded."""


class LoginManager:
    """Owns the on-disk browser profiles used for authenticated automation."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else settings.sessions_path
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_session_dir(self, user_id: str, platform: str) -> Path:
        safe_user = "".join(c for c in str(user_id) if c.isalnum() or c in "-_")[:64] or "user"
        safe_platform = "".join(c for c in str(platform) if c.isalnum())[:32] or "platform"
        path = self.base_dir / safe_user / safe_platform
        path.mkdir(parents=True, exist_ok=True)
        return path

    def has_session(self, user_id: str, platform: str) -> bool:
        """A profile that has actually been used contains a Cookies database."""
        session_dir = self.get_session_dir(user_id, platform)
        return (session_dir / "Default" / "Cookies").exists() or (session_dir / "Cookies").exists()

    def clear_session(self, user_id: str, platform: str) -> bool:
        session_dir = self.get_session_dir(user_id, platform)
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
            logger.info("Cleared %s session for user %s", platform, user_id)
            return True
        return False

    @asynccontextmanager
    async def browser_context(self, user_id: str, platform: str, headless: bool | None = None):
        """Yield ``(context, page)`` for a persistent profile, always cleaning up."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise PlaywrightNotInstalled(
                "Playwright is not installed. Run: pip install playwright"
            ) from exc

        if headless is None:
            headless = settings.PLAYWRIGHT_HEADLESS

        session_dir = self.get_session_dir(user_id, platform)
        playwright = await async_playwright().start()
        context = None
        try:
            try:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(session_dir),
                    headless=headless,
                    viewport={"width": 1366, "height": 820},
                    locale="en-US",
                    user_agent=settings.USER_AGENT,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                )
            except Exception as exc:
                message = str(exc)
                if "Executable doesn't exist" in message or "playwright install" in message:
                    raise PlaywrightNotInstalled(
                        "Chromium is not downloaded yet. Run: python -m playwright install chromium"
                    ) from exc
                raise

            # Hide the most obvious automation tell before any page script runs.
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            page = context.pages[0] if context.pages else await context.new_page()
            yield context, page
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            try:
                await playwright.stop()
            except Exception:
                pass

    async def verify_session(self, user_id: str, platform: str) -> dict[str, Any]:
        """Load the platform and report whether the stored session is live."""
        check = PLATFORM_CHECKS.get(platform)
        if not check:
            return {"platform": platform, "is_active": False, "reason": "Platform not supported"}

        if not self.has_session(user_id, platform):
            return {"platform": platform, "is_active": False, "reason": "No stored session"}

        try:
            async with self.browser_context(user_id, platform, headless=True) as (_context, page):
                await page.goto(check["url"], timeout=25_000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                current_url = page.url.lower()

                if any(marker in current_url for marker in check["logged_out_markers"]):
                    return {
                        "platform": platform,
                        "is_active": False,
                        "reason": "Redirected to the login page",
                    }

                for selector in check["logged_in_selectors"]:
                    if await page.locator(selector).count() > 0:
                        return {
                            "platform": platform,
                            "is_active": True,
                            "verified_at": datetime.now(timezone.utc).isoformat(),
                        }

                return {
                    "platform": platform,
                    "is_active": False,
                    "reason": "Signed-in markers not found",
                }
        except PlaywrightNotInstalled as exc:
            return {"platform": platform, "is_active": False, "reason": str(exc)}
        except Exception as exc:
            logger.warning("Session verification failed for %s: %s", platform, exc)
            return {"platform": platform, "is_active": False, "reason": str(exc)[:200]}

    async def interactive_login(self, user_id: str, platform: str, timeout_seconds: int = 300) -> dict[str, Any]:
        """Open a visible browser and wait for the user to finish logging in.

        Nothing is typed on the user's behalf and no credentials are stored -
        only the resulting browser profile persists on disk.
        """
        check = PLATFORM_CHECKS.get(platform)
        if not check:
            return {"success": False, "reason": f"Platform {platform!r} is not supported"}

        try:
            async with self.browser_context(user_id, platform, headless=False) as (_context, page):
                await page.goto(check["login_url"], timeout=30_000, wait_until="domcontentloaded")
                logger.info("Waiting up to %ds for %s login by user %s", timeout_seconds, platform, user_id)

                deadline = asyncio.get_running_loop().time() + timeout_seconds
                while asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(2)
                    try:
                        current_url = page.url.lower()
                    except Exception:
                        return {"success": False, "reason": "Browser window was closed"}

                    if any(marker in current_url for marker in check["logged_out_markers"]):
                        continue
                    for selector in check["logged_in_selectors"]:
                        if await page.locator(selector).count() > 0:
                            logger.info("%s login captured for user %s", platform, user_id)
                            return {
                                "success": True,
                                "platform": platform,
                                "session_dir": str(self.get_session_dir(user_id, platform)),
                            }

                return {"success": False, "reason": "Timed out waiting for sign-in"}
        except PlaywrightNotInstalled as exc:
            return {"success": False, "reason": str(exc)}
        except Exception as exc:
            logger.warning("Interactive login failed for %s: %s", platform, exc)
            return {"success": False, "reason": str(exc)[:200]}


login_manager = LoginManager()
