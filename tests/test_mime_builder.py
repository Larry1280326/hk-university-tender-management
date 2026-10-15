"""Unit tests for the unified MIME message builder, the IMAP session manager, and the connection test."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
from mailer.mime import build_mime_message
from mailer.template import TenderEmailDraft
from mailer.google_account import GoogleAccountMailer

# Gmail localizes system folder names per account language. This is the name the account
# this bug was reported against really reports -- not "[Gmail]/Drafts".
_LOCALIZED_DRAFTS = b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/&g0l6Pw-"'


class TestMimeBuilderAndSession(unittest.TestCase):

    def test_build_mime_message_basic(self):
        draft = TenderEmailDraft(
            tender_no="TEST-MIME-01",
            description="Supply of Office Laptops",
            recipient_name="Mr. Smith",
            recipient_email="smith@ust.hk",
            subject="Request for Tender Documents – TEST-MIME-01",
            body="Dear Mr. Smith,\n\nPlease send tender documents.",
        )

        msg = build_mime_message(draft, from_email="sender@example.com")
        self.assertEqual(msg["From"], "sender@example.com")
        self.assertEqual(msg["To"], "smith@ust.hk")
        self.assertEqual(msg["Subject"], "Request for Tender Documents – TEST-MIME-01")
        self.assertIn("Please send tender documents.", msg.get_content())

    def test_build_mime_message_with_attachment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_pdf = Path(tmpdir) / "sample_br.pdf"
            sample_pdf.write_bytes(b"%PDF-1.4 test attachment content")

            draft = TenderEmailDraft(
                tender_no="TEST-MIME-02",
                description="Testing attachment",
                recipient_name="Ms. Wong",
                recipient_email="wong@ust.hk",
                subject="Request for Tender Documents – TEST-MIME-02",
                body="Here is the BR certificate.",
                attachment_path=str(sample_pdf),
            )

            msg = build_mime_message(draft)
            attachments = list(msg.iter_attachments())
            self.assertEqual(len(attachments), 1)
            att = attachments[0]
            self.assertEqual(att.get_filename(), "sample_br.pdf")
            self.assertEqual(att.get_content_type(), "application/pdf")
            self.assertEqual(att.get_content(), b"%PDF-1.4 test attachment content")

    def test_a_promised_attachment_that_is_missing_is_refused(self):
        """The body says the file is enclosed, so a silent drop sends a false promise.

        Regression: this used to build the message with no attachment and no warning,
        which is how a wrong BR_CERTIFICATE_PATH reached a real Gmail draft unnoticed.
        """
        draft = TenderEmailDraft(
            tender_no="TEST-MIME-03",
            description="Testing missing attachment",
            recipient_name="Ms. Wong",
            recipient_email="wong@ust.hk",
            subject="Subject",
            body="Body",
            attachment_path="non_existent_file.pdf",
        )

        with self.assertRaises(FileNotFoundError) as caught:
            build_mime_message(draft)

        self.assertIn("non_existent_file.pdf", str(caught.exception))

    def test_a_draft_that_asks_for_no_attachment_still_builds(self):
        """Opting out stays possible; only a promised-but-missing file is an error."""
        draft = TenderEmailDraft(
            tender_no="TEST-MIME-04",
            description="No attachment asked for",
            recipient_name="Ms. Wong",
            recipient_email="wong@ust.hk",
            subject="Subject",
            body="Body",
        )

        self.assertEqual(list(build_mime_message(draft).iter_attachments()), [])

    @patch("imaplib.IMAP4_SSL")
    def test_imap_session_context_manager(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap

        mailer = GoogleAccountMailer(username="user@test.com", app_password="password123")

        with mailer.imap_session() as mail:
            self.assertEqual(mail, mock_imap)
            mock_imap.login.assert_called_once_with("user@test.com", "password123")

        mock_imap.logout.assert_called_once()

    @patch("imaplib.IMAP4_SSL")
    def test_imap_session_refuses_to_connect_without_credentials(self, mock_imap_cls):
        """A missing App Password must fail loudly rather than attempt an anonymous login."""
        with patch.object(config, "CONTACT_EMAIL", ""), patch.object(
            config, "GMAIL_APP_PASSWORD", ""
        ):
            mailer = GoogleAccountMailer()
            self.assertFalse(mailer.is_configured())

            with self.assertRaises(ValueError):
                with mailer.imap_session():
                    pass

        mock_imap_cls.assert_not_called()

    @patch("imaplib.IMAP4_SSL")
    def test_imap_session_exception_still_logs_out(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap

        mailer = GoogleAccountMailer(username="user@test.com", app_password="password123")

        with self.assertRaises(RuntimeError):
            with mailer.imap_session() as mail:
                raise RuntimeError("Unexpected failure during IMAP operation")

        mock_imap.logout.assert_called_once()

    @patch("imaplib.IMAP4_SSL")
    def test_save_draft_with_automatic_tagging(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap

        # Mock list() to return standard drafts folder
        mock_imap.list.return_value = ("OK", [b'(\\Drafts) "/" "[Gmail]/Drafts"'])
        # Mock append() returning RFC 4315 APPENDUID response
        mock_imap.append.return_value = ("OK", [b"[APPENDUID 12345 777] (Success)"])
        mock_imap.uid.return_value = ("OK", [b"OK"])

        mailer = GoogleAccountMailer(username="user@test.com", app_password="password123")

        # Verify draft creation applies base HKUST Tenders tag
        draft = TenderEmailDraft(
            tender_no="TNA2600090",
            description="Automated Laboratory Liquid Handling and Sampling System",
            recipient_name="Officer",
            recipient_email="tender@ust.hk",
            subject="Request for Tender Documents – TNA2600090 - Automated Laboratory Liquid Handling and Sampling System",
            body="Draft body here",
        )

        success, applied_tags = mailer.save_draft(draft, apply_tags=True, tag_prefix="HKUST Tenders")
        self.assertTrue(success)
        self.assertEqual(applied_tags, ["HKUST Tenders"])

        # Verify IMAP append was called
        mock_imap.append.assert_called_once()
        # Verify STORE was called to attach label to uid 777
        calls = [c[0] for c in mock_imap.uid.call_args_list]
        self.assertTrue(any(c[0] == "STORE" and c[1] == "777" for c in calls))


class TestConnection(unittest.TestCase):
    """``test_connection`` backs the "Test Google Account Connection" menu entry.

    Its ``(bool, str)`` pair is what the user reads, so both halves are asserted here:
    a failure must say why, and a success must name the folder it actually resolved.
    """

    def test_missing_credentials_report_a_configuration_problem(self):
        with patch.object(config, "CONTACT_EMAIL", ""), patch.object(
            config, "GMAIL_APP_PASSWORD", ""
        ):
            success, message = GoogleAccountMailer().test_connection()

        self.assertFalse(success)
        self.assertIn("GMAIL_USER", message)
        self.assertIn("GMAIL_APP_PASSWORD", message)

    @patch("imaplib.IMAP4_SSL")
    def test_success_names_the_drafts_folder_it_resolved(self, mock_imap_cls):
        """The folder must be the localized one Gmail reports, not an assumed "[Gmail]/Drafts"."""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.list.return_value = ("OK", [_LOCALIZED_DRAFTS])

        mailer = GoogleAccountMailer(username="user@test.com", app_password="password123")
        success, message = mailer.test_connection()

        self.assertTrue(success)
        self.assertIn("[Gmail]/&g0l6Pw-", message)
        mock_imap.login.assert_called_once_with("user@test.com", "password123")

    @patch("imaplib.IMAP4_SSL")
    def test_login_failure_is_reported_not_raised(self, mock_imap_cls):
        """The menu path prints this message, so a bad password must not crash the app."""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.login.side_effect = RuntimeError("Invalid credentials")

        mailer = GoogleAccountMailer(username="user@test.com", app_password="wrong")
        success, message = mailer.test_connection()

        self.assertFalse(success)
        self.assertIn("Invalid credentials", message)

    @patch("imaplib.IMAP4_SSL")
    def test_connection_is_closed_even_after_a_failure(self, mock_imap_cls):
        """A failed probe must not leave the IMAP connection open on the account."""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.list.side_effect = RuntimeError("server closed the connection")

        success, _ = GoogleAccountMailer(
            username="user@test.com", app_password="password123"
        ).test_connection()

        self.assertFalse(success)
        mock_imap.logout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
