"""Validation and storage helpers for the uploaded BR certificate.

Kept free of Streamlit imports so the rules can be unit-tested without a running app.
"""

import os
from pathlib import Path
from typing import Mapping

MAX_CERTIFICATE_BYTES = 5 * 1024 * 1024

# Accepted certificate signatures. The draft body promises an attachment, so a wrong or
# truncated upload must fail here rather than at the Gmail step.
_MAGIC_PREFIXES = (b"%PDF-", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


class CertificateError(ValueError):
    """The uploaded BR certificate is not a usable document."""


def scalar_secrets(values: Mapping) -> dict:
    """Top-level scalar secrets only.

    Nested tables (for example ``[auth.credentials]``) belong to the authenticator and
    must not leak into the process environment.
    """
    result = {}
    for key, value in values.items():
        if isinstance(value, Mapping):
            continue
        if isinstance(value, (str, int, float, bool)):
            result[str(key)] = str(value)
    return result


def validate_certificate_bytes(data: bytes) -> bytes:
    """Reject empty, oversized or non-document payloads before they reach a draft."""
    if not data:
        raise CertificateError("BR certificate is empty")
    if len(data) > MAX_CERTIFICATE_BYTES:
        raise CertificateError(
            f"BR certificate is {len(data)} bytes; the limit is {MAX_CERTIFICATE_BYTES}"
        )
    if not any(data.startswith(prefix) for prefix in _MAGIC_PREFIXES):
        raise CertificateError(
            "BR certificate does not look like a PDF or image (bad magic bytes)"
        )
    return data


def write_certificate(data: bytes, dest_dir: Path) -> Path:
    """Write already-validated certificate bytes atomically into ``dest_dir``."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "br_certificate.pdf"
    temporary = dest_dir / ".br_certificate.pdf.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return target
