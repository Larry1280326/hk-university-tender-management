"""Reproduces the original navigation timeout locally, deterministically.

The bug this guards against: the login page loads Google reCAPTCHA from an
external host, and `page.goto(..., wait_until="networkidle")` waits for that
request to finish. On a busy link it outlives the 30s default and the scrape
dies before it starts.

Rather than needing a saturated link, a local TLS server accepts connections and
never answers, and the page's third-party requests are redirected to it. That is
a faithful stand-in for the slow request, with no sudo, no VPN changes, and no
background download required.

Gated behind RUN_LIVE_PORTAL_TEST=1 (it hits the real portal) and
RUN_SLOW_NETWORK_TEST=1 (it deliberately waits out a timeout):

    RUN_LIVE_PORTAL_TEST=1 RUN_SLOW_NETWORK_TEST=1 \\
        uv run python -m unittest tests.test_portal_slow_network -v
"""

import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
from scraper import browser_utils
from scraper.auth import LOGIN_READY_SELECTOR
from scraper.browser_utils import NavigationError

# How long the old wait condition is given before we call it hung.
LEGACY_WAIT_MS = 8000


class HangingTlsServer:
    """Accepts TCP connections and never completes the TLS handshake.

    A request redirected here stays in flight indefinitely, which is what a
    saturated link looks like from the browser's point of view.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port = self._sock.getsockname()[1]
        self._held = []
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        # Must keep the original scheme: Route.continue_ rejects a protocol change.
        return f"https://127.0.0.1:{self.port}/hang.js"

    def _serve(self) -> None:
        while True:
            try:
                self._held.append(self._sock.accept()[0])
            except OSError:
                return  # socket closed during teardown

    def close(self) -> None:
        self._sock.close()
        for connection in self._held:
            try:
                connection.close()
            except OSError:
                pass  # already torn down by the peer


def _make_router(server: HangingTlsServer, seen: list):
    """Send the page's third-party reCAPTCHA requests to the hanging server."""

    def router(route):
        url = route.request.url
        if "recaptcha.net" in url or "gstatic.com" in url:
            seen.append(url)
            route.continue_(url=server.url)
        else:
            route.continue_()

    return router


@unittest.skipUnless(
    os.getenv("RUN_LIVE_PORTAL_TEST") == "1",
    "Live portal test skipped by default. Set RUN_LIVE_PORTAL_TEST=1 to run.",
)
@unittest.skipUnless(
    os.getenv("RUN_SLOW_NETWORK_TEST") == "1",
    "Slow-network test skipped by default. Set RUN_SLOW_NETWORK_TEST=1 to run.",
)
class TestSlowNetworkResilience(unittest.TestCase):
    """The login page must not depend on the external reCAPTCHA request finishing."""

    def setUp(self):
        self.server = HangingTlsServer()
        self.addCleanup(self.server.close)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(headless=True)
        self.addCleanup(self.browser.close)

    def _context(self, router):
        context = self.browser.new_context(
            viewport={"width": 1400, "height": 900}, ignore_https_errors=True
        )
        self.addCleanup(context.close)
        context.route("**/*", router)
        return context

    def _hung_login_page(self, seen):
        """Return a page whose reCAPTCHA request is in flight forever."""
        page = self._context(_make_router(self.server, seen)).new_page()
        self.assertTrue(
            browser_utils.navigate(
                page,
                config.HKUST_LOGIN_URL,
                ready_selector=LOGIN_READY_SELECTOR,
                label="login-page",
            )
        )
        return page

    def test_readiness_does_not_wait_for_the_hung_third_party(self):
        seen = []
        started = time.monotonic()
        page = self._hung_login_page(seen)
        elapsed = time.monotonic() - started

        # The setup is only meaningful if the slow request was actually diverted.
        self.assertTrue(seen, "No reCAPTCHA request was intercepted; test setup is not exercising the bug")
        self.assertLess(
            elapsed,
            20.0,
            f"Readiness took {elapsed:.1f}s, so it is still gated on the slow third-party request",
        )

    def test_the_old_wait_condition_still_hangs(self):
        """The regression this fix removes: networkidle waits on the hung request."""
        page = self._hung_login_page([])

        with self.assertRaises(PlaywrightTimeoutError):
            page.goto(
                config.HKUST_LOGIN_URL,
                wait_until="networkidle",
                timeout=LEGACY_WAIT_MS,
            )

    def test_login_refuses_loudly_rather_than_hanging(self):
        page = self._hung_login_page([])

        with self.assertRaises(NavigationError) as ctx:
            browser_utils.wait_for_predicate(
                page,
                browser_utils.is_recaptcha_callable,
                timeout_ms=3000,
                poll_ms=500,
                label="the reCAPTCHA library",
                url=config.HKUST_LOGIN_URL,
            )

        self.assertEqual(ctx.exception.stage, "predicate")

    def test_blocking_the_recaptcha_hosts_makes_login_impossible(self):
        """Evidence for why request blocking is not a fix.

        The portal's login handler dereferences `grecaptcha` before any check, so
        with the library unavailable the handler throws and no login request is
        ever sent. The tool must therefore never block these hosts.
        """
        seen = []
        context = self._context(_make_router(self.server, seen))
        page = context.new_page()
        browser_utils.navigate(
            page,
            config.HKUST_LOGIN_URL,
            ready_selector=LOGIN_READY_SELECTOR,
            label="login-page",
        )

        self.assertTrue(seen, "reCAPTCHA request was not intercepted")
        self.assertFalse(
            browser_utils.is_recaptcha_callable(page),
            "expected window.grecaptcha to be unusable while its script is unreachable",
        )

    def test_a_failed_navigation_is_retried(self):
        attempts = {"documents": 0}

        def router(route):
            if route.request.resource_type == "document":
                attempts["documents"] += 1
                if attempts["documents"] == 1:
                    route.abort("connectionreset")
                    return
            route.continue_()

        page = self._context(router).new_page()

        with patch.object(config, "NAV_BACKOFF_SECONDS", 0.2):
            self.assertTrue(
                browser_utils.navigate(
                    page,
                    config.HKUST_LOGIN_URL,
                    ready_selector=LOGIN_READY_SELECTOR,
                    label="login-page",
                )
            )

        self.assertEqual(attempts["documents"], 2, "the first attempt should have been retried")

    def test_a_persistently_broken_navigation_reports_the_url_and_stage(self):
        page = self._context(
            lambda route: route.abort("connectionreset")
            if route.request.resource_type == "document"
            else route.continue_()
        ).new_page()

        with patch.object(config, "NAV_BACKOFF_SECONDS", 0.2), patch.object(
            config, "DEBUG_ARTIFACTS", False
        ):
            with self.assertRaises(NavigationError) as ctx:
                browser_utils.navigate(
                    page,
                    config.HKUST_LOGIN_URL,
                    ready_selector=LOGIN_READY_SELECTOR,
                    label="login-page",
                )

        self.assertEqual(ctx.exception.url, config.HKUST_LOGIN_URL)
        self.assertEqual(ctx.exception.stage, "goto")
        self.assertEqual(ctx.exception.attempts, config.NAV_ATTEMPTS)


if __name__ == "__main__":
    os.environ["RUN_LIVE_PORTAL_TEST"] = "1"
    os.environ["RUN_SLOW_NETWORK_TEST"] = "1"
    unittest.main()
