# HKUST, CUHK & CityU e-Tendering Automation & Gmail Drafter

Automated workflow supporting **HKUST** (`w5.ab.ust.hk/jstd`), **CUHK (CUPro)** (`cupro-cuhk.com`), and **CityU (ETS)** (`etender.cityu.edu.hk`):
1. **HKUST Workflow**:
   - Before scraping, scan Gmail (`Inbox`, `Drafts`, and `Sent`) for tenders you have already dealt with over the last `MAIL_LOOKBACK_DAYS` days (default 60), so they are hidden from the scrape.
   - Log in to the HKUST e-Tendering Portal (`https://w5.ab.ust.hk/jstd/td_login`), accepting Terms & Conditions if redirected to `td_welcome`, and wait for Google reCAPTCHA readiness.
   - Scrape the offer selection page (`td_tender_offr_sel`), which renders **My Tender Invitation / Interested Tender(s)** (`#inviteTenderList`) and **Tender Notice** (`#openTenderList`).
   - Open the interactive checklist directly for selection, download released documents for selected **invited tenders** into `<DOCUMENT_DIR>/HKUST/<project_name>/`, and automatically draft personalized enquiry emails in **Gmail** (with company details and Business Registration certificate attached) for selected **open tenders**.
2. **CUHK (CUPro) Workflow**:
   - Log in to the CUHK Procurement System (`https://cupro-cuhk.com/Supplier/en/Login/index.aspx`), automatically accepting the supplier declarations.
   - Scrape active (`Status == "Issued"`) **Request for Quotation (RFQ)** (`Q-YYYYNNNN`) and **Tender Invitation** (`T-YYYYNNNN`) notices.
   - Group active notices in the interactive checklist into **Expression of Interest Submitted / Invited** (documents already unlocked) and **Open Tender Notice (Submit EOI & Download)**.
   - For selected open tenders, automatically submit **Expression of Interest (Yes)** on CUPro to unlock the tender documents, and download all released documents, addenda, registration forms, and query responses directly into `<DOCUMENT_DIR>/CUHK/<project_name>/`.
3. **CityU (ETS) Workflow**:
   - Log in to the CityU e-Tendering System (`https://etender.cityu.edu.hk/Supplier/en/Login/index.aspx`), accepting the supplier declarations.
   - Scrape active (`Status == "Issued"`) **Request for Quotation (RFQ)** (`Q-YYYYNNNN`) and **Tender Invitation** (`T-YYYYNNNN`) notices.
   - Group active notices into **Expression of Interest Submitted / Invited** and **Open Tender Notice (Submit EOI & Download)**.
   - For selected open tenders, submit **Expression of Interest (Yes)** to unlock the documents, then download all released files into `<DOCUMENT_DIR>/CityU/<project_name>/`.

CityU runs the same ETS platform as CUHK, so the Tender/RFQ search and detail-page handling share the same behaviour; only the login entry point and host differ.

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

# CUHK (CUPro) Supplier Portal Credentials
CUHK_USER_ID=your_cuhk_user_id_here
CUHK_PASSWORD=your_cuhk_password_here

# CityU e-Tendering System Supplier Credentials
CITYU_USER_ID=your_cityu_user_id_here
CITYU_PASSWORD=your_cityu_password_here

# Path to your Business Registration (BR) Certificate file (PDF or image)
BR_CERTIFICATE_PATH=assets/br_certificate.pdf

# Google Account (Using App Password - Recommended, zero Cloud Console setup!)
# Generate at: https://myaccount.google.com/apppasswords
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=your_16_char_app_password
GMAIL_DRAFTS_URL=https://mail.google.com/mail/#drafts
MAIL_TAG=HKUST Tenders
# How far back Inbox/Drafts/Sent are scanned to decide a tender is already handled
# MAIL_LOOKBACK_DAYS=60

