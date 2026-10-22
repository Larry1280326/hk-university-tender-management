"""Build the Gemini prompt for HKUST invitation downloads.

The app never calls the Gemini API: the supplier's documents must not leave the
machine through a server-side request. The prompt is handed to the browser component,
which passes it to the Gemini web composer through the Send to Gemini extension. Nothing
here reads, merges, or converts the documents -- it only lists them.
"""

from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import config
from scraper.models import TenderNotice
from scraper.paths import tender_dir_name

GEMINI_EXTENSION_ID = "gklmoobnblcdiglifjmdilofgndghmml"

# The consumer composer accepts roughly ten attachments per prompt. The prompt may list
# more, so it tells the reader how to split the upload rather than failing.
GEMINI_MAX_ATTACHMENTS = 10

TO_CONFIRM = "[TO CONFIRM]"

OUTPUT_TABLE = (
    "| Document | Page | Field | Proposed value | Status | Notes |\n"
    "| --- | --- | --- | --- | --- | --- |"
)


def _value(raw: str) -> str:
    """Show an unset company detail as something the reader must not invent."""
    return raw.strip() if raw and raw.strip() else f"{TO_CONFIRM} (not provided)"


def _document_lines(tender: TenderNotice, paths: Sequence[str]) -> List[str]:
    """One bullet per downloaded file, plus the portal's own name for it."""
    lines = []
    names = {document.file_name: document for document in tender.documents}
    for path in paths:
        document = names.get(Path(path).name)
        if document:
            detail = (
                f"{document.doc_type}, {document.size}"
                if document.size
                else document.doc_type
            )
            lines.append(f"- `{path}` ({detail})")
        else:
            lines.append(f"- `{path}`")
    return lines


def _files_by_tender(
    tenders: Sequence[TenderNotice], document_paths: Sequence[str]
) -> Tuple[Dict[str, List[str]], List[str]]:
    """Group prompt paths by the tender folder they sit in.

    The prompt mirrors the downloaded ZIP, so a path is ``<HKUST>/<tender folder>/<file>``.
    A file the downloader wrote elsewhere still belongs in the prompt, so it is returned
    separately instead of being dropped.
    """
    buckets: Dict[str, List[str]] = {tender_dir_name(tender): [] for tender in tenders}
    unassigned: List[str] = []
    for path in document_paths:
        parts = Path(path).parts
        folder = parts[-2] if len(parts) >= 2 else ""
        bucket = buckets.get(folder)
        # ``buckets.get(folder) or unassigned`` would misroute every file: an existing
        # bucket starts empty, which is falsey, so the first file would land outside.
        if bucket is None:
            unassigned.append(path)
        else:
            bucket.append(path)
    return buckets, unassigned


def document_paths(files: Iterable[Path], root: Path) -> List[str]:
    """Paths for the prompt, using the same layout as the downloaded ZIP.

    The ZIP keeps the tender folder under the university label, so the prompt's file list
    matches what the user sees after unzipping. Symlinks, missing files, and paths that
    would climb out of the root are skipped.
    """
    root = Path(root).resolve()
    paths: List[str] = []
    seen = set()
    for raw in files:
        path = Path(raw)
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            # A file outside the download root cannot be named relative to it; keep its
            # own name so the prompt still lists it.
            relative = Path(resolved.name)
        if not relative.parts or any(
            part in ("", ".", "..") for part in relative.parts
        ):
            continue
        label = f"{root.name}/{relative.as_posix()}"
        if label in seen:
            continue
        seen.add(label)
        paths.append(label)
    return paths


def build_gemini_prompt(
    tenders: Sequence[TenderNotice], document_paths: Sequence[str]
) -> str:
    """The brief Gemini receives, with the downloaded files listed by tender."""
    buckets, unassigned = _files_by_tender(tenders, document_paths)
    sections: List[str] = [
        "# HKUST tender forms: preparation brief",
        "",
        "You are helping a Hong Kong supplier prepare the forms for an HKUST tender "
        "invitation. The attached files were downloaded from the HKUST e-Tender portal. "
        "Work out exactly what has to be filled in, signed, stamped and returned, and "
        "propose the values.",
        "",
        "## Supplier details",
        "",
        f"- Company name: {_value(config.COMPANY_NAME)}",
        f"- Contact person: {_value(config.CONTACT_PERSON_NAME)}",
        f"- Email: {_value(config.CONTACT_EMAIL)}",
        f"- Phone: {_value(config.CONTACT_PHONE)}",
        f"- Address: {_value(config.COMPANY_ADDRESS)}",
    ]
    if config.SIGN_OFF.strip():
        sections.append(f"- Preferred sign-off: {config.SIGN_OFF.strip()}")
    sections.extend(
        [
            "",
            "Use these details verbatim for supplier fields. If a form asks for anything "
            f"not listed above, write `{TO_CONFIRM}` instead of guessing.",
            "",
            "## Tenders and files in this download",
            "",
        ]
    )

    for index, tender in enumerate(tenders, start=1):
        contact = " ".join(
            part
            for part in (tender.contact_person, tender.contact_email)
            if part and part.strip() and part.strip() != "Sir/Madam"
        )
        sections.extend(
            [
                f"### Tender {index}: {tender.tender_no}",
                "",
                f"- Description: {tender.description or TO_CONFIRM}",
                f"- Closing: {tender.closing_date or TO_CONFIRM}",
                f"- Portal contact: {contact or TO_CONFIRM}",
            ]
        )
        lines = _document_lines(tender, buckets.get(tender_dir_name(tender), []))
        if lines:
            sections.append("- Files downloaded for this tender:")
            sections.extend(f"  {line}" for line in lines)
        else:
            sections.append("- Files downloaded for this tender: none")
        sections.append("")

    if unassigned:
        sections.extend(
            [
                "### Other files in this download",
                "",
                *[f"- `{path}`" for path in unassigned],
                "",
            ]
        )

    sections.extend(
        [
            "## What to produce",
            "",
            "1. Read every attached document. Identify the forms that must be completed, "
            "the fields each one requires, and anything that must be signed, stamped, "
            "dated or witnessed.",
            "2. Propose a value for every field, using the supplier details above or the "
            "documents themselves. Mark anything you cannot source as "
            f"`{TO_CONFIRM}`.",
            "3. List any required attachment or supporting document that is missing "
            "from this download.",
            "4. At the end, list the questions the supplier must answer before the "
            "forms can be completed.",
            "",
            "Present the field-by-field answers as a Markdown table:",
            "",
            OUTPUT_TABLE,
            "",
            "Status must be one of: `from supplier details`, `from document`, "
            f"`{TO_CONFIRM}`.",
            "",
            "## Rules",
            "",
            "- Never invent a value: no company numbers, registration numbers, dates, "
            "amounts, names or signatures that are not in the attachments or the "
            "supplier details above.",
            "- Do not claim you have filled in, saved, signed or submitted any PDF. You "
            "are proposing values for the supplier to enter.",
            "- Do not use web search or outside knowledge about this tender; rely on the "
            "attachments.",
            "- Do not give legal, tax or insurance advice. Point out what the documents "
            "require and let the supplier decide.",
            "",
            "## Attachments",
            "",
            f"This download contains {len(document_paths)} document(s). Gemini accepts "
            f"about {GEMINI_MAX_ATTACHMENTS} files per prompt, so if there are more, ask "
            "the supplier which files to upload next and start with the forms that must "
            "be filled in.",
        ]
    )
    return "\n".join(sections).strip() + "\n"
