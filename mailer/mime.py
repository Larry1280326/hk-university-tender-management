"""MIME message builder for tender email drafts and attachments."""

import mimetypes
import time
from email.message import EmailMessage
from email.policy import default
from email.utils import formatdate
from pathlib import Path
from typing import Optional

from mailer.template import TenderEmailDraft


def build_mime_message(
    draft_info: TenderEmailDraft,
    from_email: Optional[str] = None,
) -> EmailMessage:
    """Construct a modern RFC-compliant email message with UTF-8 support and attachments.
    
    Args:
        draft_info: Details of the tender email draft.
        from_email: Optional sender email address. If omitted, From header is left unset.
        
    Returns:
        EmailMessage: Modern email message object compatible with IMAP, SMTP, and Gmail API.
    """
    msg = EmailMessage(policy=default)
    if from_email:
        msg["From"] = from_email
    msg["To"] = draft_info.recipient_email
    msg["Subject"] = draft_info.subject
    msg["Date"] = formatdate(time.time(), localtime=True)

    # Plain text body
    msg.set_content(draft_info.body)

    # Attach file if specified
    if draft_info.attachment_path:
        attach_path = Path(draft_info.attachment_path)
        if not attach_path.is_file():
            raise FileNotFoundError(
                f"Attachment not found: {attach_path}. The body refers to it, so building "
                "the message without it would send a promise it cannot keep."
            )

        ctype, encoding = mimetypes.guess_type(str(attach_path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)

        with open(attach_path, "rb") as fp:
            msg.add_attachment(
                fp.read(),
                maintype=maintype,
                subtype=subtype,
                filename=attach_path.name,
            )

    return msg
