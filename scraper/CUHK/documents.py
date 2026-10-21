"""Expression of Interest (EOI) submission and document download for CUHK (CUPro).

On CUPro, open tenders lock their documents behind an ``Expression of Interest`` form
(``EOI/New.aspx`` -> ``EOI/Create.aspx`` with ``response_type=1`` ("Yes")). Once EOI is
submitted (or for tenders where EOI was already submitted), ``View.aspx`` exposes direct
``DownloadFile.aspx?file_id=...`` links for all tender documents, addenda, registration
forms, and query responses.
"""

import re
from pathlib import Path
from typing import List, Optional, Set
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import config
from scraper.models import TenderNotice
from scraper.paths import tender_dir_name

FILE_ID_REGEX = re.compile(r"file_id=(\d+)")


def _view_url_for_tender(tender: TenderNotice) -> str:
    """Resolve the View.aspx endpoint for a Tender or RFQ."""
    if tender.category.upper() == "RFQ" or tender.tender_no.upper().startswith("Q-"):
        return urljoin(config.CUHK_RFQ_SEARCH_URL, "View.aspx")
    return urljoin(config.CUHK_TENDER_SEARCH_URL, "View.aspx")


def _open_view_page(page: Page, view_url: str, portal_id: str) -> None:
    """POST ``tender_id`` to ``View.aspx`` and wait for ``#MainContent`` to be visible."""
    with page.expect_navigation(
        wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
    ):
        page.evaluate(
            """([actionUrl, tenderId]) => {
                let form = document.forms['ViewForm'];
                if (!form) {
                    if (!document.body || document.body.tagName === 'FRAMESET') {
                        document.documentElement.innerHTML = '<body></body>';
                    }
                    form = document.createElement('form');
                    form.method = 'post';
                    document.body.appendChild(form);
                }
                form.action = actionUrl;
                let input = form.querySelector("input[name='tender_id']");
                if (!input) {
                    input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'tender_id';
                    form.appendChild(input);
                }
                input.value = tenderId;
                form.submit();
            }""",
            [view_url, portal_id],
        )
    page.wait_for_selector(
        "#MainContent", state="attached", timeout=config.DETAIL_TIMEOUT_MS
    )


def _submit_eoi_if_needed(
    page: Page,
    tender: TenderNotice,
    view_url: str,
    *,
    log=print,
) -> None:
    """Submit 'Expression of Interest: Yes' if the tender still has an EOI form."""
    has_eoi = (
        page.locator(
            "#MainContent form[name='NewEOI'], "
            "#MainContent input[value='Expression of Interest']"
        ).count()
        > 0
    )
    if not has_eoi:
        return

    log(f"[*] Submitting Expression of Interest (Yes) for {tender.tender_no}...")
    with page.expect_navigation(
        wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
    ):
        page.evaluate(
            """() => {
                const form = document.forms['NewEOI'];
                if (form) {
                    form.submit();
                } else {
                    const btn = document.querySelector("input[value='Expression of Interest']");
                    if (btn) btn.click();
                }
            }"""
        )
    page.wait_for_selector(
        "input[name='response_type'][value='1']",
        state="attached",
        timeout=config.DETAIL_TIMEOUT_MS,
    )

    with page.expect_navigation(
        wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
    ):
        page.evaluate(
            """() => {
                const radio = document.querySelector("input[name='response_type'][value='1']");
                if (radio) radio.checked = true;
                const form = document.forms['PostForm'];
                if (form) {
                    form.submit();
                } else {
                    const btn = document.querySelector("input[name='SubmitButton']");
                    if (btn) btn.click();
                }
            }"""
        )

    # Re-open View.aspx now that EOI has unlocked the tender's downloadable files.
    _open_view_page(page, view_url, tender.portal_id or "")
    tender.invited = True
    tender.needs_eoi = False


def _unique_filename(raw_name: str, href: str, used_names: Set[str]) -> str:
    """Avoid overwriting when an Addendum re-uploads a file with the same filename."""
    clean_name = Path(raw_name).name or "document"
    if clean_name not in used_names:
        used_names.add(clean_name)
        return clean_name

    stem = Path(clean_name).stem
    suffix = Path(clean_name).suffix
    m = FILE_ID_REGEX.search(href)
    tag = m.group(1) if m else str(len(used_names) + 1)
    candidate = f"{stem}_{tag}{suffix}"
    used_names.add(candidate)
    return candidate


def _download_one_cuhk(
    page: Page,
    tender: TenderNotice,
    dest_root: Path,
    *,
    log=print,
) -> List[Path]:
    """Open one CUHK tender, submit EOI if needed, and download all unlocked files."""
    if not tender.portal_id:
        raise LookupError(f"{tender.tender_no} is missing its CUPro portal_id")

    view_url = _view_url_for_tender(tender)
    _open_view_page(page, view_url, tender.portal_id)

    if tender.needs_eoi:
        _submit_eoi_if_needed(page, tender, view_url, log=log)

    hrefs: List[str] = []
    seen_hrefs: Set[str] = set()
    for link in page.locator("#MainContent a[href*='DownloadFile.aspx']").all():
        href = (link.get_attribute("href") or "").strip()
        if href and href not in seen_hrefs:
            seen_hrefs.add(href)
            hrefs.append(href)

    if not hrefs:
        return []

    dest = dest_root / tender_dir_name(tender)
    dest.mkdir(parents=True, exist_ok=True)

    saved: List[Path] = []
    used_names: Set[str] = set()
    for href in hrefs:
        with page.expect_download(timeout=config.DETAIL_TIMEOUT_MS) as download_info:
            page.locator(f"#MainContent a[href='{href}']").first.click()
        download = download_info.value
        target_name = _unique_filename(download.suggested_filename, href, used_names)
        target = dest / target_name
        download.save_as(str(target))
        saved.append(target)

    return saved


def process_cuhk_tenders(
    page: Page,
    tenders: List[TenderNotice],
    dest_root: Optional[Path] = None,
    *,
    log=print,
    progress_callback=None,
    error_callback=None,
) -> List[Path]:
    """Submit EOI (where needed) and download all documents for selected CUHK tenders.

    Defaults ``dest_root`` to ``config.DOCUMENT_DIR / "CUHK"``.
    A failure on one tender is reported and skipped rather than aborting the rest.
    """
    dest_root = Path(dest_root or (config.DOCUMENT_DIR / "CUHK"))
    saved: List[Path] = []
    total = len(tenders)
    for index, tender in enumerate(tenders, start=1):
        if progress_callback:
            progress_callback(index, total, tender.tender_no)
        try:
            saved.extend(_download_one_cuhk(page, tender, dest_root, log=log))
        except (PlaywrightTimeoutError, PlaywrightError, LookupError) as e:
            message = (
                f"[!] Could not process CUHK tender {tender.tender_no}: "
                f"{type(e).__name__}: {e}"
            )
            log(message)
            if error_callback:
                error_callback(tender.tender_no, e)
    return saved
