"""Main CLI application for HKUST Vendor Automation & Gmail Drafting."""

import argparse
import sys
import webbrowser
from contextlib import contextmanager
from typing import List, Optional, Set

# Ensure standard UTF-8 console output for Windows cmd/PowerShell
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
import questionary

import config
from scraper.auth import HKUSTAuthManager
from scraper.documents import download_tender_documents
from scraper.tender_parser import TenderParser, TenderNotice
from mailer.template import generate_tender_email, TenderEmailDraft
from mailer.google_account import GoogleAccountMailer
from mailer.webmail_helper import generate_gmail_compose_url, export_eml_file
from cli_views import (
    console,
    display_welcome_banner,
    display_tender_table,
    display_invited_tenders,
    display_draft_preview,
)


@contextmanager
def portal_session(headless: bool = True):
    """Yield a logged-in ``(page, parser)`` and close the browser on exit.

    Must never wrap an interactive prompt: ``sync_playwright()`` keeps a running asyncio
    event loop on the main thread for as long as this context is open, and prompt_toolkit
    (which questionary uses) calls ``asyncio.run()``, which refuses to start inside a
    running loop. Every portal_session is therefore entered, used, and exited around a
    single scrape or download.
    """
    auth_mgr = HKUSTAuthManager(headless=headless)

    with sync_playwright() as p:
        console.print("[*] Launching Chromium browser...", style="cyan")
        browser = p.chromium.launch(headless=headless)
        context = auth_mgr.get_context(browser)
        page = context.new_page()

        try:
            if config.HKUST_VENDOR_ID and config.HKUST_PASSWORD:
                console.print(f"[*] Logging in as Vendor ID: {config.HKUST_VENDOR_ID}...", style="cyan")
                auth_mgr.perform_login(page)
            else:
                console.print("[yellow]No login credentials in .env, accessing public page...[/yellow]")

            yield page, TenderParser(page)
        finally:
            context.close()
            browser.close()


def scrape_tenders(
    parser: TenderParser,
    existing_tenders: Optional[Set[str]] = None,
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
        if config.HKUST_DEBUG_ARTIFACTS:
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

    invited = [t for t in tenders if t.invited]
    open_tenders = [t for t in tenders if not t.invited]

    # Shown as two groups: invitations carry documents, open tenders carry contacts.
    if invited:
        display_invited_tenders(invited)
    if open_tenders:
        display_tender_table(open_tenders)

    handle_tender_selection_and_drafting(tenders, headless=headless)


def _tender_choice_title(t: TenderNotice) -> str:
    """Checklist label: tender number, description, and whichever detail fits the group."""
    if t.invited:
        count = len(t.documents)
        detail = f"{count} document{'s' if count != 1 else ''} to download"
    elif t.contact_email:
        named = t.contact_person and t.contact_person != "Sir/Madam"
        detail = f"{t.contact_person} <{t.contact_email}>" if named else f"<{t.contact_email}>"
    elif t.contact_person and t.contact_person != "Sir/Madam":
        detail = t.contact_person
    else:
        detail = ""

    return f"{t.tender_no} — {t.description}" + (f" ({detail})" if detail else "")


def select_suitable_tenders(
    tenders: List[TenderNotice],
    preselected_refs: Optional[set] = None,
) -> List[TenderNotice]:
    """Interactive checklist or select all for tender selection."""
    if not tenders:
        return []

    mode = questionary.select(
        "How would you like to select suitable tenders?",
        choices=[
            "1. Interactive Checklist (pick individual tenders)",
            "2. Select All tenders",
            "3. Cancel / Exit",
        ],
    ).ask()

    if not mode or mode.startswith("3"):
        return []

    if mode.startswith("1"):
        invited = [t for t in tenders if t.invited]
        open_tenders = [t for t in tenders if not t.invited]

        choices = []
        for label, group in (
            ("My Tender Invitation / Interested Tender(s)", invited),
            ("Tender Notice", open_tenders),
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
        selected = questionary.checkbox("Select tenders:", choices=choices).ask()
        return selected or []

    if mode.startswith("2"):
        return tenders

    return []


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

    console.print(f"[*] Downloading documents to [bold]{config.DOCUMENTS_DIR}[/bold]...", style="cyan")
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
        console.print(f"  * {path.name} [dim]({path.parent.name})[/dim]")
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

        if not current_selected:
            console.print("[yellow]No tenders selected.[/yellow]")
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
                "3. Cancel / Exit",
            ],
        ).ask()

        if not action or action.startswith("3"):
            console.print("[yellow]Cancelled draft creation.[/yellow]")
            return

        if action.startswith("2"):
            console.print("[cyan]Returning to tender selection...[/cyan]")
            continue

        draft_emails_for_tenders(drafts)
        break


