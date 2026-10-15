"""Google App Password integration using IMAP.

Requires zero Google Cloud Console setup!
Only needs:
1. Google 2-Step Verification enabled.
2. A 16-character App Password from https://myaccount.google.com/apppasswords
"""

import imaplib
import time
from contextlib import contextmanager
from datetime import date, timedelta
from email import message_from_bytes
import re
from typing import Optional, Tuple, List, Dict, Generator, Set

import config
from mailer.template import TenderEmailDraft
from mailer.mime import build_mime_message
from mailer.email_parser import (
    decode_mime_words,
    extract_tender_refs_from_subject,
)

__all__ = ["GoogleAccountMailer"]



class GoogleAccountMailer:
    """Handles Gmail drafting and tender tagging over IMAP."""

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

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
    def imap_session(self) -> Generator[imaplib.IMAP4_SSL, None, None]:
        """Context manager for IMAP connection, login, and clean logout.

        No folder is selected here: each caller opens the mailbox it needs itself, so one
        unreachable folder cannot take the whole session down with it.
        """
        if not self.is_configured():
            raise ValueError(
                "Gmail App Password not configured in .env!\n"
                "Please add GMAIL_APP_PASSWORD to your .env file."
            )

        mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
        try:
            mail.login(self.username, self.app_password)
            yield mail
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def _find_folder_by_flag(
        self,
        mail: imaplib.IMAP4_SSL,
        flag: str,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve a Gmail system folder by its IMAP attribute, e.g. ``\\Sent``.

        Folder names cannot be relied on: Gmail localizes them per account language, so
        one account's Sent mailbox is ``[Gmail]/Sent Mail`` while another's is
        ``[Gmail]/&XfJT0ZCuTvY-``. The flag is matched only in the attribute section,
        since a folder *name* could contain the same text.
        """
        typ, mailboxes = mail.list()
        if typ == "OK" and mailboxes:
            for mb in mailboxes:
                attributes, delimiter, name = mb.decode(
                    "utf-8", errors="ignore"
                ).partition(' "/" ')
                if delimiter and flag in attributes:
                    return name.strip().strip('"')

        return default

    def _find_drafts_folder(self, mail: imaplib.IMAP4_SSL) -> str:
        """Auto-detect the Gmail Drafts mailbox across all account languages."""
        return self._find_folder_by_flag(mail, "\\Drafts", default="[Gmail]/Drafts")

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

    # Mailboxes examined to decide whether a tender has already been dealt with, in the
    # order the report lists them.
    HANDLED_SCAN_FOLDERS = ("Inbox", "Drafts", "Sent")

    # Gmail returns a 500-subject FETCH in about a fifth of the time 100-subject batches
    # take, so the round-trip count dominates the cost of this scan.
    SUBJECT_FETCH_BATCH = 500

    def _select_folder(self, mail: imaplib.IMAP4_SSL, folder: str) -> None:
        """Open ``folder`` read-only, retrying with the bare (unquoted) name.

        Gmail rejects the quoted spelling of some localized names, so both are tried
        before giving up.
        """
        for candidate in (f'"{folder}"', folder):
            status, _ = mail.select(candidate, readonly=True)
            if status == "OK":
                return
        raise RuntimeError(f"Could not open mailbox folder: {folder}")

    def _scan_selected_folder(self, mail: imaplib.IMAP4_SSL, criteria: str) -> Set[str]:
        """Tender references in the subject lines of the currently selected mailbox.

        Only subjects are fetched. Bodies cost minutes rather than seconds on this
        mailbox, and a quoted reply body would attribute an unrelated tender to a thread.
        """
        refs: Set[str] = set()
        typ, data = mail.uid("SEARCH", None, criteria)
        if typ != "OK" or not data or not data[0]:
            return refs

        uids = data[0].split()
        for start in range(0, len(uids), self.SUBJECT_FETCH_BATCH):
            batch = b",".join(uids[start : start + self.SUBJECT_FETCH_BATCH])
            status, fetched = mail.uid(
                "FETCH", batch, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
            )
            if status != "OK" or not fetched:
                continue
            for item in fetched:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                subject = decode_mime_words(message_from_bytes(item[1]).get("Subject", ""))
                refs.update(extract_tender_refs_from_subject(subject))

        return refs

    def get_handled_tenders(self, lookback_days: Optional[int] = None) -> Dict[str, List[str]]:
        """Map each tender ref to the mailbox(es) it appears in, e.g. ``{"TNH2600105": ["Sent", "Inbox"]}``.

        A tender counts as handled when its reference shows up in a subject line of Inbox,
        Drafts or Sent within the lookback window -- so a reply you sent counts even though
        nobody ever labelled it.
        """
        if not self.is_configured():
            return {}

        days = config.HKUST_MAIL_LOOKBACK_DAYS if lookback_days is None else lookback_days
        since = (date.today() - timedelta(days=max(1, days))).strftime("%d-%b-%Y")
        criteria = f"(SINCE {since})"

        found: Dict[str, List[str]] = {}
        # All three mailboxes share one connection: every extra login is another chance to
        # time out on a slow link, and Gmail caps concurrent IMAP connections.
        with self.imap_session() as mail:
            folders = {
                "Inbox": "INBOX",
                "Drafts": self._find_folder_by_flag(mail, "\\Drafts", default="[Gmail]/Drafts"),
                "Sent": self._find_folder_by_flag(mail, "\\Sent", default="[Gmail]/Sent Mail"),
            }

            for label in self.HANDLED_SCAN_FOLDERS:
                folder = folders.get(label)
                if not folder:
                    continue
                try:
                    self._select_folder(mail, folder)
                    refs = self._scan_selected_folder(mail, criteria)
                except Exception as e:
                    # One unreachable mailbox must not stop the others, or block the scrape.
                    print(f"[!] Could not scan {label} for already-handled tenders: {type(e).__name__}: {e}")
                    continue
                for ref in refs:
                    found.setdefault(ref, []).append(label)

        return found
