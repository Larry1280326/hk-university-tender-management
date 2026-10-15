"""Email parsing and tender reference extraction utilities."""

import re
from email.header import decode_header
from typing import Optional, Set

HKUST_TN_PATTERN = re.compile(
    r"\b(TN[A-Z0-9]{6,12}(?:\s*PQ)?)\b",
    re.IGNORECASE,
)
# HKUST's slashed references are "<PREFIX>/<YEAR>/<SEQ>", e.g. EO/2026/012, FO/2025/102.
# The year segment is restricted to a real year on purpose: a bare \d{4} also matched
# unrelated references of the same shape living in the same mailbox, e.g. the works order
# "YFI/4401/025-VO" (project 4401), which is not a tender at all. Note the live portal
# issues only the TN-form numbers below; this branch exists for older tender references.
HKUST_CODE_PATTERN = re.compile(
    r"\b([A-Z]{2,6}/(?:19|20)\d{2}/\d{2,5}(?:[A-Z0-9_-]+)?)\b",
    re.IGNORECASE,
)


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


def extract_tender_refs_from_subject(text: str) -> Set[str]:
    """Every strict tender reference in ``text``, with no loose keyword fallback.

    Scanning a whole mailbox decides which tenders to *hide* as already dealt with, so it
    trusts only unambiguous codes: a looser match would hide a tender nobody has acted on.
    """
    if not text:
        return set()

    refs = {match.strip() for match in HKUST_TN_PATTERN.findall(text)}
    refs.update(match.strip() for match in HKUST_CODE_PATTERN.findall(text))
    return {ref for ref in refs if len(ref) >= 6}


def normalize_tender_ref(ref: Optional[str]) -> str:
    """Normalize tender number for consistent comparison (e.g. TNP2600099, PU/2026/001)."""
    if not ref:
        return ""
    return re.sub(r"[\s\-_/]", "", ref).upper()
