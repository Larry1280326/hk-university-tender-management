"""Rich terminal UI views and presentation helpers."""

from rich.console import Console
from rich.panel import Panel

import config
from mailer.template import TenderEmailDraft

console = Console()


def display_welcome_banner():
    """Print the stylized welcome banner with company and portal configuration."""
    company_display = config.COMPANY_NAME or "[dim]Not configured[/dim]"
    contact_display = config.CONTACT_PERSON_NAME or "[dim]Not configured[/dim]"
    email_display = config.CONTACT_EMAIL or "[dim]Not configured[/dim]"
    phone_display = config.CONTACT_PHONE or "[dim]Not configured[/dim]"

    banner_text = (
        f"[green]Company:[/green] {company_display} | [green]Contact:[/green] {contact_display}\n"
        f"[green]Email:[/green] {email_display} | [green]Phone:[/green] {phone_display}"
    )
    console.print(Panel(banner_text, expand=False, border_style="cyan"))


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
