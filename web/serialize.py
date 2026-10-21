"""JSON round-trips for tender objects crossing the Streamlit/worker process boundary."""

from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from scraper.models import TenderDocument, TenderNotice

_NOTICE_FIELDS = set(TenderNotice.__dataclass_fields__)
_DOCUMENT_FIELDS = set(TenderDocument.__dataclass_fields__)


def tender_to_payload(tender: TenderNotice) -> Dict[str, Any]:
    return asdict(tender)


def tender_from_payload(payload: Dict[str, Any]) -> TenderNotice:
    """Rebuild a tender, ignoring fields a newer/older UI may have added.

    Filtering rather than splatting the dict keeps a stale browser tab from breaking a
    worker after a redeploy adds or removes a field.
    """
    fields = {key: value for key, value in (payload or {}).items() if key in _NOTICE_FIELDS}
    raw_documents = fields.pop("documents", []) or []
    documents = [
        TenderDocument(
            **{
                key: value
                for key, value in (document or {}).items()
                if key in _DOCUMENT_FIELDS
            }
        )
        for document in raw_documents
    ]
    return TenderNotice(documents=documents, **fields)


def tenders_to_payloads(tenders: Iterable[TenderNotice]) -> List[Dict[str, Any]]:
    return [tender_to_payload(tender) for tender in tenders]


def tenders_from_payloads(payloads: Iterable[Dict[str, Any]]) -> List[TenderNotice]:
    return [tender_from_payload(payload) for payload in payloads]
