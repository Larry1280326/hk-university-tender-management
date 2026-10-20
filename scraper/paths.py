"""Filesystem naming helpers shared by the university downloaders."""

import re

from scraper.models import TenderNotice


def safe_dir_name(name: str) -> str:
    """Sanitize a project description or tender number into a safe single-segment folder name.

    Preserves single spaces and standard punctuation while replacing path separators,
    illegal filesystem characters, and ``..`` sequences with underscores.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name or "")
    cleaned = re.sub(r"\.{2,}", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "tender"


def tender_dir_name(tender: TenderNotice) -> str:
    """Folder name for a tender's downloaded documents, preferring its project description."""
    return safe_dir_name(tender.description or tender.tender_no)
