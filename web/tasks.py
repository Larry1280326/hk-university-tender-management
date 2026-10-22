"""Business actions executed inside the worker process.

Nothing here imports Streamlit; the worker passes a ``Reporter`` whose ``log`` and
``progress`` methods write to files the UI polls.
"""

import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import config
from mailer.google_account import GoogleAccountMailer
from mailer.template import generate_tender_email
from scraper import browser_utils
from scraper import sessions
from scraper.CityU.documents import process_cityu_tenders
from scraper.CUHK.documents import process_cuhk_tenders
from scraper.HKUST.documents import download_tender_documents
from web.gemini import build_gemini_prompt, document_paths
from web.serialize import tenders_from_payloads, tenders_to_payloads
from web.zip_utils import zip_directory

UNIVERSITY_LABELS = {"hkust": "HKUST", "cuhk": "CUHK", "cityu": "CityU"}
SESSION_FACTORIES = {
    "hkust": sessions.portal_session,
    "cuhk": sessions.cuhk_portal_session,
    "cityu": sessions.cityu_portal_session,
}
DOWNLOADERS = {
    "hkust": download_tender_documents,
    "cuhk": process_cuhk_tenders,
    "cityu": process_cityu_tenders,
}


def _ensure_chromium(reporter) -> None:
    """Install the browser once per runtime directory.

    ``pip install playwright`` does not download Chromium; a hosted container starts
    without it. Doing this inside the worker keeps the Streamlit script responsive and
    gives the user the installer's output in the job log.
    """
    if os.getenv("PLAYWRIGHT_AUTO_INSTALL", "1").strip().lower() in ("0", "false", "no"):
        return
    marker = Path(config.RUNTIME_DIR) / ".chromium-installed"
    if marker.exists():
        return

    reporter.log("[*] Installing Chromium (first run; this can take a minute)...")
    process = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    output = (process.stdout or "") + (process.stderr or "")
    if process.returncode != 0:
        raise RuntimeError(
            "playwright install chromium failed:\n" + output[-4000:]
        )
    reporter.log(output.strip()[-2000:] or "Chromium installed.")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")


def _university(request: Dict[str, Any]) -> str:
    university = str(request.get("university") or "").lower()
    if university not in SESSION_FACTORIES:
        raise ValueError(f"Unknown university: {university!r}")
    return university


def _workspace(request: Dict[str, Any]) -> Path:
    raw = request.get("workspace")
    if not raw:
        raise ValueError("The request has no workspace directory")
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_scan(request: Dict[str, Any], reporter) -> Dict[str, Any]:
    """Log in, scrape active tenders, and return them as JSON payloads."""
    university = _university(request)
    options = request.get("options") or {}

    existing = set()
    if university == "hkust" and options.get("hide_handled", True):
        mailer = GoogleAccountMailer()
        if mailer.is_configured():
            # Imported lazily so the worker does not drag in the CLI's argparse setup.
            from app import get_processed_tender_numbers

            existing = get_processed_tender_numbers(log=reporter.log, mailer=mailer)

    _ensure_chromium(reporter)
    reporter.update(stage="logging in and scraping", force=True)
    with SESSION_FACTORIES[university](headless=True, log=reporter.log) as (_page, parser):
        tenders = parser.extract_tenders(
            progress_callback=reporter.progress,
            existing_tenders=existing if university == "hkust" else None,
        )

    reporter.log(f"[OK] Found {len(tenders)} tender(s).")
    # ``handled_count`` is the number of rows this scan actually removed, not how many
    # references Gmail held: the two differ whenever the mailbox remembers tenders that
    # have since closed and left the portal listing.
    skipped = len(getattr(parser, "skipped_handled_refs", []) or [])
    return {
        "tenders": tenders_to_payloads(tenders),
        "handled_count": skipped,
        "handled_known_count": len(existing),
        # Only HKUST consults Gmail, so only HKUST has a filter state to report.
        "hide_handled": (
            bool(options.get("hide_handled", True)) if university == "hkust" else None
        ),
    }


def run_download(request: Dict[str, Any], reporter) -> Dict[str, Any]:
    """Open a session and download documents for the selected tenders."""
    university = _university(request)
    label = UNIVERSITY_LABELS[university]
    tenders = tenders_from_payloads(request.get("tenders") or [])
    if not tenders:
        raise ValueError("No tenders were selected")

    workspace = _workspace(request)
    dest_root = workspace / "documents" / label
    dest_root.mkdir(parents=True, exist_ok=True)

    options = request.get("options") or {}
    # HKUST open tenders draft an email; CUHK and CityU submit an EOI. The EOI is the
    # whole step: its documents are fetched later from the invitation project screen.
    eoi_only = bool(options.get("eoi_only")) and university != "hkust"
    submitted: List[str] = []

    _ensure_chromium(reporter)
    if eoi_only:
        stage = f"submitting Expression of Interest for {len(tenders)} tender(s)"
    else:
        stage = f"downloading documents for {len(tenders)} tender(s)"
    reporter.update(stage=stage, force=True)
    with SESSION_FACTORIES[university](headless=True, log=reporter.log) as (page, _parser):
        extra = {"eoi_only": True, "on_eoi": submitted.append} if eoi_only else {}
        saved = DOWNLOADERS[university](
            page,
            tenders,
            dest_root,
            log=reporter.log,
            progress_callback=reporter.progress,
            **extra,
        )

    if eoi_only:
        reporter.log(
            f"[OK] Submitted Expression of Interest for {len(submitted)} tender(s)."
        )
        return {"files": [], "zip": "", "eoi_submitted": submitted}

    # An invitation project is downloaded on its own and offered file by file, so there
    # is nothing to zip. Open-tender downloads keep their single archive.
    archive = None
    if options.get("archive", True):
        archive = zip_directory(
            dest_root, workspace / f"{label.lower()}-documents.zip"
        )
    reporter.log(f"[OK] Downloaded {len(saved)} file(s).")
    result = {
        "files": [str(path.relative_to(workspace)) for path in saved],
        "zip": str(archive) if archive else "",
    }

    # Only invitation project screens ask for a prompt; open-tender downloads do not.
    # The prompt is a bonus: a failure here must never turn a successful download into
    # a failed job.
    if options.get("gemini") and saved:
        try:
            prompt = build_gemini_prompt(
                tenders,
                document_paths(saved, dest_root),
                university=university,
            )
            result["gemini"] = {"prompt": prompt}
        except Exception as exc:  # noqa: BLE001 - the download already succeeded
            reporter.log(f"[!] Gemini prompt not built: {type(exc).__name__}: {exc}")

    return result


