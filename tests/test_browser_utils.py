"""Unit tests for navigation helpers. No network access required."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import config
from scraper import browser_utils
from scraper.browser_utils import NavigationError


class FakeClock:
    """Stand-in for the ``time`` module so sleeps are instant and monotonic advances."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakePage:
    """Minimal Page double so retry control flow can be asserted exactly."""

    def __init__(
        self,
        *,
        goto=None,
        wait_for_selector=None,
        evaluate=None,
        url: str = "about:blank",
    ) -> None:
        self.goto_calls = []
        self.selector_calls = []
        self.handlers = {}
        self.url = url
        self.screenshots = []
        self._content = "<html>failure</html>"
        self._goto = goto or (lambda url, wait_until, timeout: None)
        self._wait_for_selector = wait_for_selector or (lambda sel, state, timeout: None)
        self._evaluate = evaluate or (lambda expression: False)

    def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        return self._goto(url, wait_until, timeout)

    def wait_for_selector(self, selector, state=None, timeout=None):
        self.selector_calls.append((selector, state, timeout))
        return self._wait_for_selector(selector, state, timeout)

    def evaluate(self, expression):
        return self._evaluate(expression)

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def screenshot(self, path=None, full_page=None):
        self.screenshots.append(path)
        Path(path).write_bytes(b"fake-png")

    def content(self):
        return self._content


def raise_always(error):
    def handler(*args, **kwargs):
        raise error

    return handler


