"""Unit tests for the already-handled mailbox scan, tender reference parsing, and Gmail tagging."""

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from email.message import EmailMessage

import config
from mailer.google_account import GoogleAccountMailer
from mailer.email_parser import (
    extract_tender_refs_from_subject,
    normalize_tender_ref,
    decode_mime_words,
)

# Gmail localizes system folder names per account language. These are the names the
# account this bug was reported against really reports -- not "[Gmail]/Sent Mail".
_DRAFTS = "[Gmail]/&g0l6Pw-"
_SENT = "[Gmail]/&XfJT0ZCuTvY-"
_LOCALIZED_FOLDERS = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/&g0l6Pw-"',
    b'(\\Sent \\HasNoChildren) "/" "[Gmail]/&XfJT0ZCuTvY-"',
]


def scan_mock(subjects_by_folder, fail_select=()):
    """An imaplib.IMAP4_SSL stand-in whose folders hold the given subject lines."""
    mock_imap = MagicMock()
    state = {"folder": None, "criteria": [], "selected": []}

    mock_imap.login.return_value = ("OK", [b"Logged in"])
    mock_imap.list.return_value = ("OK", list(_LOCALIZED_FOLDERS))

    def select(mailbox, readonly=False):
        name = mailbox.strip('"')
        state["folder"] = name
        state["selected"].append(name)
        return ("NO", [b"cannot select"]) if name in fail_select else ("OK", [b"1"])

    mock_imap.select.side_effect = select

    def uid(command, *args):
        subjects = subjects_by_folder.get(state["folder"], [])
        if command == "SEARCH":
            state["criteria"].append(args[-1])
            return ("OK", [b" ".join(str(n + 1).encode() for n in range(len(subjects)))])
        if command == "FETCH":
            fetched = []
            for number, subject in enumerate(subjects, 1):
                msg = EmailMessage()
                msg["Subject"] = subject
                fetched.append((f"{number} (BODY[HEADER.FIELDS (SUBJECT)] {{0}}".encode(), msg.as_bytes()))
            return ("OK", fetched)
        return ("OK", [])

    mock_imap.uid.side_effect = uid
    return mock_imap, state




