"""Unit tests for HKUST automation mailer and parser logic."""

import unittest
from pathlib import Path
import tempfile
import config
from mailer.template import generate_tender_email
from mailer.webmail_helper import generate_gmail_compose_url, export_eml_file
from scraper.HKUST.parser import EMAIL_REGEX, PHONE_REGEX


class TestTenderWorkflow(unittest.TestCase):

    def test_email_template_generation(self):
        with unittest.mock.patch.multiple(
            config,
            COMPANY_NAME="Acme Global Limited",
            CONTACT_PERSON_NAME="Jane Doe, Manager",
            CONTACT_EMAIL="tender@acmeglobal.com",
            CONTACT_PHONE="+852 1234 5678",
            COMPANY_ADDRESS="123 Science Park Road, Shatin, N.T.",
        ):
            draft = generate_tender_email(
                tender_no="PU/2026/001",
                description="Supply of IT Equipment",
                recipient_name="Ms. Mary Lee",
                recipient_email="marylee@ust.hk",
            )

            self.assertEqual(
                draft.subject, "Request for Tender Documents – PU/2026/001 - Supply of IT Equipment"
            )
            self.assertEqual(draft.recipient_name, "Ms. Mary Lee")
            self.assertEqual(draft.recipient_email, "marylee@ust.hk")
            self.assertIn("Acme Global Limited", draft.body)
            self.assertIn("Jane Doe, Manager", draft.body)
            self.assertIn("tender@acmeglobal.com", draft.body)
            self.assertIn("+852 1234 5678", draft.body)
            self.assertIn("123 Science Park Road, Shatin, N.T.", draft.body)
            self.assertIn("Business Registration Certificate (BR)", draft.body)

    def test_webmail_helper_and_eml_export(self):
        draft = generate_tender_email(
            tender_no="TEST-002",
            description="Test Tender Description",
            recipient_name="Mr. Chan",
            recipient_email="chan@ust.hk",
        )

        compose_url = generate_gmail_compose_url(draft)
        self.assertIn("https://mail.google.com/mail/u/0/?view=cm", compose_url)
        self.assertIn("chan%40ust.hk", compose_url)

        with tempfile.TemporaryDirectory() as tmpdir:
            eml_path = export_eml_file(draft, Path(tmpdir))
            self.assertTrue(eml_path.exists())
            with open(eml_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                self.assertIn("To: chan@ust.hk", content)
                self.assertIn("Subject: Request for Tender Documents", content)

    def test_regex_matching(self):
        sample_text = (
            "Enquiry: Please contact Ms. Alice Wong at 2358-8888 or email: alicewong@ust.hk for details."
        )
        email_match = EMAIL_REGEX.search(sample_text)
        self.assertIsNotNone(email_match)
        self.assertEqual(email_match.group(0), "alicewong@ust.hk")

        phone_match = PHONE_REGEX.search(sample_text)
        self.assertIsNotNone(phone_match)
        self.assertEqual(phone_match.group(0), "2358-8888")


if __name__ == "__main__":
    unittest.main()
