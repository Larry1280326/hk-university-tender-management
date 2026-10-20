"""Tests for the two separate tender tables on the offer selection page.

Both tables share a six-column layout (Tender No. | Description | Closing Date | Submit
Bid | Edit Bid | Last Submission Record) and their rows carry the ``tender-dtl-row``
class. The invitation table's detail pages hold documents; the open table's hold the
enquiry contact.
"""

import unittest
from unittest.mock import MagicMock

from scraper.HKUST.parser import TenderParser
from scraper.models import TenderDocument
from tests.portal_fixtures import (
    DOCUMENT_DETAIL_HTML,
    ENQUIRY_DETAIL_HTML,
    INVITE_TITLE,
    OPEN_TITLE,
    parser_on,
    rendered_page,
    tender_table,
)

INVITE_ROWS = [("TNA2600106 EOI", "Expression of Interest on TSMC MPW", "29-Oct-2026 15:00:00")]
OPEN_ROWS = [
    ("TNK2600096", "IBM Maximo Application Suite", "22-Oct-2026 15:00:00"),
    ("TNH2600105", "Window Leakage Repair Works", "22-Oct-2026 15:00:00"),
]


def listing_html(invite_rows, open_rows):
    return tender_table("inviteTenderList", INVITE_TITLE, invite_rows) + tender_table(
        "openTenderList", OPEN_TITLE, open_rows
    )


class TestTenderTableSplit(unittest.TestCase):

    def _extract(self, html, documents=None, enquiry=("Mimi WONG", "pumimi@ust.hk", "raw")):
        """Run extraction over rendered HTML with the detail page stubbed out."""
        with rendered_page(html) as page, parser_on(page) as parser:
            parser._open_detail_page = MagicMock()
            parser._parse_document_list = MagicMock(return_value=documents or [])
            parser._parse_enquiry_details = MagicMock(return_value=enquiry)
            return parser.extract_tenders(), parser

    def test_reads_both_tables_and_maps_six_columns(self):
        """Rows come from both tables, with the description column read as column 1."""
        tenders, _ = self._extract(listing_html(INVITE_ROWS, OPEN_ROWS))

        self.assertEqual(
            [t.tender_no for t in tenders],
            ["TNA2600106 EOI", "TNK2600096", "TNH2600105"],
        )
        self.assertEqual([t.invited for t in tenders], [True, False, False])

        invited = tenders[0]
        self.assertEqual(invited.description, "Expression of Interest on TSMC MPW")
        self.assertEqual(invited.closing_date, "29-Oct-2026 15:00:00")

        opened = tenders[1]
        self.assertEqual(opened.description, "IBM Maximo Application Suite")
        self.assertEqual(opened.closing_date, "22-Oct-2026 15:00:00")

    def test_empty_invitation_table_yields_no_invited_tenders(self):
        """An empty table renders a placeholder row, which must not be read as a tender."""
        tenders, parser = self._extract(listing_html([], OPEN_ROWS))

        self.assertEqual([t.tender_no for t in tenders], ["TNK2600096", "TNH2600105"])
        self.assertFalse(any(t.invited for t in tenders))
        parser._parse_document_list.assert_not_called()

    def test_detail_parser_is_chosen_by_group(self):
        """Invited rows read the document list; open rows read the enquiry section."""
        documents = [
            TenderDocument(
                doc_type="Invitation",
                file_name="TNA2600106EOI_I01.pdf",
                subject="Invitation",
                updated_on="15-Oct-2026 10:20:37",
                size="415.6 KB",
            )
        ]
        tenders, parser = self._extract(listing_html(INVITE_ROWS, OPEN_ROWS), documents=documents)

        self.assertEqual(parser._parse_document_list.call_count, 1)
        self.assertEqual(parser._parse_enquiry_details.call_count, 2)
        self.assertEqual(tenders[0].documents, documents)

        # Every row's detail page is opened via its description button.
        self.assertEqual(parser._open_detail_page.call_count, 3)

    def test_open_tenders_carry_contact_details(self):
        tenders, _ = self._extract(listing_html([], OPEN_ROWS))

        self.assertEqual(tenders[0].contact_person, "Mimi WONG")
        self.assertEqual(tenders[0].contact_email, "pumimi@ust.hk")


class TestDetailPageParsing(unittest.TestCase):

    def test_document_list_is_parsed_from_invited_detail_page(self):
        with rendered_page(DOCUMENT_DETAIL_HTML) as page:
            documents = TenderParser(page)._parse_document_list()

        self.assertEqual(len(documents), 1)
        doc = documents[0]
        self.assertEqual(doc.file_name, "TNA2600106EOI_I01.pdf")
        self.assertEqual(doc.doc_type, "Invitation")
        self.assertEqual(doc.subject, "Invitation")
        self.assertEqual(doc.updated_on, "15-Oct-2026 10:20:37")
        self.assertEqual(doc.size, "415.6 KB")

    def test_enquiry_is_parsed_from_open_detail_page(self):
        with rendered_page(ENQUIRY_DETAIL_HTML) as page:
            contact_person, contact_email, raw_enquiry = TenderParser(page)._parse_enquiry_details()

        self.assertEqual(contact_person, "Mimi WONG")
        self.assertEqual(contact_email, "pumimi@ust.hk")
        self.assertIn("pumimi@ust.hk", raw_enquiry)

    def test_document_list_is_empty_when_page_has_no_documents(self):
        """The open tenders' detail page has no doc-dl-btn, so nothing is returned."""
        with rendered_page(ENQUIRY_DETAIL_HTML) as page:
            self.assertEqual(TenderParser(page)._parse_document_list(), [])


if __name__ == "__main__":
    unittest.main()
