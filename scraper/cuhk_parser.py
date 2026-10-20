"""Scraper and parser for the CUHK Procurement System (CUPro).

CUPro organises procurement notices under two separate search sections:
1. ``/Supplier/en/RFQ/Search.aspx`` — Request for Quotation (refs ``Q-YYYYNNNN``)
2. ``/Supplier/en/Tender/Search.aspx`` — Tender Invitation (refs ``T-YYYYNNNN``)

In both sections, active opportunities carry ``Status == "Issued"``.
On a tender's detail page (``View.aspx``):
- If an ``Expression of Interest`` button (``form[name='NewEOI']``) is present, the
  supplier has not yet submitted an EOI, so downloadable files are not yet unlocked
  (``invited=False, needs_eoi=True``).
- Otherwise, the supplier has already submitted EOI (or was directly invited) and all
  tender documents, addenda, and query responses are unlocked for download
  (``invited=True, needs_eoi=False``).
"""

import re
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import config
from scraper import browser_utils
from scraper.tender_parser import TenderDocument, TenderNotice

DO_VIEW_REGEX = re.compile(r"doView\(['\"](\d+)['\"]\)")
FILE_ID_REGEX = re.compile(r"file_id=(\d+)")


def _clean_text(raw: str) -> str:
    """Collapse internal whitespace and non-breaking spaces into single spaces."""
    return re.sub(r"\s+", " ", (raw or "").replace("\xa0", " ")).strip()


