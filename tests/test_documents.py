"""Unit tests for tender document download helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
from scraper.HKUST.documents import download_tender_documents
from scraper.paths import safe_dir_name, tender_dir_name
from scraper.models import TenderNotice


class TestSafeDirName(unittest.TestCase):

    def test_project_name_preserves_spaces(self):
        self.assertEqual(
            safe_dir_name("Expression of Interest on TSMC MPW Chip Fabrication Service"),
            "Expression of Interest on TSMC MPW Chip Fabrication Service",
        )

    def test_slashes_are_replaced(self):
        self.assertEqual(
            safe_dir_name("Term contract for Notebook Supply 2026/2027"),
            "Term contract for Notebook Supply 2026_2027",
        )

    def test_path_separators_cannot_escape_the_destination(self):
        """A hostile name must not resolve outside the documents directory."""
        self.assertNotIn("/", safe_dir_name("../../etc/passwd"))
        self.assertNotIn("..", safe_dir_name("../../etc/passwd"))
        self.assertNotIn("..", safe_dir_name(".."))

    def test_empty_input_falls_back_to_a_usable_name(self):
        self.assertEqual(safe_dir_name("   "), "tender")

    def test_tender_dir_name_uses_project_description(self):
        tender = TenderNotice(
            tender_no="TNA2600106 EOI",
            description="Expression of Interest on TSMC MPW Chip Fabrication Service",
            closing_date="29-Oct-2026",
            contact_person="Sir/Madam",
            contact_email="",
            invited=True,
        )
        self.assertEqual(
            tender_dir_name(tender),
            "Expression of Interest on TSMC MPW Chip Fabrication Service",
        )

    def test_hkust_downloads_default_under_document_dir_hkust_project_subfolder(self):
        tender = TenderNotice(
            tender_no="TNA2600106 EOI",
            description="Expression of Interest on TSMC MPW Chip Fabrication Service",
            closing_date="29-Oct-2026",
            contact_person="Sir/Madam",
            contact_email="",
            invited=True,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_dir = Path(tmpdir)
            with patch.object(config, "DOCUMENT_DIR", doc_dir), patch(
                "scraper.HKUST.documents._download_one",
                side_effect=lambda _page, t, root: [
                    root / tender_dir_name(t) / "doc.pdf"
                ],
            ) as dl_one:
                saved = download_tender_documents(MagicMock(), [tender])

            dl_one.assert_called_once()
            self.assertEqual(dl_one.call_args.args[2], doc_dir / "HKUST")
            self.assertEqual(
                saved,
                [
                    doc_dir
                    / "HKUST"
                    / "Expression of Interest on TSMC MPW Chip Fabrication Service"
                    / "doc.pdf"
                ],
            )


if __name__ == "__main__":
    unittest.main()
