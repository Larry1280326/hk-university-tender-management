"""Configuration module for HKUST Vendor Automation."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Downloaded tender documents, one subfolder per tender number.
DOCUMENTS_DIR = Path(os.getenv("HKUST_DOCUMENTS_DIR", str(BASE_DIR / "output_documents")))

# Load environment variables from .env
load_dotenv(BASE_DIR / ".env")

# HKUST Portal Config
HKUST_WELCOME_URL = os.getenv(
    "HKUST_WELCOME_URL", "https://w5.ab.ust.hk/jstd/td_welcome?page=td_login"
)
HKUST_LOGIN_URL = os.getenv("HKUST_LOGIN_URL", "https://w5.ab.ust.hk/jstd/td_login")
HKUST_TENDER_NOTICE_URL = os.getenv(
    "HKUST_TENDER_NOTICE_URL", "https://w5.ab.ust.hk/jstd/td_tender_notice"
)
# The offer-selection page renders two tender tables: tenders the vendor was invited
# to (documents already released) and open tenders (documents requested by email).
HKUST_TENDER_OFFR_SEL_URL = os.getenv(
    "HKUST_TENDER_OFFR_SEL_URL", "https://w5.ab.ust.hk/jstd/td_tender_offr_sel"
)
HKUST_VENDOR_ID = os.getenv("HKUST_VENDOR_ID", "")
HKUST_PASSWORD = os.getenv("HKUST_PASSWORD", "")


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back to the default on a bad value."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[!] {name}={raw!r} is not an integer; using default {default}.")
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to the default on a bad value."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[!] {name}={raw!r} is not a number; using default {default}.")
        return default


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean from the environment, falling back to the default on a bad value."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    print(f"[!] {name}={raw!r} is not a boolean; using default {default}.")
    return default


# Navigation tuning. The HKUST login page loads Google reCAPTCHA from an external
# host, and that request is slow when the link is busy, so waiting for network idle
# is unreliable there. These bound every wait explicitly instead of inheriting
# Playwright's 30s default.
HKUST_NAV_TIMEOUT_MS = _env_int("HKUST_NAV_TIMEOUT_MS", 15000)
HKUST_READY_TIMEOUT_MS = _env_int("HKUST_READY_TIMEOUT_MS", 15000)
HKUST_SCRIPT_TIMEOUT_MS = _env_int("HKUST_SCRIPT_TIMEOUT_MS", 30000)
HKUST_POST_LOGIN_TIMEOUT_MS = _env_int("HKUST_POST_LOGIN_TIMEOUT_MS", 20000)
HKUST_DETAIL_TIMEOUT_MS = _env_int("HKUST_DETAIL_TIMEOUT_MS", 20000)
HKUST_NAV_ATTEMPTS = _env_int("HKUST_NAV_ATTEMPTS", 3)
HKUST_NAV_BACKOFF_SECONDS = _env_float("HKUST_NAV_BACKOFF_SECONDS", 2.0)
HKUST_RETRY_ON_NET_ERRORS = _env_flag("HKUST_RETRY_ON_NET_ERRORS", True)
HKUST_DEBUG_ARTIFACTS = _env_flag("HKUST_DEBUG_ARTIFACTS", True)


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
BR_CERTIFICATE_PATH = Path(
    os.getenv("BR_CERTIFICATE_PATH", str(ASSETS_DIR / "br_certificate.pdf"))
)

# Google Account Credentials (App Password / IMAP)
GMAIL_USER = os.getenv("GMAIL_USER", os.getenv("CONTACT_EMAIL", ""))
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GMAIL_DRAFTS_URL = os.getenv("GMAIL_DRAFTS_URL", "https://mail.google.com/mail/#drafts")
HKUST_MAIL_TAG = os.getenv("HKUST_MAIL_TAG", "HKUST Tenders")

# How far back to look when deciding whether a tender has already been dealt with. The
# check reads subject lines in Inbox, Drafts and Sent, so this also bounds its cost.
HKUST_MAIL_LOOKBACK_DAYS = _env_int("HKUST_MAIL_LOOKBACK_DAYS", 60)

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
