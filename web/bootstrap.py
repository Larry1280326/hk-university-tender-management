"""Bridge hosted secrets into the environment ``config.py`` reads at import time.

Streamlit Community Cloud exposes secrets through ``st.secrets`` rather than the process
environment. This module must be imported and called *before* ``import config``.
"""

import hashlib
import os
from pathlib import Path
from typing import Mapping, MutableMapping, Optional

from web.secrets_bridge import (
    CertificateError,
    certificate_payload_length,
    materialize_br_certificate,
    scalar_secrets,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_LAUNCH_ARGS = "--no-sandbox --disable-dev-shm-usage --disable-gpu"


def _streamlit_secrets() -> Mapping:
    try:
        import streamlit as st

        return dict(st.secrets)
    except Exception:
        # Imported outside a Streamlit runtime, or secrets are not configured yet.
        return {}


def bootstrap_environment(
    secrets: Optional[Mapping] = None,
    *,
    environ: Optional[MutableMapping[str, str]] = None,
    runtime_dir: Optional[Path] = None,
) -> MutableMapping[str, str]:
    """Copy secrets to ``os.environ`` and materialize runtime paths.

    Shell variables win (``setdefault``), which keeps local ``.env``/exported values
    authoritative while a deployed Space gets everything from ``st.secrets``.
    """
    env = os.environ if environ is None else environ
    values = _streamlit_secrets() if secrets is None else dict(secrets)

    for key, value in scalar_secrets(values).items():
        env.setdefault(key, value)

    root = Path(runtime_dir or env.get("RUNTIME_DIR") or PROJECT_ROOT / ".runtime")
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    assets = Path(env.get("ASSETS_DIR") or root / "assets")
    if not assets.is_absolute():
        assets = PROJECT_ROOT / assets
    for path in (root, assets):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    env.setdefault("RUNTIME_DIR", str(root))
    env.setdefault("ASSETS_DIR", str(assets))
    env.setdefault("PLAYWRIGHT_LAUNCH_ARGS", DEFAULT_LAUNCH_ARGS)

    payload_length = certificate_payload_length(values)
    env["BR_CERTIFICATE_CONFIGURED"] = "1" if payload_length else "0"
    env["BR_CERTIFICATE_B64_LENGTH"] = str(payload_length)

    try:
        certificate = materialize_br_certificate(values, assets)
    except CertificateError as exc:
        # Leave the path unset so drafting refuses to start with a clear message.
        env.pop("BR_CERTIFICATE_PATH", None)
        env["BR_CERTIFICATE_ERROR"] = str(exc)
        certificate = None
    else:
        env.pop("BR_CERTIFICATE_ERROR", None)
    if certificate is not None:
        env["BR_CERTIFICATE_PATH"] = str(certificate)

    return env


def materialize_certificate_from_secrets() -> tuple:
    """Retry decoding the BR certificate from the live ``st.secrets`` mapping.

    Returns ``(path, "")`` on success or ``(None, reason)`` so Diagnostics can show the
    real problem (empty value vs invalid base64 vs bad magic bytes).
    """
    values = _streamlit_secrets()
    if not values:
        return None, "No secrets were loaded."
    assets = Path(os.environ.get("ASSETS_DIR") or (PROJECT_ROOT / ".runtime" / "assets"))
    try:
        path = materialize_br_certificate(values, assets)
    except CertificateError as exc:
        os.environ["BR_CERTIFICATE_ERROR"] = str(exc)
        return None, str(exc)
    if path is None:
        message = "BR_CERTIFICATE_B64 is empty or missing."
        os.environ["BR_CERTIFICATE_ERROR"] = message
        return None, message
    os.environ["BR_CERTIFICATE_PATH"] = str(path)
    os.environ.pop("BR_CERTIFICATE_ERROR", None)
    return path, ""


def certificate_fingerprint(path: Optional[str]) -> str:
    """Short SHA-256 for diagnostics; never exposes the certificate itself."""
    if not path:
        return "not configured"
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return f"unreadable: {exc}"
    return f"{len(data)} bytes, sha256:{hashlib.sha256(data).hexdigest()[:16]}"
