"""Unit tests for mailbox reading, tender number parsing, and Gmail tagging."""

import unittest
from unittest.mock import MagicMock, patch
from email.message import EmailMessage

from mailer.google_account import GoogleAccountMailer
from mailer.email_parser import (
    TenderReceivedEmail,
    extract_tender_no_from_text,
    extract_tender_name_from_text,
    extract_all_tender_refs,
    normalize_tender_ref,
    decode_mime_words,
    parse_gmail_labels,
)




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

    def test_extract_all_tender_refs(self):
        # One subject quoting two references: both are collected, where
        # extract_tender_no_from_text would only ever return the first.
        subject = (
            "Re: Request for Tender Documents – TNL2600072 - Supply of Chairs, "
            "also see TNA2600095 PQ"
        )
        refs = extract_all_tender_refs(subject)
        self.assertEqual(refs, {"TNL2600072", "TNA2600095 PQ"})
        # The first match is always part of the set
        self.assertIn(extract_tender_no_from_text(subject), refs)

        # Slashed HKUST format alongside a TN code
        mixed = "Tender No.: EO/2026/012 and Request for Tender Documents – TNL2600072 - Works"
        self.assertEqual(extract_all_tender_refs(mixed), {"TNL2600072", "EO/2026/012"})

        # Nothing to find
        self.assertEqual(extract_all_tender_refs("Lunch meeting tomorrow"), set())
        self.assertEqual(extract_all_tender_refs(""), set())

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
    def test_read_emails_and_tagging(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap

        # Select & Search returns 1 UID
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.uid.side_effect = lambda cmd, *args: self._mock_imap_uid(cmd, *args)

        mailer = GoogleAccountMailer(username="user@test.com", app_password="password123")

        # 1. Test read_emails
        emails = mailer.read_emails(folder="INBOX", limit=5)
        self.assertEqual(len(emails), 1)
        email_item = emails[0]
        self.assertEqual(email_item.uid, "101")
        self.assertEqual(email_item.tender_no, "PU/2026/001")
        self.assertIn("pur@ust.hk", email_item.sender)

        # 2. Test tag_hkust_tender_emails
        tagged = mailer.tag_hkust_tender_emails(
            emails=[email_item],
            tag_prefix="HKUST-Tenders",
        )
        self.assertEqual(len(tagged), 1)
        self.assertEqual(tagged[0]["tender_no"], "PU/2026/001")
        # Should apply base tag only
        self.assertEqual(tagged[0]["applied_tags"], ["HKUST-Tenders"])

    def test_normalize_tender_ref(self):
        self.assertEqual(normalize_tender_ref("TNP2600099"), "TNP2600099")
        self.assertEqual(normalize_tender_ref("TNP-2600099"), "TNP2600099")
        self.assertEqual(normalize_tender_ref("tnp_2600099"), "TNP2600099")
        self.assertEqual(normalize_tender_ref("PU/2026/001"), "PU2026001")
        self.assertEqual(normalize_tender_ref("  EO / 2026 / 012  "), "EO2026012")
        self.assertEqual(normalize_tender_ref(""), "")
        self.assertEqual(normalize_tender_ref(None), "")

    @patch("imaplib.IMAP4_SSL")
    def test_get_existing_tender_numbers(self, mock_imap_cls):
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.login.return_value = ("OK", [b"Logged in"])
        mock_imap.select.return_value = ("OK", [b"2"])
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "HKUST Tenders"'])

        # 2 messages with different tender subjects
        msg1 = EmailMessage()
        msg1["Subject"] = "Request for Tender Documents – TNP2600099 - Office Renovation"
        msg2 = EmailMessage()
        msg2["Subject"] = "Re: Request for Tender Documents – PU/2026/001"

        def mock_uid(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"101 102"])
            elif cmd == "FETCH":
                return ("OK", [
                    (b"101 (BODY[HEADER.FIELDS (SUBJECT)] {60}", msg1.as_bytes()),
                    (b"102 (BODY[HEADER.FIELDS (SUBJECT)] {55}", msg2.as_bytes()),
                ])
            return ("OK", [])

        mock_imap.uid.side_effect = mock_uid

        mailer = GoogleAccountMailer(username="test@gmail.com", app_password="password123")
        existing = mailer.get_existing_tender_numbers()

        self.assertIn("TNP2600099", existing)
        self.assertIn("PU/2026/001", existing)

    @patch("imaplib.IMAP4_SSL")
    def test_get_existing_tender_numbers_uses_single_session(self, mock_imap_cls):
        """Folder lookup (LIST) and the header fetch must share one login."""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "HKUST Tenders"'])

        msg = EmailMessage()
        msg["Subject"] = "Request for Tender Documents – TNP2600099 - Office Renovation"

        def mock_uid(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"101"])
            elif cmd == "FETCH":
                return ("OK", [
                    (b"101 (UID 101 BODY[HEADER.FIELDS (SUBJECT FROM DATE)] {60}", msg.as_bytes()),
                ])
            return ("OK", [])

        mock_imap.uid.side_effect = mock_uid

        mailer = GoogleAccountMailer(username="test@gmail.com", app_password="password123")
        existing = mailer.get_existing_tender_numbers()

        self.assertIn("TNP2600099", existing)
        self.assertEqual(mock_imap_cls.call_count, 1)
        self.assertEqual(mock_imap.login.call_count, 1)

    @patch("imaplib.IMAP4_SSL")
    def test_read_emails_batches_every_uid_newest_first(self, mock_imap_cls):
        """limit=None covers every UID in one batch; limit=N takes the newest N."""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.select.return_value = ("OK", [b"5"])

        batches = []
        msg = EmailMessage()
        msg["Subject"] = "Request for Tender Documents – TNL2600072 - Supply of Chairs"

        def mock_uid(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"1 2 3 4 5"])
            elif cmd == "FETCH":
                batches.append(args[0])
                return ("OK", [
                    (b"5 (UID 5 BODY[HEADER.FIELDS (SUBJECT FROM DATE)] {40}", msg.as_bytes()),
                ])
            return ("OK", [])

        mock_imap.uid.side_effect = mock_uid

        mailer = GoogleAccountMailer(username="test@gmail.com", app_password="password123")

        mailer.read_emails(folder="INBOX", limit=None)
        self.assertEqual(batches[0], b"5,4,3,2,1")

        mailer.read_emails(folder="INBOX", limit=2)
        self.assertEqual(batches[1], b"5,4")

    @patch("imaplib.IMAP4_SSL")
    def test_read_emails_skips_non_tuple_fetch_elements(self, mock_imap_cls):
        """A real FETCH response ends with a bare b')' element, not a message tuple."""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.select.return_value = ("OK", [b"1"])

        msg = EmailMessage()
        msg["Subject"] = "Request for Tender Documents – TNL2600072 - Supply of Chairs"

        def mock_uid(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"7"])
            elif cmd == "FETCH":
                return ("OK", [
                    (b"7 (UID 7 BODY[HEADER.FIELDS (SUBJECT FROM DATE)] {40}", msg.as_bytes()),
                    b")",
                ])
            return ("OK", [])

        mock_imap.uid.side_effect = mock_uid

        mailer = GoogleAccountMailer(username="test@gmail.com", app_password="password123")
        emails = mailer.read_emails(folder="INBOX", limit=None)

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].uid, "7")
        self.assertEqual(emails[0].tender_no, "TNL2600072")

    @patch("imaplib.IMAP4_SSL")
    def test_fetch_hkust_tender_emails_uses_single_session(self, mock_imap_cls):
        """Folder lookup and the fetch share one login; the INBOX filter still applies."""
        mock_imap = MagicMock()
        mock_imap_cls.return_value = mock_imap
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "HKUST Tenders"'])

        msg = EmailMessage()
        msg["Subject"] = "Request for Tender Documents – TNL2600072 - Supply of Chairs"
        msg["From"] = "HKUST Purchasing <pur@ust.hk>"

        def mock_uid(cmd, *args):
            if cmd == "SEARCH":
                return ("OK", [b"101"])
            elif cmd == "FETCH":
                return ("OK", [
                    (b"101 (UID 101 BODY[HEADER.FIELDS (SUBJECT FROM DATE)] {40}", msg.as_bytes()),
                ])
            return ("OK", [])

        mock_imap.uid.side_effect = mock_uid

        mailer = GoogleAccountMailer(username="test@gmail.com", app_password="password123")
        emails = mailer.fetch_hkust_tender_emails(limit=10)

        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0].tender_no, "TNL2600072")
        self.assertEqual(mock_imap_cls.call_count, 1)
        self.assertEqual(mock_imap.login.call_count, 1)

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


if __name__ == "__main__":
    unittest.main()
