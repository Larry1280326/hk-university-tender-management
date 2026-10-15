"""Authentication and session management for HKUST e-Tendering portal."""

from typing import Optional
from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import config
from scraper import browser_utils
from scraper.browser_utils import NavigationError

# This navigation legitimately lands on either the login form or the Terms page,
# so readiness is the presence of either one's marker element.
LOGIN_READY_SELECTOR = "#vendorId, #tncChkBox"


class HKUSTAuthManager:
    """Manages login, Terms acceptance, and session persistence for HKUST portal."""

    def __init__(self, headless: bool = True):
        self.headless = headless

    def get_context(self, browser: Browser) -> BrowserContext:
        """Create fresh browser context with bounded default timeouts.

        ``accept_downloads`` is required for the tender document downloads: without it
        Chromium cancels the response and ``page.expect_download`` never fires.
        """
        context = browser.new_context(
            viewport={"width": 1400, "height": 900}, accept_downloads=True
        )
        context.set_default_navigation_timeout(config.HKUST_NAV_TIMEOUT_MS)
        context.set_default_timeout(config.HKUST_READY_TIMEOUT_MS)
        return context

    def perform_login(
        self,
        page: Page,
        vendor_id: Optional[str] = None,
        password: Optional[str] = None,
    ) -> bool:
        """Navigate to HKUST portal, accept Terms if required, and log in."""
        v_id = vendor_id or config.HKUST_VENDOR_ID
        v_pwd = password or config.HKUST_PASSWORD

        if not v_id or not v_pwd:
            raise ValueError(
                "HKUST Vendor ID or Password is missing!\n"
                "Please configure HKUST_VENDOR_ID and HKUST_PASSWORD in your .env file."
            )

        print(f"[*] Navigating to HKUST login page: {config.HKUST_LOGIN_URL}")
        browser_utils.navigate(
            page,
            config.HKUST_LOGIN_URL,
            ready_selector=LOGIN_READY_SELECTOR,
            label="login-page",
        )

        self._accept_terms_if_present(page)

        print("[*] Waiting for login form...")
        page.wait_for_selector("#vendorId:visible", timeout=config.HKUST_READY_TIMEOUT_MS)

        print(f"[*] Entering Vendor ID: {v_id}")
        self._fill_credentials(page, v_id, v_pwd)

        # The portal's callLoginApi() dereferences grecaptcha before it builds the
        # POST to /td_login_proc, so clicking Logon before that library loads raises
        # ReferenceError and sends no request at all - a silent failure.
        self._ensure_recaptcha_callable(page, v_id, v_pwd)

        print("[*] Submitting login credentials...")
        page.locator("#submitBtn").click(timeout=config.HKUST_READY_TIMEOUT_MS)
        self._wait_for_login_result(page)

        # Check for login errors
        warning_modal = page.locator("#warningModal")
        if warning_modal.is_visible():
            err_text = page.locator("#warningMsg").inner_text().strip()
            raise RuntimeError(f"HKUST Login Failed: {err_text}")

        alert_box = page.locator("#alertBox")
        if alert_box.is_visible() and alert_box.inner_text().strip():
            print(f"[!] Portal alert: {alert_box.inner_text().strip()}")

        print(f"[+] Login successful. Current URL: {page.url}")
        return True

    def _fill_credentials(self, page: Page, v_id: str, v_pwd: str) -> None:
        """Fill the login form. Re-run after any reload, which wipes the fields."""
        page.wait_for_selector("#vendorId:visible", timeout=config.HKUST_READY_TIMEOUT_MS)
        page.locator("#vendorId:visible").fill(v_id)
        page.locator("#password:visible").fill(v_pwd)

    def _accept_terms_if_present(self, page: Page) -> None:
        """Accept the Terms page when the portal routes there instead of to the login form."""
        if "/td_welcome" not in page.url and page.locator("#tncChkBox").count() == 0:
            return

        print("[*] Terms agreement required. Accepting Terms & Conditions...")
        page.evaluate("""() => {
            const el = document.getElementById('tncChkBox');
            if (el) {
                el.checked = true;
                if (typeof $ !== 'undefined') $(el).prop('checked', true).trigger('change');
            }
        }""")
        page.locator("#submitBtn").click(timeout=config.HKUST_READY_TIMEOUT_MS)
        try:
            page.wait_for_url("**/td_login*", timeout=config.HKUST_READY_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            browser_utils.navigate(
                page,
                config.HKUST_LOGIN_URL,
                ready_selector=LOGIN_READY_SELECTOR,
                label="login-page-fallback",
            )

    def _ensure_recaptcha_callable(self, page: Page, v_id: str, v_pwd: str) -> None:
        """Reload the login page until the page's own handler can call grecaptcha.

        Waiting longer cannot help by itself: the browser will not re-request a
        script whose fetch already failed. Only a fresh document does, so a retry
        reloads the page rather than extending the timeout.
        """
        attempts = max(1, config.HKUST_NAV_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            try:
                browser_utils.wait_for_predicate(
                    page,
                    browser_utils.is_recaptcha_callable,
                    timeout_ms=config.HKUST_SCRIPT_TIMEOUT_MS,
                    label="the reCAPTCHA library",
                    url=config.HKUST_LOGIN_URL,
                )
                return
            except NavigationError:
                if attempt == attempts:
                    break
                print(
                    f"[!] reCAPTCHA library not ready (attempt {attempt}/{attempts}); "
                    "reloading the login page..."
                )
                browser_utils.navigate(
                    page,
                    config.HKUST_LOGIN_URL,
                    ready_selector=LOGIN_READY_SELECTOR,
                    label="login-reload",
                )
                self._accept_terms_if_present(page)
                self._fill_credentials(page, v_id, v_pwd)

        raise NavigationError(
            config.HKUST_LOGIN_URL,
            "predicate",
            attempts,
            RuntimeError("window.grecaptcha never became callable"),
            detail=(
                "The portal's login handler calls grecaptcha.getResponse() "
                "unconditionally, so login cannot proceed without this script. Check "
                "connectivity to www.recaptcha.net. Do NOT block recaptcha.net or "
                "gstatic.com - doing so breaks login entirely."
            ),
        )

    @staticmethod
    def _login_settled(page: Page) -> bool:
        """True once the login POST has resolved.

        Either the portal navigated away from the login URL, or it rendered one of
        its own error/alert boxes. Covering both means a rejected login is reported
        as soon as the portal answers rather than after the full timeout.
        """
        if "/td_login" not in page.url:
            return True
        return (
            page.locator("#warningModal").is_visible()
            or page.locator("#alertBox").is_visible()
        )

    def _wait_for_login_result(self, page: Page) -> None:
        """Wait for the login POST to settle before inspecting the result."""
        page.wait_for_load_state("domcontentloaded", timeout=config.HKUST_NAV_TIMEOUT_MS)
        try:
            browser_utils.wait_for_predicate(
                page,
                self._login_settled,
                timeout_ms=config.HKUST_POST_LOGIN_TIMEOUT_MS,
                label="the login result",
                url=config.HKUST_LOGIN_URL,
            )
        except NavigationError:
            pass  # a slow POST; the checks that follow will decide what happened
