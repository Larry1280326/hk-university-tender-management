"""Navigation helpers tuned for a slow, externally-hosted portal.

The HKUST login page calls ``grecaptcha.getResponse()`` unconditionally from its
login handler, so a navigation is only ready once the page's own JavaScript can
run. Waiting for network idle is not a proxy for that: the page pulls an 850 KB
reCAPTCHA bundle from gstatic.com, and on a busy link that request alone outlives
Playwright's 30s default. Every wait here is therefore pinned to the specific DOM
node or JS object the following code actually touches.
"""

from __future__ import annotations

import re
import time
import weakref
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import config


class NavigationError(RuntimeError):
    """A navigation failed after exhausting its attempts."""

    def __init__(
        self,
        url: str,
        stage: str,
        attempts: int,
        last_error: BaseException,
        detail: str = "",
    ) -> None:
        self.url = url
        self.stage = stage  # "goto" | "selector" | "predicate"
        self.attempts = attempts
        self.last_error = last_error
        self.detail = detail
        message = (
            f"Navigation to {url} failed after {attempts} attempt(s) "
            f"at stage '{stage}': {type(last_error).__name__}: {last_error}"
        )
        if detail:
            message = f"{message}\n    {detail}"
        super().__init__(message)


# Network-level failures that a fresh request will very likely get past. Timeouts
# are always retried; these are retried only when RETRY_ON_NET_ERRORS is on,
# because a saturated link surfaces as a reset rather than as a timeout.
_RETRYABLE_NET_MARKERS = (
    "net::ERR_TIMED_OUT",
    "net::ERR_CONNECTION_RESET",
    "net::ERR_CONNECTION_CLOSED",
    "net::ERR_CONNECTION_TIMED_OUT",
    "net::ERR_CONNECTION_REFUSED",
    "net::ERR_SSL_PROTOCOL_ERROR",
    "net::ERR_SSL_VERSION_OR_CIPHER_MISMATCH",
    "net::ERR_NETWORK_CHANGED",
    "net::ERR_NAME_NOT_RESOLVED",
    "net::ERR_ADDRESS_UNREACHABLE",
    "net::ERR_SOCKET_NOT_CONNECTED",
    "net::ERR_EMPTY_RESPONSE",
    "net::ERR_HTTP2_PROTOCOL_ERROR",
    "net::ERR_ABORTED",
)


def _is_retryable(error: BaseException, retry_on_net_errors: bool) -> bool:
    """Timeouts always retry; transient network errors retry only when enabled.

    Application errors (including a failed login) are never retryable, so they
    propagate on their first occurrence.
    """
    if isinstance(error, PlaywrightTimeoutError):
        return True
    if isinstance(error, PlaywrightError) and retry_on_net_errors:
        message = str(error)
        return any(marker in message for marker in _RETRYABLE_NET_MARKERS)
    return False


class RequestTracker:
    """Records in-flight and failed requests so a timeout can be explained."""

    MAX_TRACKED = 200

    def __init__(self) -> None:
        self._in_flight: Dict[str, None] = {}
        self._failed: List[Tuple[str, str]] = []

    def attach(self, page: Page) -> "RequestTracker":
        page.on("request", self._on_request)
        page.on("requestfinished", self._on_settled)
        page.on("requestfailed", self._on_failed)
        return self

    def _on_request(self, request) -> None:
        if len(self._in_flight) < self.MAX_TRACKED:
            self._in_flight.pop(request.url, None)
            self._in_flight[request.url] = None

    def _on_settled(self, request) -> None:
        self._in_flight.pop(request.url, None)

    def _on_failed(self, request) -> None:
        self._in_flight.pop(request.url, None)
        if len(self._failed) < self.MAX_TRACKED:
            self._failed.append((request.url, request.failure or "unknown error"))

    def summary(self, limit: int = 3) -> str:
        """Render the most recent pending and failed requests, newest last."""
        parts: List[str] = []
        pending = list(self._in_flight)
        if pending:
            parts.append("in-flight: " + "; ".join(pending[-limit:]))
        if self._failed:
            failed = self._failed[-limit:]
            parts.append("failed: " + "; ".join(f"{url} ({reason})" for url, reason in failed))
        return " | ".join(parts)


_TRACKERS: "weakref.WeakKeyDictionary[Page, RequestTracker]" = weakref.WeakKeyDictionary()


def attach_tracker(page: Page) -> RequestTracker:
    """Attach a request tracker to ``page`` once, returning the same one after."""
    try:
        tracker = _TRACKERS.get(page)
    except TypeError:  # pragma: no cover - page not weak-referenceable
        return RequestTracker().attach(page)
    if tracker is None:
        tracker = RequestTracker().attach(page)
        try:
            _TRACKERS[page] = tracker
        except TypeError:  # pragma: no cover
            pass
    return tracker