# Company Information (Loaded dynamically into email drafts)
COMPANY_NAME=Your Company Name
CONTACT_PERSON_NAME=Contact Person, Title
CONTACT_EMAIL=contact@example.com
CONTACT_PHONE=12345678
COMPANY_ADDRESS=Your Company Address
# Optional custom sign-off override (defaults to Contact Person + Company Name)
# SIGN_OFF="Best regards,\nYour Name\nYour Company Name"

# Where tender documents are saved (organized under <DOCUMENT_DIR>/<UNIVERSITY>/<tender_no>/)
DOCUMENT_DIR=output_documents

# Optional: Portal URL & Navigation Tuning Overrides (defaults shown)
# HKUST_WELCOME_URL=https://w5.ab.ust.hk/jstd/td_welcome?page=td_login
# HKUST_LOGIN_URL=https://w5.ab.ust.hk/jstd/td_login
# HKUST_TENDER_OFFR_SEL_URL=https://w5.ab.ust.hk/jstd/td_tender_offr_sel
# CUHK_LOGIN_URL=https://cupro-cuhk.com/Supplier/en/Login/index.aspx
# CUHK_ACCEPT_TERMS_URL=https://cupro-cuhk.com/Supplier/en/Login/AcceptTerms.aspx
# CUHK_TENDER_SEARCH_URL=https://cupro-cuhk.com/Supplier/en/Tender/Search.aspx
# CUHK_RFQ_SEARCH_URL=https://cupro-cuhk.com/Supplier/en/RFQ/Search.aspx
# CITYU_BASE_URL=https://etender.cityu.edu.hk
# CITYU_LOGIN_URL=https://etender.cityu.edu.hk/Supplier/en/Login/index.aspx
# CITYU_ACCEPT_TERMS_URL=https://etender.cityu.edu.hk/Supplier/en/Login/AcceptTerms.aspx
# CITYU_TENDER_SEARCH_URL=https://etender.cityu.edu.hk/Supplier/en/Tender/Search.aspx
# CITYU_RFQ_SEARCH_URL=https://etender.cityu.edu.hk/Supplier/en/RFQ/Search.aspx
# NAV_TIMEOUT_MS=15000          # loading the page document itself
# READY_TIMEOUT_MS=15000        # waiting for a specific element to appear
# SCRIPT_TIMEOUT_MS=30000       # waiting for external scripts (e.g. reCAPTCHA) to load
# POST_LOGIN_TIMEOUT_MS=20000   # waiting for the login result (never retried)
# DETAIL_TIMEOUT_MS=20000       # per-tender detail page
# NAV_ATTEMPTS=3                # set to 1 to disable retries
# NAV_BACKOFF_SECONDS=2.0       # sleeps 2s, then 4s, between attempts
# RETRY_ON_NET_ERRORS=true      # also retry transient network resets
# DEBUG_ARTIFACTS=true          # save screenshot + HTML to assets/debug/ on failure
```

Place your official Business Registration certificate in `assets/br_certificate.pdf` (or customize
`BR_CERTIFICATE_PATH`). Both `BR_CERTIFICATE_PATH` and `DOCUMENT_DIR` accept relative paths
(resolved against the project root), absolute paths, or `~` home-relative paths. If the configured BR
certificate file is missing, drafting stops with an error instead of creating drafts whose body promises
a certificate they do not enclose.

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
- **1. HKUST**:
  1. Scans `Inbox`, `Drafts`, and `Sent` via IMAP to skip already-handled tenders.
  2. Logs in and scrapes both tender tables (`#inviteTenderList` and `#openTenderList`).
  3. Opens the interactive tender checklist directly (`Space` to toggle, `a` to select all, `i` to invert, `Enter` to confirm).
  4. If any **invited tenders** are selected, prompts to download their released documents into `<DOCUMENT_DIR>/HKUST/<project_name>/`.
  5. If any **open tenders** are selected, verifies `BR_CERTIFICATE_PATH` exists, shows a sample email preview card, and lets you confirm draft creation (`1. Yes, Create Drafts in Gmail`), return to modify your selection (`2. Back to Selection`), or return to the main menu (`3. Back to Main Menu`).
