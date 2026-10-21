"""Browser sessions shared by the CLI and the Streamlit worker.

Each context manager yields a logged-in ``(page, parser)`` and closes the browser on
exit. They are kept here rather than in ``app.py`` so the web worker can use them
without importing the CLI (and its argparse/questionary surface).
"""

import shlex
from contextlib import contextmanager
from typing import Callable, Optional

from playwright.sync_api import sync_playwright

import config
from scraper.CityU.auth import CityUAuthManager
from scraper.CityU.parser import CityUTenderParser
from scraper.CUHK.auth import CUHKAuthManager
from scraper.CUHK.parser import CUHKTenderParser
from scraper.HKUST.auth import HKUSTAuthManager
from scraper.HKUST.parser import TenderParser


def _launch_args(explicit: Optional[str]) -> list:
    """Resolve Chromium launch flags once, so a server can inject container-safe ones."""
    raw = config.PLAYWRIGHT_LAUNCH_ARGS if explicit is None else explicit
    return shlex.split(raw) if raw else []


@contextmanager
def portal_session(
    headless: bool = True,
    *,
    log: Callable[[str], None] = print,
    launch_args: Optional[str] = None,
):
    """Yield a logged-in HKUST ``(page, parser)`` and close the browser on exit.

    Must never wrap an interactive prompt: ``sync_playwright()`` keeps a running asyncio
    event loop on the main thread for as long as this context is open, and prompt_toolkit
    (which questionary uses) calls ``asyncio.run()``, which refuses to start inside a
    running loop. Every session is therefore entered, used, and exited around a single
    scrape or download.
    """
    auth_mgr = HKUSTAuthManager(headless=headless)

    with sync_playwright() as p:
        log("[*] Launching Chromium browser...")
        browser = p.chromium.launch(headless=headless, args=_launch_args(launch_args))
        context = auth_mgr.get_context(browser)
        page = context.new_page()

        try:
            if config.HKUST_VENDOR_ID and config.HKUST_PASSWORD:
                log(f"[*] Logging in as Vendor ID: {config.HKUST_VENDOR_ID}...")
                auth_mgr.perform_login(page)
            else:
                log("No login credentials in .env, accessing public page...")

            yield page, TenderParser(page)
        finally:
            context.close()
            browser.close()


@contextmanager
def cuhk_portal_session(
    headless: bool = True,
    *,
    log: Callable[[str], None] = print,
    launch_args: Optional[str] = None,
):
    """Yield a logged-in CUHK ``(page, parser)`` and close the browser on exit."""
    auth_mgr = CUHKAuthManager(headless=headless)

    with sync_playwright() as p:
        log("[*] Launching Chromium browser...")
        browser = p.chromium.launch(headless=headless, args=_launch_args(launch_args))
        context = auth_mgr.get_context(browser)
        page = context.new_page()

        try:
            log(f"[*] Logging into CUHK CUPro as User ID: {config.CUHK_USER_ID}...")
            auth_mgr.perform_login(page)
            yield page, CUHKTenderParser(page)
        finally:
            context.close()
            browser.close()


@contextmanager
def cityu_portal_session(
    headless: bool = True,
    *,
    log: Callable[[str], None] = print,
    launch_args: Optional[str] = None,
):
    """Yield a logged-in CityU ``(page, parser)`` and close the browser on exit."""
    auth_mgr = CityUAuthManager(headless=headless)

    with sync_playwright() as p:
        log("[*] Launching Chromium browser...")
        browser = p.chromium.launch(headless=headless, args=_launch_args(launch_args))
        context = auth_mgr.get_context(browser)
        page = context.new_page()

        try:
            log(f"[*] Logging into CityU e-Tendering as User ID: {config.CITYU_USER_ID}...")
            auth_mgr.perform_login(page)
            yield page, CityUTenderParser(page)
        finally:
            context.close()
            browser.close()
