"""Email parsing and tender reference extraction utilities."""

import re
from dataclasses import dataclass, field, asdict
from email.header import decode_header
from typing import Optional, List, Dict, Any, Set

TEMPLATE_PATTERN = re.compile(
    r"Request for Tender Documents\s*[–\-]\s*([A-Za-z0-9/_\-\s]+?)\s*[–\-]",
    re.IGNORECASE,
)
TEMPLATE_FULL_PATTERN = re.compile(
    r"Request for Tender Documents\s*[–\-]\s*([A-Za-z0-9/_\-\s]+?)\s*[–\-]\s*([^\r\n]+)",
    re.IGNORECASE,
)
HKUST_TN_PATTERN = re.compile(
    r"\b(TN[A-Z0-9]{6,12}(?:\s*PQ)?)\b",
    re.IGNORECASE,
)
HKUST_CODE_PATTERN = re.compile(
    r"\b([A-Z]{2,6}/\d{4}/\d{2,5}(?:[A-Z0-9_-]+)?)\b",
    re.IGNORECASE,
)
TENDER_REF_KEYWORD_PATTERN = re.compile(
    r"\bTender\s*(?:No\.?|Ref\.?|Reference)\s*[:：#]?\s*([A-Za-z0-9/_-]+)",
    re.IGNORECASE,
)
GENERIC_IGNORE_WORDS = {
    "documents", "document", "notice", "invitation", "enquiry", "details",
    "clarification", "submission", "schedule", "requirements", "for", "the",
}


@dataclass
class TenderReceivedEmail:
    """Represents an incoming email message identified or tagged for HKUST tenders."""
    msg_id: str
    uid: str
    subject: str
    sender: str
    date: str
    body_snippet: str
    tender_no: Optional[str] = None
    tender_name: Optional[str] = None
    labels: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def decode_mime_words(header_value: Optional[str]) -> str:
    """Decode MIME encoded email header fields into plain Unicode text."""
    if not header_value:
        return ""
    try:
        decoded_fragments = decode_header(header_value)
        pieces = []
        for piece, charset in decoded_fragments:
            if isinstance(piece, bytes):
                encoding = charset or "utf-8"
                try:
                    pieces.append(piece.decode(encoding, errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    pieces.append(piece.decode("utf-8", errors="replace"))
            else:
                pieces.append(str(piece))
        return "".join(pieces).strip()
    except Exception:
        return str(header_value).strip()


def extract_tender_no_from_text(
    text: str,
    known_tenders: Optional[List[str]] = None,
) -> Optional[str]:
    """Identify and extract HKUST tender reference number from email subject or body."""
    if not text:
        return None

    # 1. Match against known active tender numbers if provided
    if known_tenders:
        text_lower = text.lower()
        for ref in known_tenders:
            if ref and ref.lower() in text_lower:
                return ref

    # 2. Match standard template: "Request for Tender Documents – <Tender No.> - <Description>"
    tmpl_match = TEMPLATE_PATTERN.search(text)
    if tmpl_match:
        found_tmpl = tmpl_match.group(1).strip().rstrip(".,:;")
        if len(found_tmpl) >= 4:
            return found_tmpl

    # 3. Match HKUST TN code format (e.g. TNL2600072, TNK2600074, TNA2600095 PQ)
    tn_match = HKUST_TN_PATTERN.search(text)
    if tn_match:
        found_tn = tn_match.group(1).strip().rstrip(".,:;")
        if len(found_tn) >= 4:
            return found_tn

    # 4. Match standard HKUST tender format with slashes (e.g. PU/2026/001, EO/2026/012)
    code_match = HKUST_CODE_PATTERN.search(text)
    if code_match:
        found_code = code_match.group(1).strip().rstrip(".,:;")
        if len(found_code) >= 4:
            return found_code

    # 5. Match explicit keyword indicators (e.g. Tender No.: CC-2025-001)
    kw_match = TENDER_REF_KEYWORD_PATTERN.search(text)
    if kw_match:
        found_kw = kw_match.group(1).strip().rstrip(".,:;")
        if found_kw.lower() not in GENERIC_IGNORE_WORDS and len(found_kw) >= 3:
            return found_kw

    return None


def extract_all_tender_refs(subject: str) -> Set[str]:
    """Collect every tender reference a subject mentions, not just the first one.

    A single subject can legitimately quote more than one reference, so the
    mailbox pre-check uses this instead of extract_tender_no_from_text() to
    avoid missing a tender that a reply happens to mention second.
    """
    if not subject:
        return set()

    refs: Set[str] = set()

    first_match = extract_tender_no_from_text(subject)
    if first_match:
        refs.add(first_match)

    for tn in HKUST_TN_PATTERN.findall(subject):
        if tn:
            refs.add(tn.strip())

    for code in HKUST_CODE_PATTERN.findall(subject):
        if code:
            refs.add(code.strip())

    return refs


def normalize_tender_ref(ref: Optional[str]) -> str:
    """Normalize tender number for consistent comparison (e.g. TNP2600099, PU/2026/001)."""
    if not ref:
        return ""
    return re.sub(r"[\s\-_/]", "", ref).upper()


def extract_tender_name_from_text(text: str) -> Optional[str]:
    """Extract tender title/description from email subject or text."""
    if not text:
        return None

    # 1. Match standard template: Request for Tender Documents - <No> - <Description>
    match = TEMPLATE_FULL_PATTERN.search(text)
    if match:
        desc = match.group(2).strip()
        desc = re.sub(r"\[.*?\]", "", desc).strip()
        if len(desc) >= 3:
            return desc

    # 2. Match pattern: <tender_no> - <description>
    dash_match = re.search(
        r"(?:TN[A-Z0-9]{6,12}|[A-Z]{2,6}/\d{4}/\d{2,5})\s*[–\-:]\s*([^\r\n]+)",
        text,
        re.IGNORECASE,
    )
    if dash_match:
        desc = dash_match.group(1).strip()
        desc = re.sub(r"\[.*?\]", "", desc).strip()
        if len(desc) >= 3 and not any(w in desc.lower() for w in ["re:", "fwd:"]):
            return desc

    return None


def parse_gmail_labels(raw_metadata: str) -> List[str]:
    """Parse Gmail labels from IMAP FETCH response metadata (X-GM-LABELS)."""
    if not raw_metadata:
        return []
    match = re.search(r"X-GM-LABELS\s*\((.*?)\)", raw_metadata, re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    content = match.group(1).strip()
    if not content:
        return []
    labels = []
    for quoted, unquoted in re.findall(r'"([^"]*)"|(\S+)', content):
        val = (quoted or unquoted).strip()
        if val:
            labels.append(val)
    return labels
