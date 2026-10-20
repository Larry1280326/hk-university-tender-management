"""End-to-end simulation of tender selection, email generation, and draft packaging."""

import unittest
import tempfile
from pathlib import Path

import config
from scraper.models import TenderNotice
from mailer.template import generate_tender_email
from mailer.webmail_helper import generate_gmail_compose_url, export_eml_file


class TestEndToEndMock(unittest.TestCase):

    def test_mock_tender_pipeline(self):
        sample_tenders = [
            TenderNotice(
                tender_no="PU/2026/089",
                description="Supply and Installation of Smart Facility Monitoring System",
                closing_date="2026-10-15 12:00",
                contact_person="Ms. Sarah Wong",
                contact_email="sarahwong@ust.hk",
                contact_phone="2358-6123",
                tender_type="Information Technology",
            ),
            TenderNotice(
                tender_no="EO/2026/012",
                description="Provision of Electrical & Renovation Works at Academic Building",
                closing_date="2026-10-22 12:00",
                contact_person="Mr. David Chan",
                contact_email="davidchan@ust.hk",
                contact_phone="2358-7456",
                tender_type="Building & Maintenance",
            ),
        ]

        with unittest.mock.patch.multiple(
            config,
            COMPANY_NAME="Sample Enterprise Limited",
            CONTACT_PERSON_NAME="Alex Smith, Director",
            CONTACT_EMAIL="alex@sampleenterprise.com",
            CONTACT_PHONE="+852 9876 5432",
        ):
            with tempfile.TemporaryDirectory() as tmp_dir:
                out_dir = Path(tmp_dir)

                for t in sample_tenders:
                    draft = generate_tender_email(
                        tender_no=t.tender_no,
                        description=t.description,
                        recipient_name=t.contact_person,
                        recipient_email=t.contact_email,
                    )

                    self.assertEqual(
                        draft.subject,
                        f"Request for Tender Documents \u2013 {t.tender_no} - {t.description}",
                    )
                    self.assertIn(f"Dear {t.contact_person},", draft.body)
                    self.assertIn(f"Tender No.: {t.tender_no}- {t.description}", draft.body)
                    self.assertIn("Sample Enterprise Limited", draft.body)
                    self.assertIn("Alex Smith, Director", draft.body)
                    self.assertIn("alex@sampleenterprise.com", draft.body)
                    self.assertIn("+852 9876 5432", draft.body)
                    self.assertIn("Business Registration Certificate (BR)", draft.body)

                # EML export
                eml_file = export_eml_file(draft, out_dir)
                self.assertTrue(eml_file.exists())

                # Direct Gmail web compose link
                web_url = generate_gmail_compose_url(draft)
                self.assertIn("mail.google.com", web_url)


if __name__ == "__main__":
    unittest.main()
