"""Tests for the flows driven from app.py: the menu loop, the pre-scrape mailbox check,
tender selection, and select -> download.

Only the invitation table's rows carry documents, so the download prompt must fire for
those and never for open tenders.
"""

import contextlib
import io
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import app
import config
from scraper.models import TenderDocument, TenderNotice


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


class TestProcessedTenderNumbers(unittest.TestCase):
    """The pre-scrape check decides which tenders are HIDDEN from the scrape.

    Erring permissive offers a tender twice; erring strict hides one the user never acted
    on, which is the worse failure and the one these tests pin down.
    """

    def test_unconfigured_mailer_hides_nothing(self):
        """Without credentials the scrape must still list every tender, so do not even scan."""
        with patch.object(app, "GoogleAccountMailer") as mailer_cls:
            mailer_cls.return_value.is_configured.return_value = False

            result = app.get_processed_tender_numbers()

        self.assertEqual(result, set())
        mailer_cls.return_value.get_handled_tenders.assert_not_called()

    def test_returns_every_reference_the_scan_found(self):
        with patch.object(app, "GoogleAccountMailer") as mailer_cls:
            mailer_cls.return_value.is_configured.return_value = True
            mailer_cls.return_value.get_handled_tenders.return_value = {
                "TNH2600105": ["Inbox", "Sent"],
                "TNP2600099": ["Sent"],
            }

            result = app.get_processed_tender_numbers()

        self.assertEqual(
            result,
            {("hkust", "TNH2600105"), ("hkust", "TNP2600099")},
        )

    def test_empty_scan_hides_nothing(self):
        with patch.object(app, "GoogleAccountMailer") as mailer_cls:
            mailer_cls.return_value.is_configured.return_value = True
            mailer_cls.return_value.get_handled_tenders.return_value = {}

            result = app.get_processed_tender_numbers()

        self.assertEqual(result, set())

    def test_summary_reports_the_count_without_listing_individual_references(self):
        with patch.object(app, "GoogleAccountMailer") as mailer_cls:
            mailer_cls.return_value.is_configured.return_value = True
            mailer_cls.return_value.get_handled_tenders.return_value = {
                "TNH2600105": ["Inbox", "Sent"],
            }

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                app.get_processed_tender_numbers()

        output = buffer.getvalue()
        self.assertIn("1 tender reference(s) found in your mailboxes", output)
        self.assertNotIn("TNH2600105", output)
        self.assertNotIn("found in:", output)


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


class TestSelectSuitableTenders(unittest.TestCase):
    """Interrupting the checklist and confirming nothing must stay distinguishable.

    The caller says "Returning to the main menu" for one and "No tenders selected" for
    the other, so collapsing both into an empty list would lose the difference.
    """

    def test_an_interrupted_prompt_returns_none(self):
        with patch.object(app, "questionary") as q:
            q.checkbox.return_value.ask.return_value = None

            self.assertIsNone(app.select_suitable_tenders([open_tender()]))
            q.select.assert_not_called()

    def test_confirming_an_empty_checklist_returns_an_empty_list(self):
        with patch.object(app, "questionary") as q:
            q.checkbox.return_value.ask.return_value = []

            self.assertEqual(app.select_suitable_tenders([open_tender()]), [])
            q.select.assert_not_called()

    def test_returns_checked_tenders_directly_from_checklist(self):
        tender = open_tender()
        with patch.object(app, "questionary") as q:
            q.checkbox.return_value.ask.return_value = [tender]

            selected = app.select_suitable_tenders([tender])

        self.assertEqual([t.tender_no for t in selected], [tender.tender_no])
        q.select.assert_not_called()

    def test_nothing_to_choose_from_returns_none(self):
        with patch.object(app, "questionary"):
            self.assertIsNone(app.select_suitable_tenders([]))


