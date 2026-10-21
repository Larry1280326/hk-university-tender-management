"""Pure helpers for turning hosted secrets into files and environment values.

Kept free of Streamlit imports so the rules can be unit-tested without a running app.
"""

import base64
import binascii
import os
from pathlib import Path
from typing import Mapping, Optional

MAX_CERTIFICATE_BYTES = 5 * 1024 * 1024

# Accepted certificate signatures. The draft body promises an attachment, so a wrong or
# truncated decode must fail here rather than at the Gmail step.
_MAGIC_PREFIXES = (b"%PDF-", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


class CertificateError(ValueError):
    """The configured BR certificate could not be decoded or is not a document."""


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


def _certificate_payload(values: Mapping) -> str:
    """Return the base64 payload, joining ``_1..N`` chunks when used."""
    single = values.get("BR_CERTIFICATE_B64")
    if single:
        return str(single)

    chunks = []
    for key in values:
        if not str(key).startswith("BR_CERTIFICATE_B64_"):
            continue
        suffix = str(key)[len("BR_CERTIFICATE_B64_"):]
        if suffix.isdigit():
            chunks.append((int(suffix), str(values[key])))
    if not chunks:
        return ""
    chunks.sort(key=lambda item: item[0])
    return "".join(value for _, value in chunks)


def certificate_payload_length(values: Mapping) -> int:
    """Length of the configured base64 payload, for diagnostics (never the value)."""
    return len(_certificate_payload(values))


def decode_br_certificate(values: Mapping) -> Optional[bytes]:
    """Decode the configured BR certificate, or return ``None`` when none is configured."""
    payload = _certificate_payload(values)
    if not payload:
        return None

    compact = "".join(payload.split())
    try:
        data = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CertificateError(f"BR_CERTIFICATE_B64 is not valid base64: {exc}") from exc

    if not data:
        raise CertificateError("BR_CERTIFICATE_B64 decoded to an empty file")
    if len(data) > MAX_CERTIFICATE_BYTES:
        raise CertificateError(
            f"BR certificate is {len(data)} bytes; the limit is {MAX_CERTIFICATE_BYTES}"
        )
    if not any(data.startswith(prefix) for prefix in _MAGIC_PREFIXES):
        raise CertificateError(
            "BR certificate does not look like a PDF or image (bad magic bytes)"
        )
    return data


def materialize_br_certificate(values: Mapping, dest_dir: Path) -> Optional[Path]:
    """Write the decoded certificate atomically into ``dest_dir``, returning its path."""
    data = decode_br_certificate(values)
    if data is None:
        return None

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "br_certificate.pdf"
    temporary = dest_dir / ".br_certificate.pdf.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return target
