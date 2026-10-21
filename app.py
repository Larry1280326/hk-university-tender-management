"""Main CLI application for HKUST, CUHK & CityU tender automation and Gmail drafting."""

import argparse
import sys
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

# Ensure standard UTF-8 console output for Windows cmd/PowerShell
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
import questionary

import config
from scraper import sessions as portal_sessions
from scraper.HKUST.documents import download_tender_documents
from scraper.HKUST.parser import TenderParser
from scraper.CUHK.documents import process_cuhk_tenders
from scraper.CityU.documents import process_cityu_tenders
from scraper.models import TenderNotice
from mailer.template import generate_tender_email, TenderEmailDraft
from mailer.email_parser import handled_tender_key
from mailer.google_account import GoogleAccountMailer
from mailer.webmail_helper import generate_gmail_compose_url, export_eml_file
from cli_views import (
    console,
    display_welcome_banner,
    display_draft_preview,
)


@contextmanager
def portal_session(headless: bool = True):
    """Yield a logged-in HKUST ``(page, parser)`` and close the browser on exit."""
    with portal_sessions.portal_session(headless=headless, log=console.print) as pair:
        yield pair


@contextmanager
def cuhk_portal_session(headless: bool = True):
    """Yield a logged-in CUHK ``(page, parser)`` and close the browser on exit."""
    with portal_sessions.cuhk_portal_session(headless=headless, log=console.print) as pair:
        yield pair


@contextmanager
def cityu_portal_session(headless: bool = True):
    """Yield a logged-in CityU ``(page, parser)`` and close the browser on exit."""
    with portal_sessions.cityu_portal_session(headless=headless, log=console.print) as pair:
        yield pair