- **2. CUHK**:
  1. Logs into CUHK CUPro (`cupro-cuhk.com`) and scrapes active (`Issued`) RFQs and Tender Invitations.
  2. Opens the interactive checklist grouped by **Expression of Interest Submitted / Invited** vs. **Open Tender Notice (Submit EOI & Download)**.
  3. Submits **Expression of Interest (Yes)** on CUPro for any selected open tenders (unlocking their files) and downloads all documents into `<DOCUMENT_DIR>/CUHK/<project_name>/`.
- **3. CityU**:
  1. Logs into CityU ETS (`etender.cityu.edu.hk`) and scrapes active (`Issued`) RFQs and Tender Invitations.
  2. Opens the same grouped checklist.
  3. Submits **Expression of Interest (Yes)** for selected open tenders and downloads all documents into `<DOCUMENT_DIR>/CityU/<project_name>/`.
- **4. Test Google Account Connection**: Verifies Google App Password and IMAP connection (and reports the resolved localized Drafts folder).
- **5. Exit**

The menu stays up until you choose **5. Exit**, so cancelling a scrape — or finishing one —
returns you here rather than closing the program. Ctrl-C at the prompt also exits.

#### Direct Command Line Flags:

```bash
# Display help and all available CLI arguments
uv run python app.py --help

# Test Google IMAP / App Password connection (requires GMAIL_APP_PASSWORD in .env)
uv run python app.py --test

# Run portal scraper with visible Chromium browser
uv run python app.py --visible
```

---

## Tender Tracking & Gmail Labels

1. **Already-handled check (runs before every scrape)**: the tool reads the **subject lines** of your
   Inbox, Drafts and Sent folders for the last `MAIL_LOOKBACK_DAYS` days (default 60). A tender
   found there is hidden from the tender list, and the run reports the total number of handled tender
   references found. This is what makes a reply you sent count as handled — labels are never consulted, so
   nothing has to be tagged for it to work.
2. **Immediate Draft Tagging**: When drafts are created in Gmail, the `HKUST Tenders` label (configured by `MAIL_TAG` in `.env`) is automatically attached to the newly saved drafts.

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

Of the 119 tests in the suite:
- **112 unit tests run offline by default**, verifying mock workflows, MIME generation, IMAP session handling, the account connection check, the already-handled mailbox scan, the interactive menu loop, tender selection, document download helpers, attachment validation, regex extraction, and `.env` dynamic loading without requiring network access.
- **7 live-portal / slow-network integration tests are skipped by default** (`tests/test_portal_reachability.py` and `tests/test_portal_slow_network.py`). Enable them explicitly when testing against the live HKUST portal:
  ```bash
  RUN_LIVE_PORTAL_TEST=1 RUN_SLOW_NETWORK_TEST=1 uv run python -m unittest discover -s tests -v
  ```

---

## Roadmap: Beyond HKUST

The tool is HKUST-shaped: its portal URLs, login selectors, table markup, tender-number
formats and Gmail label are all HKUST values, and the menu offers a single scrape action
now labelled with its university. The intent is to cover other Hong Kong universities,
which do not publish their tenders the same way. **None of this is built yet** — this
section records the plan so the next step does not have to rediscover it.

### What is already portable

Most of the machinery does not care which university it is talking to, and should be
reused rather than rebuilt:

- **`scraper/browser_utils.py`** — `NavigationError`, the retry policy
  (`_is_retryable`, `_RETRYABLE_NET_MARKERS`), `RequestTracker`, `wait_for_predicate`,
  `dump_failure_artifacts`, `navigate`. Only its `config.*` reads are HKUST-branded.
- **`mailer/mime.py`** and **`mailer/webmail_helper.py`** — entirely generic.
- **The IMAP mechanics in `mailer/google_account.py`** — `imap_session`,
  `_find_folder_by_flag`, `save_draft`, `get_handled_tenders`. `save_draft` already takes a
  `tag_prefix`, so a per-university Gmail label needs no IMAP change, only threading.
