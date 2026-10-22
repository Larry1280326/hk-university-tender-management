"""Resolve a download job's file list against the session workspace.

HKUST project downloads are offered file by file, so the relative paths in the job result
have to be turned back into real files without ever leaving the workspace.
"""

import mimetypes
from pathlib import Path
from typing import Iterable, List

DEFAULT_MIME = "application/octet-stream"


def downloadable_files(relative_paths: Iterable[str], workspace: Path) -> List[Path]:
    """Existing regular files under ``workspace``, in the order the job listed them.

    Absolute paths, paths that climb out of the workspace, symlinks, directories, and
    files that have since disappeared are skipped: a stale or tampered result must not
    read files outside this session's workspace.
    """
    root = Path(workspace).resolve()
    files: List[Path] = []
    seen = set()
    for raw in relative_paths or ():
        path = root / str(raw)
        if path.is_symlink() or not path.is_file():
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        files.append(resolved)
    return files


def download_mime(path: Path) -> str:
    """Media type for the browser; unknown types are downloaded as binary."""
    return mimetypes.guess_type(Path(path).name)[0] or DEFAULT_MIME
