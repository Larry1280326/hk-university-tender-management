"""Streamlit Community Cloud entrypoint.

Run locally with ``streamlit run web/streamlit_app.py``.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

st.set_page_config(page_title="Tender Portal", page_icon="📄", layout="wide")

from web.bootstrap import bootstrap_environment  # noqa: E402

# Must happen before ``config`` is imported: config reads os.environ at import time.
bootstrap_environment()

import config  # noqa: E402,F401
from web.auth import authenticate  # noqa: E402
from web.ui import render_app  # noqa: E402

authenticate_result = authenticate()
render_app(authenticate_result)
