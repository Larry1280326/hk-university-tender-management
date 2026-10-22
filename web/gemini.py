"""Build the Gemini prompt for invitation project downloads.

The app never calls the Gemini API: the supplier's documents must not leave the
machine through a server-side request. The prompt is handed to the browser component,
which passes it to the Gemini web composer through the Send to Gemini extension. Nothing
here reads, merges, or converts the documents -- it only lists them.

The instructions for filling the forms live in the ``pdf-form-filler`` skill the
supplier keeps in Gemini, so the prompt only invokes that skill and describes the
project: the same short prompt for every download, with the tender and file list
changing.
"""

from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from scraper.models import TenderNotice
from scraper.paths import tender_dir_name

GEMINI_EXTENSION_ID = "gklmoobnblcdiglifjmdilofgndghmml"

# The Gemini skill that carries the form-filling instructions.
GEMINI_SKILL = "@pdf-form-filler"

TO_CONFIRM = "[TO CONFIRM]"

UNIVERSITY_LABELS = {"hkust": "HKUST", "cuhk": "CUHK", "cityu": "CityU"}


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

    Each prompt path is ``<university>/<tender folder>/<file>``, the download layout.
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
    """Paths for the prompt, matching the download folder layout.

    The download keeps the tender folder under the university label, so the prompt's file
    list matches the files on disk. Symlinks, missing files, and paths that would climb
    out of the root are skipped.
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
    tenders: Sequence[TenderNotice],
    document_paths: Sequence[str],
    *,
    university: str = "hkust",
) -> str:
    """The short brief Gemini receives: the skill, the project, and its files."""
    key = str(university or "").lower()
    label = UNIVERSITY_LABELS.get(key, key.upper() or "University")

    buckets, unassigned = _files_by_tender(tenders, document_paths)
    sections: List[str] = [GEMINI_SKILL, ""]
    many = len(tenders) > 1

    for index, tender in enumerate(tenders, start=1):
        if many:
            sections.extend([f"## Tender {index}: {tender.tender_no}", ""])
        else:
            sections.extend(["## Tender", ""])
        contact = " ".join(
            part
            for part in (tender.contact_person, tender.contact_email)
            if part and part.strip() and part.strip() != "Sir/Madam"
        )
        sections.extend(
            [
                f"- University: {label}",
                f"- Tender: {tender.tender_no}"
                + (f" — {tender.description}" if tender.description else ""),
                f"- Closing: {tender.closing_date or TO_CONFIRM}",
                f"- Portal contact: {contact or TO_CONFIRM}",
            ]
        )
        lines = _document_lines(tender, buckets.get(tender_dir_name(tender), []))
        if lines:
            sections.append("- Files:")
            sections.extend(f"  {line}" for line in lines)
        else:
            sections.append("- Files: none")
        sections.append("")

    if unassigned:
        sections.extend(
            [
                "## Other files in this download",
                "",
                *[f"- `{path}`" for path in unassigned],
                "",
            ]
        )

    return "\n".join(sections).strip() + "\n"
