"""Google App Password integration using IMAP (for Drafts & Inbox) and SMTP (for Sending).

Requires zero Google Cloud Console setup!
Only needs:
1. Google 2-Step Verification enabled.
2. A 16-character App Password from https://myaccount.google.com/apppasswords
"""

import imaplib
import smtplib
import time
from contextlib import contextmanager
from email import message_from_bytes
import re
from typing import Optional, Tuple, List, Dict, Any, Generator, Set

import config
from mailer.template import TenderEmailDraft
from mailer.mime import build_mime_message
from mailer.email_parser import (
    TenderReceivedEmail,
    decode_mime_words,
    extract_tender_no_from_text,
    extract_tender_name_from_text,
    extract_all_tender_refs,
    parse_gmail_labels,
)

__all__ = ["GoogleAccountMailer"]



class GoogleAccountMailer:
    """Handles Gmail drafts, sending, mailbox reading, and tender tagging via IMAP & SMTP."""

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587

    def __init__(
        self,
        username: Optional[str] = None,
        app_password: Optional[str] = None,
    ):
        self.username = username or config.CONTACT_EMAIL
        self.app_password = (
            app_password
            or config.GMAIL_APP_PASSWORD
        ).replace(" ", "")

    def is_configured(self) -> bool:
        """Check if username and app password are present."""
        return bool(self.username and self.app_password)

    @contextmanager
    def imap_session(
        self,
        folder: Optional[str] = None,
        readonly: bool = False,
    ) -> Generator[imaplib.IMAP4_SSL, None, None]:
        """Context manager for IMAP connection, login, optional folder selection, and clean logout."""
        if not self.is_configured():
            raise ValueError(
                "Gmail App Password not configured in .env!\n"
                "Please add GMAIL_APP_PASSWORD to your .env file."
            )

        mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
        try:
            mail.login(self.username, self.app_password)
            if folder:
                self._select_folder(mail, folder, readonly=readonly)
            yield mail
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def _select_folder(
        self,
        mail: imaplib.IMAP4_SSL,
        folder: str,
        readonly: bool = False,
    ) -> None:
        """Select a mailbox, retrying unquoted for names Gmail refuses to quote."""
        status, _ = mail.select(f'"{folder}"', readonly=readonly)
        if status != "OK":
            status, _ = mail.select(folder, readonly=readonly)
            if status != "OK":
                raise RuntimeError(f"Could not open mailbox folder: {folder}")

    def _find_drafts_folder(self, mail: imaplib.IMAP4_SSL) -> str:
        """Auto-detect the Gmail Drafts mailbox across all account languages."""
        typ, mailboxes = mail.list()
        if typ == "OK" and mailboxes:
            for mb in mailboxes:
                mb_str = mb.decode("utf-8", errors="ignore")
                if "\\Drafts" in mb_str:
                    parts = mb_str.split(' "/" ')
                    if len(parts) > 1:
                        return parts[-1].strip().strip('"')

        return "[Gmail]/Drafts"

    def save_draft(
        self,
        draft_info: TenderEmailDraft,
        apply_tags: bool = True,
        tag_prefix: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """Append message directly into Gmail's Drafts folder using IMAP and apply tender tags.
        
        Returns:
            Tuple[bool, List[str]]: (Success boolean, List of applied Gmail tags)
        """
        msg = build_mime_message(draft_info, from_email=self.username)
        raw_bytes = msg.as_bytes()

        applied_tags: List[str] = []
        with self.imap_session() as mail:
            drafts_folder = self._find_drafts_folder(mail)
            res, data = mail.append(
                drafts_folder,
                "(\\Draft)",
                imaplib.Time2Internaldate(time.time()),
                raw_bytes,
            )
            if res != "OK":
                raise RuntimeError(f"Failed to append draft to {drafts_folder}: {data}")

            if apply_tags:
                prefix = tag_prefix or config.HKUST_MAIL_TAG
                tags_to_apply = [prefix]

                # Ensure label exists in Gmail
                for tag in tags_to_apply:
                    try:
                        mail.create(f'"{tag}"')
                    except Exception:
                        pass

                # Parse UID from APPENDUID (RFC 4315) or locate newest draft
                new_uid = None
                if data and data[0]:
                    meta_str = (
                        data[0].decode("utf-8", errors="ignore")
                        if isinstance(data[0], bytes)
                        else str(data[0])
                    )
                    uid_match = re.search(r"APPENDUID\s+\d+\s+(\d+)", meta_str)
                    if uid_match:
                        new_uid = uid_match.group(1)

                if not new_uid:
                    try:
                        mail.select(f'"{drafts_folder}"')
                        typ, sdata = mail.uid("SEARCH", None, f'HEADER Subject "{draft_info.subject}"')
                        if typ == "OK" and sdata and sdata[0]:
                            uids = sdata[0].split()
                            if uids:
                                new_uid = (
                                    uids[-1].decode("utf-8")
                                    if isinstance(uids[-1], bytes)
                                    else str(uids[-1])
                                )
                    except Exception:
                        pass

                if new_uid:
                    try:
                        mail.select(f'"{drafts_folder}"')
                        for tag in tags_to_apply:
                            try:
                                store_res, _ = mail.uid("STORE", new_uid, "+X-GM-LABELS", f'("{tag}")')
                                if store_res == "OK":
                                    applied_tags.append(tag)
                            except Exception:
                                pass
                    except Exception:
                        pass

        return True, applied_tags

    def send_email(self, draft_info: TenderEmailDraft) -> bool:
        """Send email directly using Gmail SMTP."""
        if not self.is_configured():
            raise ValueError("Gmail App Password not configured in .env!")

        msg = build_mime_message(draft_info, from_email=self.username)
        with smtplib.SMTP(self.SMTP_HOST, self.SMTP_PORT) as server:
            server.starttls()
            server.login(self.username, self.app_password)
            server.send_message(msg)
        return True

    def test_connection(self) -> Tuple[bool, str]:
        """Verify IMAP login and locate Drafts folder."""
        if not self.is_configured():
            return False, "Missing GMAIL_USER or GMAIL_APP_PASSWORD in .env"

        try:
            with self.imap_session() as mail:
                drafts_folder = self._find_drafts_folder(mail)
                return True, f"Connected successfully! Detected Drafts mailbox: '{drafts_folder}'"
        except Exception as e:
            return False, f"Connection failed: {e}"

    def _list_mailboxes(self, mail: imaplib.IMAP4_SSL) -> List[str]:
        """List mailbox / label names from an already-open session."""
        typ, mailboxes = mail.list()
        names: List[str] = []
        if typ == "OK" and mailboxes:
            for mb in mailboxes:
                mb_str = mb.decode("utf-8", errors="ignore")
                parts = mb_str.split(' "/" ')
                if len(parts) > 1:
                    names.append(parts[-1].strip().strip('"'))
        return names

    def list_mailboxes(self) -> List[str]:
        """List all available mailbox folder / label names in the Gmail account."""
        with self.imap_session() as mail:
            return self._list_mailboxes(mail)

    def _find_tender_tag_folder(self, mail: imaplib.IMAP4_SSL) -> Optional[str]:
        """Find the HKUST Tenders tag folder within an already-open session."""
        target = config.HKUST_MAIL_TAG
        mailboxes = self._list_mailboxes(mail)
        for mb in mailboxes:
            if mb.lower() == target.lower():
                return mb
        for mb in mailboxes:
            if mb.lower() in ("hkust tenders", "hkust-tenders", "hkust_tenders"):
                return mb
        return None

    def find_tender_tag_folder(self) -> Optional[str]:
        """Find the HKUST Tenders tag folder if it exists in Gmail."""
        try:
            with self.imap_session() as mail:
                return self._find_tender_tag_folder(mail)
        except Exception:
            pass
        return None

    def _search_uids(
        self,
        mail: imaplib.IMAP4_SSL,
        search_criteria: str = "ALL",
    ) -> List[bytes]:
        """Return the UIDs matching an IMAP search in the selected mailbox."""
        typ, data = mail.uid("SEARCH", None, search_criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def _fetch_emails(
        self,
        mail: imaplib.IMAP4_SSL,
        uids: List[bytes],
        limit: Optional[int] = None,
        known_tenders: Optional[List[str]] = None,
    ) -> List[TenderReceivedEmail]:
        """Batch-fetch subject/from/date headers plus Gmail labels for the given UIDs.

        The single header-read path: one batched UID FETCH covers every message,
        so no message body is ever downloaded and only one round trip is spent.
        Pass limit=None to read every UID.
        """
        if not uids:
            return []

        # Newest emails first; limit=None means the whole mailbox
        selected = uids if limit is None else uids[-limit:]
        selected = list(reversed(selected))

        fetch_status, fetch_data = mail.uid(
            "FETCH",
            b",".join(selected),
            "(X-GM-LABELS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])",
        )

        if fetch_status != "OK" or not fetch_data:
            return []

        results: List[TenderReceivedEmail] = []
        for item in fetch_data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue

            meta_str = (
                item[0].decode("utf-8", errors="ignore")
                if isinstance(item[0], bytes)
                else str(item[0])
            )
            uid_match = re.search(r"UID\s+(\d+)", meta_str)
            uid_str = uid_match.group(1) if uid_match else "unknown"

            labels = parse_gmail_labels(meta_str)
            hdr_msg = message_from_bytes(item[1])

            subject = decode_mime_words(hdr_msg.get("Subject", ""))

            results.append(
                TenderReceivedEmail(
                    msg_id=uid_str,
                    uid=uid_str,
                    subject=subject,
                    sender=decode_mime_words(hdr_msg.get("From", "")),
                    date=decode_mime_words(hdr_msg.get("Date", "")),
                    body_snippet=subject,
                    tender_no=extract_tender_no_from_text(subject, known_tenders=known_tenders),
                    tender_name=extract_tender_name_from_text(subject),
                    labels=labels,
                )
            )

        return results

    def get_existing_tender_numbers(self) -> Set[str]:
        """Fetch all tender reference numbers from emails tagged with HKUST Tenders in Gmail.

        Resolves the tag folder and reads it within a single IMAP session, then
        keeps every reference each subject mentions (see extract_all_tender_refs).
        """
        if not self.is_configured():
            return set()

        existing_numbers: Set[str] = set()
        try:
            with self.imap_session() as mail:
                tender_folder = self._find_tender_tag_folder(mail)
                if not tender_folder:
                    return set()

                self._select_folder(mail, tender_folder, readonly=True)
                for email_item in self._fetch_emails(mail, self._search_uids(mail, "ALL")):
                    existing_numbers |= extract_all_tender_refs(email_item.subject)
        except Exception:
            pass

        return existing_numbers

    def read_emails(
        self,
        folder: str = "INBOX",
        search_criteria: str = "ALL",
        limit: Optional[int] = None,
        known_tenders: Optional[List[str]] = None,
    ) -> List[TenderReceivedEmail]:
        """Read and parse emails quickly using batch header & label fetching.

        Args:
            limit: Maximum number of newest messages to read; None reads them all.
        """
        with self.imap_session(folder=folder, readonly=True) as mail:
            return self._fetch_emails(
                mail,
                self._search_uids(mail, search_criteria),
                limit=limit,
                known_tenders=known_tenders,
            )

    def fetch_hkust_tender_emails(
        self,
        folder: Optional[str] = None,
        limit: int = 50,
        known_tenders: Optional[List[str]] = None,
    ) -> List[TenderReceivedEmail]:
        """Fetch emails related to HKUST or tenders."""
        with self.imap_session() as mail:
            target_folder = folder or self._find_tender_tag_folder(mail) or "INBOX"
            self._select_folder(mail, target_folder, readonly=True)

            if target_folder != "INBOX":
                return self._fetch_emails(
                    mail,
                    self._search_uids(mail, "ALL"),
                    limit=limit,
                    known_tenders=known_tenders,
                )

            try:
                emails = self._fetch_emails(
                    mail,
                    self._search_uids(mail, 'X-GM-RAW "from:ust.hk OR subject:tender OR tender"'),
                    limit=limit,
                    known_tenders=known_tenders,
                )
            except Exception:
                emails = self._fetch_emails(
                    mail,
                    self._search_uids(mail, "ALL"),
                    limit=limit,
                    known_tenders=known_tenders,
                )

        filtered: List[TenderReceivedEmail] = []
        for e in emails:
            is_ust = "ust.hk" in e.sender.lower()
            has_tender = "tender" in e.subject.lower() or bool(e.tender_no)
            if is_ust or has_tender:
                filtered.append(e)

        return filtered

    def tag_hkust_tender_emails(
        self,
        emails: Optional[List[TenderReceivedEmail]] = None,
        tag_prefix: Optional[str] = None,
        folder: str = "INBOX",
        known_tenders: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Tag HKUST tender emails in Gmail under the HKUST Tenders tag."""
        prefix = tag_prefix or config.HKUST_MAIL_TAG

        if emails is None:
            emails = self.fetch_hkust_tender_emails(
                folder=folder, known_tenders=known_tenders
            )

        if not emails:
            return []

        tagged_summary: List[Dict[str, Any]] = []

        with self.imap_session(folder=folder) as mail:
            created_labels = set()

            for e in emails:
                tags_to_apply = [prefix]
                applied: List[str] = []
                for tag in tags_to_apply:
                    if tag in e.labels:
                        applied.append(tag)
                        continue

                    if tag not in created_labels:
                        try:
                            mail.create(f'"{tag}"')
                        except Exception:
                            pass
                        created_labels.add(tag)

                    try:
                        res, _ = mail.uid("STORE", e.uid, "+X-GM-LABELS", f'("{tag}")')
                        if res == "OK":
                            applied.append(tag)
                            if tag not in e.labels:
                                e.labels.append(tag)
                    except Exception:
                        pass

                tagged_summary.append({
                    "uid": e.uid,
                    "subject": e.subject,
                    "sender": e.sender,
                    "tender_no": e.tender_no,
                    "tender_name": e.tender_name,
                    "applied_tags": applied,
                })

            return tagged_summary
