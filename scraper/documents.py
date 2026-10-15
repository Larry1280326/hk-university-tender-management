"""Download tender documents for tenders the vendor was invited to.

Only the invitation table's detail pages expose documents; open tenders show a
"Request for Tender Document" contact instead, which the drafting flow handles.

Requires ``accept_downloads=True`` on the browser context (see
:meth:`scraper.auth.HKUSTAuthManager.get_context`), otherwise Chromium cancels the
response and ``page.expect_download`` never fires.
"""

import re
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import config
from scraper import browser_utils
from scraper.tender_parser import (
    DETAIL_READY_SELECTOR,
    DIRECTORY_ROW_SELECTOR,
    INVITE_TABLE_SELECTOR,
    TENDER_ROW_SELECTOR,
    TenderNotice,
)

LISTING_URL = config.HKUST_TENDER_OFFR_SEL_URL


def _normalise(url: str) -> str:
    """Drop the portal's ``?dummy=`` cache-buster so URL comparisons are meaningful."""
    return url.split("?")[0].split("#")[0].rstrip("/")


def safe_dir_name(tender_no: str) -> str:
    """Turn a tender number into a folder name: ``TNA2600106 EOI`` -> ``TNA2600106_EOI``."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", tender_no).strip("._-") or "tender"


def _ensure_listing(page: Page) -> None:
    """Return to the offer selection page, preferring browser history."""
    if _normalise(page.url) == _normalise(LISTING_URL):
        return
    try:
        page.go_back(wait_until="domcontentloaded", timeout=config.HKUST_NAV_TIMEOUT_MS)
        if _normalise(page.url) != _normalise(LISTING_URL):
            raise PlaywrightTimeoutError("go_back did not return to the offer selection page")
        page.wait_for_selector(
            DIRECTORY_ROW_SELECTOR, state="attached", timeout=config.HKUST_READY_TIMEOUT_MS
        )
    except (PlaywrightTimeoutError, PlaywrightError):
        browser_utils.navigate(
            page,
            LISTING_URL,
            ready_selector=DIRECTORY_ROW_SELECTOR,
            label="tender-documents-return",
        )


def _invited_row(page: Page, tender_no: str):
    """Locate the invitation row for ``tender_no`` by its Tender No. cell."""
    rows = page.locator(f"{INVITE_TABLE_SELECTOR} {TENDER_ROW_SELECTOR}")
    for index in range(rows.count()):
        row = rows.nth(index)
        cells = row.locator("td").all()
        if cells and cells[0].inner_text().strip() == tender_no:
            return row
    raise LookupError(f"{tender_no} is no longer listed under My Tender Invitation")


def _document_file_names(page: Page) -> List[str]:
    """File names currently listed on the detail page, in display order."""
    names: List[str] = []
    for row in page.locator("table tr").all():
        if row.locator(".doc-dl-btn").count() == 0:
            continue
        cells = row.locator("td").all()
        if len(cells) >= 2:
            names.append(cells[1].inner_text().strip())
    return names


def _download_one(page: Page, tender: TenderNotice, dest_root: Path) -> List[Path]:
    """Open one invited tender's detail page and save every document it lists."""
    _ensure_listing(page)
    _invited_row(page, tender.tender_no).locator(".tender-offer-dtl-dl-link").first.click(
        timeout=config.HKUST_DETAIL_TIMEOUT_MS
    )
    page.wait_for_load_state("domcontentloaded", timeout=config.HKUST_NAV_TIMEOUT_MS)
    page.wait_for_selector(
        DETAIL_READY_SELECTOR, state="attached", timeout=config.HKUST_DETAIL_TIMEOUT_MS
    )

    dest = dest_root / safe_dir_name(tender.tender_no)
    dest.mkdir(parents=True, exist_ok=True)

    saved: List[Path] = []
    for file_name in _document_file_names(page):
        # Re-locate per file rather than holding row handles: a download rewrites that
        # row's "Last Download On" cell, which can invalidate a held handle.
        row = page.locator("table tr").filter(has_text=file_name).first
        with page.expect_download(timeout=config.HKUST_DETAIL_TIMEOUT_MS) as download_info:
            row.locator(".doc-dl-btn").first.click()
        download = download_info.value
        # ``suggested_filename`` comes from the server's Content-Disposition, so take
        # only its basename rather than letting it contribute path components.
        target = dest / Path(download.suggested_filename).name
        download.save_as(str(target))
        saved.append(target)
    return saved


def download_tender_documents(
    page: Page,
    tenders: List[TenderNotice],
    dest_root: Optional[Path] = None,
) -> List[Path]:
    """Download every document of each invited tender into ``dest_root/<tender_no>/``.

    A failure on one tender is reported and skipped rather than aborting the rest.
    """
    dest_root = Path(dest_root or config.DOCUMENTS_DIR)
    saved: List[Path] = []
    for tender in tenders:
        try:
            saved.extend(_download_one(page, tender, dest_root))
        except (PlaywrightTimeoutError, PlaywrightError, LookupError) as e:
            print(
                f"[!] Could not download documents for {tender.tender_no}: "
                f"{type(e).__name__}: {e}"
            )
    return saved