def run_draft(request: Dict[str, Any], reporter) -> Dict[str, Any]:
    """Create Gmail drafts (or offline EML fallback) for the selected open tenders."""
    tenders = tenders_from_payloads(request.get("tenders") or [])
    if not tenders:
        raise ValueError("No tenders were selected")

    certificate = Path(config.BR_CERTIFICATE_PATH)
    if not certificate.is_file():
        raise RuntimeError(
            f"BR certificate not found at {certificate}. Upload the BR certificate "
            "from the sidebar before creating drafts."
        )

    draftable = [
        tender for tender in tenders if not tender.invited and tender.contact_email
    ]
    drafts = [
        generate_tender_email(
            tender_no=tender.tender_no,
            description=tender.description,
            recipient_name=tender.contact_person,
            recipient_email=tender.contact_email,
        )
        for tender in draftable
    ]
    if not drafts:
        raise ValueError("None of the selected tenders has a contact email")

    reporter.update(stage=f"creating {len(drafts)} draft(s)")
    # Imported lazily: app pulls in questionary/rich, which the worker does not need
    # until this point.
    from app import draft_emails_for_tenders

    workspace = _workspace(request)
    outcome = draft_emails_for_tenders(
        drafts,
        open_browser=False,
        log=reporter.log,
        export_dir=workspace / "drafts",
    )
    reporter.log(
        f"[OK] Created {outcome.created_count} draft(s); "
        f"{len(outcome.eml_paths)} EML file(s)."
    )
    return {
        "created_count": outcome.created_count,
        "eml_files": [str(path.relative_to(workspace)) for path in outcome.eml_paths],
        "compose_urls": outcome.compose_urls,
        "errors": outcome.errors,
        "skipped_no_recipient": outcome.skipped_no_recipient,
        "draftable_count": len(draftable),
    }


def run_diagnostic(request: Dict[str, Any], reporter) -> Dict[str, Any]:
    """Report whether this host can actually run the workflows."""
    from playwright.sync_api import sync_playwright

    result: Dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "debian": _debian_version(),
        "launch_args": config.PLAYWRIGHT_LAUNCH_ARGS,
        "certificate": bool(Path(config.BR_CERTIFICATE_PATH).is_file()),
    }

    try:
        _ensure_chromium(reporter)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=config.PLAYWRIGHT_LAUNCH_ARGS.split()
                if config.PLAYWRIGHT_LAUNCH_ARGS
                else None,
            )
            result["chromium"] = browser.version
            page = browser.new_page()
            result["portals"] = _probe_portals(page)
            browser.close()
    except Exception as exc:
        result["chromium"] = ""
        result["browser_error"] = f"{type(exc).__name__}: {exc}"

    try:
        result["gmail"] = GoogleAccountMailer().test_connection()
    except Exception as exc:
        result["gmail"] = (False, f"{type(exc).__name__}: {exc}")
    return result


def _debian_version() -> str:
    try:
        return Path("/etc/debian_version").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _probe_portals(page) -> list:
    targets = [
        ("HKUST", config.HKUST_LOGIN_URL, True),
        ("CUHK", config.CUHK_LOGIN_URL, False),
        ("CityU", config.CITYU_LOGIN_URL, False),
    ]
    results = []
    for name, url, check_recaptcha in targets:
        started = time.monotonic()
        entry = {"name": name, "url": url, "ok": False, "error": ""}
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            entry["status"] = response.status if response else None
            entry["final_url"] = page.url
            entry["title"] = page.title()
            entry["ok"] = True
            if check_recaptcha:
                try:
                    browser_utils.wait_for_predicate(
                        page,
                        browser_utils.is_recaptcha_callable,
                        timeout_ms=config.SCRIPT_TIMEOUT_MS,
                        label="the reCAPTCHA library",
                        url=url,
                    )
                    entry["recaptcha_callable"] = True
                except Exception:
                    entry["recaptcha_callable"] = False
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        results.append(entry)
    return results


ACTIONS = {
    "scan": run_scan,
    "download": run_download,
    "draft": run_draft,
    "diagnostic": run_diagnostic,
}


def dispatch(request: Dict[str, Any], reporter) -> Dict[str, Any]:
    kind = str(request.get("kind") or "")
    if kind == "selftest":
        reporter.log("selftest running")
        seconds = float((request.get("options") or {}).get("sleep", 0) or 0)
        if seconds:
            reporter.update(stage="selftest sleep", current=0, total=1, force=True)
            time.sleep(seconds)
        reporter.update(stage="selftest", current=1, total=1, message="ok", force=True)
        return {"ok": True, "kind": kind}
    action = ACTIONS.get(kind)
    if action is None:
        raise ValueError(f"Unknown job kind: {kind!r}")
    return action(request, reporter)
