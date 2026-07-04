"""
Browser automation module — Playwright wrapper for submitting freelance applications.
Handles cookie persistence, browser lifecycle, and platform-specific form filling.
"""
import os, json, logging, asyncio, time
from pathlib import Path
from typing import Optional, Dict, Any

log = logging.getLogger("freelance.browser")

COOKIES_DIR = "data/cookies"
DATA_DIR = "data"

def ensure_dirs():
    os.makedirs(COOKIES_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

def get_cookies_path(platform: str) -> str:
    return f"{COOKIES_DIR}/{platform}_cookies.json"

def save_cookies(platform: str, cookies: list):
    ensure_dirs()
    with open(get_cookies_path(platform), "w") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    log.info(f"Cookies saved for {platform}: {len(cookies)} cookies")

def load_cookies(platform: str) -> list:
    try:
        with open(get_cookies_path(platform)) as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def cookies_valid(platform: str) -> bool:
    """Check if we have cookies and they're not too old."""
    cookies = load_cookies(platform)
    if not cookies:
        return False
    # Check if any cookie has expired
    now = time.time()
    for c in cookies:
        expires = c.get("expires", 0)
        if expires > 0 and expires < now:
            return False
    return True


class BrowserManager:
    """Manages Playwright browser instances for automation."""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self._loop = None

    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    async def start(self):
        """Start Playwright browser."""
        try:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            log.info("Playwright browser started")
            return True
        except Exception as e:
            log.error(f"Failed to start browser: {e}")
            return False

    async def stop(self):
        """Stop browser."""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except:
            pass

    async def new_context(self, platform: str):
        """Create a new browser context with saved cookies."""
        context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )

        # Load saved cookies
        cookies = load_cookies(platform)
        if cookies:
            # Playwright needs specific format
            pw_cookies = []
            for c in cookies:
                cookie = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                }
                if c.get("expires") and c["expires"] > 0:
                    cookie["expires"] = c["expires"]
                pw_cookies.append(cookie)
            if pw_cookies:
                try:
                    await context.add_cookies(pw_cookies)
                    log.info(f"Loaded {len(pw_cookies)} cookies for {platform}")
                except Exception as e:
                    log.warning(f"Cookie load error: {e}")

        return context

    async def save_context_cookies(self, context, platform: str):
        """Save cookies from browser context."""
        cookies = await context.cookies()
        save_cookies(platform, cookies)

    def run_async(self, coro):
        """Run an async coroutine synchronously."""
        loop = self._get_loop()
        try:
            return loop.run_until_complete(coro)
        except RuntimeError as e:
            if "already running" in str(e):
                # If loop is already running, create a new one
                self._loop = asyncio.new_event_loop()
                return self._loop.run_until_complete(coro)
            raise


# Singleton instance
browser_manager = BrowserManager()


