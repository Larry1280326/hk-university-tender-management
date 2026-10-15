"""Tests for the select -> download flow driven from app.py.

Only the invitation table's rows carry documents, so the download prompt must fire for
those and never for open tenders.
"""

import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
from scraper.tender_parser import TenderDocument, TenderNotice


def invited_tender():
    return TenderNotice(
        tender_no="TNA2600106 EOI",
        description="Expression of Interest on TSMC MPW Chip Fabrication Service",
        closing_date="29-Oct-2026 15:00:00",
        contact_person="Sir/Madam",
        contact_email="",
        invited=True,
        documents=[
            TenderDocument(
                doc_type="Invitation",
                file_name="TNA2600106EOI_I01.pdf",
                subject="Invitation",
                updated_on="15-Oct-2026 10:20:37",
                size="415.6 KB",
            )
        ],
    )


def open_tender():
    return TenderNotice(
        tender_no="TNK2600096",
        description="IBM Maximo Application Suite",
        closing_date="22-Oct-2026 15:00:00",
        contact_person="Mimi WONG",
        contact_email="pumimi@ust.hk",
    )


@contextmanager
def fake_portal_session(page):
    """Stand in for portal_session, yielding ``(page, parser)`` like the real one."""
    with patch.object(app, "portal_session") as session:
        session.return_value.__enter__.return_value = (page, MagicMock())
        yield session


class TestOfferDocumentDownload(unittest.TestCase):

    def test_confirmed_prompt_downloads_and_records_the_tender(self):
        page = MagicMock()
        saved_file = Path("/tmp/TNA2600106_EOI/TNA2600106EOI_I01.pdf")
        downloaded = set()

        with patch.object(app, "questionary") as q, patch.object(
            app, "download_tender_documents", return_value=[saved_file]
        ) as download, fake_portal_session(page):
            q.confirm.return_value.ask.return_value = True

            app._offer_document_download([invited_tender()], downloaded, headless=True)

        q.confirm.assert_called_once()
        self.assertEqual(download.call_args.args[0], page)
        self.assertIn("TNA2600106 EOI", downloaded)

    def test_declining_the_prompt_opens_no_browser(self):
        with patch.object(app, "questionary") as q, patch.object(
            app, "download_tender_documents"
        ) as download, fake_portal_session(MagicMock()) as session:
            q.confirm.return_value.ask.return_value = False

            app._offer_document_download([invited_tender()], set(), headless=True)

        download.assert_not_called()
        session.assert_not_called()

    def test_already_downloaded_tender_is_not_prompted_again(self):
        """Choosing "Back to Selection" must not re-download or re-open a browser."""
        with patch.object(app, "questionary") as q, patch.object(
            app, "download_tender_documents"
        ) as download, fake_portal_session(MagicMock()) as session:
            app._offer_document_download([invited_tender()], {"TNA2600106 EOI"}, headless=True)

        q.confirm.assert_not_called()
        download.assert_not_called()
        session.assert_not_called()

    def test_session_failure_does_not_abort_the_run(self):
        """A download that fails must not take the drafting flow down with it."""
        with patch.object(app, "questionary") as q, fake_portal_session(MagicMock()):
            q.confirm.return_value.ask.return_value = True
            app.portal_session.side_effect = RuntimeError("login failed")

            app._offer_document_download([invited_tender()], set(), headless=True)


class TestSelectionFlow(unittest.TestCase):

    def test_open_tender_selection_never_prompts_for_download(self):
        with patch.object(app, "select_suitable_tenders", return_value=[open_tender()]), patch.object(
            app, "_offer_document_download"
        ) as offer, patch.object(app, "display_draft_preview"), patch.object(app, "questionary") as q:
            q.select.return_value.ask.return_value = "3. Cancel / Exit"

            app.handle_tender_selection_and_drafting([open_tender()])

        offer.assert_not_called()

    def test_invited_selection_prompts_for_download(self):
        with patch.object(
            app, "select_suitable_tenders", return_value=[invited_tender()]
        ), patch.object(app, "_offer_document_download") as offer:
            app.handle_tender_selection_and_drafting([invited_tender()])

        # Only an invited tender was selected, so there is nothing to draft an email for.
        offer.assert_called_once()


class TestChoiceLabels(unittest.TestCase):

    def test_invited_label_shows_document_count(self):
        self.assertIn("1 document to download", app._tender_choice_title(invited_tender()))

    def test_open_label_shows_contact(self):
        self.assertIn("Mimi WONG <pumimi@ust.hk>", app._tender_choice_title(open_tender()))

    def test_open_label_omits_the_default_placeholder_contact(self):
        """A tender whose enquiry was not parsed must not claim "Sir/Madam" as a contact."""
        tender = open_tender()
        tender.contact_person = "Sir/Madam"
        tender.contact_email = ""

        self.assertNotIn("Sir/Madam", app._tender_choice_title(tender))


if __name__ == "__main__":
    unittest.main()
