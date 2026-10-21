"""Real portal markup shared by the tender parser tests.

The structures below mirror what ``td_tender_offr_sel`` and its ``td_tender_offr_dtl_sel``
detail page actually render, so the parser's selectors are exercised against the real
shapes rather than against MagicMock stand-ins. Everything is loaded with
``page.set_content``, so no network access is needed.
"""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from playwright.sync_api import sync_playwright

import config
from scraper.HKUST.parser import TenderParser

INVITE_TITLE = "My Tender Invitation / Interested Tender(s)"
OPEN_TITLE = "Tender Notice"

_COLUMNS = (
    "Tender No.",
    "Description",
    "Closing Date &amp; Time (HKT)#",
    "Submit Bid",
    "Edit Bid",
    "Last Submission Record (HKT)#",
)

# A DataTables placeholder row, rendered when a table has no data. It deliberately has
# no tender-dtl-row class, so the parser must not read it as a tender.
_EMPTY_ROW = (
    '<tr class="odd"><td colspan="6" class="dataTables_empty">'
    "No data available in table</td></tr>"
)

# Mirrors the document list on an invited tender's detail page.
DOCUMENT_DETAIL_HTML = """
<table id="DataTables_Table_0">
  <thead>
    <tr><th>Type</th><th>File Name</th><th>Doc Subject</th><th>Updated On (HKT)</th>
        <th>Size</th><th>Download</th><th>Last Download On (HKT)</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Invitation</td><td>TNA2600106EOI_I01.pdf</td><td>Invitation</td>
      <td>15-Oct-2026 10:20:37</td><td>415.6 KB</td>
      <td><button type="button" class="btn btn-primary btn-xs action-btn doc-dl-btn"></button></td>
      <td>15-Oct-2026 10:25:30</td>
    </tr>
  </tbody>
</table>
"""

# Mirrors the enquiry section on an open tender's detail page. The <br> line breaks are
# load-bearing: CONTACT_PERSON_REGEX stops at a newline, so the contact person's name
# must end the line the way it does on the real page.
ENQUIRY_DETAIL_HTML = """
<table class="table table-striped table-bordered table-hover" width="100%">
  <tbody>
    <tr>
      <td>Request for Tender Document</td>
      <td>Tenderers who have interest to the captioned tender should send an email with
      their contact details including Company Name, Company Address, Name of the Contact
      Person, Email Address and a copy of Business Registration Certificate of the Company
      to <a href="mailto:{0}"><b>pumimi@ust.hk</b></a> for the attention of Mimi WONG of
      the Purchasing Office.</td>
    </tr>
    <tr>
      <td>Enquiry</td>
      <td>For enquiry on this tender, please send an email with your Company Name, Company
      Address, Name of the Contact Person and Email Address to the following contact
      point:<br><br>Purchasing Office Contact Person: Mimi WONG<br>Email Address: pumimi@ust.hk</td>
    </tr>
  </tbody>
</table>
"""


def tender_table(table_id: str, title: str, rows) -> str:
    """Build a DataTables-shaped tender table from ``(tender_no, description, closing)``."""
    header = "".join(f"<th>{c}</th>" for c in _COLUMNS)
    body = "".join(
        f"""
        <tr class="tender-dtl-row odd">
          <td>{tender_no}</td>
          <td><button type="button" class="btn btn-xs btn-link tender-offer-dtl-dl-link">{description}</button></td>
          <td>{closing}</td>
          <td><button type="button" class="action-btn btn btn-info btn-sm tender-offer-submit-btn">Submit Offer</button></td>
          <td><button type="button" class="btn btn-xs btn-link tender-offer-edit-link">/</button></td>
          <td></td>
        </tr>"""
        for tender_no, description, closing in rows
    )
    return f"""
<table class="table table-striped dt-responsive tndr-list-tbl dataTable" id="{table_id}">
  <thead>
    <tr><th colspan="6">{title}</th></tr>
    <tr>{header}</tr>
  </thead>
  <tbody>{body or _EMPTY_ROW}</tbody>
</table>"""


@contextmanager
def rendered_page(html: str):
    """Yield a Chromium page with ``html`` loaded, skipping if no browser is installed."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - depends on the local install
            raise unittest.SkipTest(f"Chromium unavailable: {e}")
        try:
            page = browser.new_page()
            page.set_content(html)
            yield page
        finally:
            browser.close()


@contextmanager
def parser_on(page):
    """A ``TenderParser`` over an already-rendered page with portal navigation stubbed.

    ``set_content`` leaves the page at ``about:blank``, so pointing the configured listing
    URL there makes ``_on_listing_page`` succeed and keeps ``extract_tenders`` from trying
    to return to a real URL mid-test. The parser reads the URL per call, so patching
    ``config`` is what takes effect.
    """
    with patch.object(config, "HKUST_TENDER_OFFR_SEL_URL", "about:blank"), patch(
        "scraper.browser_utils.navigate", return_value=True
    ):
        yield TenderParser(page)
