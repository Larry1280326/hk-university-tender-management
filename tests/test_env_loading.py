"""Unit test verifying that environment variables from .env are correctly loaded and applied."""

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values, load_dotenv


class TestEnvLoading(unittest.TestCase):
    """Verifies that config and email generation faithfully load all values from .env."""

    def test_current_env_file_loaded(self):
        """Verify that the actual local .env file is read and config variables are populated."""
        import config

        # The local .env should have populated these values (not empty fallback strings)
        self.assertTrue(hasattr(config, "COMPANY_NAME"))
        self.assertTrue(hasattr(config, "CONTACT_PERSON_NAME"))
        self.assertTrue(hasattr(config, "CONTACT_EMAIL"))
        self.assertTrue(hasattr(config, "CONTACT_PHONE"))
        self.assertTrue(hasattr(config, "GMAIL_DRAFTS_URL"))

        # Verify values match whatever is inside .env
        env_path = config.BASE_DIR / ".env"
        if env_path.exists():
            env_data = dotenv_values(env_path)
            if "COMPANY_NAME" in env_data:
                self.assertEqual(config.COMPANY_NAME, env_data["COMPANY_NAME"])
            if "CONTACT_EMAIL" in env_data:
                self.assertEqual(config.CONTACT_EMAIL, env_data["CONTACT_EMAIL"])
            if "CONTACT_PERSON_NAME" in env_data:
                self.assertEqual(config.CONTACT_PERSON_NAME, env_data["CONTACT_PERSON_NAME"])
            if "CONTACT_PHONE" in env_data:
                self.assertEqual(config.CONTACT_PHONE, env_data["CONTACT_PHONE"])

    def test_dynamic_env_file_simulation(self):
        """Simulate a brand new .env file with custom company details and verify full reload."""
        custom_env_content = (
            "COMPANY_NAME=Dynamic Testing Global Ltd\n"
            "CONTACT_PERSON_NAME=Dr. Alan Turing, Head of Engineering\n"
            "CONTACT_EMAIL=alan.turing@dynamic-testing.com\n"
            "CONTACT_PHONE=85288889999\n"
            "COMPANY_ADDRESS=Tower B, Cyberport 4, Hong Kong\n"
            "GMAIL_USER=alan.imap@gmail.com\n"
            "DOCUMENT_DIR=custom_docs/tenders\n"
            'GMAIL_DRAFTS_URL=https://mail.google.com/mail/u/5/#drafts\n'
            'MAIL_TAG=Custom-HKUST-Tag\n'
            'SIGN_OFF="Warmly,\\nAlan Turing\\nDynamic Testing Global Ltd"\n'
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_env_path = Path(tmpdir) / ".env"
            temp_env_path.write_text(custom_env_content, encoding="utf-8")

            # Load the temporary .env with override=True into os.environ
            load_dotenv(temp_env_path, override=True)

            # Reload config module to simulate starting up with this .env
            import config
            importlib.reload(config)

            # 1. Check config attributes
            self.assertEqual(config.COMPANY_NAME, "Dynamic Testing Global Ltd")
            self.assertEqual(config.CONTACT_PERSON_NAME, "Dr. Alan Turing, Head of Engineering")
            self.assertEqual(config.CONTACT_EMAIL, "alan.turing@dynamic-testing.com")
            self.assertEqual(config.CONTACT_PHONE, "85288889999")
            self.assertEqual(config.COMPANY_ADDRESS, "Tower B, Cyberport 4, Hong Kong")
            self.assertEqual(config.GMAIL_USER, "alan.imap@gmail.com")
            self.assertEqual(config.DOCUMENT_DIR, config.BASE_DIR / "custom_docs" / "tenders")
            self.assertEqual(config.DOCUMENTS_DIR, config.BASE_DIR / "custom_docs" / "tenders")
            self.assertEqual(config.GMAIL_DRAFTS_URL, "https://mail.google.com/mail/u/5/#drafts")
            self.assertEqual(config.MAIL_TAG, "Custom-HKUST-Tag")

            from mailer.google_account import GoogleAccountMailer
            self.assertEqual(GoogleAccountMailer().username, "alan.imap@gmail.com")

            # 2. Check that the email template rendering uses these dynamic values
            from mailer.template import generate_tender_email
            draft = generate_tender_email(
                tender_no="TENDER-ENV-999",
                description="Quantum Server Deployment",
                recipient_name="Prof. David Chen",
                recipient_email="davidchen@ust.hk",
            )

            self.assertIn("Dynamic Testing Global Ltd", draft.body)
            self.assertIn("Dr. Alan Turing, Head of Engineering", draft.body)
            self.assertIn("alan.turing@dynamic-testing.com", draft.body)
            self.assertIn("85288889999", draft.body)
            self.assertIn("Tower B, Cyberport 4, Hong Kong", draft.body)
            self.assertIn("Warmly,\nAlan Turing\nDynamic Testing Global Ltd", draft.body)

            # Clean up and reload actual project .env
            os.environ.pop("DOCUMENT_DIR", None)
            os.environ.pop("MAIL_TAG", None)
            load_dotenv(config.BASE_DIR / ".env", override=True)
            importlib.reload(config)


class TestConfiguredPaths(unittest.TestCase):
    """A relative path in .env must mean "under the project root", not "under $PWD".

    Regression: BR_CERTIFICATE_PATH=assets/br.pdf was read against the current
    directory, so the certificate attached when the tool was launched from the project
    root and was silently dropped when it was launched from anywhere else.
    """

    def test_relative_path_resolves_under_the_project_root(self):
        import config

        self.assertEqual(
            config._project_path("assets/br.pdf"), config.BASE_DIR / "assets" / "br.pdf"
        )

    def test_absolute_path_is_left_alone(self):
        import config

        self.assertEqual(config._project_path("/tmp/br.pdf"), Path("/tmp/br.pdf"))

    def test_the_home_shortcut_is_expanded(self):
        import config

        self.assertEqual(config._project_path("~/br.pdf"), Path.home() / "br.pdf")

    def test_the_br_certificate_path_is_always_absolute(self):
        """Absolute means a launch directory can no longer change what gets attached."""
        import config

        self.assertTrue(config.BR_CERTIFICATE_PATH.is_absolute())


if __name__ == "__main__":
    unittest.main()