def draft_emails_for_tenders(drafts: List[TenderEmailDraft]) -> None:
    """Execute draft creation via Google App Password (IMAP) or offline EML fallback."""
    created_count = 0

    # 1. Primary: Google Account App Password (IMAP)
    if config.GMAIL_APP_PASSWORD:
        console.print("[*] Connecting to Gmail via App Password (IMAP)...", style="cyan")
        account_mailer = GoogleAccountMailer()
        for d in drafts:
            if not d.recipient_email:
                console.print(f"[yellow]Skipping {d.tender_no}: No recipient email.[/yellow]")
                continue
            try:
                success, applied_tags = account_mailer.save_draft(d, apply_tags=True)
                if success:
                    created_count += 1
                    desc_str = f" - {d.description}" if d.description else ""
                    console.print(
                        f"  [green][OK][/green] Saved to Gmail 'Drafts' for [bold]{d.tender_no}[/bold]{desc_str} (To: {d.recipient_email})"
                    )
            except Exception as e:
                console.print(f"[!] Note on drafting {d.tender_no} via App Password: {e}", style="yellow")

    # 2. If saved via App Password, open browser
    if created_count > 0:
        console.print(f"\n[bold green]Success! {created_count} draft(s) saved directly to your Gmail 'Drafts' folder.[/bold green]")
        drafts_url = config.GMAIL_DRAFTS_URL
        console.print(f"[*] Opening Gmail Drafts: [bold underline cyan]{drafts_url}[/bold underline cyan]")
        try:
            webbrowser.open(drafts_url)
        except Exception as e:
            console.print(f"[!] Could not launch browser: {e}", style="yellow")
    else:
        # Fallback: Offline .EML files + 1-Click Direct Gmail Compose Web Links
        console.print("[*] Generating fallback offline .EML files and direct Gmail web links...", style="cyan")
        output_dir = config.BASE_DIR / "output_drafts"
        output_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[*] Exporting .eml files to: [bold]{output_dir}[/bold]")

        for d in drafts:
            if not d.recipient_email:
                continue
            eml_path = export_eml_file(d, output_dir)
            web_link = generate_gmail_compose_url(d)
            console.print(f"\n[bold cyan]Tender {d.tender_no}:[/bold cyan]")
            console.print(f"  * EML file: {eml_path.name}")
            console.print(f"  * Direct Gmail Compose URL:\n    {web_link}")

        console.print("\n[green]Done! EML files exported to output_drafts directory.[/green]")


def get_processed_tender_numbers() -> Set[str]:
    """Scan Inbox, Drafts and Sent for tenders already dealt with, and report where each was found.

    Returns just the tender references, which the scraper uses to hide those rows; the
    per-tender mailbox name is printed so a wrong match is visible rather than silent.
    """
    mailer = GoogleAccountMailer()
    if not mailer.is_configured():
        return set()

    days = config.HKUST_MAIL_LOOKBACK_DAYS
    console.print(f"[*] Checking Inbox, Drafts and Sent (last {days} days)...", style="cyan")
    with console.status("[cyan]Scanning mailboxes for already-handled tenders...", spinner="dots"):
        handled = mailer.get_handled_tenders()

    if not handled:
        console.print(
            f"[dim]No tender references found in Inbox, Drafts or Sent in the last "
            f"{days} days.[/dim]"
        )
        return set()

    # Most refs here are old tenders no longer on the portal, so this lists what the mail
    # says rather than claiming each one was hidden; only the still-listed ones are.
    console.print(
        f"[+] [bold green]{len(handled)}[/bold green] tender reference(s) found in your mailboxes"
        f" - any still on the portal are hidden from the list:"
    )
    for ref, sources in sorted(handled.items()):
        console.print(f"      {ref:<16} [yellow]found in: {', '.join(sources)}[/yellow]")

    return set(handled)


def interactive_menu():
    """Display main CLI menu and route user choices."""
    choice = questionary.select(
        "HKUST Workflow - Select an option:",
        choices=[
            "1. Scrape HKUST Tenders & Draft Emails",
            "2. Test Google Account Connection",
            "3. Exit",
        ],
    ).ask()

    if not choice or choice.startswith("3"):
        console.print("[yellow]Exiting.[/yellow]")
        return

    if choice.startswith("1"):
        run_tender_workflow(headless=True)

    elif choice.startswith("2"):
        console.print(f"[*] Testing Google App Password connection for {config.GMAIL_USER}...", style="cyan")
        mailer = GoogleAccountMailer()
        success, msg = mailer.test_connection()
        if success:
            console.print(f"[bold green][OK] {msg}[/bold green]")
        else:
            console.print(f"[bold red][!] {msg}[/bold red]")


def parse_cli_args():
    """Configure and parse command-line flags."""
    parser = argparse.ArgumentParser(
        description="HKUST e-Tendering Portal Automation & Gmail Drafter",
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

    interactive_menu()



if __name__ == "__main__":
    main()