- **`scraper/paths.py`** — `safe_dir_name` / `tender_dir_name`, shared by the HKUST,
  CUHK, and CityU downloaders.
- **The HKUST download loop in `scraper/HKUST/documents.py`** — `_ensure_listing`,
  `_download_one`.
- **All company/identity configuration** — `COMPANY_*`, `CONTACT_*`, `SIGN_OFF`,
  `BR_CERTIFICATE_PATH`, `GMAIL_*`, the two email templates.

### What is HKUST-specific today

Each of these is a HKUST value sitting in a shared module, and has to move behind a
per-university provider:

| Where | What |
|---|---|
| `scraper/HKUST/auth.py` | `HKUSTAuthManager`, `LOGIN_READY_SELECTOR`, `#vendorId` / `#password` / `#submitBtn` / `#tncChkBox` / `#warningModal`, the `/td_login` and `/td_welcome` URL markers, the reCAPTCHA handling |
| `scraper/HKUST/parser.py` | `#inviteTenderList`, `#openTenderList`, `tr.tender-dtl-row`, `.tender-offer-dtl-dl-link`, `.doc-dl-btn`, the text-based `DETAIL_READY_SELECTOR`, the fixed column order, the `Enquiry` / `Request for Tender Document` row-heading matching |
| `config.py` | `HKUST_WELCOME_URL`, `HKUST_LOGIN_URL`, `HKUST_TENDER_OFFR_SEL_URL`, `HKUST_VENDOR_ID`, `HKUST_PASSWORD`, `HKUST_DOCUMENTS_DIR`, the five portal timeouts, `HKUST_MAIL_TAG` |
| `mailer/email_parser.py` | `HKUST_TN_PATTERN`, `HKUST_CODE_PATTERN` — the ref formats the mailbox scan matches on |
| `app.py`, `cli_views.py` | menu strings, banner, table titles |

Five of the `HKUST_*` names are merely misnamed rather than portal-specific —
`HKUST_NAV_ATTEMPTS`, `HKUST_NAV_BACKOFF_SECONDS`, `HKUST_RETRY_ON_NET_ERRORS`,
`HKUST_DEBUG_ARTIFACTS`, `HKUST_MAIL_LOOKBACK_DAYS`. `HKUST_TENDER_NOTICE_URL` is dead:
nothing reads it.

### Target architecture

Step 1 (models out) and the per-university directory split are done; the layout is
now:

```
scraper/
  __init__.py
  browser_utils.py           # shared navigation/retry helpers
  models.py                  # TenderNotice, TenderDocument — no project imports at all
  paths.py                   # safe_dir_name / tender_dir_name, shared by all downloaders
  HKUST/
    auth.py
    parser.py
    documents.py
  CUHK/
    auth.py
    parser.py
    documents.py
  CityU/
    auth.py
    parser.py
    documents.py
app.py                       # menu and per-university workflows
mailer/email_parser.py       # keeps normalize_tender_ref / decode_mime_words

# still to come:
scraper/base.py              # TenderProvider protocol, PlaywrightProvider base, PortalConfig
scraper/registry.py          # all_providers(), get_provider(slug)
scraper/<SLUG>/provider.py   # identity, defaults, wiring
scraper/<SLUG>/refs.py       # the tender-number patterns
```

**A `Protocol`, not an ABC, at the boundary** — the implementations are structurally
different machines, and a Protocol means a public HTTP feed never has to inherit from a
browser base, import `playwright`, or fake a `Page`. **A concrete base class for the
Playwright family** (ordinary inheritance, no ABC) holds what login portals genuinely share:
the browser lifecycle, the timeout wiring, the download loop, and the "no prompt inside the
browser session" invariant already documented in `portal_session`. The two archetypes stay
independent because the interface is expressed only in outcomes — `list[TenderNotice]`,
`list[Path]`, `set[str]` — never in browser or HTTP vocabulary.

Capability differences (`requires_login`, `supports_documents`) should be **data flags the
caller branches on**, not a caught `NotImplementedError` and not a type check.