class TestSelectionFlow(unittest.TestCase):

    def test_going_back_from_selection_names_the_destination(self):
        with patch.object(app, "select_suitable_tenders", return_value=None):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                app.handle_tender_selection_and_drafting([open_tender()])

        self.assertIn("Returning to the main menu", buffer.getvalue())

    def test_confirming_an_empty_checklist_says_nothing_was_selected(self):
        """Distinct from going back: here the user chose, and chose nothing."""
        with patch.object(app, "select_suitable_tenders", return_value=[]):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                app.handle_tender_selection_and_drafting([open_tender()])

        output = buffer.getvalue().lower()
        self.assertIn("no tenders selected", output)
        self.assertIn("returning to the main menu", output)

    def test_a_missing_br_certificate_stops_before_anything_is_drafted(self):
        """Creating nothing beats creating drafts that promise a missing attachment."""
        with patch.object(app, "select_suitable_tenders", return_value=[open_tender()]), patch.object(
            app, "questionary"
        ) as q, patch.object(app, "display_draft_preview") as preview, patch.object(
            config, "BR_CERTIFICATE_PATH", Path("/nonexistent/br.pdf")
        ):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                app.handle_tender_selection_and_drafting([open_tender()])

        self.assertIn("BR certificate not found", buffer.getvalue())
        preview.assert_not_called()
        q.select.assert_not_called()

    def test_going_back_from_the_draft_menu_names_the_destination(self):
        with patch.object(app, "select_suitable_tenders", return_value=[open_tender()]), patch.object(
            app, "questionary"
        ) as q:
            q.select.return_value.ask.return_value = "3. Back to Main Menu"

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                app.handle_tender_selection_and_drafting([open_tender()])

        self.assertIn("Returning to the main menu", buffer.getvalue())


    def test_open_tender_selection_never_prompts_for_download(self):
        with patch.object(app, "select_suitable_tenders", return_value=[open_tender()]), patch.object(
            app, "_offer_document_download"
        ) as offer, patch.object(app, "display_draft_preview"), patch.object(app, "questionary") as q:
            q.select.return_value.ask.return_value = "3. Back to Main Menu"

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

    def test_cuhk_open_label_indicates_eoi_submission_and_download(self):
        tender = open_tender()
        tender.tender_no = "T-20260422"
        tender.needs_eoi = True
        tender.contact_person = "Phyllis HM Leung"
        tender.contact_email = "pyhllis@cuhk.edu.hk"

        title = app._tender_choice_title(tender)
        self.assertIn("Submit EOI & download", title)
        self.assertIn("Phyllis HM Leung <pyhllis@cuhk.edu.hk>", title)


class TestMainMenuLoop(unittest.TestCase):
    """The menu holds the session open, so a cancelled run must not close the program."""

    def test_exit_closes_the_menu(self):
        with patch.object(app, "questionary") as q:
            q.select.return_value.ask.return_value = "5. Exit"

            self.assertFalse(app.interactive_menu())

    def test_interrupting_the_prompt_also_closes_the_menu(self):
        """questionary answers None on Ctrl-C, a request to leave rather than to loop."""
        with patch.object(app, "questionary") as q:
            q.select.return_value.ask.return_value = None

            self.assertFalse(app.interactive_menu())

    def test_running_a_workflow_leaves_the_menu_open(self):
        with patch.object(app, "questionary") as q, patch.object(
            app, "run_tender_workflow"
        ) as workflow:
            q.select.return_value.ask.return_value = "1. HKUST"

            self.assertTrue(app.interactive_menu())

        workflow.assert_called_once()

    def test_running_cuhk_workflow_leaves_the_menu_open(self):
        with patch.object(app, "questionary") as q, patch.object(
            app, "run_cuhk_workflow"
        ) as workflow:
            q.select.return_value.ask.return_value = "2. CUHK"

            self.assertTrue(app.interactive_menu())

        workflow.assert_called_once()

    def test_running_cityu_workflow_leaves_the_menu_open(self):
        with patch.object(app, "questionary") as q, patch.object(
            app, "run_cityu_workflow"
        ) as workflow:
            q.select.return_value.ask.return_value = "3. CityU"

            self.assertTrue(app.interactive_menu())

        workflow.assert_called_once()

    def test_testing_the_connection_leaves_the_menu_open(self):
        with patch.object(app, "questionary") as q, patch.object(app, "GoogleAccountMailer") as mailer:
            q.select.return_value.ask.return_value = "4. Test Google Account Connection"
            mailer.return_value.test_connection.return_value = (True, "ok")

            self.assertTrue(app.interactive_menu())

        mailer.return_value.test_connection.assert_called_once()

    def test_main_reshows_the_menu_until_exit(self):
        """Cancelling a scrape must land back on the menu, not drop the user to the shell."""
        answers = iter([
            "1. HKUST",
            "2. CUHK",
            "3. CityU",
            "5. Exit",
        ])
        with patch("sys.argv", ["app.py"]), patch.object(
            app, "display_welcome_banner"
        ), patch.object(app, "questionary") as q, patch.object(
            app, "run_tender_workflow"
        ) as hkust_workflow, patch.object(
            app, "run_cuhk_workflow"
        ) as cuhk_workflow, patch.object(
            app, "run_cityu_workflow"
        ) as cityu_workflow:
            q.select.return_value.ask.side_effect = lambda: next(answers)

            app.main()

        self.assertEqual(hkust_workflow.call_count, 1)
        self.assertEqual(cuhk_workflow.call_count, 1)
        self.assertEqual(cityu_workflow.call_count, 1)


if __name__ == "__main__":
    unittest.main()