def scrape_tenders(
    parser: TenderParser,
    existing_tenders: Optional[Set[Tuple[str, str]]] = None,
) -> List[TenderNotice]:
    """Extract both tender tables behind a progress bar."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Scraping tenders...", total=100)

        def update_progress(curr, total, name):
            pct = int(curr / total * 100) if total else 0
            progress.update(task, completed=pct, description=f"[cyan]Parsing: {name}")

        return parser.extract_tenders(
            progress_callback=update_progress,
            existing_tenders=existing_tenders,
        )


def run_tender_workflow(headless: bool = True) -> None:
    """Scrape, show both groups, select, optionally download documents, then draft."""
    existing_tenders = get_processed_tender_numbers()
    mode = "headless" if headless else "visible"
    console.print(f"[*] Directly fetching all active tenders ({mode})...", style="cyan")

    # The session covers the scrape and nothing else: prompts must not run inside it,
    # or questionary's asyncio.run() collides with Playwright's event loop.
    try:
        with portal_session(headless=headless) as (page, parser):
            tenders = scrape_tenders(parser, existing_tenders)
    except Exception as e:
        console.print(f"[bold red]Scraper Error:[/bold red] {type(e).__name__}: {e}")
        if config.DEBUG_ARTIFACTS:
            console.print_exception(show_locals=False)
        return

    if not tenders:
        if existing_tenders:
            console.print(
                "\n[bold green][OK] All active tenders on the HKUST portal have already been drafted/sent in Gmail! No new tenders to process.[/bold green]"
            )
        else:
            console.print("[yellow]No active tenders found or failed to fetch.[/yellow]")
        return

    handle_tender_selection_and_drafting(tenders, headless=headless)


def _tender_choice_title(t: TenderNotice) -> str:
    """Checklist label: tender number, description, and whichever detail fits the group."""
    if t.invited:
        count = len(t.documents)
        detail = f"{count} document{'s' if count != 1 else ''} to download"
    else:
        if t.contact_email:
            named = t.contact_person and t.contact_person != "Sir/Madam"
            contact_str = (
                f"{t.contact_person} <{t.contact_email}>"
                if named
                else f"<{t.contact_email}>"
            )
        elif t.contact_person and t.contact_person != "Sir/Madam":
            contact_str = t.contact_person
        else:
            contact_str = ""

        if t.needs_eoi:
            detail = (
                f"Submit EOI & download — {contact_str}"
                if contact_str
                else "Submit EOI & download"
            )
        else:
            detail = contact_str

    return f"{t.tender_no} — {t.description}" + (f" ({detail})" if detail else "")


def select_suitable_tenders(
    tenders: List[TenderNotice],
    preselected_refs: Optional[set] = None,
    invited_heading: str = "My Tender Invitation / Interested Tender(s)",
    open_heading: str = "Tender Notice",
) -> Optional[List[TenderNotice]]:
    """Interactive checklist for tender selection.

    Returns the chosen tenders, an empty list when the checklist was confirmed with
    nothing ticked, and None when the prompt was cancelled/interrupted. The caller
    reports the last two differently, so they must not collapse into one value.
    """
    if not tenders:
        return None

    invited = [t for t in tenders if t.invited]
    open_tenders = [t for t in tenders if not t.invited]

    choices = []
    for label, group in (
        (invited_heading, invited),
        (open_heading, open_tenders),
    ):
        if not group:
            continue
        # Only separating when both groups exist; a lone group needs no heading.
        if invited and open_tenders:
            choices.append(questionary.Separator(f"── {label} ──"))
        for t in group:
            is_checked = bool(preselected_refs and t.tender_no in preselected_refs)
            choices.append(
                questionary.Choice(
                    title=_tender_choice_title(t),
                    value=t,
                    checked=is_checked,
                )
            )

    console.print("[dim]Tip: Space to toggle, 'a' to select all, Enter to confirm.[/dim]")
    return questionary.checkbox("Select tenders:", choices=choices).ask()


def _offer_document_download(
    invited_tenders: List[TenderNotice],
    downloaded_refs: Set[str],
    headless: bool = True,
) -> None:
    """Ask whether to download invited tenders' documents, then fetch each one once.

    Opens its own short session, because the scrape session is closed before any prompt
    (see :func:`portal_session`) and the decision is only known after the user has chosen.
    """
    pending = [t for t in invited_tenders if t.tender_no not in downloaded_refs]

    if not pending:
        console.print(
            "[dim]Documents for the selected invited tender(s) were already downloaded "
            "in this run.[/dim]"
        )
        return

    count = len(pending)
    if not questionary.confirm(
        f"Download tender documents for {count} invited tender(s)?", default=True
    ).ask():
        console.print("[yellow]Skipped document download.[/yellow]")
        return

    hkust_docs_dir = config.DOCUMENT_DIR / "HKUST"
    console.print(
        f"[*] Downloading documents to [bold]{hkust_docs_dir}[/bold]...", style="cyan"
    )
    try:
        with portal_session(headless=headless) as (page, _parser):
            saved = download_tender_documents(page, pending)
    except Exception as e:
        # A failed download must not abandon the tenders that are still to be drafted.
        console.print(f"[bold red]Document download failed:[/bold red] {type(e).__name__}: {e}")
        return

    if not saved:
        console.print("[yellow]No documents were downloaded.[/yellow]")
        return

    console.print(f"[bold green][OK] Downloaded {len(saved)} document(s):[/bold green]")
    for path in saved:
        console.print(f"  * {path.name} [dim](HKUST/{path.parent.name})[/dim]")
    downloaded_refs.update(t.tender_no for t in pending)


def handle_tender_selection_and_drafting(
    tenders: List[TenderNotice],
    headless: bool = True,
) -> None:
    """Loop between selection, download, preview, and drafting with back-navigation."""
    if not tenders:
        return

    current_selected = []
    downloaded_refs: Set[str] = set()
    while True:
        preselected_refs = {t.tender_no for t in current_selected} if current_selected else None
        current_selected = select_suitable_tenders(tenders, preselected_refs=preselected_refs)

        if current_selected is None:
            console.print("[cyan]Returning to the main menu...[/cyan]")
            return

        if not current_selected:
            console.print("[yellow]No tenders selected - returning to the main menu.[/yellow]")
            return

        # Invitations carry documents; only open tenders have a contact to email.
        invited_selected = [t for t in current_selected if t.invited]
        draftable = [t for t in current_selected if not t.invited]

        if invited_selected:
            _offer_document_download(invited_selected, downloaded_refs, headless=headless)

        if not draftable:
            console.print(
                "[yellow]No open tenders selected - nothing to draft an enquiry email for.[/yellow]"
            )
            return

        # The body promises the BR certificate, so a missing one stops the flow here
        # rather than producing drafts that reference an attachment they do not carry.
        if not config.BR_CERTIFICATE_PATH.is_file():
            console.print(
                f"[bold red][!] BR certificate not found:[/bold red] "
                f"{config.BR_CERTIFICATE_PATH}\n"
                "[red]Point BR_CERTIFICATE_PATH in .env at the file - a draft must not "
                "promise an attachment it does not carry.[/red]"
            )
            return

        drafts: List[TenderEmailDraft] = [
            generate_tender_email(
                tender_no=t.tender_no,
                description=t.description,
                recipient_name=t.contact_person,
                recipient_email=t.contact_email,
            )
            for t in draftable
        ]

        # Display preview card of sample draft
        console.print("\n[bold yellow]Draft Preview (Sample):[/bold yellow]")
        display_draft_preview(drafts[0], count=len(drafts))

        action = questionary.select(
            f"Ready to create drafts for {len(drafts)} selected tenders?",
            choices=[
                f"1. Yes, Create {len(drafts)} Drafts in Gmail",
                "2. Back to Selection (Modify which tenders are selected)",
                "3. Back to Main Menu",
            ],
        ).ask()

        if not action or action.startswith("3"):
            console.print("[cyan]Returning to the main menu...[/cyan]")
            return

        if action.startswith("2"):
            console.print("[cyan]Returning to tender selection...[/cyan]")
            continue

        draft_emails_for_tenders(drafts)
        break


@dataclass
class DraftOutcome:
    """What a drafting run produced, so a caller can report it without parsing stdout."""

    created_count: int = 0
    eml_paths: List[Path] = field(default_factory=list)
    compose_urls: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped_no_recipient: List[str] = field(default_factory=list)


def draft_emails_for_tenders(
    drafts: List[TenderEmailDraft],
    *,
    open_browser: bool = True,
    log=console.print,
    export_dir: Optional[Path] = None,
) -> DraftOutcome:
    """Execute draft creation via Google App Password (IMAP) or offline EML fallback."""
    outcome = DraftOutcome()

    # 1. Primary: Google Account App Password (IMAP)
    if config.GMAIL_APP_PASSWORD:
        log("[*] Connecting to Gmail via App Password (IMAP)...")
        account_mailer = GoogleAccountMailer()
        for d in drafts:
            if not d.recipient_email:
                outcome.skipped_no_recipient.append(d.tender_no)
                log(f"Skipping {d.tender_no}: No recipient email.")
                continue
            try:
                success, applied_tags = account_mailer.save_draft(d, apply_tags=True)
                if success:
                    outcome.created_count += 1
                    desc_str = f" - {d.description}" if d.description else ""
                    log(
                        f"  [OK] Saved to Gmail 'Drafts' for {d.tender_no}{desc_str} "
                        f"(To: {d.recipient_email})"
                    )
            except Exception as e:
                message = f"Note on drafting {d.tender_no} via App Password: {e}"
                outcome.errors.append(message)
                log(f"[!] {message}")

    # 2. If saved via App Password, open browser
    if outcome.created_count > 0:
        log(
            f"Success! {outcome.created_count} draft(s) saved directly to your Gmail "
            "'Drafts' folder."
        )
        drafts_url = config.GMAIL_DRAFTS_URL
        log(f"[*] Gmail Drafts: {drafts_url}")
        if open_browser:
            try:
                webbrowser.open(drafts_url)
            except Exception as e:
                log(f"[!] Could not launch browser: {e}")
    else:
        # Fallback: Offline .EML files + 1-Click Direct Gmail Compose Web Links
        log("[*] Generating fallback offline .EML files and direct Gmail web links...")
        output_dir = Path(export_dir or (config.BASE_DIR / "output_drafts"))
        output_dir.mkdir(parents=True, exist_ok=True)
        log(f"[*] Exporting .eml files to: {output_dir}")

        for d in drafts:
            if not d.recipient_email:
                outcome.skipped_no_recipient.append(d.tender_no)
                continue
            eml_path = export_eml_file(d, output_dir)
            web_link = generate_gmail_compose_url(d)
            outcome.eml_paths.append(eml_path)
            outcome.compose_urls.append(web_link)
            log(f"Tender {d.tender_no}:")
            log(f"  * EML file: {eml_path.name}")
            log(f"  * Direct Gmail Compose URL: {web_link}")

        log("Done! EML files exported to the output_drafts directory.")

    return outcome


def get_processed_tender_numbers(*, log=console.print, mailer=None) -> Set[Tuple[str, str]]:
    """Scan Inbox, Drafts and Sent for tenders already dealt with.

    Returns ``(slug, normalized_ref)`` keys, which the scraper uses to hide those rows.
    The slug keeps a reference handled at one university from hiding another university's
    identically numbered tender.
    """
    mailer = mailer or GoogleAccountMailer()
    if not mailer.is_configured():
        return set()

    days = config.MAIL_LOOKBACK_DAYS
    log(f"[*] Checking Inbox, Drafts and Sent (last {days} days)...")
    log("Scanning mailboxes for already-handled tenders...")
    handled = mailer.get_handled_tenders()

    if not handled:
        log(
            f"No tender references found in Inbox, Drafts or Sent in the last "
            f"{days} days."
        )
        return set()

    # Most refs here are old tenders no longer on the portal, so this reports the count
    # found in mail rather than claiming each one was hidden; only the still-listed ones are.
    log(
        f"{len(handled)} tender reference(s) found in your mailboxes"
        f" - any still on the portal are hidden from the list."
    )

    return {handled_tender_key(TenderParser.SLUG, ref) for ref in handled}


def handle_cuhk_selection_and_download(
    tenders: List[TenderNotice],
    headless: bool = True,
) -> None:
    """Interactive selection, EOI submission, and document download for CUHK tenders."""
    if not tenders:
        return

    selected = select_suitable_tenders(
        tenders,
        invited_heading="Expression of Interest Submitted / Invited",
        open_heading="Open Tender Notice (Submit EOI & Download)",
    )

    if selected is None:
        console.print("[cyan]Returning to the main menu...[/cyan]")
        return

    if not selected:
        console.print("[yellow]No tenders selected - returning to the main menu.[/yellow]")
        return

    eoi_needed = [t for t in selected if t.needs_eoi]
    total_count = len(selected)
    if eoi_needed:
        prompt_msg = (
            f"Submit EOI for {len(eoi_needed)} open tender(s) and download documents "
            f"for {total_count} selected tender(s)?"
        )
    else:
        prompt_msg = f"Download tender documents for {total_count} selected tender(s)?"

    if not questionary.confirm(prompt_msg, default=True).ask():
        console.print("[yellow]Skipped CUHK tender processing.[/yellow]")
        return

    cuhk_docs_dir = config.DOCUMENT_DIR / "CUHK"
    console.print(
        f"[*] Processing CUHK tender(s) and downloading documents to "
        f"[bold]{cuhk_docs_dir}[/bold]...",
        style="cyan",
    )
    try:
        with cuhk_portal_session(headless=headless) as (page, _parser):
            saved = process_cuhk_tenders(page, selected)
    except Exception as e:
        console.print(f"[bold red]CUHK processing failed:[/bold red] {type(e).__name__}: {e}")
        return

    if not saved:
        console.print("[yellow]No documents were downloaded.[/yellow]")
        return

    console.print(f"[bold green][OK] Downloaded {len(saved)} document(s):[/bold green]")
    for path in saved:
        console.print(f"  * {path.name} [dim](CUHK/{path.parent.name})[/dim]")


def run_cuhk_workflow(headless: bool = True) -> None:
    """Scrape active CUHK RFQs & Tenders, select, submit EOI if needed, and download."""
    if not config.CUHK_USER_ID or not config.CUHK_PASSWORD:
        console.print(
            "[bold red][!] CUHK credentials missing![/bold red] "
            "Please configure CUHK_USER_ID and CUHK_PASSWORD in your .env file."
        )
        return

    mode = "headless" if headless else "visible"
    console.print(f"[*] Fetching active CUHK RFQs and Tenders ({mode})...", style="cyan")

    try:
        with cuhk_portal_session(headless=headless) as (_page, parser):
            tenders = scrape_tenders(parser)
    except Exception as e:
        console.print(f"[bold red]CUHK Scraper Error:[/bold red] {type(e).__name__}: {e}")
        if config.DEBUG_ARTIFACTS:
            console.print_exception(show_locals=False)
        return

    if not tenders:
        console.print("[yellow]No active CUHK tenders or RFQs found.[/yellow]")
        return

    handle_cuhk_selection_and_download(tenders, headless=headless)


def handle_cityu_selection_and_download(
    tenders: List[TenderNotice],
    headless: bool = True,
) -> None:
    """Interactive selection, EOI submission, and document download for CityU tenders."""
    if not tenders:
        return

    selected = select_suitable_tenders(
        tenders,
        invited_heading="Expression of Interest Submitted / Invited",
        open_heading="Open Tender Notice (Submit EOI & Download)",
    )

    if selected is None:
        console.print("[cyan]Returning to the main menu...[/cyan]")
        return

    if not selected:
        console.print("[yellow]No tenders selected - returning to the main menu.[/yellow]")
        return

    eoi_needed = [t for t in selected if t.needs_eoi]
    total_count = len(selected)
    if eoi_needed:
        prompt_msg = (
            f"Submit EOI for {len(eoi_needed)} open tender(s) and download documents "
            f"for {total_count} selected tender(s)?"
        )
    else:
        prompt_msg = f"Download tender documents for {total_count} selected tender(s)?"

    if not questionary.confirm(prompt_msg, default=True).ask():
        console.print("[yellow]Skipped CityU tender processing.[/yellow]")
        return

    cityu_docs_dir = config.DOCUMENT_DIR / "CityU"
    console.print(
        f"[*] Processing CityU tender(s) and downloading documents to "
        f"[bold]{cityu_docs_dir}[/bold]...",
        style="cyan",
    )
    try:
        with cityu_portal_session(headless=headless) as (page, _parser):
            saved = process_cityu_tenders(page, selected)
    except Exception as e:
        console.print(f"[bold red]CityU processing failed:[/bold red] {type(e).__name__}: {e}")
        return

    if not saved:
        console.print("[yellow]No documents were downloaded.[/yellow]")
        return

    console.print(f"[bold green][OK] Downloaded {len(saved)} document(s):[/bold green]")
    for path in saved:
        console.print(f"  * {path.name} [dim](CityU/{path.parent.name})[/dim]")


def run_cityu_workflow(headless: bool = True) -> None:
    """Scrape active CityU RFQs & Tenders, select, submit EOI if needed, and download."""
    if not config.CITYU_USER_ID or not config.CITYU_PASSWORD:
        console.print(
            "[bold red][!] CityU credentials missing![/bold red] "
            "Please configure CITYU_USER_ID and CITYU_PASSWORD in your .env file."
        )
        return

    mode = "headless" if headless else "visible"
    console.print(f"[*] Fetching active CityU RFQs and Tenders ({mode})...", style="cyan")

    try:
        with cityu_portal_session(headless=headless) as (_page, parser):
            tenders = scrape_tenders(parser)
    except Exception as e:
        console.print(f"[bold red]CityU Scraper Error:[/bold red] {type(e).__name__}: {e}")
        if config.DEBUG_ARTIFACTS:
            console.print_exception(show_locals=False)
        return

    if not tenders:
        console.print("[yellow]No active CityU tenders or RFQs found.[/yellow]")
        return

    handle_cityu_selection_and_download(tenders, headless=headless)


def interactive_menu() -> bool:
    """Show the main menu once and route the choice.

    Returns whether the menu should be shown again, so that a run the user cancels
    lands back here instead of taking the whole program down with it: only Exit ends it.
    """
    choice = questionary.select(
        "Select an option:",
        choices=[
            "1. HKUST",
            "2. CUHK",
            "3. CityU",
            "4. Test Google Account Connection",
            "5. Exit",
        ],
    ).ask()

    # questionary answers None when the prompt itself is interrupted, which is also a
    # request to leave rather than to carry on.
    if not choice or choice.startswith("5"):
        console.print("[yellow]Exiting.[/yellow]")
        return False

    if choice.startswith("1"):
        run_tender_workflow(headless=True)

    elif choice.startswith("2"):
        run_cuhk_workflow(headless=True)

    elif choice.startswith("3"):
        run_cityu_workflow(headless=True)

    elif choice.startswith("4"):
        console.print(f"[*] Testing Google App Password connection for {config.GMAIL_USER}...", style="cyan")
        mailer = GoogleAccountMailer()
        success, msg = mailer.test_connection()
        if success:
            console.print(f"[bold green][OK] {msg}[/bold green]")
        else:
            console.print(f"[bold red][!] {msg}[/bold red]")

    return True


def parse_cli_args():
    """Configure and parse command-line flags."""
    parser = argparse.ArgumentParser(
        description="University e-Tendering Portal Automation & Gmail Drafter",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--test", action="store_true", help="Test Google IMAP / App Password connection")
    parser.add_argument("--visible", action="store_true", help="Run scraper with visible Chromium browser")
    return parser.parse_args()


def main():
    display_welcome_banner()
    args = parse_cli_args()

    if args.test:
        if config.GMAIL_APP_PASSWORD:
            console.print(f"[*] Testing Google App Password connection for {config.GMAIL_USER}...", style="cyan")
            mailer = GoogleAccountMailer()
            success, msg = mailer.test_connection()
            status_style = "bold green" if success else "bold red"
            prefix = "[OK]" if success else "[!]"
            console.print(f"[{status_style}]{prefix} {msg}[/{status_style}]")
        return

    if args.visible:
        run_tender_workflow(headless=False)
        return

    # One process, many actions: cancelling a scrape returns to this menu rather than
    # closing the program, and only Exit unwinds the loop.
    while interactive_menu():
        pass



if __name__ == "__main__":
    main()
