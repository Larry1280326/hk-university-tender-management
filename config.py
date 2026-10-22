"""Configuration module for HKUST Vendor Automation."""

import os
from pathlib import Path
from typing import Tuple
from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).resolve().parent


def _project_path(raw: str) -> Path:
    """Resolve a configured path against the project root when it is relative.

    ``Path("assets/br.pdf")`` is read against the *current* directory, so the same .env
    would point at the certificate when launched from the project root and at nothing
    at all when launched from anywhere else.
    """
    path = Path(raw).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


# Load environment variables from .env
load_dotenv(BASE_DIR / ".env")

# Writable directories. A hosted deployment (Streamlit Cloud) points both at a runtime
# directory rather than the read-only-ish repository checkout.
ASSETS_DIR = _project_path(os.getenv("ASSETS_DIR", "") or str(BASE_DIR / "assets"))
RUNTIME_DIR = _project_path(os.getenv("RUNTIME_DIR", "") or str(BASE_DIR / ".runtime"))
for _dir in (ASSETS_DIR, RUNTIME_DIR):
    try:
        _dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # a read-only checkout must not break import; the caller reports the failure

# Downloaded tender documents root, organized as <DOCUMENT_DIR>/<UNIVERSITY>/<tender_no>/.
DOCUMENT_DIR = _project_path(
    os.getenv("DOCUMENT_DIR", "")
    or os.getenv("HKUST_DOCUMENTS_DIR", "")
    or str(BASE_DIR / "output_documents")
)
DOCUMENTS_DIR = DOCUMENT_DIR

# HKUST Portal Config
HKUST_WELCOME_URL = os.getenv(
    "HKUST_WELCOME_URL", "https://w5.ab.ust.hk/jstd/td_welcome?page=td_login"
)
HKUST_LOGIN_URL = os.getenv("HKUST_LOGIN_URL", "https://w5.ab.ust.hk/jstd/td_login")
# The offer-selection page renders two tender tables: tenders the vendor was invited
# to (documents already released) and open tenders (documents requested by email).
HKUST_TENDER_OFFR_SEL_URL = os.getenv(
    "HKUST_TENDER_OFFR_SEL_URL", "https://w5.ab.ust.hk/jstd/td_tender_offr_sel"
)
HKUST_VENDOR_ID = os.getenv("HKUST_VENDOR_ID", "")
HKUST_PASSWORD = os.getenv("HKUST_PASSWORD", "")

# CUHK (CUPro) Portal Config
CUHK_LOGIN_URL = os.getenv(
    "CUHK_LOGIN_URL", "https://cupro-cuhk.com/Supplier/en/Login/index.aspx"
)
CUHK_ACCEPT_TERMS_URL = os.getenv(
    "CUHK_ACCEPT_TERMS_URL", "https://cupro-cuhk.com/Supplier/en/Login/AcceptTerms.aspx"
)
CUHK_TENDER_SEARCH_URL = os.getenv(
    "CUHK_TENDER_SEARCH_URL", "https://cupro-cuhk.com/Supplier/en/Tender/Search.aspx"
)
CUHK_RFQ_SEARCH_URL = os.getenv(
    "CUHK_RFQ_SEARCH_URL", "https://cupro-cuhk.com/Supplier/en/RFQ/Search.aspx"
)
CUHK_USER_ID = os.getenv("CUHK_USER_ID", "")
CUHK_PASSWORD = os.getenv("CUHK_PASSWORD", "")

# CityU (e-Tendering System) Portal Config. CityU runs the same ETS platform as CUHK,
# so the Tender/RFQ search and View.aspx endpoints keep the same layout.
CITYU_BASE_URL = os.getenv("CITYU_BASE_URL", "https://etender.cityu.edu.hk")
CITYU_LOGIN_URL = os.getenv(
    "CITYU_LOGIN_URL", f"{CITYU_BASE_URL}/Supplier/en/Login/index.aspx"
)
# AcceptTerms.aspx rejects a direct GET, so login starts at CITYU_LOGIN_URL and
# submits its LoginForm to reach the declaration page.
CITYU_ACCEPT_TERMS_URL = os.getenv(
    "CITYU_ACCEPT_TERMS_URL",
    f"{CITYU_BASE_URL}/Supplier/en/Login/AcceptTerms.aspx",
)
CITYU_TENDER_SEARCH_URL = os.getenv(
    "CITYU_TENDER_SEARCH_URL", f"{CITYU_BASE_URL}/Supplier/en/Tender/Search.aspx"
)
CITYU_RFQ_SEARCH_URL = os.getenv(
    "CITYU_RFQ_SEARCH_URL", f"{CITYU_BASE_URL}/Supplier/en/RFQ/Search.aspx"
)
CITYU_USER_ID = os.getenv("CITYU_USER_ID", "")
CITYU_PASSWORD = os.getenv("CITYU_PASSWORD", "")


def _env_raw(name: str, fallback_name: str = "") -> Tuple[str, str]:
    """Return ``(used_name, raw_value)`` checking ``name`` first, then ``fallback_name``."""
    raw = os.getenv(name, "").strip()
    if raw or not fallback_name:
        return name, raw
    fb_raw = os.getenv(fallback_name, "").strip()
    if fb_raw:
        return fallback_name, fb_raw
    return name, ""