class NavigateRetryTests(unittest.TestCase):
    """Retries re-navigate; application errors never retry."""

    def setUp(self):
        self.clock = FakeClock()
        self.addCleanup(patch.stopall)
        patch.object(config, "HKUST_DEBUG_ARTIFACTS", False).start()
        patch.object(config, "HKUST_NAV_ATTEMPTS", 3).start()
        patch.object(config, "HKUST_NAV_BACKOFF_SECONDS", 2.0).start()
        patch.object(config, "HKUST_NAV_TIMEOUT_MS", 15000).start()
        patch.object(browser_utils, "time", self.clock).start()
        self.print_patch = patch("builtins.print").start()

    def test_retries_transient_timeout_then_succeeds(self):
        calls = {"n": 0}

        def goto(url, wait_until, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PlaywrightTimeoutError("Timeout 15000ms exceeded.")

        page = FakePage(goto=goto)
        self.assertTrue(browser_utils.navigate(page, "https://example.test/login"))

        self.assertEqual(len(page.goto_calls), 3)
        self.assertEqual(self.clock.sleeps, [2.0, 4.0])
        # Every attempt re-requests the document rather than waiting longer.
        self.assertEqual({call[1] for call in page.goto_calls}, {"domcontentloaded"})
        # The user can see that a retry is happening and why.
        retry_lines = [c.args[0] for c in self.print_patch.call_args_list if c.args]
        self.assertTrue(any("retrying in 2s" in line for line in retry_lines), retry_lines)

    def test_exhausting_attempts_raises_with_url_and_stage(self):
        page = FakePage(goto=raise_always(PlaywrightTimeoutError("Timeout 15000ms exceeded.")))

        with self.assertRaises(NavigationError) as ctx:
            browser_utils.navigate(page, "https://example.test/login", label="login-page")

        error = ctx.exception
        self.assertEqual(error.url, "https://example.test/login")
        self.assertEqual(error.stage, "goto")
        self.assertEqual(error.attempts, 3)
        self.assertEqual(len(page.goto_calls), 3)
        self.assertIn("https://example.test/login", str(error))
        self.assertIn("stage 'goto'", str(error))

    def test_login_failure_is_never_retried(self):
        page = FakePage(goto=raise_always(RuntimeError("HKUST Login Failed: bad password")))

        with self.assertRaises(RuntimeError):
            browser_utils.navigate(page, "https://example.test/login")

        # One attempt only: re-posting credentials risks an account lockout.
        self.assertEqual(len(page.goto_calls), 1)

    def test_selector_stage_is_reported(self):
        page = FakePage(
            wait_for_selector=raise_always(PlaywrightTimeoutError("selector not found"))
        )

        with self.assertRaises(NavigationError) as ctx:
            browser_utils.navigate(
                page, "https://example.test/login", ready_selector="#vendorId"
            )

        self.assertEqual(ctx.exception.stage, "selector")
        self.assertIn("#vendorId", str(ctx.exception))

    def test_allow_missing_returns_false_instead_of_raising(self):
        page = FakePage(
            wait_for_selector=raise_always(PlaywrightTimeoutError("no rows"))
        )

        result = browser_utils.navigate(
            page,
            "https://example.test/notices",
            ready_selector="table tbody tr",
            allow_missing=True,
        )

        self.assertFalse(result)
        self.assertEqual(len(page.goto_calls), 3)  # still retries before giving up

    def test_goto_failure_still_raises_when_allow_missing(self):
        page = FakePage(goto=raise_always(PlaywrightTimeoutError("Timeout")))

        with self.assertRaises(NavigationError) as ctx:
            browser_utils.navigate(
                page, "https://example.test/notices", ready_selector="table tbody tr",
                allow_missing=True,
            )

        # A document that never loaded is a real failure, not an empty result.
        self.assertEqual(ctx.exception.stage, "goto")

    def test_network_reset_retried_when_enabled(self):
        calls = {"n": 0}

        def goto(url, wait_until, timeout):
            calls["n"] += 1
            if calls["n"] < 2:
                raise PlaywrightError("net::ERR_CONNECTION_RESET")

        page = FakePage(goto=goto)
        self.assertTrue(browser_utils.navigate(page, "https://example.test/login"))
        self.assertEqual(len(page.goto_calls), 2)

    def test_network_reset_not_retried_when_disabled(self):
        page = FakePage(goto=raise_always(PlaywrightError("net::ERR_CONNECTION_RESET")))

        with patch.object(config, "HKUST_RETRY_ON_NET_ERRORS", False):
            with self.assertRaises(PlaywrightError):
                browser_utils.navigate(page, "https://example.test/login")

        self.assertEqual(len(page.goto_calls), 1)

    def test_unrelated_playwright_error_is_not_retried(self):
        page = FakePage(goto=raise_always(PlaywrightError("Target page, context or browser has been closed")))

        with self.assertRaises(PlaywrightError):
            browser_utils.navigate(page, "https://example.test/login")

        self.assertEqual(len(page.goto_calls), 1)


class WaitForPredicateTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.addCleanup(patch.stopall)
        patch.object(browser_utils, "time", self.clock).start()
        patch("builtins.print").start()

    def test_returns_as_soon_as_predicate_holds(self):
        seen = {"n": 0}

        def predicate(page):
            seen["n"] += 1
            return seen["n"] >= 3

        browser_utils.wait_for_predicate(
            FakePage(), predicate, timeout_ms=5000, poll_ms=500
        )

        self.assertEqual(seen["n"], 3)
        self.assertEqual(self.clock.sleeps, [0.5, 0.5])

    def test_raises_predicate_stage_on_timeout(self):
        with self.assertRaises(NavigationError) as ctx:
            browser_utils.wait_for_predicate(
                FakePage(),
                lambda page: False,
                timeout_ms=1000,
                poll_ms=500,
                label="the reCAPTCHA library",
                url="https://example.test/login",
            )

        self.assertEqual(ctx.exception.stage, "predicate")
        self.assertIn("reCAPTCHA", str(ctx.exception))
        self.assertEqual(ctx.exception.url, "https://example.test/login")

    def test_playwright_error_is_treated_as_not_ready(self):
        """A page mid-navigation makes the probe throw; that is not a failure."""

        def predicate(page):
            raise PlaywrightError("Execution context was destroyed")

        with self.assertRaises(NavigationError):
            browser_utils.wait_for_predicate(FakePage(), predicate, timeout_ms=1000, poll_ms=500)

    def test_transient_playwright_error_then_success(self):
        seen = {"n": 0}

        def predicate(page):
            seen["n"] += 1
            if seen["n"] == 1:
                raise PlaywrightError("Execution context was destroyed")
            return True

        browser_utils.wait_for_predicate(FakePage(), predicate, timeout_ms=5000, poll_ms=500)
        self.assertEqual(seen["n"], 2)


class RecaptchaProbeTests(unittest.TestCase):
    def test_true_when_library_is_callable(self):
        page = FakePage(evaluate=lambda expression: True)
        self.assertTrue(browser_utils.is_recaptcha_callable(page))

    def test_false_while_library_is_still_loading(self):
        page = FakePage(evaluate=lambda expression: False)
        self.assertFalse(browser_utils.is_recaptcha_callable(page))

    def test_false_when_probe_cannot_run(self):
        page = FakePage(evaluate=raise_always(PlaywrightError("Execution context was destroyed")))
        self.assertFalse(browser_utils.is_recaptcha_callable(page))

    def test_probe_is_guarded_so_it_cannot_throw(self):
        # The expression must test for existence before dereferencing, because a
        # throwing probe would surface as a hard failure.
        probe = browser_utils._RECAPTCHA_PROBE
        self.assertIn("typeof grecaptcha === 'undefined'", probe)
        self.assertIn("try {", probe)
        self.assertIn("catch (e)", probe)


class RequestTrackerTests(unittest.TestCase):
    class FakeRequest:
        def __init__(self, url, failure=None):
            self.url = url
            self.failure = failure

    def test_summary_reports_in_flight_and_failed_requests(self):
        page = FakePage()
        tracker = browser_utils.attach_tracker(page)

        page.handlers["request"][0](self.FakeRequest("https://cdn.test/pending.js"))
        page.handlers["requestfailed"][0](
            self.FakeRequest("https://cdn.test/dead.js", "net::ERR_CONNECTION_RESET")
        )

        summary = tracker.summary()
        self.assertIn("https://cdn.test/pending.js", summary)
        self.assertIn("https://cdn.test/dead.js", summary)
        self.assertIn("net::ERR_CONNECTION_RESET", summary)

    def test_finished_request_leaves_no_trace(self):
        page = FakePage()
        tracker = browser_utils.attach_tracker(page)
        request = self.FakeRequest("https://cdn.test/ok.js")

        page.handlers["request"][0](request)
        page.handlers["requestfinished"][0](request)

        self.assertEqual(tracker.summary(), "")

    def test_attach_tracker_is_idempotent(self):
        page = FakePage()
        first = browser_utils.attach_tracker(page)
        second = browser_utils.attach_tracker(page)
        self.assertIs(first, second)
        self.assertEqual(len(page.handlers["request"]), 1)


class FailureArtifactTests(unittest.TestCase):
    def test_writes_screenshot_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = FakePage()
            with patch.object(config, "ASSETS_DIR", Path(tmp)), patch.object(
                config, "HKUST_DEBUG_ARTIFACTS", True
            ), patch("builtins.print"):
                path = browser_utils.dump_failure_artifacts(page, "login page")

            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertTrue(path.name.endswith("-login-page.png"))
            self.assertTrue(path.with_suffix(".html").read_text().startswith("<html>"))
            self.assertEqual(page.screenshots, [str(path)])

    def test_disabled_by_config(self):
        with patch.object(config, "HKUST_DEBUG_ARTIFACTS", False):
            self.assertIsNone(browser_utils.dump_failure_artifacts(FakePage(), "login"))

    def test_dump_failure_never_masks_the_original_error(self):
        page = FakePage()
        page.screenshot = raise_always(PlaywrightError("browser closed"))

        with patch.object(config, "HKUST_DEBUG_ARTIFACTS", True), patch("builtins.print"):
            self.assertIsNone(browser_utils.dump_failure_artifacts(page, "login"))


if __name__ == "__main__":
    unittest.main()
