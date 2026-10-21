"""Bridge hosted secrets into the environment ``config.py`` reads at import time.

Streamlit Community Cloud exposes secrets through ``st.secrets`` rather than the process
environment. This module must be imported and called *before* ``import config``.
"""

import hashlib
import os
from pathlib import Path
from typing import Mapping, MutableMapping, Optional

from web.secrets_bridge import scalar_secrets

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
    """Copy secrets to ``os.environ`` and prepare the writable runtime directories.

    Shell variables win (``setdefault``), which keeps local ``.env``/exported values
    authoritative while a deployed Space gets everything from ``st.secrets``.
    """
    env = os.environ if environ is None else environ
    values = _streamlit_secrets() if secrets is None else dict(secrets)

    for key, value in scalar_secrets(values).items():
        # The certificate is uploaded in the UI now. A legacy multi-hundred-KB secret
        # must not be copied into the environment (and the worker's Popen env).
        if key.startswith("BR_CERTIFICATE_B64"):
            continue
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
    return env


def certificate_fingerprint(path: Optional[str]) -> str:
    """Short SHA-256 for diagnostics; never exposes the certificate itself."""
    if not path:
        return "not configured"
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return f"unreadable: {exc}"
    return f"{len(data)} bytes, sha256:{hashlib.sha256(data).hexdigest()[:16]}"