# The portal's callLoginApi() dereferences grecaptcha before any check, and then
# calls .length on the result. So "ready" means: the object exists, getResponse is
# callable, and it returns a string. The expression is guarded so it can never
# throw, which matters because a throwing probe would surface as a hard failure.
_RECAPTCHA_PROBE = """() => {
  try {
    if (typeof grecaptcha === 'undefined') return false;
    if (typeof grecaptcha.getResponse !== 'function') return false;
    return typeof grecaptcha.getResponse() === 'string';
  } catch (e) { return false; }
}"""


def is_recaptcha_callable(page: Page) -> bool:
    """True once the portal's login handler can safely call grecaptcha.getResponse()."""
    try:
        return bool(page.evaluate(_RECAPTCHA_PROBE))
    except PlaywrightError:
        return False


def wait_for_predicate(
    page: Page,
    predicate: Callable[[Page], bool],
    *,
    timeout_ms: int,
    poll_ms: int = 500,
    label: str = "",
    url: str = "",
) -> None:
    """Poll ``predicate`` until it holds, or raise once ``timeout_ms`` elapses.

    Polling happens in Python rather than via ``page.wait_for_function`` so that it
    cannot stall on requestAnimationFrame in a headless or backgrounded window.
    """
    label = label or "condition"
    deadline = time.monotonic() + (timeout_ms / 1000)
    while True:
        try:
            if predicate(page):
                return
        except PlaywrightError:
            pass  # e.g. the page is mid-navigation; treat as "not ready yet"
        if time.monotonic() >= deadline:
            raise NavigationError(
                url,
                "predicate",
                1,
                PlaywrightTimeoutError(f"{label} was not satisfied within {timeout_ms}ms"),
            )
        time.sleep(poll_ms / 1000)


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def dump_failure_artifacts(page: Page, label: str) -> Optional[Path]:
    """Write a screenshot and the page HTML for post-mortem, never masking the original error."""
    if not config.DEBUG_ARTIFACTS:
        return None
    try:
        debug_dir = config.ASSETS_DIR / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{_SLUG_RE.sub('-', label).strip('-') or 'failure'}"
        screenshot_path = debug_dir / f"{stem}.png"
        page.screenshot(path=str(screenshot_path), full_page=False)
        (debug_dir / f"{stem}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - a dump must never mask the real failure
        print(f"[!] Could not write debug artifacts: {type(e).__name__}: {e}")
        return None
    print(f"[!] Saved failure artifacts to {screenshot_path}")
    return screenshot_path


def navigate(
    page: Page,
    url: str,
    *,
    ready_selector: Optional[str] = None,
    ready_state: str = "attached",
    allow_missing: bool = False,
    attempts: Optional[int] = None,
    timeout_ms: Optional[int] = None,
    backoff_seconds: Optional[float] = None,
    label: str = "",
) -> bool:
    """Go to ``url`` and wait for the element the caller needs, retrying on transient failure.

    Navigates with ``domcontentloaded`` rather than ``load`` or ``networkidle``: the
    portal's slow third-party assets are not part of its readiness. Returns ``True``
    once ready, or ``False`` if ``ready_selector`` never appeared and
    ``allow_missing`` is set (an empty result is not a failure). Raises
    :class:`NavigationError` otherwise.
    """
    attach_tracker(page)

    attempts = max(1, config.NAV_ATTEMPTS if attempts is None else attempts)
    timeout_ms = config.NAV_TIMEOUT_MS if timeout_ms is None else timeout_ms
    backoff = config.NAV_BACKOFF_SECONDS if backoff_seconds is None else backoff_seconds
    label = label or url

    stage = "goto"
    last_error: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            delay = backoff * (attempt - 1)
            print(
                f"[!] {label}: {type(last_error).__name__} on attempt {attempt - 1}/{attempts}; "
                f"retrying in {delay:g}s..."
            )
            time.sleep(delay)
        try:
            stage = "goto"
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if ready_selector:
                stage = "selector"
                page.wait_for_selector(ready_selector, state=ready_state, timeout=timeout_ms)
            return True
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            last_error = e
            if not _is_retryable(e, config.RETRY_ON_NET_ERRORS):
                raise

    if allow_missing and stage == "selector":
        return False

    detail_parts = []
    if ready_selector:
        detail_parts.append(f"waited for selector {ready_selector!r}")
    tracker_summary = attach_tracker(page).summary()
    if tracker_summary:
        detail_parts.append(tracker_summary)
    dump_failure_artifacts(page, label)
    raise NavigationError(url, stage, attempts, last_error, detail="; ".join(detail_parts))
