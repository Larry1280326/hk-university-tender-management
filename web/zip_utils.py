"""Safe directory-to-ZIP helper for downloaded tender documents."""

import zipfile
from pathlib import Path
from typing import Optional

MAX_ZIP_BYTES = 200 * 1024 * 1024


def directory_size(root: Path) -> int:
    return sum(
        path.stat().st_size
        for path in Path(root).rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def zip_directory(root: Path, zip_path: Path) -> Optional[Path]:
    """Zip every regular file under ``root`` using safe relative names.

    Symlinks are skipped and names that would escape the archive are refused, so a
    server-controlled directory can never turn into a traversal in the downloaded zip.
    Returns ``None`` when there is nothing to archive.
    """
    root = Path(root).resolve()
    zip_path = Path(zip_path)
    if not root.is_dir():
        return None

    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root)
        if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
            continue
        entries.append((path, relative.as_posix()))

    if not entries:
        return None

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in entries:
            archive.write(path, arcname)
    return zip_path
