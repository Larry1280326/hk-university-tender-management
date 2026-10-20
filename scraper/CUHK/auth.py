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

        Also stubs out CUPro's synchronous top-menu script (``menu.aspx?foo=...``),
        frameset landing pages, and decorative images so page loads are not stalled by
        non-essential subresources.
        """
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            accept_downloads=True,
            ignore_https_errors=True,
        )
        context.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
        context.set_default_timeout(config.READY_TIMEOUT_MS)

        fast_common_js = (
            "function $(id){return document.getElementById(id);}\n"
            "function showLayer(n){var e=$(n);if(e){e.style.visibility='';e.style.display='block';}return true;}\n"
            "function hideLayer(n){var e=$(n);if(e){e.style.visibility='hidden';e.style.display='none';}return true;}\n"
            "function lTrim(s){return String(s).replace(/^ +/,'');}\n"
            "function rTrim(s){return String(s).replace(/ +$/,'');}\n"
            "function trim(s){return rTrim(lTrim(s));}\n"
            "function isDate(){return true;}\n"
            "function isNumeric(s){return /^[0-9]+$/.test(s);}\n"
            "function doSubmitForm(f){f.submit();}\n"
        )
        context.route(
            "**/common.js*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body=fast_common_js,
            ),
        )
        context.route(
            "**/jquery.min.js*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body="",
            ),
        )
        context.route(
            "**/Global.css*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/css",
                body="",
            ),
        )
        context.route(
            "**/menu.aspx*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body="function etsDrawMenu(){}",
            ),
        )
        context.route(
            "**/Supplier/en/index.htm*",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body="<html><body></body></html>"
            ),
        )
        context.route(
            "**/TopFrame.aspx*",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body="<html><body></body></html>"
            ),
        )
        context.route(
            "**/GeneralInfo.aspx*",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body="<html><body></body></html>"
            ),
        )
        context.route(
            "**/*.{png,gif,jpg,jpeg}",
            lambda route: route.fulfill(
                status=200, content_type="image/gif", body=b""
            ),
        )
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
                ready_selector="form[name='PostForm']",
                ready_state="attached",
                label="cuhk-accept-terms",
            )
            page.wait_for_load_state("domcontentloaded", timeout=config.NAV_TIMEOUT_MS)

            print("[*] Accepting CUHK Terms & Conditions...")
            with page.expect_navigation(
                url="**/Login/New.aspx*",
                wait_until="domcontentloaded",
                timeout=config.NAV_TIMEOUT_MS,
            ):
                page.evaluate(
                    """() => {
                        for (const cb of document.querySelectorAll("input[name='terms']")) {
                            cb.checked = true;
                        }
                        document.forms['PostForm'].submit();
                    }"""
                )

            page.wait_for_selector(
                "form[name='PostForm'] input[name='username']",
                state="attached",
                timeout=config.READY_TIMEOUT_MS,
            )
            page.wait_for_load_state("domcontentloaded", timeout=config.NAV_TIMEOUT_MS)

            print(f"[*] Submitting CUHK login credentials for User ID: {u_id}...")
            page.evaluate(
                """([user, pwd]) => {
                    window.onload = null;
                    for (let i = 1; i < 1000; i++) clearTimeout(i);
                    const form = document.forms['PostForm'];
                    form.username.value = user;
                    form.password.value = pwd;
                    form.submit();
                }""",
                [u_id, u_pwd],
            )
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

    @staticmethod
    def _login_settled(page: Page, alerts: List[str]) -> bool:
        """True once the login submission has left /Login/ or raised an alert."""
        if alerts:
            return True
        try:
            page.wait_for_timeout(50)
        except Exception:
            pass
        return "/Login/" not in page.url

    def _wait_for_login_result(self, page: Page, alerts: List[str]) -> None:
        """Wait for the login submission on New.aspx to resolve."""
        try:
            browser_utils.wait_for_predicate(
                page,
                lambda p: self._login_settled(p, alerts),
                timeout_ms=config.POST_LOGIN_TIMEOUT_MS,
                poll_ms=50,
                label="the CUHK login result",
                url=config.CUHK_LOGIN_URL,
            )
            page.wait_for_load_state("domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
        except (NavigationError, PlaywrightTimeoutError):
            pass
