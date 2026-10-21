"""Pure selection rules shared by the UI and its tests."""

from typing import Iterable, List, Sequence, Set

from scraper.models import TenderNotice

GROUP_INVITED = "invited"
GROUP_OPEN = "open"


def split_groups(tenders: Iterable[TenderNotice]):
    """Return ``(invited, open)`` in the order the portal displays them."""
    invited: List[TenderNotice] = []
    open_tenders: List[TenderNotice] = []
    for tender in tenders:
        (invited if tender.invited else open_tenders).append(tender)
    return invited, open_tenders


def selected_tenders(
    tenders: Sequence[TenderNotice], selected_refs: Iterable[str]
) -> List[TenderNotice]:
    wanted = set(selected_refs or ())
    return [tender for tender in tenders if tender.tender_no in wanted]


def draftable_tenders(tenders: Iterable[TenderNotice]) -> List[TenderNotice]:
    """Open tenders with a contact address; invitations are not emailed."""
    return [
        tender for tender in tenders if not tender.invited and bool(tender.contact_email)
    ]


def skipped_for_missing_email(tenders: Iterable[TenderNotice]) -> List[str]:
    return [
        tender.tender_no
        for tender in tenders
        if not tender.invited and not tender.contact_email
    ]


def eoi_candidates(tenders: Iterable[TenderNotice]) -> List[TenderNotice]:
    return [tender for tender in tenders if tender.needs_eoi]
