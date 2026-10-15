# HKUST e-Tendering Automation & Gmail Drafter

Automated workflow to:
1. Log in to the HKUST e-Tendering Portal (`https://w5.ab.ust.hk/jstd/td_welcome?page=td_login`) and accept the Terms & Conditions.
2. Scrape the offer selection page (`td_tender_offr_sel`), which renders **two separate tables**:
   - **My Tender Invitation / Interested Tender(s)** — tenders you were invited to. The portal has
     already released these documents, so the tool can download them.
   - **Tender Notice** — open tenders. No documents are released; the detail page carries the
     Purchasing Office contact to email instead.
3. Show the two groups separately, then allow interactive selection.
4. Automatically draft personalized enquiry emails directly in **Gmail** (with company details and Business Registration certificate attached).
5. Before scraping, check Inbox, Drafts and Sent for tenders you have already dealt with, so they are not offered again.

---

## Quick Start

### 1. First-Time Setup (Environment & Browsers)

Synchronize project dependencies with `uv` and install Chromium for Playwright:

```bash
# 1. Sync dependencies and create the .venv environment
uv sync

# 2. Install Playwright browser binary (Chromium)
uv run playwright install chromium
```

> **Tip:** Running `uv sync` ensures your editor/IDE recognizes `.venv` immediately. Installing Chromium is required for portal scraping.

---

### 2. Configure Credentials (`.env`)

Copy `.env.example` to `.env` (or edit existing `.env`):

```ini
# HKUST Vendor Portal Credentials
HKUST_VENDOR_ID=your_vendor_id_here
HKUST_PASSWORD=your_password_here

# Path to your Business Registration (BR) Certificate file (PDF or image)
BR_CERTIFICATE_PATH=assets/br_certificate.pdf

# Google Account (Using App Password - Recommended, zero Cloud Console setup!)
# Generate at: https://myaccount.google.com/apppasswords
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=your_16_char_app_password
GMAIL_DRAFTS_URL=https://mail.google.com/mail/#drafts
HKUST_MAIL_TAG=HKUST Tenders

# Company Information (Loaded dynamically into email drafts)
COMPANY_NAME=Your Company Name
CONTACT_PERSON_NAME=Contact Person, Title
CONTACT_EMAIL=contact@example.com
CONTACT_PHONE=12345678
COMPANY_ADDRESS=Your Company Address
# Optional custom sign-off override (defaults to Contact Person + Company Name)
# SIGN_OFF="Best regards,\nYour Name\nYour Company Name"
```

Place your official Business Registration certificate in `assets/br_certificate.pdf` (or customize `BR_CERTIFICATE_PATH`).

---

### 3. Gmail Integration Setup

The application supports direct Gmail integration without requiring any Google Cloud Console configuration:

#### 1. Direct Gmail Drafting (Google App Password)
1. Ensure **2-Step Verification** is enabled on your Google Account.
2. Go to [Google App Passwords](https://myaccount.google.com/apppasswords).
3. Create an app password (e.g. named `HKUST Workflow`).
4. Set `GMAIL_USER` and `GMAIL_APP_PASSWORD` in your `.env` file.
5. **Capabilities**:
   - Saves drafts directly to your Gmail **Drafts** folder via IMAP.

#### 2. Offline Mode & Direct Web Links (Automatic Fallback)
If `GMAIL_APP_PASSWORD` is not configured, the tool automatically:
- Exports ready-to-send `.eml` files to `output_drafts/` (double-click to open in Outlook or Windows Mail).
- Generates 1-click pre-filled Gmail compose web links.

---

### 4. Run the Automation

Run directly with `uv`:

```bash
uv run python app.py
```

#### Interactive Main Menu:
- **1. Scrape HKUST Tenders & Draft Emails**: Headless scraping of both tender tables + interactive tender checklist + optional download of invited-tender documents (see `HKUST_DOCUMENTS_DIR`) + Gmail draft creation with automatic tagging.
- **2. Test Google Account Connection**: Verifies Google App Password and IMAP connection.
- **3. Exit**

The menu stays up until you choose **3. Exit**, so cancelling a scrape — or finishing one —
returns you here rather than closing the program. Ctrl-C at the prompt also exits.

#### Direct Command Line Flags:

```bash
# Display help and all available CLI arguments
uv run python app.py --help

# Test Google IMAP / App Password connection
uv run python app.py --test

# Run portal scraper with visible Chromium browser
uv run python app.py --visible
```

---

## Tender Tracking & Gmail Labels

1. **Already-handled check (runs before every scrape)**: the tool reads the **subject lines** of your
   Inbox, Drafts and Sent folders for the last `HKUST_MAIL_LOOKBACK_DAYS` days (default 60). A tender
   found there is hidden from the tender list, and the run prints which mailbox each hidden tender was
   found in. This is what makes a reply you sent count as handled — labels are never consulted, so
   nothing has to be tagged for it to work.
2. **Immediate Draft Tagging**: When drafts are created in Gmail, the `HKUST Tenders` label (configured by `HKUST_MAIL_TAG` in `.env`) is automatically attached to the newly saved drafts.

The `HKUST Tenders` label is applied only to drafts the tool creates. Nothing in the tool reads it
back, so it exists purely for your own organisation in Gmail.

---

## Email Template Generated

- **Subject**:
  `Request for Tender Documents – <Tender No.> - <Description>`

- **Body**:
  ```
  Dear <Recipient Name>,

  I am writing to express our interest in Tender No.: <Tender No.>- <Description>.

  In accordance with your requirements, here are our company details for your records:
  Company Name: <COMPANY_NAME from .env>
  Name of Contact Person: <CONTACT_PERSON_NAME from .env>
  Email Address: <CONTACT_EMAIL from .env>
  Phone Number: <CONTACT_PHONE from .env>
  Company Address: <COMPANY_ADDRESS from .env>

  Please find attached a copy of our Business Registration Certificate (BR) for your verification.

  Kindly confirm receipt and advise of any further steps or requirements. Thank you.

  <SIGN_OFF from .env>
  ```

- **Attachment**:
  Attached Business Registration Certificate (`assets/br_certificate.pdf` or configured path).

---

## Running Unit Tests

To run the automated verification suite:

```bash
uv run python -m unittest discover -s tests -v
```

All 88 tests verify mock workflows, MIME generation, IMAP session handling, the account connection check, the already-handled mailbox scan, the interactive menu loop, regex extraction, and `.env` dynamic loading without requiring network access.