**Shared models.** `TenderNotice` / `TenderDocument` move to a root `models.py` that imports
nothing from the project — that is what lets the HTTP provider, the mail layer and the tests
depend on them without dragging in a browser. Re-export from the old path for one step so the
move stays mechanical. Add a defaulted `provider: str = ""` field: it carries per-university
identity into the selection title, the documents folder and the mailbox report without a new
plumbing parameter.

**Configuration.** The flat `HKUST_` prefix *is already* the namespaced scheme, with one
namespace — generalise it to `<SLUG>_<KEY>` rather than replacing it. A `PORTALS=…` scheme
would break the live `.env`, `tests/test_env_loading.py`, and every `patch.object(config,
"HKUST_*")` in the suite for no gain. `config.py` keeps owning how env is read and coerced
(`_env_int` / `_env_float` / `_env_flag` are reused verbatim); each provider package owns its
defaults, because "the HKUST login URL" is provider knowledge, not configuration knowledge.
Promoted neutral knobs keep the old spelling alive via nested fallback —
`_env_int("NAV_ATTEMPTS", _env_int("HKUST_NAV_ATTEMPTS", 3))`.

> **The `.env` is shared with another tool.** It also holds `DEEPSEEK_*`, `GOOGLE_DRIVE_*`
> and `TRACKER_INTERVAL_SECONDS`, none of which `config.py` reads. Any namespacing work must
> be purely additive and must not rewrite existing keys.

**Documents and labels.** One flat Gmail label per university (`HKUST Tenders`,
`PolyU Tenders`) leaves the user's existing label intact rather than orphaning it. The
documents directory stays `output_documents/<tender_no>/` for HKUST — a live tool should not
silently move files the user already has — while later providers default to
`output_documents/<slug>/`.

### Why the interface must not assume a browser

The universities do not publish the same way, and this is the main design constraint:

- **HKU** — Finance/Estates Office notice pages plus an XML feed
  (`feo.hku.hk/finance/xml/tendernotice.xml`): plausibly a plain HTTP fetch, no Playwright.
- **HKMU** — `etender.hkmu.edu.hk` exposes a public "Open Tender Notice" list: possibly no login.
- **PolyU** — `tendering.polyu.edu.hk` e-Tendering System, supplier login required.
- **CUHK** — CUPro (`cupro-cuhk.com`), supplier registration required.

If the provider interface took a logged-in Playwright page, the feed-based sources would have
to fake one. It has to accept "a way to produce tender notices", of which Playwright is one
implementation.

### Three latent defects to fix on the way

These are real today, not hypotheticals, and each one silently breaks the moment a second
university exists:

1. **The menu router.** `interactive_menu()` routes with `choice.startswith("1")` /
   `"2"` / `"3"` (`app.py`). At ten or more entries `"10. Exit".startswith("1")` is `True`,
   so Exit would dispatch into the scrape workflow. The flat list must route on the parsed
   leading integer.
2. **`LISTING_URL` is bound at import time.** `scraper/HKUST/parser.py` and
   `scraper/HKUST/documents.py` both read `config.HKUST_TENDER_OFFR_SEL_URL` into a module or class
   constant, so the URL is fixed the moment the module is imported. A per-provider URL must
   become an instance attribute read at call time, or a second provider silently keeps
   pointing at HKUST.
3. **`normalize_tender_ref` collapses namespaces.** It strips `/ - _` and whitespace and
   upper-cases, so `EO/2026/012` and another university's `EO/2026/012` both become
   `EO2026012` — indistinguishable. The hide key for already-handled tenders must be
   `(slug, normalized_ref)`, not the normalized ref alone.

### Migration steps

Every step below leaves the suite green and the tool working. `[move]` = no behaviour
change; `[structure]` = same behaviour, different wiring; `[UI]`/`[behaviour]` = observable
change.

