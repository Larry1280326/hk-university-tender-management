"""Integration test for HKUST portal reachability and login flow."""

import os
import unittest
from playwright.sync_api import sync_playwright

import config
from scraper import browser_utils
from scraper.auth import HKUSTAuthManager, LOGIN_READY_SELECTOR


class TestPortalReachability(unittest.TestCase):
    """Verifies HKUST portal terms acceptance and login form readiness."""

    @unittest.skipUnless(
        os.getenv("RUN_LIVE_PORTAL_TEST") == "1",
        "Live portal test skipped by default. Set RUN_LIVE_PORTAL_TEST=1 to run.",
    )
    def test_portal_flow(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                # Use the app's own context so the viewport and default timeouts
                # under test match production.
                context = HKUSTAuthManager().get_context(browser)
                page = context.new_page()

                browser_utils.navigate(
                    page,
                    config.HKUST_WELCOME_URL,
                    ready_selector="#tncChkBox, #vendorId",
                    label="welcome",
                )
                title = page.title()
                self.assertIn("HKUST", title, f"Unexpected page title: {title}")

                # Terms may already have been accepted, in which case the portal
                # goes straight to the login form and there is nothing to tick.
                if page.locator("#tncChkBox").count() > 0:
                    tnc_box = page.locator("#tncChkBox")
                    tnc_box.check()
                    self.assertTrue(tnc_box.is_checked(), "Failed to check terms checkbox")
                    page.click("#submitBtn", timeout=config.HKUST_READY_TIMEOUT_MS)

                page.wait_for_selector("#vendorId:visible", timeout=config.HKUST_READY_TIMEOUT_MS)

                is_login_page = "/td_login" in page.url or page.locator("#vendorId").count() > 0
                self.assertTrue(is_login_page, "Did not reach logon page after accepting terms")

                self.assertTrue(
                    page.locator("#vendorId").is_visible(), "Vendor ID input not visible"
                )
                self.assertTrue(
                    page.locator("#password").is_visible(), "Password input not visible"
                )

                # The portal's login handler calls grecaptcha.getResponse() before
                # it builds the request, so the form is unusable until this loads.
                browser_utils.wait_for_predicate(
                    page,
                    browser_utils.is_recaptcha_callable,
                    timeout_ms=config.HKUST_SCRIPT_TIMEOUT_MS,
                    label="the reCAPTCHA library",
                    url=config.HKUST_LOGIN_URL,
                )
            finally:
                browser.close()


if __name__ == "__main__":
    # If run directly from command line, enable live test
    os.environ["RUN_LIVE_PORTAL_TEST"] = "1"
    unittest.main()
