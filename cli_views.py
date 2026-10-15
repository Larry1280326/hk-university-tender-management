"""Rich terminal UI views, tables, and presentation helpers."""

from typing import List
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config
from scraper.tender_parser import TenderNotice
from mailer.template import TenderEmailDraft

console = Console()


def display_welcome_banner():
    """Print the stylized welcome banner with company and portal configuration."""
    company_display = config.COMPANY_NAME or "[dim]Not configured[/dim]"
    contact_display = config.CONTACT_PERSON_NAME or "[dim]Not configured[/dim]"
    email_display = config.CONTACT_EMAIL or "[dim]Not configured[/dim]"
    phone_display = config.CONTACT_PHONE or "[dim]Not configured[/dim]"

    banner_text = (
        "[bold cyan]HKUST e-Tendering Portal Automation & Gmail Drafter[/bold cyan]\n"
        f"[green]Company:[/green] {company_display} | [green]Contact:[/green] {contact_display}\n"
        f"[green]Email:[/green] {email_display} | [green]Phone:[/green] {phone_display}\n"
        f"[dim]Portal URL: {config.HKUST_WELCOME_URL}[/dim]"
    )
    console.print(Panel(banner_text, expand=False, border_style="cyan"))


def display_tender_table(tenders: List[TenderNotice]):
    """Render a structured table of active tender notices."""
    table = Table(
        title=f"[bold yellow]Available HKUST Tender Notices ({len(tenders)} New)[/bold yellow]",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Tender No.", style="bold cyan", width=16)
    table.add_column("Description / Subject", style="white", min_width=30)
    table.add_column("Closing Date", style="green", width=20)
    table.add_column("Contact Person", style="yellow", width=22)
    table.add_column("Contact Email", style="blue", width=24)

    for idx, t in enumerate(tenders, start=1):
        table.add_row(
            str(idx),
            t.tender_no,
            t.description,
            t.closing_date or "N/A",
            t.contact_person or "Sir/Madam",
            t.contact_email or "[red]Missing Email[/red]",
        )

    console.print(table)


def display_invited_tenders(tenders: List[TenderNotice]):
    """Render the "My Tender Invitation / Interested Tender(s)" group on its own.

    These rows carry downloadable documents instead of enquiry contact details, so they
    get their own columns rather than sharing the open-tender table.
    """
    table = Table(
        title=(
            "[bold yellow]My Tender Invitation / Interested Tender(s) "
            f"({len(tenders)})[/bold yellow]"
        ),
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Tender No.", style="bold cyan", width=18)
    table.add_column("Description / Subject", style="white", min_width=30)
    table.add_column("Closing Date", style="green", width=20)
    table.add_column("Documents Available", style="blue", width=34)

    for idx, t in enumerate(tenders, start=1):
        if t.documents:
            shown = ", ".join(d.file_name for d in t.documents[:2])
            if len(t.documents) > 2:
                shown += f" [dim](+{len(t.documents) - 2} more)[/dim]"
        else:
            shown = "[yellow]None released[/yellow]"

        table.add_row(
            str(idx),
            t.tender_no,
            t.description,
            t.closing_date or "N/A",
            shown,
        )

    console.print(table)


def display_draft_preview(sample: TenderEmailDraft, count: int):
    """Render a preview card of a generated tender email draft."""
    preview_box = (
        f"[bold]To:[/bold] {sample.recipient_email}\n"
        f"[bold]Subject:[/bold] {sample.subject}\n"
        f"[bold]Attachment:[/bold] {sample.attachment_path or '[yellow]None[/yellow]'}\n\n"
        f"[dim]{sample.body}[/dim]"
    )
    console.print(
        Panel(
            preview_box,
            title=f"Sample Email Preview (1 of {count} selected)",
            border_style="yellow",
        )
    )
