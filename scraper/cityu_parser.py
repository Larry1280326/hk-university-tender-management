"""Scraper and parser for CityU's e-Tendering System (ETS).

ETS organises procurement notices under two separate search sections:
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
from datetime import date
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

import config
from scraper import browser_utils
from scraper.tender_parser import TenderDocument, TenderNotice

DO_VIEW_REGEX = re.compile(r"doView\(['\"](\d+)['\"]\)")


def _clean_text(raw: str) -> str:
    """Collapse internal whitespace and non-breaking spaces into single spaces."""
    return re.sub(r"\s+", " ", (raw or "").replace("\xa0", " ")).strip()


class CityUTenderParser:
    """Scrapes active ('Issued') RFQs and Tender Invitations from CityU ETS."""

    def __init__(self, page: Page):
        self.page = page

    @property
    def search_sections(self) -> List[Tuple[str, str]]:
        """Return ``(category, search_url)`` pairs in scraping order."""
        return [
            ("RFQ", config.CITYU_RFQ_SEARCH_URL),
            ("Tender", config.CITYU_TENDER_SEARCH_URL),
        ]

    def _submit_search(self, search_url: str, category: str) -> bool:
        """Navigate to an ETS Search.aspx page and submit it to reach List.aspx."""
        if not browser_utils.navigate(
            self.page,
            search_url,
            ready_selector="form[name='PostForm'], table tr.trtext1",
            ready_state="attached",
            allow_missing=True,
            label=f"cityu-{category.lower()}-search",
        ):
            return False

        self.page.wait_for_load_state("domcontentloaded", timeout=config.NAV_TIMEOUT_MS)

        # If the page is Search.aspx (not already List.aspx), submit PostForm to Filter.aspx
        # sorted by Issue Date descending (order=2) and filtered to Closing Date >= today
        # so the server does not query and paginate hundreds of historical closed notices.
        if self.page.locator("form[name='ViewForm']").count() == 0:
            today = date.today()
            y, m, d = f"{today.year:04d}", f"{today.month:02d}", f"{today.day:02d}"
            with self.page.expect_navigation(
                wait_until="domcontentloaded", timeout=config.NAV_TIMEOUT_MS
            ):
                self.page.evaluate(
                    """([year, month, day]) => {
                        const form = document.forms['PostForm'];
                        if (form) {
                            const setVal = (name, val) => {
                                const el = form.querySelector(`[name='${name}']`);
                                if (el) el.value = val;
                            };
                            setVal('date_closed_from_1', year);
                            setVal('date_closed_from_2', month);
                            setVal('date_closed_from_3', day);
                            setVal('order', '2');
                            form.submit();
                        }
                    }""",
                    [y, m, d],
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

        Results are ordered by Issue Date, not by Status, so a page with no 'Issued'
        rows may still be followed by pages that contain them. Pagination therefore
        continues until there is no next-page link.
        """
        if not self._submit_search(search_url, category):
            return []

        list_url = urljoin(search_url, "List.aspx")
        view_url = urljoin(search_url, "View.aspx")
        issued_items: List[Dict[str, str]] = []
        page_num = 1

        while True:
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

            next_page = page_num + 1
            next_link = self.page.locator(f"a[href='List.aspx?Page={next_page}']")
            if next_link.count() == 0:
                break

            page_num = next_page
            browser_utils.navigate(
                self.page,
                f"{list_url}?Page={page_num}",
                ready_selector="table tr.trtext1, div.headtext1",
                label=f"cityu-{category.lower()}-list-p{page_num}",
            )

        return issued_items

    def _fetch_and_parse_detail(
        self, view_url: str, portal_id: str
    ) -> Tuple[str, str, Optional[str], bool, bool, List[TenderDocument]]:
        """Fetch View.aspx via in-page fetch() + DOMParser and extract details.

        Avoids full browser navigations and repeated subresource loads for each
        tender detail page.
        """
        data = self.page.evaluate(
            """async ([actionUrl, tenderId]) => {
                const resp = await fetch(actionUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: 'Page=1&tender_id=' + encodeURIComponent(tenderId),
                    credentials: 'include'
                });
                const html = await resp.text();
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const main = doc.querySelector('#MainContent') || doc;

                const fields = {};
                for (const row of main.querySelectorAll('table tr')) {
                    const cells = Array.from(row.querySelectorAll('td'));
                    let idx = 0;
                    while (idx + 1 < cells.length) {
                        const cls = cells[idx].getAttribute('class') || '';
                        if (cls.includes('tdtext1')) {
                            const label = (cells[idx].textContent || '').replace(/\\s+/g, ' ').trim();
                            const val = (cells[idx + 1].textContent || '').replace(/\\s+/g, ' ').trim();
                            if (label) {
                                fields[label.toLowerCase()] = val;
                            }
                            idx += 2;
                        } else {
                            idx += 1;
                        }
                    }
                }

                const hasEoiButton = Boolean(
                    main.querySelector(
                        "form[name='NewEOI'], input[value='Expression of Interest']"
                    )
                );

                const docs = [];
                const seenHrefs = new Set();
                if (!hasEoiButton) {
                    for (const link of main.querySelectorAll("a[href*='DownloadFile.aspx']")) {
                        const href = (link.getAttribute('href') || '').trim();
                        if (!href || seenHrefs.has(href)) continue;
                        seenHrefs.add(href);

                        const title = (link.getAttribute('title') || '').replace(/\\s+/g, ' ').trim();
                        const tr = link.closest('tr');
                        const cells = tr
                            ? Array.from(tr.children)
                                  .filter(c => c.tagName === 'TD')
                                  .map(td => (td.textContent || '').replace(/\\s+/g, ' ').trim())
                            : [];
                        docs.push({ href, title, cells });
                    }
                }

                return { fields, hasEoiButton, docs };
            }""",
            [view_url, portal_id],
        )

        fields: Dict[str, str] = data.get("fields", {})
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

        if data.get("hasEoiButton"):
            return person, email, phone, False, True, []

        documents: List[TenderDocument] = []
        for raw_doc in data.get("docs", []):
            href = raw_doc.get("href", "")
            title = _clean_text(raw_doc.get("title", ""))
            cells: List[str] = raw_doc.get("cells", [])

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

        return person, email, phone, True, False, documents

    def extract_tenders(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        existing_tenders: Optional[Set[str]] = None,
    ) -> List[TenderNotice]:
        """Extract all active ('Issued') RFQs and Tenders from CityU ETS."""
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
                (
                    contact_person,
                    contact_email,
                    contact_phone,
                    invited,
                    needs_eoi,
                    documents,
                ) = self._fetch_and_parse_detail(item["view_url"], item["portal_id"])
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                print(
                    f"[!] CityU detail page for {tender_no} not parsed: "
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
