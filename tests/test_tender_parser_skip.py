"""Unit tests for skipping already-processed tenders in TenderParser and CLI selection."""

import unittest
from unittest.mock import MagicMock

from scraper.HKUST.parser import OPEN_TABLE_SELECTOR, TenderParser
from mailer.email_parser import handled_tender_key, normalize_tender_ref
from tests.portal_fixtures import OPEN_TITLE, parser_on, rendered_page, tender_table


class TestTenderParserSkip(unittest.TestCase):

    def test_extract_tenders_skips_existing(self):
        """A tender already drafted in Gmail is skipped without opening its detail page."""
        html = tender_table(
            "openTenderList",
            OPEN_TITLE,
            [
                ("TNP2600099", "Fitting Out Works for Office", "2026-10-15"),
                ("TNK2600096", "IBM Maximo Application Suite", "2026-10-20"),
            ],
        )

        with rendered_page(html) as page, parser_on(page) as parser:
            parser._open_detail_page = MagicMock()
            parser._parse_enquiry_details = MagicMock(
                return_value=("Mimi WONG", "pumimi@ust.hk", "Raw info")
            )

            progress_calls = []
            tenders = parser.extract_tenders(
                progress_callback=lambda curr, total, name: progress_calls.append(name),
                existing_tenders={("hkust", "TNP2600099")},
            )

        # Only the new tender is returned; the already-drafted one is dropped entirely.
        self.assertEqual([t.tender_no for t in tenders], ["TNK2600096"])
        self.assertFalse(tenders[0].already_processed)
        self.assertEqual(tenders[0].contact_person, "Mimi WONG")
        self.assertEqual(tenders[0].contact_email, "pumimi@ust.hk")

        # Row 0 was skipped, so only row 1's detail page was ever opened.
        parser._open_detail_page.assert_called_once_with(OPEN_TABLE_SELECTOR, 1)

        # Progress callback should record the skip.
        self.assertTrue(any("Already in Gmail" in msg for msg in progress_calls))

        # Only the skipped row is reported, so the UI can say what was actually hidden.
        self.assertEqual(parser.skipped_handled_refs, ["TNP2600099"])

    def test_reference_handled_at_another_university_does_not_hide_it(self):
        """The hide key is (slug, ref): the same number at CUHK is a different tender."""
        html = tender_table(
            "openTenderList",
            OPEN_TITLE,
            [("TNP2600099", "Fitting Out Works for Office", "2026-10-15")],
        )

        with rendered_page(html) as page, parser_on(page) as parser:
            parser._open_detail_page = MagicMock()
            parser._parse_enquiry_details = MagicMock(
                return_value=("Mimi WONG", "pumimi@ust.hk", "Raw info")
            )

            tenders = parser.extract_tenders(
                existing_tenders={("cuhk", "TNP2600099")},
            )

        self.assertEqual([t.tender_no for t in tenders], ["TNP2600099"])

    def test_normalize_tender_ref_consistency(self):
        """Ensure normalization handles variations with slashes, hyphens, and whitespace."""
        self.assertEqual(
            normalize_tender_ref("TNP2600099"),
            normalize_tender_ref(" tnp-2600099 "),
        )
        self.assertEqual(
            normalize_tender_ref("PU/2026/001"),
            normalize_tender_ref("pu-2026-001"),
        )

    def test_workflow_marker_does_not_change_identity(self):
        """The portal writes "TNG2600120 EOI"; Gmail subjects carry "TNG2600120"."""
        self.assertEqual(
            normalize_tender_ref("TNG2600120 EOI"),
            normalize_tender_ref("TNG2600120"),
        )
        self.assertEqual(
            handled_tender_key("hkust", "TNG2600120 EOI"),
            handled_tender_key("hkust", "TNG2600120"),
        )


if __name__ == "__main__":
    unittest.main()