class CUHKTenderParser:
    """Scrapes active ('Issued') RFQs and Tender Invitations from CUHK CUPro."""

    def __init__(self, page: Page):
        self.page = page

    @property
    def search_sections(self) -> List[Tuple[str, str]]:
        """Return ``(category, search_url)`` pairs in scraping order."""
        return [
            ("RFQ", config.CUHK_RFQ_SEARCH_URL),
            ("Tender", config.CUHK_TENDER_SEARCH_URL),
        ]

    def _submit_search(self, search_url: str, category: str) -> bool:
        """Navigate to a CUPro Search.aspx page and submit it to reach List.aspx."""
        if not browser_utils.navigate(
            self.page,
            search_url,
            ready_selector="form[name='PostForm'], table tr.trtext1",
            ready_state="attached",
            allow_missing=True,
            label=f"cuhk-{category.lower()}-search",
        ):
            return False

        # If the page is Search.aspx (not already List.aspx), submit PostForm to Filter.aspx
        # which redirects to List.aspx sorted by Issue Date descending (order=2).
        if self.page.locator("form[name='ViewForm']").count() == 0:
            self.page.wait_for_selector(
                "#MainContent", state="visible", timeout=config.READY_TIMEOUT_MS
            )
            self.page.evaluate(
                """() => {
                    const form = document.forms['PostForm'];
                    if (form) {
                        const orderSelect = form.querySelector("select[name='order']");
                        if (orderSelect) orderSelect.value = '2';
                        form.submit();
                    }
                }"""
            )
            self.page.wait_for_url("**/List.aspx*", timeout=config.NAV_TIMEOUT_MS)
            self.page.wait_for_load_state(
                "domcontentloaded", timeout=config.NAV_TIMEOUT_MS
            )

        self.page.wait_for_selector(
            "table tr.trtext1, div.headtext1",
            state="attached",
            timeout=config.READY_TIMEOUT_MS,
        )
        return True

    def _collect_issued_rows_in_section(
        self, category: str, search_url: str
    ) -> List[Dict[str, str]]:
        """Paginate List.aspx and collect all rows whose Status is 'Issued'.

        Results are ordered by Issue Date descending, so pagination stops as soon as a
        page contains zero 'Issued' rows or has no next-page link.
        """
        if not self._submit_search(search_url, category):
            return []

        list_url = urljoin(search_url, "List.aspx")
        view_url = urljoin(search_url, "View.aspx")
        issued_items: List[Dict[str, str]] = []
        page_num = 1

        while True:
            page_issued_count = 0
            rows = self.page.locator("table tr.trtext2, table tr.trtext3").all()

            for row in rows:
                cells = row.locator("td").all()
                if len(cells) < 6:
                    continue

                ref_link = cells[1].locator("a").first
                if ref_link.count() == 0:
                    continue

                tender_no = _clean_text(ref_link.inner_text())
                onclick = ref_link.get_attribute("onclick") or ""
                m = DO_VIEW_REGEX.search(onclick)
                if not m:
                    continue
                portal_id = m.group(1)

                subj_span = cells[2].locator("span[title]").first
                if subj_span.count() > 0 and subj_span.get_attribute("title"):
                    description = _clean_text(subj_span.get_attribute("title") or "")
                else:
                    description = _clean_text(cells[2].inner_text())

                status = _clean_text(cells[3].inner_text())
                closing_date = _clean_text(cells[5].inner_text())

                if status.lower() == "issued":
                    page_issued_count += 1
                    issued_items.append(
                        {
                            "portal_id": portal_id,
                            "tender_no": tender_no,
                            "description": description,
                            "closing_date": closing_date,
                            "category": category,
                            "view_url": view_url,
                        }
                    )

            if page_issued_count == 0:
                break

            next_page = page_num + 1
            next_link = self.page.locator(f"a[href='List.aspx?Page={next_page}']")
            if next_link.count() == 0:
                break

            page_num = next_page
            browser_utils.navigate(
                self.page,
                f"{list_url}?Page={page_num}",
                ready_selector="table tr.trtext1, div.headtext1",
                label=f"cuhk-{category.lower()}-list-p{page_num}",
            )

        return issued_items

    def _open_detail_page(self, view_url: str, portal_id: str) -> None:
        """Open View.aspx for ``portal_id`` by POSTing ``tender_id``."""
        with self.page.expect_navigation(
            wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
        ):
            self.page.evaluate(
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
        self.page.wait_for_selector(
            "#MainContent", state="visible", timeout=config.DETAIL_TIMEOUT_MS
        )

    def _parse_contact_details(self) -> Tuple[str, str, Optional[str]]:
        """Extract contact person, email, and phone from View.aspx's Contact table."""
        fields: Dict[str, str] = {}
        for row in self.page.locator("#MainContent table tr").all():
            cells = row.locator("td").all()
            idx = 0
            while idx + 1 < len(cells):
                cls = cells[idx].get_attribute("class") or ""
                if "tdtext1" in cls:
                    label = _clean_text(cells[idx].inner_text())
                    val = _clean_text(cells[idx + 1].inner_text())
                    if label:
                        fields[label.lower()] = val
                    idx += 2
                else:
                    idx += 1

        email = fields.get("#1 email address") or fields.get("#2 email address") or ""
        phone = fields.get("#1 tel. number") or fields.get("#2 tel. number") or None

        name1 = fields.get("#1 name", "")
        name2 = fields.get("#2 name", "")
        if name1 and "@" not in name1:
            person = name1
        elif name2 and "@" not in name2:
            person = name2
        else:
            person = "Sir/Madam"

        return person, email, phone

    def _parse_document_list(self) -> List[TenderDocument]:
        """Parse all downloadable files unlocked on View.aspx."""
        documents: List[TenderDocument] = []
        seen_hrefs: Set[str] = set()

        for link in self.page.locator("#MainContent a[href*='DownloadFile.aspx']").all():
            href = (link.get_attribute("href") or "").strip()
            if not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            title = _clean_text(link.get_attribute("title") or "")
            row_info = link.evaluate(
                """(el) => {
                    const tr = el.closest('tr');
                    if (!tr) return { cells: [] };
                    const tds = Array.from(tr.children).filter(c => c.tagName === 'TD');
                    return { cells: tds.map(td => td.innerText.trim()) };
                }"""
            )
            cells: List[str] = row_info.get("cells", [])

            if len(cells) >= 3:
                subject = _clean_text(cells[0])
                size = _clean_text(cells[2])
                doc_type = "Document"
            elif cells:
                subject = _clean_text(cells[0])
                size = ""
                doc_type = "Registration Form"
            else:
                subject = title or "Document"
                size = ""
                doc_type = "Document"

            file_name = title or subject or href
            documents.append(
                TenderDocument(
                    doc_type=doc_type,
                    file_name=file_name,
                    subject=subject,
                    updated_on="",
                    size=size,
                )
            )

        return documents

    def _parse_detail_page(self) -> Tuple[str, str, Optional[str], bool, bool, List[TenderDocument]]:
        """Inspect View.aspx for contact details, EOI requirement, and unlocked files."""
        contact_person, contact_email, contact_phone = self._parse_contact_details()
        has_eoi_button = (
            self.page.locator(
                "#MainContent form[name='NewEOI'], "
                "#MainContent input[value='Expression of Interest']"
            ).count()
            > 0
        )
        if has_eoi_button:
            return contact_person, contact_email, contact_phone, False, True, []

        documents = self._parse_document_list()
        return contact_person, contact_email, contact_phone, True, False, documents

    def extract_tenders(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        existing_tenders: Optional[Set[str]] = None,
    ) -> List[TenderNotice]:
        """Extract all active ('Issued') RFQs and Tenders from CUPro."""
        raw_items: List[Dict[str, str]] = []
        for category, search_url in self.search_sections:
            raw_items.extend(self._collect_issued_rows_in_section(category, search_url))

        total = len(raw_items)
        tenders: List[TenderNotice] = []

        for idx, item in enumerate(raw_items):
            tender_no = item["tender_no"]
            if progress_callback:
                progress_callback(idx + 1, total, tender_no)

            contact_person = "Sir/Madam"
            contact_email = ""
            contact_phone: Optional[str] = None
            invited = False
            needs_eoi = False
            documents: List[TenderDocument] = []

            try:
                self._open_detail_page(item["view_url"], item["portal_id"])
                (
                    contact_person,
                    contact_email,
                    contact_phone,
                    invited,
                    needs_eoi,
                    documents,
                ) = self._parse_detail_page()
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                print(
                    f"[!] CUHK detail page for {tender_no} not parsed: "
                    f"{type(e).__name__}: {e}"
                )

            tenders.append(
                TenderNotice(
                    tender_no=tender_no,
                    description=item["description"],
                    closing_date=item["closing_date"],
                    contact_person=contact_person,
                    contact_email=contact_email,
                    contact_phone=contact_phone,
                    tender_type=(
                        "EOI Submitted / Invited"
                        if invited
                        else f"Open {item['category']}"
                    ),
                    invited=invited,
                    documents=documents,
                    portal_id=item["portal_id"],
                    category=item["category"],
                    needs_eoi=needs_eoi,
                )
            )

        return tenders