def _env_int(name: str, default: int, fallback_name: str = "") -> int:
    """Read an int from the environment, falling back to the default on a bad value."""
    used_name, raw = _env_raw(name, fallback_name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[!] {used_name}={raw!r} is not an integer; using default {default}.")
        return default


def _env_float(name: str, default: float, fallback_name: str = "") -> float:
    """Read a float from the environment, falling back to the default on a bad value."""
    used_name, raw = _env_raw(name, fallback_name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[!] {used_name}={raw!r} is not a number; using default {default}.")
        return default


def _env_flag(name: str, default: bool, fallback_name: str = "") -> bool:
    """Read a boolean from the environment, falling back to the default on a bad value."""
    used_name, raw_orig = _env_raw(name, fallback_name)
    raw = raw_orig.lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    print(f"[!] {used_name}={raw_orig!r} is not a boolean; using default {default}.")
    return default


# Navigation tuning. Portal pages load external scripts (such as Google reCAPTCHA on
# HKUST), so waiting for network idle is unreliable on a busy link. These bound every
# wait explicitly instead of inheriting Playwright's 30s default.
NAV_TIMEOUT_MS = _env_int("NAV_TIMEOUT_MS", 15000, "HKUST_NAV_TIMEOUT_MS")
READY_TIMEOUT_MS = _env_int("READY_TIMEOUT_MS", 15000, "HKUST_READY_TIMEOUT_MS")
SCRIPT_TIMEOUT_MS = _env_int("SCRIPT_TIMEOUT_MS", 30000, "HKUST_SCRIPT_TIMEOUT_MS")
POST_LOGIN_TIMEOUT_MS = _env_int(
    "POST_LOGIN_TIMEOUT_MS", 20000, "HKUST_POST_LOGIN_TIMEOUT_MS"
)
DETAIL_TIMEOUT_MS = _env_int("DETAIL_TIMEOUT_MS", 20000, "HKUST_DETAIL_TIMEOUT_MS")
NAV_ATTEMPTS = _env_int("NAV_ATTEMPTS", 3, "HKUST_NAV_ATTEMPTS")
NAV_BACKOFF_SECONDS = _env_float("NAV_BACKOFF_SECONDS", 2.0, "HKUST_NAV_BACKOFF_SECONDS")
RETRY_ON_NET_ERRORS = _env_flag("RETRY_ON_NET_ERRORS", True, "HKUST_RETRY_ON_NET_ERRORS")
DEBUG_ARTIFACTS = _env_flag("DEBUG_ARTIFACTS", True, "HKUST_DEBUG_ARTIFACTS")

# Extra Chromium flags for the host environment, e.g. "--no-sandbox --disable-dev-shm-usage"
# inside a container. Empty by default, so the CLI launches exactly as it always has.
PLAYWRIGHT_LAUNCH_ARGS = os.getenv("PLAYWRIGHT_LAUNCH_ARGS", "").strip()

# Backwards-compatible aliases
HKUST_NAV_TIMEOUT_MS = NAV_TIMEOUT_MS
HKUST_READY_TIMEOUT_MS = READY_TIMEOUT_MS
HKUST_SCRIPT_TIMEOUT_MS = SCRIPT_TIMEOUT_MS
HKUST_POST_LOGIN_TIMEOUT_MS = POST_LOGIN_TIMEOUT_MS
HKUST_DETAIL_TIMEOUT_MS = DETAIL_TIMEOUT_MS
HKUST_NAV_ATTEMPTS = NAV_ATTEMPTS
HKUST_NAV_BACKOFF_SECONDS = NAV_BACKOFF_SECONDS
HKUST_RETRY_ON_NET_ERRORS = RETRY_ON_NET_ERRORS
HKUST_DEBUG_ARTIFACTS = DEBUG_ARTIFACTS


# Company Information (Configured solely via .env)
COMPANY_NAME = os.getenv("COMPANY_NAME", "")
CONTACT_PERSON_NAME = os.getenv("CONTACT_PERSON_NAME", "")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "")
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "")
COMPANY_ADDRESS = os.getenv("COMPANY_ADDRESS", "")


def _build_default_sign_off() -> str:
    lines = ["Best regards,"]
    if CONTACT_PERSON_NAME:
        lines.append(CONTACT_PERSON_NAME)
    if COMPANY_NAME:
        lines.append(COMPANY_NAME)
    return "\n".join(lines)


SIGN_OFF = os.getenv("SIGN_OFF", "") or _build_default_sign_off()

# Business Registration (BR) Certificate Path
BR_CERTIFICATE_PATH = _project_path(
    os.getenv("BR_CERTIFICATE_PATH", "") or str(ASSETS_DIR / "br_certificate.pdf")
)

# Google Account Credentials (App Password / IMAP)
GMAIL_USER = os.getenv("GMAIL_USER", "") or os.getenv("CONTACT_EMAIL", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GMAIL_DRAFTS_URL = os.getenv("GMAIL_DRAFTS_URL", "https://mail.google.com/mail/#drafts")
MAIL_TAG = os.getenv("MAIL_TAG", "") or os.getenv("HKUST_MAIL_TAG", "HKUST Tenders")
HKUST_MAIL_TAG = MAIL_TAG

# How far back to look when deciding whether a tender has already been dealt with. The
# check reads subject lines in Drafts and Sent, so this also bounds its cost.
MAIL_LOOKBACK_DAYS = _env_int("MAIL_LOOKBACK_DAYS", 60, "HKUST_MAIL_LOOKBACK_DAYS")
HKUST_MAIL_LOOKBACK_DAYS = MAIL_LOOKBACK_DAYS

# Email Template
EMAIL_SUBJECT_TEMPLATE = "Request for Tender Documents – {tender_no} - {description}"

EMAIL_BODY_TEMPLATE = """Dear {recipient_name},

I am writing to express our interest in Tender No.: {tender_no}- {description}.

In accordance with your requirements, here are our company details for your records:
Company Name: {company_name}
Name of Contact Person: {contact_person}
Email Address: {contact_email}
Phone Number: {contact_phone}
Company Address: {company_address}

Please find attached a copy of our Business Registration Certificate (BR) for your verification.

Kindly confirm receipt and advise of any further steps or requirements. Thank you.

{sign_off}
"""
