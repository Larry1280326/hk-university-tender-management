"""Authentication and session management for the CUHK Procurement System (CUPro)."""

from typing import List, Optional
from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import config
from scraper import browser_utils
from scraper.browser_utils import NavigationError


class CUHKAuthManager:
    """Manages Terms acceptance and login for the CUHK CUPro Supplier portal."""

    def __init__(self, headless: bool = True):
        self.headless = headless

    def get_context(self, browser: Browser) -> BrowserContext:
        """Create a browser context configured for CUPro.

        ``accept_downloads=True`` is required for tender document downloads, and
        ``ignore_https_errors=True`` is required because ``cupro-cuhk.com`` omits the
        intermediate CA certificate from its TLS chain.
        """
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            accept_downloads=True,
            ignore_https_errors=True,
        )
        context.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
        context.set_default_timeout(config.READY_TIMEOUT_MS)
        return context

    def perform_login(
        self,
        page: Page,
        user_id: Optional[str] = None,
        password: Optional[str] = None,
    ) -> bool:
        """Accept the CUPro terms and log in with the configured Supplier User ID."""
        u_id = user_id or config.CUHK_USER_ID
        u_pwd = password or config.CUHK_PASSWORD

        if not u_id or not u_pwd:
            raise ValueError(
                "CUHK User ID or Password is missing!\n"
                "Please configure CUHK_USER_ID and CUHK_PASSWORD in your .env file."
            )

        alerts: List[str] = []

        def _on_dialog(dialog) -> None:
            alerts.append(dialog.message)
            dialog.accept()

        page.on("dialog", _on_dialog)
        try:
            print(f"[*] Navigating to CUHK Terms page: {config.CUHK_ACCEPT_TERMS_URL}")
            browser_utils.navigate(
                page,
                config.CUHK_ACCEPT_TERMS_URL,
                ready_selector="#MainContent",
                ready_state="visible",
                label="cuhk-accept-terms",
            )

            self._accept_terms(page)

            print("[*] Waiting for CUHK login form...")
            page.wait_for_url("**/Login/New.aspx*", timeout=config.READY_TIMEOUT_MS)
            # New.aspx runs a 500ms onload timer that clears username/password right after
            # revealing #MainContent, so wait for load + #MainContent visible before filling.
            page.wait_for_load_state("load", timeout=config.NAV_TIMEOUT_MS)
            page.wait_for_selector(
                "#MainContent", state="visible", timeout=config.READY_TIMEOUT_MS
            )

            print(f"[*] Entering CUHK User ID: {u_id}")
            page.locator("input[name='username']").fill(u_id)
            page.locator("input[name='password']").fill(u_pwd)

            print("[*] Submitting CUHK login credentials...")
            with page.expect_navigation(
                wait_until="domcontentloaded",
                timeout=config.POST_LOGIN_TIMEOUT_MS,
            ):
                page.evaluate("document.forms['PostForm'].submit()")
            self._wait_for_login_result(page, alerts)

            if alerts:
                raise RuntimeError(f"CUHK Login Failed: {alerts[-1]}")
            if "/Login/" in page.url:
                raise RuntimeError(
                    f"CUHK Login Failed: still on login page ({page.url})"
                )

            print(f"[+] CUHK Login successful. Current URL: {page.url}")
            return True
        finally:
            try:
                page.remove_listener("dialog", _on_dialog)
            except Exception:
                pass

    def _accept_terms(self, page: Page) -> None:
        """Tick all three declaration checkboxes on AcceptTerms.aspx and click Accept."""
        checkboxes = page.locator("input[name='terms']")
        count = checkboxes.count()
        for i in range(count):
            checkboxes.nth(i).check()
        page.locator("input[value='Accept']").click(timeout=config.READY_TIMEOUT_MS)

    @staticmethod
    def _login_settled(page: Page, alerts: List[str]) -> bool:
        """True once the login submission has left /Login/ or raised an alert."""
        if alerts:
            return True
        return "/Login/" not in page.url

    def _wait_for_login_result(self, page: Page, alerts: List[str]) -> None:
        """Wait for the 500ms delayed form submission on New.aspx to resolve."""
        try:
            browser_utils.wait_for_predicate(
                page,
                lambda p: self._login_settled(p, alerts),
                timeout_ms=config.POST_LOGIN_TIMEOUT_MS,
                label="the CUHK login result",
                url=config.CUHK_LOGIN_URL,
            )
            page.wait_for_load_state("domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
        except (NavigationError, PlaywrightTimeoutError):
            pass
