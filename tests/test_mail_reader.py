"""Unit tests for mailbox reading, tender number parsing, and Gmail tagging."""

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from email.message import EmailMessage

import config
from mailer.google_account import GoogleAccountMailer
from mailer.email_parser import (
    TenderReceivedEmail,
    extract_tender_no_from_text,
    extract_tender_name_from_text,
    extract_tender_refs_from_subject,
    normalize_tender_ref,
    decode_mime_words,
    parse_gmail_labels,
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

    def test_extract_tender_no_from_text(self):
        # Case 1: Standard tender number in subject
        subject1 = "Request for Tender Documents – PU/2026/001 - Supply of IT Equipment"
        self.assertEqual(extract_tender_no_from_text(subject1), "PU/2026/001")

        # Case 2: HKUST TN code in template subject
        subject2 = "Request for Tender Documents – TNL2600072 - Supply and Installation of Slide"
        self.assertEqual(extract_tender_no_from_text(subject2), "TNL2600072")

        # Case 3: HKUST TN code with PQ
        subject3 = "Request for Tender Documents – TNA2600095 PQ - Express of Interest and Pre-Te"
        self.assertEqual(extract_tender_no_from_text(subject3), "TNA2600095 PQ")

        # Case 4: Reply with tender number
        subject4 = "Re: Tender No.: EO/2026/012 - Provision of Electrical & Renovation Works"
        self.assertEqual(extract_tender_no_from_text(subject4), "EO/2026/012")

        # Case 5: Tender ref variation
        subject5 = "Enquiry regarding Tender Ref: FO/2025/102"
        self.assertEqual(extract_tender_no_from_text(subject5), "FO/2025/102")

        # Case 6: Match against known tender list
        known = ["CUSTOM-TENDER-99", "PU/2026/001"]
        text6 = "Questions about custom-tender-99 from contractor"
        self.assertEqual(extract_tender_no_from_text(text6, known_tenders=known), "CUSTOM-TENDER-99")

        # Case 7: Irrelevant email
        subject7 = "Lunch meeting tomorrow at HKUST cafeteria"
        self.assertIsNone(extract_tender_no_from_text(subject7))

    def test_extract_tender_name_from_text(self):
        # Case 1: Standard template with tender name
        subject1 = "Request for Tender Documents – TNA2600090 - Automated Laboratory Liquid Handling and Sampling System"
        self.assertEqual(
            extract_tender_name_from_text(subject1),
            "Automated Laboratory Liquid Handling and Sampling System",
        )

        # Case 2: Reply with tender name
        subject2 = "Re: Request for Tender Documents – PU/2026/001 - Supply of Office Laptops"
        self.assertEqual(
            extract_tender_name_from_text(subject2),
            "Supply of Office Laptops",
        )

        # Case 3: Slashed tender with name
        subject3 = "PU/2026/001 - Supply of Scientific Equipment"
        self.assertEqual(
            extract_tender_name_from_text(subject3),
            "Supply of Scientific Equipment",
        )

        # Case 4: No name present
        subject4 = "General notification without tender details"
        self.assertIsNone(extract_tender_name_from_text(subject4))

    def test_decode_mime_words(self):
        # Plain string
        self.assertEqual(decode_mime_words("Simple Subject"), "Simple Subject")
        self.assertEqual(decode_mime_words(""), "")
        self.assertEqual(decode_mime_words(None), "")

        # MIME encoded-word (UTF-8)
        encoded = "=?UTF-8?B?VEVTVCAtIFBVLzIwMjYvMDAx?="  # "TEST - PU/2026/001" in base64
        self.assertEqual(decode_mime_words(encoded), "TEST - PU/2026/001")

    def test_parse_gmail_labels(self):
        raw = '123 (UID 456 X-GM-LABELS ("\\\\Important" "HKUST-Tenders" "PU-2026-001") RFC822 {100}'
        labels = parse_gmail_labels(raw)
        self.assertIn("\\\\Important", labels)
        self.assertIn("HKUST-Tenders", labels)
        self.assertIn("PU-2026-001", labels)

        # Unquoted system label
        raw2 = "12 (UID 34 X-GM-LABELS (\\Inbox \\Sent))"
        labels2 = parse_gmail_labels(raw2)
        self.assertEqual(labels2, ["\\Inbox", "\\Sent"])

        # Empty
        raw3 = "12 (UID 34 X-GM-LABELS ())"
        self.assertEqual(parse_gmail_labels(raw3), [])

    @patch("imaplib.IMAP4_SSL")
    def test_list_mailboxes(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "[Gmail]/Drafts"',
                b'(\\HasNoChildren) "/" "HKUST-Tenders"',
            ],
        )

        mailer = GoogleAccountMailer(username="user@test.com", app_password="password123")
        mailboxes = mailer.list_mailboxes()

        self.assertIn("INBOX", mailboxes)
        self.assertIn("[Gmail]/Drafts", mailboxes)
        self.assertIn("HKUST-Tenders", mailboxes)

    @patch("imaplib.IMAP4_SSL")
    def test_read_emails(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap

        # Select & Search returns 1 UID
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.uid.side_effect = lambda cmd, *args: self._mock_imap_uid(cmd, *args)

        mailer = GoogleAccountMailer(username="user@test.com", app_password="password123")

        emails = mailer.read_emails(folder="INBOX", limit=5)
        self.assertEqual(len(emails), 1)
        email_item = emails[0]
        self.assertEqual(email_item.uid, "101")
        self.assertEqual(email_item.tender_no, "PU/2026/001")
        self.assertIn("pur@ust.hk", email_item.sender)

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

    def _mock_imap_uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"101"])
        elif cmd == "FETCH":
            # Build sample message
            msg = EmailMessage()
            msg["Subject"] = "Re: Request for Tender Documents – PU/2026/001"
            msg["From"] = "HKUST Purchasing <pur@ust.hk>"
            msg["Date"] = "Mon, 28 Sep 2026 15:30:00 +0800"
            msg.set_content("Dear Sir, please find the documents attached.")
            meta_header = b'1 (UID 101 X-GM-LABELS ("\\\\Inbox") RFC822 {150}'
            return ("OK", [(meta_header, msg.as_bytes())])
        elif cmd == "STORE":
            return ("OK", [b"OK"])
        return ("OK", [])


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

    def test_keyword_pattern_does_not_capture_word_tails(self):
        """Regression: these used to return "tice", "tification" and "erence"."""
        self.assertIsNone(extract_tender_no_from_text("Tender Notice"))
        self.assertIsNone(extract_tender_no_from_text("Tender Notification"))
        self.assertIsNone(extract_tender_no_from_text("Tender Reference"))

    def test_keyword_pattern_still_reads_a_real_reference(self):
        self.assertEqual(
            extract_tender_no_from_text("Tender Reference: CC-2025-001"), "CC-2025-001"
        )
        self.assertEqual(extract_tender_no_from_text("Tender No.: EO/2026/012"), "EO/2026/012")


if __name__ == "__main__":
    unittest.main()
