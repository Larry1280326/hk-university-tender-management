"""Shared tender data models used by every university scraper."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TenderDocument:
    """One downloadable file attached to an invited tender."""

    doc_type: str
    file_name: str
    subject: str
    updated_on: str
    size: str


@dataclass
class TenderNotice:
    """Structured data for an active tender notice."""

    tender_no: str
    description: str
    closing_date: str
    contact_person: str
    contact_email: str
    contact_phone: Optional[str] = None
    tender_type: Optional[str] = None
    raw_enquiry: Optional[str] = None
    already_processed: bool = False
    # True when documents are already unlocked: an HKUST invitation row, or a CUHK
    # / CityU notice whose Expression of Interest has been submitted.
    invited: bool = False
    documents: List[TenderDocument] = field(default_factory=list)
    portal_id: Optional[str] = None
    category: str = "Tender"
    needs_eoi: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