class TestMailReader(unittest.TestCase):

    def test_decode_mime_words(self):
        # Plain string
        self.assertEqual(decode_mime_words("Simple Subject"), "Simple Subject")
        self.assertEqual(decode_mime_words(""), "")
        self.assertEqual(decode_mime_words(None), "")

        # MIME encoded-word (UTF-8)
        encoded = "=?UTF-8?B?VEVTVCAtIFBVLzIwMjYvMDAx?="  # "TEST - PU/2026/001" in base64
        self.assertEqual(decode_mime_words(encoded), "TEST - PU/2026/001")

    def test_normalize_tender_ref(self):
        self.assertEqual(normalize_tender_ref("TNP2600099"), "TNP2600099")
        self.assertEqual(normalize_tender_ref("TNP-2600099"), "TNP2600099")
        self.assertEqual(normalize_tender_ref("tnp_2600099"), "TNP2600099")
        self.assertEqual(normalize_tender_ref("PU/2026/001"), "PU2026001")
        self.assertEqual(normalize_tender_ref("  EO / 2026 / 012  "), "EO2026012")
        self.assertEqual(normalize_tender_ref(""), "")
        self.assertEqual(normalize_tender_ref(None), "")

    @patch("imaplib.IMAP4_SSL")
    def test_handled_tenders_report_the_mailbox_they_were_found_in(self, mock_imap_cls):
        """A reply the user sent counts as handled even though nothing was ever labelled."""
        mock_imap, _ = scan_mock(
            {
                _SENT: ["Re: Request for Tender Documents – TNH2600105 - Window Leakage Repair"],
                _DRAFTS: [],
                "INBOX": ["Re : Request for Tender Documents – TNH2600105 - Window Leakage Repair"],
            }
        )
        mock_imap_cls.return_value = mock_imap

        handled = GoogleAccountMailer(
            username="test@gmail.com", app_password="password123"
        ).get_handled_tenders()

        self.assertEqual(handled, {"TNH2600105": ["Inbox", "Sent"]})

    @patch("imaplib.IMAP4_SSL")
    def test_localized_sent_folder_is_resolved_by_imap_flag(self, mock_imap_cls):
        """Sent is not named "Sent Mail" on this account, so the name must not be assumed."""
        mock_imap, state = scan_mock({_SENT: [], _DRAFTS: [], "INBOX": []})
        mock_imap_cls.return_value = mock_imap

        GoogleAccountMailer(
            username="test@gmail.com", app_password="password123"
        ).get_handled_tenders()

        self.assertIn(_SENT, state["selected"])
        self.assertNotIn("[Gmail]/Sent Mail", state["selected"])

    @patch("imaplib.IMAP4_SSL")
    def test_scan_is_bounded_by_the_lookback_window(self, mock_imap_cls):
        mock_imap, state = scan_mock({_SENT: [], _DRAFTS: [], "INBOX": []})
        mock_imap_cls.return_value = mock_imap

        GoogleAccountMailer(
            username="test@gmail.com", app_password="password123"
        ).get_handled_tenders()

        expected = (date.today() - timedelta(days=config.HKUST_MAIL_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        self.assertIn(f"(SINCE {expected})", state["criteria"])

    @patch("imaplib.IMAP4_SSL")
    def test_unreachable_mailbox_does_not_abort_the_scan(self, mock_imap_cls):
        """One mailbox that cannot be opened must not cost the others' results."""
        mock_imap, _ = scan_mock(
            {
                _SENT: ["Request for Tender Documents – TNP2600099 - Office Renovation"],
                _DRAFTS: [],
                "INBOX": [],
            },
            fail_select=(_DRAFTS,),
        )
        mock_imap_cls.return_value = mock_imap

        handled = GoogleAccountMailer(
            username="test@gmail.com", app_password="password123"
        ).get_handled_tenders()

        self.assertEqual(handled, {"TNP2600099": ["Sent"]})

class TestStrictTenderRefExtraction(unittest.TestCase):
    """The mailbox scan must only ever see tender codes, never keyword fragments."""

    def test_tender_codes_are_extracted(self):
        self.assertEqual(
            extract_tender_refs_from_subject(
                "Re : Request for Tender Documents – TNH2600105 - Window Leakage Repair"
            ),
            {"TNH2600105"},
        )
        self.assertEqual(
            extract_tender_refs_from_subject("Re: PU/2026/001 - Supply of Laptops"),
            {"PU/2026/001"},
        )

    def test_subjects_without_a_code_yield_nothing(self):
        for subject in (
            "Tender Notice",
            "Tender Notification",
            "Tender Reference",
            "Your HKUST account statement is ready",
            "Lunch meeting tomorrow at HKUST cafeteria",
        ):
            self.assertEqual(extract_tender_refs_from_subject(subject), set(), subject)

    def test_non_tender_project_references_are_rejected(self):
        """Regression: this mailbox's own works orders matched the slashed tender pattern.

        "YFI/4401/025-VO" is a works order for construction project 4401, not an HKUST
        tender. It only matched because the pattern accepted any four digits in the
        segment that means a year.
        """
        for subject in (
            "Residential Development at No. 125 Repulse Bay Road E&M and P&D Works YFI/4401/025-VO",
            "Residential Development at No. 125 Repulse Bay Road E&M and P&D Works YFI/4401/025-IP01",
            "J4402 Winfield Building - Works order YFI/4402/006 - VO",
            "Fwd: Project J4401 Repulse Bay - Works Order YFI/4401/025 (Chun King)",
        ):
            self.assertEqual(extract_tender_refs_from_subject(subject), set(), subject)

    def test_slashed_tender_references_still_match(self):
        self.assertEqual(
            extract_tender_refs_from_subject("Re: EO/2026/012 - Renovation Works"),
            {"EO/2026/012"},
        )
        self.assertEqual(
            extract_tender_refs_from_subject("Enquiry regarding Tender Ref: FO/2025/102"),
            {"FO/2025/102"},
        )

if __name__ == "__main__":
    unittest.main()