class BaseSubmitter:
    """Base class for platform-specific form submitters."""

    PLATFORM = "unknown"

    async def login_page_url(self) -> str:
        """URL for login page."""
        raise NotImplementedError

    async def is_logged_in(self, page) -> bool:
        """Check if we're logged in."""
        raise NotImplementedError

    async def fill_and_submit(self, page, task_url: str, response_text: str) -> Dict[str, Any]:
        """Fill the application form and submit. Returns success status."""
        raise NotImplementedError

    async def submit(self, task_url: str, response_text: str, browser_mgr: BrowserManager) -> Dict[str, Any]:
        """Main entry point: create context, navigate, submit, save cookies."""
        context = await browser_mgr.new_context(self.PLATFORM)
        page = await context.new_page()

        try:
            # Navigate to task
            await page.goto(task_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # Check if logged in
            logged_in = await self.is_logged_in(page)
            if not logged_in:
                return {
                    "success": False,
                    "error": "not_logged_in",
                    "message": f"Не авторизован на {self.PLATFORM}. Используй /login {self.PLATFORM}",
                }

            # Fill and submit
            result = await self.fill_and_submit(page, task_url, response_text)

            # Save cookies after successful interaction
            await browser_mgr.save_context_cookies(context, self.PLATFORM)

            return result

        except Exception as e:
            log.error(f"Submit error on {self.PLATFORM}: {e}")
            return {
                "success": False,
                "error": "exception",
                "message": str(e),
            }
        finally:
            await context.close()


class FlruSubmitter(BaseSubmitter):
    """Submit applications on Fl.ru"""

    PLATFORM = "flru"

    async def login_page_url(self):
        return "https://www.fl.ru/"

    async def is_logged_in(self, page):
        try:
            # Check if user menu or profile element exists
            logged_in = await page.query_selector(".b-topnav__auth-user, .b-topnav__login-btn-active, .b-topnav__user")
            return logged_in is not None
        except:
            return False

    async def fill_and_submit(self, page, task_url, response_text):
        try:
            # Fl.ru project page — look for "Откликнуться" button
            respond_btn = await page.query_selector("a.b-applica__btn, a[href*='respond'], .project-action-respond")
            if not respond_btn:
                # Try clicking a button that shows the form
                await page.click("text=Откликнуться", timeout=5000)
                await page.wait_for_timeout(1500)
            else:
                await respond_btn.click()
                await page.wait_for_timeout(1500)

            # Fill the response textarea
            textarea = await page.query_selector("textarea, .b-applica textarea, #project-respond-text")
            if textarea:
                await textarea.fill(response_text)
                await page.wait_for_timeout(500)

                # Submit
                submit = await page.query_selector("button[type='submit'], .b-applica__submit, input[type='submit']")
                if submit:
                    await submit.click()
                    await page.wait_for_timeout(2000)
                    return {"success": True, "message": "Отклик отправлен на Fl.ru"}

            return {"success": False, "error": "form_not_found", "message": "Форма отклика не найдена"}
        except Exception as e:
            return {"success": False, "error": "fill_error", "message": str(e)}


class FreelancerSubmitter(BaseSubmitter):
    """Submit applications on Freelancer.com"""

    PLATFORM = "freelancer"

    async def login_page_url(self):
        return "https://www.freelancer.com/login"

    async def is_logged_in(self, page):
        try:
            # Check for user avatar or dashboard link
            logged_in = await page.query_selector(".logged-in-user, .user-profile, a[href*='dashboard']")
            return logged_in is not None
        except:
            return False

    async def fill_and_submit(self, page, task_url, response_text):
        try:
            # Freelancer.com — look for "Place a Bid" or "Bid on this project"
            bid_btn = await page.query_selector("button[data-testid='bid-button'], .bid-on-project, button:has-text('Bid')")
            if bid_btn:
                await bid_btn.click()
                await page.wait_for_timeout(2000)

            # Fill bid amount (optional — we'll put 0 or a placeholder)
            amount_input = await page.query_selector("input[name='amount'], input[data-testid='bid-amount']")
            if amount_input:
                await amount_input.fill("100")  # Placeholder amount

            # Fill description
            textarea = await page.query_selector("textarea[name='description'], textarea[data-testid='bid-description'], .bid-description textarea")
            if textarea:
                await textarea.fill(response_text)
                await page.wait_for_timeout(500)

                # Submit bid
                submit = await page.query_selector("button[type='submit'], .bid-submit, button:has-text('Place Bid')")
                if submit:
                    await submit.click()
                    await page.wait_for_timeout(2000)
                    return {"success": True, "message": "Bid отправлен на Freelancer.com"}

            return {"success": False, "error": "form_not_found", "message": "Форма bid не найдена"}
        except Exception as e:
            return {"success": False, "error": "fill_error", "message": str(e)}


class KworkSubmitter(BaseSubmitter):
    """Submit applications on Kwork.ru"""

    PLATFORM = "kwork"

    async def login_page_url(self):
        return "https://kwork.ru/"

    async def is_logged_in(self, page):
        try:
            logged_in = await page.query_selector(".user-menu, .header-auth-user, a[href*='profile']")
            return logged_in is not None
        except:
            return False

    async def fill_and_submit(self, page, task_url, response_text):
        try:
            # Kwork — look for "Заказать" or "Откликнуться"
            respond_btn = await page.query_selector("a.want-to-buy, .kwork-response-btn, button:has-text('Заказать')")
            if respond_btn:
                await respond_btn.click()
                await page.wait_for_timeout(2000)

            textarea = await page.query_selector("textarea, .response-form textarea, #response-text")
            if textarea:
                await textarea.fill(response_text)
                await page.wait_for_timeout(500)

                submit = await page.query_selector("button[type='submit'], .response-submit")
                if submit:
                    await submit.click()
                    await page.wait_for_timeout(2000)
                    return {"success": True, "message": "Отклик отправлен на Kwork"}

            return {"success": False, "error": "form_not_found", "message": "Форма отклика не найдена"}
        except Exception as e:
            return {"success": False, "error": "fill_error", "message": str(e)}


class GithubSubmitter(BaseSubmitter):
    """Submit comments on GitHub Issues (for bounty/hiring issues)."""

    PLATFORM = "github"

    async def login_page_url(self):
        return "https://github.com/login"

    async def is_logged_in(self, page):
        try:
            logged_in = await page.query_selector(".logged-in, img.avatar, .AppHeader-user")
            return logged_in is not None
        except:
            return False

    async def fill_and_submit(self, page, task_url, response_text):
        try:
            # GitHub issue page — add a comment
            textarea = await page.query_selector("#issue_body, textarea.comment-form__text, .new-comment textarea")
            if textarea:
                await textarea.fill(response_text)
                await page.wait_for_timeout(500)

                submit = await page.query_selector("button[type='submit'], .btn-primary:has-text('Comment')")
                if submit:
                    await submit.click()
                    await page.wait_for_timeout(2000)
                    return {"success": True, "message": "Комментарий добавлен на GitHub"}

            return {"success": False, "error": "form_not_found", "message": "Форма комментария не найдена"}
        except Exception as e:
            return {"success": False, "error": "fill_error", "message": str(e)}


# Registry of submitters
SUBMITTERS = {
    "flru": FlruSubmitter(),
    "freelancer": FreelancerSubmitter(),
    "kwork": KworkSubmitter(),
    "github": GithubSubmitter(),
}


def get_submitter(platform: str) -> Optional[BaseSubmitter]:
    return SUBMITTERS.get(platform)


async def submit_application(platform: str, task_url: str, response_text: str) -> Dict[str, Any]:
    """Submit an application to a platform using Playwright."""
    submitter = get_submitter(platform)
    if not submitter:
        return {
            "success": False,
            "error": "unsupported_platform",
            "message": f"Платформа {platform} не поддерживает автоотправку",
        }

    # Ensure browser is running
    if not browser_manager.browser:
        started = await browser_manager.start()
        if not started:
            return {
                "success": False,
                "error": "browser_failed",
                "message": "Не удалось запустить браузер",
            }

    return await submitter.submit(task_url, response_text, browser_manager)