| # | Step | Class |
|---|---|---|
| 1 | ✅ **Models out.** `scraper/models.py` now owns `TenderNotice` / `TenderDocument`; importers use it directly. Still to add: the defaulted `provider` field. | `[move]` |
| 2 | ✅ **Per-university packages.** `scraper/{HKUST,CUHK,CityU}/{auth,parser,documents}.py`; shared `scraper/paths.py`. Still to do: delete the dead `HKUST_TENDER_NOTICE_URL` and fix the `LISTING_URL` binding. | `[move]` |
| 3 | **The interface and registry.** `scraper/base.py` + `scraper/registry.py`; `HKUSTProvider` wraps auth + parser + documents behind `fetch_tenders` / `download_documents`. `app.portal_session` moves into `PlaywrightProvider.portal_session`. | `[structure]` |
| 4 | **Config namespacing.** `load_portal_config()` in `config.py`; HKUST defaults move into its provider; the five neutral knobs are promoted with the old env spelling still honoured. | `[structure]` |
| 5 | **Flat menu.** Choices built from the registry, separator, integer-prefix router replacing `startswith`. | `[UI]` |
| 6 | **Multi-university mail.** Per-provider `extract_refs`; `get_processed_tender_numbers` scans **once** and then filters by owning university (an extra scan is another login on a slow link); hide key becomes `(slug, ref)`; the report line names the owning university. | `[behaviour]` |
| 7 | **The second provider — two of them.** Add one HTTP-feed university *and* one login portal together, each with its own `tests/fixtures/<slug>.py` and a shared `test_provider_contract.py`. A Protocol validated by only one implementation is just a rename. | new feature |

Step 4's cost is in the tests: `tests/test_browser_utils.py` and
`tests/test_portal_slow_network.py` patch `config.HKUST_NAV_ATTEMPTS`,
`HKUST_NAV_BACKOFF_SECONDS`, `HKUST_RETRY_ON_NET_ERRORS` and `HKUST_DEBUG_ARTIFACTS` in ten
places, and an alias would **not** keep those patches effective — patching an alias does not
change what `browser_utils` reads. Those tests move to the neutral names in that step.

### Risks and open questions

- **The real cost is per-portal reverse engineering, and the abstraction does not reduce it.**
  HKUST needed reCAPTCHA-callability probing, two tables whose detail pages are located by
  row-heading *text*, and localized IMAP folder names. Each login portal is work of that kind;
  a public feed is a few hours. **Validate the cheap archetype first**, then one login portal.
- **Two candidates need a supplier account before anything is visible** (PolyU, CUHK CUPro).
  Without an account such a provider is unbuildable — this gates feasibility more than any
  technical detail.
- **Portal terms of use** need checking per site. HKUST already sits behind reCAPTCHA, i.e.
  the portal is actively distinguishing browsers from automation; that is a decision to take
  deliberately, not a detail.
- **Aggregators are a tempting shortcut** (`5quote.com.hk`, `tendersontime.com` republish many
  universities at once) but they are third parties behind registration walls with some
  placeholder dates. Recommendation: evaluate, and probably do not add them as providers.
- **The `invited` split may not generalise.** `TenderNotice.invited` and the downloadable
  documents encode HKUST's separate invitation table. A feed-only university yields notices
  with `invited=False, documents=[]` always; the interface must tolerate that rather than
  inventing a parallel concept.
- **The enquiry-to-contact parsing may not generalise either.** `_parse_enquiry_details` only
  works because of HKUST's exact row headings. A provider that has no contact simply leaves
  `contact_email` empty — the drafting flow already skips those, which is the right escape
  hatch. `PHONE_REGEX` itself is Hong Kong-wide and does generalise.
- **Fixture drift.** `tests/portal_fixtures.py` is hand-authored HTML injected with
  `page.set_content`. Hand-written fixtures pass while the live parser is broken. There is a
  reuse path: `browser_utils.dump_failure_artifacts` already writes a failure page's HTML to
  `assets/debug/`, so real pages can be captured and committed (credentials stripped) instead
  of invented.
- **Nothing here is verified about the other portals yet.** Their entry points came from a web
  search, not from reading their DOM or feeds. Each needs the same verification pass HKUST got
  before it can be planned concretely.
