"""Scraper for the HKUST tender offer selection page and enquiry contact details.

The offer selection page renders two separate tender tables and they mean different
things:

* ``#inviteTenderList`` -- "My Tender Invitation / Interested Tender(s)": tenders this
  vendor was invited to or registered interest in. The portal has already released the
  tender documents, and the detail page lists them for download.
* ``#openTenderList`` -- "Tender Notice": publicly advertised tenders. No documents are
  released; the detail page instead carries a "Request for Tender Document" section
  holding the Purchasing Office contact to email.

Both tables share the same columns, and neither has the ``Tender Type`` column that the
older ``td_tender_notice`` page carried:

    0 Tender No. | 1 Description | 2 Closing Date & Time | 3 Submit Bid
    4 Edit Bid   | 5 Last Submission Record

The description is a ``<button class="tender-offer-dtl-dl-link">``, not an anchor.
"""

import re
from typing import Callable, List, Optional, Set, Tuple
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import config
from scraper import browser_utils
from scraper.models import TenderDocument, TenderNotice
from mailer.email_parser import normalize_tender_ref

EMAIL_REGEX = re.compile(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", re.IGNORECASE)
CONTACT_PERSON_REGEX = re.compile(r"Contact\s+Person\s*[:：]\s*([^\n\r]+)", re.IGNORECASE)
PHONE_REGEX = re.compile(r"(\+?852[-\s]?)?[2-9]\d{3}[-\s]?\d{4}")

INVITE_TABLE_SELECTOR = "#inviteTenderList"
OPEN_TABLE_SELECTOR = "#openTenderList"

# Data rows carry this class. DataTables' responsive mode adds child rows without it,
# so filtering on the class keeps collapsed cells from being read as extra tenders.
TENDER_ROW_SELECTOR = "tbody tr.tender-dtl-row"

# Both tender tables are on one page, so readiness is the first row of either.
DIRECTORY_ROW_SELECTOR = f"{INVITE_TABLE_SELECTOR} tbody tr, {OPEN_TABLE_SELECTOR} tbody tr"

# Detail readiness: open tenders render the enquiry rows, invited tenders render the
# document list instead. Either marker means the page the caller reads next is present.
DETAIL_READY_SELECTOR = (
    "table tr:has-text('Enquiry'), "
    "table tr:has-text('Request for Tender Document'), "
    "table tr:has-text('File Name')"
)

# Column order shared by both tender tables.
COL_TENDER_NO = 0
COL_DESCRIPTION = 1
COL_CLOSING_DATE = 2


class TenderParser:
    """Parses the invitation and open-tender tables and their detail pages."""

    LISTING_URL = config.HKUST_TENDER_OFFR_SEL_URL

    # (table selector, whether that table holds invited tenders), in display order.
    TENDER_TABLES = ((INVITE_TABLE_SELECTOR, True), (OPEN_TABLE_SELECTOR, False))

    def __init__(self, page: Page):
        self.page = page

    def _on_listing_page(self, url: Optional[str] = None) -> bool:
        """Compare URLs tolerantly, ignoring query, fragment, and trailing slash.

        The portal appends a ``?dummy=`` cache-buster to every URL, so exact string
        equality would force a full re-navigation for every row instead of a cheap
        go_back.
        """
        normalise = lambda u: u.split("?")[0].split("#")[0].rstrip("/")
        return normalise(url or self.page.url) == normalise(self.LISTING_URL)

    def _return_to_listing(self) -> None:
        """Get back to the tender tables, preferring a cheap history navigation."""
        if self._on_listing_page():
            return
        try:
            self.page.go_back(
                wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
            )
            if not self._on_listing_page():
                raise PlaywrightTimeoutError("go_back did not return to the offer selection page")
            self.page.wait_for_selector(
                DIRECTORY_ROW_SELECTOR,
                state="attached",
                timeout=config.READY_TIMEOUT_MS,
            )
        except (PlaywrightTimeoutError, PlaywrightError):
            browser_utils.navigate(
                self.page,
                self.LISTING_URL,
                ready_selector=DIRECTORY_ROW_SELECTOR,
                label="tender-offer-selection-return",
            )

    def _open_detail_page(self, table_selector: str, row_index: int) -> None:
        """Click a row's description button and wait for its detail page to render."""
        row = self.page.locator(f"{table_selector} {TENDER_ROW_SELECTOR}").nth(row_index)
        row.locator(".tender-offer-dtl-dl-link").first.click(
            timeout=config.DETAIL_TIMEOUT_MS
        )
        self.page.wait_for_load_state("domcontentloaded", timeout=config.NAV_TIMEOUT_MS)
        self.page.wait_for_selector(
            DETAIL_READY_SELECTOR, state="attached", timeout=config.DETAIL_TIMEOUT_MS
        )

    def _parse_document_list(self) -> List[TenderDocument]:
        """Read the documents listed on an invited tender's detail page.

        Columns are Type | File Name | Doc Subject | Updated On (HKT) | Size.
        """
        documents: List[TenderDocument] = []
        for row in self.page.locator("table tr").all():
            if row.locator(".doc-dl-btn").count() == 0:
                continue
            cells = [c.inner_text().strip() for c in row.locator("td").all()]
            if len(cells) < 5:
                continue
            documents.append(
                TenderDocument(
                    doc_type=cells[0],
                    file_name=cells[1],
                    subject=cells[2],
                    updated_on=cells[3],
                    size=cells[4],
                )
            )
        return documents

    def _parse_enquiry_details(self) -> Tuple[str, str, str]:
        """Extract contact person, email, and raw enquiry text from current detail page."""
        contact_person = "Sir/Madam"
        contact_email = ""
        raw_enquiry = ""

        info_rows = self.page.locator("table tr").all()
        for ir in info_rows:
            th = ir.locator("th, td").first.inner_text().strip()
            if "enquiry" in th.lower():
                enquiry_cell = ir.locator("td").last
                raw_enquiry = enquiry_cell.inner_text().strip()

                cp_match = CONTACT_PERSON_REGEX.search(raw_enquiry)
                if cp_match:
                    contact_person = cp_match.group(1).strip()

                em_match = EMAIL_REGEX.search(raw_enquiry)
                if em_match:
                    contact_email = em_match.group(1).strip()
                break

            elif "request for tender document" in th.lower():
                req_text = ir.locator("td").last.inner_text().strip()
                if not contact_email:
                    em_match = EMAIL_REGEX.search(req_text)
                    if em_match:
                        contact_email = em_match.group(1).strip()
                if contact_person == "Sir/Madam":
                    cp_match = re.search(
                        r"attention of\s+([A-Za-z\s]+?)\s+of",
                        req_text,
                        re.IGNORECASE,
                    )
                    if cp_match:
                        contact_person = cp_match.group(1).strip()

        return contact_person, contact_email, raw_enquiry

    def extract_tenders(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        existing_tenders: Optional[Set[str]] = None,
    ) -> List[TenderNotice]:
        """Extract both tender tables, skipping tenders already drafted in Gmail."""
        normalized_existing: Set[str] = set()
        if existing_tenders:
            for t_ref in existing_tenders:
                if t_ref:
                    norm = normalize_tender_ref(t_ref)
                    if norm:
                        normalized_existing.add(norm)

        if not browser_utils.navigate(
            self.page,
            self.LISTING_URL,
            ready_selector=DIRECTORY_ROW_SELECTOR,
            allow_missing=True,
            label="tender-offer-selection",
        ):
            print("[!] No tender rows found on the offer selection page.")
            return []

        # Snapshot the work list up front: returning from a detail page must not shift
        # the indices of rows still to visit.
        work: List[Tuple[str, int, bool]] = []
        for table_selector, invited in self.TENDER_TABLES:
            for row_index in range(
                self.page.locator(f"{table_selector} {TENDER_ROW_SELECTOR}").count()
            ):
                work.append((table_selector, row_index, invited))

        total_rows = len(work)
        tenders: List[TenderNotice] = []

        for position, (table_selector, row_index, invited) in enumerate(work):
            self._return_to_listing()

            row = self.page.locator(f"{table_selector} {TENDER_ROW_SELECTOR}").nth(row_index)
            cells = row.locator("td").all()

            if len(cells) <= max(COL_TENDER_NO, COL_DESCRIPTION, COL_CLOSING_DATE):
                continue

            tender_no = cells[COL_TENDER_NO].inner_text().strip()
            description = cells[COL_DESCRIPTION].inner_text().strip()
            closing_date = cells[COL_CLOSING_DATE].inner_text().strip()

            norm_no = normalize_tender_ref(tender_no)
            if norm_no and norm_no in normalized_existing:
                if progress_callback:
                    progress_callback(
                        position + 1, total_rows, f"{tender_no} (Already in Gmail - Skipped)"
                    )
                continue

            if progress_callback:
                progress_callback(position + 1, total_rows, tender_no)

            contact_person = "Sir/Madam"
            contact_email = ""
            raw_enquiry = ""
            documents: List[TenderDocument] = []

            try:
                self._open_detail_page(table_selector, row_index)
                if invited:
                    documents = self._parse_document_list()
                else:
                    contact_person, contact_email, raw_enquiry = self._parse_enquiry_details()
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                print(f"[!] Detail page for {tender_no} not parsed: {type(e).__name__}: {e}")

            tenders.append(
                TenderNotice(
                    tender_no=tender_no,
                    description=description,
                    closing_date=closing_date,
                    contact_person=contact_person,
                    contact_email=contact_email,
                    tender_type="Invitation" if invited else "Tender Notice",
                    raw_enquiry=raw_enquiry,
                    already_processed=False,
                    invited=invited,
                    documents=documents,
                )
            )

        return tenders
