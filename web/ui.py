"""Streamlit views for the three universities, job progress, and diagnostics."""

import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st

import config
from mailer.template import generate_tender_email
from web.auth import AuthResult
from web.bootstrap import certificate_fingerprint
from web.downloads import download_mime, downloadable_files
from web.gemini_bridge import BRIDGE_HEIGHT, render_gemini_bridge
from web.jobs import JobError, JobManager, TERMINAL_STATUSES
from web.select_logic import (
    draftable_tenders,
    eoi_candidates,
    selected_tenders,
    skipped_for_missing_email,
    split_groups,
)
from web.serialize import tenders_from_payloads, tenders_to_payloads
from web.secrets_bridge import (
    CertificateError,
    validate_certificate_bytes,
    write_certificate,
)

UNIVERSITIES = {
    "hkust": "HKUST",
    "cuhk": "CUHK",
    "cityu": "CityU",
}


def _manager() -> JobManager:
    manager = st.session_state.get("_job_manager")
    if manager is None:
        manager = JobManager()
        st.session_state["_job_manager"] = manager
    return manager


def _workspace() -> Path:
    raw = st.session_state.get("_workspace")
    if not raw:
        raw = str(Path(config.RUNTIME_DIR) / "workspaces" / uuid.uuid4().hex)
        Path(raw).mkdir(parents=True, exist_ok=True)
        st.session_state["_workspace"] = raw
    return Path(raw)


def _active_job_id() -> str:
    return str(st.session_state.get("active_job") or "")


def _certificate_ready() -> bool:
    return Path(config.BR_CERTIFICATE_PATH).is_file()


def _store_uploaded_certificate(uploaded) -> tuple:
    """Validate a sidebar upload and make it the attachment for subsequent drafts."""
    data = uploaded.getvalue()
    if not data.startswith(b"%PDF-"):
        return None, "Please upload the BR certificate as a PDF."
    try:
        validate_certificate_bytes(data)
    except CertificateError as exc:
        return None, str(exc)

    path = write_certificate(data, Path(config.ASSETS_DIR))
    config.BR_CERTIFICATE_PATH = path
    os.environ["BR_CERTIFICATE_PATH"] = str(path)
    os.environ.pop("BR_CERTIFICATE_ERROR", None)
    return path, ""


def _handle_br_upload() -> None:
    """Store the selected PDF as soon as the uploader changes; no submit button."""
    uploaded = st.session_state.get("br_upload")
    if uploaded is None:
        return
    path, error = _store_uploaded_certificate(uploaded)
    if error:
        st.session_state["br_upload_error"] = error
    elif path:
        st.session_state.pop("br_upload_error", None)


def _render_br_upload() -> None:
    st.sidebar.subheader("Documents")
    if not _certificate_ready():
        st.sidebar.warning("Not uploaded — drafts are disabled")

    error = st.session_state.pop("br_upload_error", None)
    if error:
        st.sidebar.error(error)

    st.sidebar.file_uploader(
        "Upload BR certificate (PDF)",
        type=["pdf"],
        key="br_upload",
        on_change=_handle_br_upload,
        help=(
            "Used as the attachment for Gmail drafts created in this running app. "
            "It is not uploaded anywhere else."
        ),
    )


def _start_job(
    kind: str,
    university: str,
    *,
    options: Optional[Dict] = None,
    tenders: Optional[List] = None,
    group: str = "",
) -> bool:
    try:
        job = _manager().start(
            kind,
            university=university,
            options=options,
            tenders=tenders or [],
            workspace=_workspace(),
            started_by=str(st.session_state.get("auth_name") or ""),
        )
    except JobError as exc:
        st.error(str(exc))
        return False
    if kind == "scan":
        # A fresh scan must not leave the previous list on screen, including a project
        # screen opened from it and anything fetched for that project.
        st.session_state.pop(f"scan_result_{university}", None)
        st.session_state.pop(_project_ref_key(university), None)
        st.session_state.pop(_project_click_key(university), None)
        st.session_state.pop(f"fetched_refs_{university}_project", None)
        st.session_state.pop(f"scan_result_download_{university}_project", None)
        st.session_state.pop(f"scan_result_download_{university}_project_error", None)
        for group in ("invited", "open"):
            prefix = f"select_{university}_{group}"
            for key in list(st.session_state.keys()):
                if key.startswith(prefix):
                    st.session_state.pop(key, None)
    elif kind == "download":
        suffix = f"_{group}" if group else ""
        # The button changes back to Fetch while a new package is being built.
        st.session_state.pop(f"scan_result_download_{university}{suffix}", None)
        st.session_state.pop(
            f"scan_result_download_{university}{suffix}_error", None
        )
        st.session_state[f"fetched_refs_{university}{suffix}"] = sorted(
            str(tender.get("tender_no") or "")
            for tender in (tenders or [])
            if tender.get("tender_no")
        )
    elif kind == "draft":
        st.session_state.pop(f"scan_result_draft_{university}", None)
        st.session_state.pop(f"scan_result_draft_{university}_error", None)
    st.session_state["active_job"] = job.job_id
    st.session_state["active_university"] = university
    st.session_state["active_kind"] = kind
    st.session_state["active_group"] = group
    # The current run already evaluated "is a job active?" before this button was
    # clicked, so without a rerun the page keeps showing the Scan button.
    st.rerun()
    return True


def _active_scope() -> str:
    return str(st.session_state.get("active_university") or "diagnostic")


def _render_log_expander(job_id: str) -> None:
    job = _manager().get(job_id)
    with st.expander("Job log", expanded=False):
        st.code(job.log_tail() if job else "", language="text")


@st.fragment(run_every=2)
def _render_active_log(job_id: str) -> None:
    _render_log_expander(job_id)


def _render_log_section(scope: str) -> None:
    """Show the log only while a job is running.

    Once the job finishes, the active job is cleared and the scan tables take over; the
    log is intentionally not kept around underneath them.
    """
    active_id = _active_job_id()
    if active_id and _active_scope() == scope:
        _render_active_log(active_id)


@st.fragment(run_every=2)
def _render_job(
    job_id: str,
    university: str,
    kind: str,
    result_key: str,
    show_progress: bool = True,
) -> None:
    manager = _manager()
    state = manager.poll(job_id)
    status = str(state.get("status") or "unknown")

    if show_progress:
        progress = state.get("progress") or {}
        current = int(progress.get("current") or 0)
        total = int(progress.get("total") or 0)
        fraction = min(1.0, current / total) if total else 0.0
        st.progress(
            fraction,
            text=(
                f"{status} · {state.get('stage', '')} · "
                f"{progress.get('message', '')}"
            ).strip(),
        )
        if status not in TERMINAL_STATUSES and kind not in ("scan", "download", "draft"):
            if st.button("Cancel job", key=f"cancel_{job_id}"):
                manager.cancel(job_id)
                st.rerun()

    if status in TERMINAL_STATUSES:
        st.session_state[result_key] = state.get("result")
        st.session_state[f"{result_key}_error"] = state.get("error") or ""
        st.session_state.pop("active_job", None)
        # Leaving these behind makes the next run believe a download/draft is still
        # active, so it reads the wrong result key and the scan table disappears.
        st.session_state.pop("active_kind", None)
        st.session_state.pop("active_university", None)
        st.session_state.pop("active_group", None)
        # Default rerun scope is "fragment" inside a fragment, which would re-render
        # this finished job instead of the results below it.
        st.rerun(scope="app")


def _app_url() -> str:
    """The page URL, used to build an absolute link to the static bridge page."""
    return str(getattr(st.context, "url", "") or "").rstrip("/")


def _render_zip_button(zip_path: str, key: str) -> None:
    if zip_path and Path(zip_path).is_file():
        with open(zip_path, "rb") as handle:
            st.download_button(
                "Download ZIP",
                handle,
                file_name=Path(zip_path).name,
                key=f"zip_{key}",
                type="primary",
            )
    else:
        st.info("No documents were found for that selection.")


def _render_download_result(result: Optional[Dict], key: str) -> None:
    zip_path = (result or {}).get("zip") or ""
    prompt = ((result or {}).get("gemini") or {}).get("prompt") or ""

    if zip_path and Path(zip_path).is_file():
        if prompt:
            # The browser button lives in an iframe, so it cannot share a row with a
            # Streamlit widget except through columns. A narrow first column keeps the
            # two controls side by side instead of half a page apart.
            gemini_column, zip_column = st.columns([1, 4])
            with gemini_column:
                st.components.v1.html(
                    render_gemini_bridge(prompt, app_url=_app_url()),
                    height=BRIDGE_HEIGHT,
                )
            with zip_column:
                _render_zip_button(zip_path, key)
        else:
            _render_zip_button(zip_path, key)
    else:
        # Project downloads are offered file by file, with no ZIP to unpack.
        if prompt:
            st.components.v1.html(
                render_gemini_bridge(prompt, app_url=_app_url()),
                height=BRIDGE_HEIGHT,
            )
        files = downloadable_files((result or {}).get("files") or [], _workspace())
        if files:
            st.markdown(f"**Documents ({len(files)})**")
            for index, path in enumerate(files):
                with open(path, "rb") as handle:
                    st.download_button(
                        f":material/download: {path.name}",
                        handle,
                        file_name=path.name,
                        key=f"file_{key}_{index}",
                        mime=download_mime(path),
                    )
        else:
            st.info("No documents were found for that selection.")

    if prompt:
        with st.expander("Gemini prompt"):
            st.code(prompt, language="markdown")


def _render_draft_result(result: Optional[Dict], key: str) -> None:
    if not result:
        return
    created = result.get("created_count", 0)
    if created:
        st.success(f"Created {created} Gmail draft(s). Open Gmail Drafts to review them.")
        st.link_button("Open Gmail Drafts", config.GMAIL_DRAFTS_URL)
    else:
        st.warning("No Gmail draft was created (App Password not configured).")
        urls = result.get("compose_urls") or []
        for url in urls:
            st.markdown(f"- [Open prefilled Gmail compose]({url})")
    for error in result.get("errors") or []:
        st.warning(error)
    skipped = result.get("skipped_no_recipient") or []
    if skipped:
        st.info("Skipped (no recipient email): " + ", ".join(skipped))


def _selection_table(tenders, key: str) -> List[str]:
    """Tender selection grid.

    The old Details column repeated "tender number — description", which forced the grid
    wider than the page. The remaining columns use compact widths so the editor fits the
    container without a horizontal scrollbar on a normal laptop.
    """
    rows = [
        {
            "Select": False,
            "Tender": tender.tender_no,
            "Description": tender.description,
            "Closing": tender.closing_date,
        }
        for tender in tenders
    ]
    edited = st.data_editor(
        rows,
        key=key,
        hide_index=True,
        width="stretch",
        column_config={
            "Select": st.column_config.CheckboxColumn(
                "Select", default=False, width="small"
            ),
            "Tender": st.column_config.TextColumn("Tender", width="small"),
            "Description": st.column_config.TextColumn("Description", width="medium"),
            "Closing": st.column_config.TextColumn("Closing", width="small"),
        },
        disabled=["Tender", "Description", "Closing"],
    )
    return [row["Tender"] for row in edited if row.get("Select")]


def _project_ref_key(university: str) -> str:
    """The session key holding the invitation project currently open in this tab."""
    return f"{university}_invitation_ref"


def _project_click_key(university: str) -> str:
    """The dataframe key holding the arrow click state for this tab.

    All three university tabs render on every run, so these keys must not be shared.
    """
    return f"open_{university}_invitation"


def _project_ref_for_click(tender_refs: tuple, click) -> Optional[str]:
    """Turn an arrow click into a tender reference.

    ``row`` is the position in the rows passed to the dataframe, which is the same order
    as ``tender_refs``; sorting the grid does not change it.
    """
    row = getattr(click, "row", None)
    if row is None:
        return None
    try:
        index = int(row)
    except (TypeError, ValueError):
        return None
    if 0 <= index < len(tender_refs):
        return tender_refs[index]
    return None


def _select_invitation_project(university: str, tender_refs: tuple) -> None:
    """Open the project whose arrow was clicked in this university's table."""
    ref = _project_ref_for_click(
        tender_refs, st.session_state.get(_project_click_key(university))
    )
    if ref is not None:
        st.session_state[_project_ref_key(university)] = ref


def _invitation_table(university: str, tenders) -> None:
    """Invitations: read-only rows, each with an arrow that opens the project.

    Only one project is handled at a time, so there is no Select column and no shared
    Fetch button; the project screen owns both.
    """
    tender_refs = tuple(tender.tender_no for tender in tenders)
    rows = [
        {
            "Tender": tender.tender_no,
            "Description": tender.description,
            "Closing": tender.closing_date,
            "Open": ":material/arrow_forward:",
        }
        for tender in tenders
    ]
    st.dataframe(
        rows,
        key=f"invitations_{university}",
        hide_index=True,
        width="stretch",
        column_config={
            "Tender": st.column_config.TextColumn("Tender", width="small"),
            "Description": st.column_config.TextColumn("Description", width="medium"),
            "Closing": st.column_config.TextColumn("Closing", width="small"),
            "Open": st.column_config.ButtonColumn(
                "",
                type="tertiary",
                width="small",
                alignment="right",
                on_click=_select_invitation_project,
                args=(university, tender_refs),
                key=_project_click_key(university),
            ),
        },
    )


def _render_draft_action(university: str, selected_open: List, busy: bool) -> None:
    """The Gmail button that belongs directly under the open-tender table."""
    if not selected_open:
        st.info("Tick one or more open tenders to create drafts.")
        return

    draftable = draftable_tenders(selected_open)
    skipped = skipped_for_missing_email(selected_open)
    if skipped:
        st.warning("No contact email, will be skipped: " + ", ".join(skipped))
    if not draftable:
        st.info("No selected tender has a contact email to draft to.")
        return

    if not _certificate_ready():
        # No attachment means no draft: stop here rather than creating a draft whose
        # body promises a certificate it does not carry.
        st.warning(
            "Attach the BR certificate first. Use **Documents → Upload BR certificate** "
            "in the sidebar; the draft button appears once it is saved."
        )
        return

    sample = draftable[0]
    draft = generate_tender_email(
        tender_no=sample.tender_no,
        description=sample.description,
        recipient_name=sample.contact_person,
        recipient_email=sample.contact_email,
    )
    st.caption(f"{len(draftable)} draftable open tender(s) selected.")
    with st.expander("Draft preview (first selected tender)"):
        st.text(f"To: {draft.recipient_email}")
        st.text(f"Subject: {draft.subject}")
        st.text(draft.body)

    if st.button(
        f"Create {len(draftable)} Gmail draft(s)",
        key=f"draft_{university}",
        disabled=busy,
    ):
        _start_job(
            "draft",
            university,
            tenders=tenders_to_payloads(draftable),
        )


def _render_process_action(
    university: str, selected: List, busy: bool, group: str = ""
) -> None:
    """The EOI/download control for one CUHK/CityU open-tender table.

    Fetch → Fetching… → Download ZIP. Each table gets its own group so its package and
    button state are independent. Invitation projects use ``_render_fetch_action``
    instead, one project at a time.
    """
    suffix = f"_{group}" if group else ""
    key = f"scan_result_download_{university}{suffix}"
    error = st.session_state.pop(f"{key}_error", None)
    if error:
        st.error(error)

    if _download_active(university, group):
        _render_fetch_progress(_active_job_id())
        st.button(
            "Fetching…", key=f"fetching_{university}{suffix}", disabled=True
        )
        return

    selected_refs = sorted(tender.tender_no for tender in selected)
    fetched_refs = st.session_state.get(f"fetched_refs_{university}{suffix}") or []
    result = st.session_state.get(key)
    zip_path = (result or {}).get("zip") or ""
    if result and fetched_refs == selected_refs and zip_path and Path(zip_path).is_file():
        _render_download_result(result, key)
        return
    if result and fetched_refs == selected_refs:
        st.info("No documents were found for that selection.")

    candidates = eoi_candidates(selected)
    if candidates:
        confirmed = st.checkbox(
            f"I understand an Expression of Interest will be submitted for "
            f"{len(candidates)} tender(s).",
            key=f"eoi_confirm_{university}{suffix}",
        )
        disabled = busy or not selected or not confirmed
    else:
        disabled = busy or not selected

    st.caption(
        f"{len(selected)} tender(s) selected · {len(candidates)} need EOI submitted."
    )
    if candidates:
        label = f"Submit EOI & Fetch Documents ({len(selected)})"
    elif selected:
        label = f"Fetch Documents ({len(selected)})"
    else:
        label = "Fetch Documents"
    if st.button(
        label,
        key=f"process_{university}{suffix}",
        disabled=disabled,
    ):
        _start_job(
            "download",
            university,
            tenders=tenders_to_payloads(selected),
            group=group,
        )


def _download_active(university: str, group: str = "") -> bool:
    return (
        bool(_active_job_id())
        and st.session_state.get("active_university") == university
        and st.session_state.get("active_kind") == "download"
        and str(st.session_state.get("active_group") or "") == group
    )


@st.fragment(run_every=2)
def _render_fetch_progress(job_id: str) -> None:
    """The download progress lives under its own table, not on a separate screen."""
    state = _manager().poll(job_id)
    progress = state.get("progress") or {}
    current = int(progress.get("current") or 0)
    total = int(progress.get("total") or 0)
    fraction = min(1.0, current / total) if total else 0.0
    st.progress(
        fraction,
        text=str(progress.get("message") or state.get("stage") or "Fetching documents…"),
    )


def _render_fetch_action(
    university: str,
    selected: List,
    busy: bool,
    group: str = "",
    *,
    archive: bool = True,
    gemini: bool = False,
) -> None:
    """One control in one place: Fetch → Fetching… → the downloaded documents.

    ``archive=False`` and ``gemini=True`` are used for an invitation project, which
    offers its files one by one instead of building a ZIP and shows the Gemini button.
    """
    suffix = f"_{group}" if group else ""
    key = f"scan_result_download_{university}{suffix}"
    error = st.session_state.pop(f"{key}_error", None)
    if error:
        st.error(error)

    if _download_active(university, group):
        _render_fetch_progress(_active_job_id())
        st.button(
            "Fetching…", key=f"fetching_{university}{suffix}", disabled=True
        )
        return

    selected_refs = sorted(tender.tender_no for tender in selected)
    fetched_refs = st.session_state.get(f"fetched_refs_{university}{suffix}") or []
    result = st.session_state.get(key)
    if result and fetched_refs == selected_refs:
        _render_download_result(result, key)
        return

    # Invitation rows normally have their EOI behind them, but a stale scan could still
    # report one missing. Confirm before a click submits an Expression of Interest.
    candidates = eoi_candidates(selected)
    if candidates:
        refs = ",".join(sorted(tender.tender_no for tender in selected))
        confirmed = st.checkbox(
            f"I understand an Expression of Interest will be submitted for "
            f"{len(candidates)} tender(s).",
            key=f"eoi_confirm_{university}{suffix}_{refs}",
        )
        disabled = busy or not selected or not confirmed
        label = "Submit EOI & Fetch Documents"
    else:
        disabled = busy or not selected
        label = "Fetch Documents"

    if st.button(
        label,
        key=f"fetch_{university}{suffix}",
        disabled=disabled,
    ):
        _start_job(
            "download",
            university,
            options={"archive": archive, "gemini": gemini},
            tenders=tenders_to_payloads(selected),
            group=group,
        )


def _render_inline_draft(university: str) -> None:
    key = f"scan_result_draft_{university}"
    if st.session_state.get(key):
        _render_draft_result(st.session_state[key], key)
    error = st.session_state.pop(f"{key}_error", None)
    if error:
        st.error(error)


def _render_scan_result(
    university: str, result: Dict, *, hide_handled: Optional[bool] = None
) -> None:
    tenders = tenders_from_payloads(result.get("tenders") or [])
    if not tenders:
        st.info("No active tenders were found.")
        return

    invited, open_tenders = split_groups(tenders)
    # Only rows this scan removed count as hidden; Gmail may know far more references.
    handled = int(result.get("handled_count") or 0)
    scanned_hide = result.get("hide_handled")
    if (
        hide_handled is not None
        and scanned_hide is not None
        and bool(scanned_hide) != bool(hide_handled)
    ):
        st.info(
            "The Gmail filter has changed since this scan. "
            "Run the scan again to refresh the list."
        )
    known = int(result.get("handled_known_count") or 0)
    if scanned_hide is True:
        filter_note = f" · {handled} of {known} handled tender(s) hidden"
    elif scanned_hide is False:
        # Say plainly that the mailbox was not consulted, so identical tables are expected.
        filter_note = " · Gmail filter off"
    else:
        filter_note = ""
    st.caption(
        f"{len(tenders)} tender(s) · {len(invited)} invitation(s) · "
        f"{len(open_tenders)} open{filter_note}"
    )
    busy = bool(_active_job_id())

    if invited:
        st.markdown("**My Tender Invitation / Interested Tender(s)**")
        _invitation_table(university, invited)
        st.caption("Open a project to fetch its documents.")

    if open_tenders:
        st.markdown("**Tender Notice**")
        open_selected = selected_tenders(
            open_tenders, _selection_table(open_tenders, f"select_{university}_open")
        )
        if university == "hkust":
            _render_draft_action(university, open_selected, busy)
            _render_inline_draft(university)
        else:
            _render_process_action(university, open_selected, busy, group="open")


def _render_project_screen(university: str, tender_ref: str, busy: bool) -> None:
    """One invitation project: its details, Fetch, then its documents and Gemini."""
    ref_key = _project_ref_key(university)
    if st.button(
        "← Back to invitations",
        key=f"back_{university}_project",
        disabled=busy,
    ):
        st.session_state.pop(ref_key, None)
        st.rerun()

    scan_result = st.session_state.get(f"scan_result_{university}") or {}
    tenders = tenders_from_payloads(scan_result.get("tenders") or [])
    tender = next((item for item in tenders if item.tender_no == tender_ref), None)
    if tender is None:
        st.session_state.pop(ref_key, None)
        st.info("That project is no longer in the scanned list.")
        return

    st.subheader(tender.tender_no)
    if tender.description:
        st.markdown(tender.description)
    details = []
    if tender.closing_date:
        details.append(f"Closing: {tender.closing_date}")
    contact = " · ".join(
        part
        for part in (tender.contact_person, tender.contact_email)
        if part and part.strip() and part.strip() != "Sir/Madam"
    )
    if contact:
        details.append(contact)
    if details:
        st.caption(" · ".join(details))

    # No ZIP for a single project: the result screen offers every file on its own, next
    # to the Gemini button.
    _render_fetch_action(
        university,
        [tender],
        busy,
        group="project",
        archive=False,
        gemini=True,
    )


def _render_portal(university: str) -> None:
    label = UNIVERSITIES[university]
    busy = bool(_active_job_id())
    scan_key = f"scan_result_{university}"

    ref_key = _project_ref_key(university)
    project_ref = str(st.session_state.get(ref_key) or "")
    if project_ref and not st.session_state.get(scan_key):
        # The scan that listed the project is gone, so there is nothing to go back to.
        st.session_state.pop(ref_key, None)
        project_ref = ""

    hide_handled: Optional[bool] = None
    options: Dict = {}
    if university == "hkust":
        # The project screen replaces the scan controls; its Back button returns to the
        # tables. Read the filter value only so the table can be rendered after Back.
        hide_handled = bool(st.session_state.get("hide_handled_hkust", True))
        if not project_ref:
            hide_handled = st.checkbox(
                "Hide tenders already handled in Gmail",
                value=True,
                key=f"hide_handled_{university}",
                # The filter is applied during the scan, so changing it mid-run would
                # only desynchronise the option from the job already reading it.
                disabled=busy,
            )
            options = {"hide_handled": hide_handled}
    elif not project_ref:
        # A project screen was reached through a scan, so the credentials are known to
        # work; asking for them again would only hide the project.
        user_id = config.CUHK_USER_ID if university == "cuhk" else config.CITYU_USER_ID
        password = config.CUHK_PASSWORD if university == "cuhk" else config.CITYU_PASSWORD
        if not user_id or not password:
            st.warning(
                f"{label} credentials are not configured. Add "
                f"{label.upper()}_USER_ID and {label.upper()}_PASSWORD to the app secrets."
            )
            return

    active_for_tab = (
        bool(_active_job_id())
        and st.session_state.get("active_university") == university
    )
    active_kind = str(st.session_state.get("active_kind") or "")
    scanning_here = active_for_tab and active_kind == "scan"

    # The project screen replaces the scan controls; its Back button returns to them.
    if not project_ref:
        if scanning_here:
            state = _manager().poll(_active_job_id())
            stopping = str(state.get("status") or "") == "cancelling"
            if st.button(
                "Stopping..." if stopping else "Stop",
                key=f"stop_{university}",
                disabled=stopping,
            ):
                _manager().cancel(_active_job_id())
                st.rerun()
        else:
            if st.button(
                f"Scan {label} tenders", key=f"scan_{university}", disabled=busy
            ):
                _start_job("scan", university, options=options)

    if active_for_tab:
        # The job's own result key; the scan table is separate and must stay visible
        # while a download/draft runs.
        active_group = str(st.session_state.get("active_group") or "")
        suffix = f"_{active_group}" if active_group else ""
        if active_kind == "download":
            active_result_key = f"download_result_{university}{suffix}"
        elif active_kind == "draft":
            active_result_key = f"draft_result_{university}"
        else:
            active_result_key = scan_key
        _render_job(
            _active_job_id(),
            university,
            active_kind,
            active_result_key,
            show_progress=(active_kind == "scan"),
        )

    error_key = f"{scan_key}_error"
    if st.session_state.get(error_key):
        st.error(st.session_state[error_key])
        st.session_state.pop(error_key, None)

    # Results of jobs started from this tab are stored under kind-specific keys by the
    # fragment callback. Move them next to the table that produced them before rendering,
    # so the first post-job run already shows the download instead of Fetch.
    for group in ("", "invited", "open", "project"):
        suffix = f"_{group}" if group else ""
        _consume_action_result(
            university,
            "download",
            f"scan_result_download_{university}{suffix}",
            group=group,
        )
    _consume_action_result(university, "draft", f"scan_result_draft_{university}")

    if project_ref:
        _render_project_screen(university, project_ref, busy)
    else:
        result = st.session_state.get(scan_key)
        if result:
            _render_scan_result(
                university,
                result,
                hide_handled=hide_handled if university == "hkust" else None,
            )

    _render_log_section(university)


def _consume_action_result(
    university: str, kind: str, target_key: str, group: str = ""
) -> None:
    suffix = f"_{group}" if group else ""
    source = f"{kind}_result_{university}{suffix}"
    if st.session_state.get(source):
        st.session_state[target_key] = st.session_state.pop(source)
        error = st.session_state.pop(f"{source}_error", None)
        if error:
            st.session_state[f"{target_key}_error"] = error
        st.rerun()


def _render_diagnostics() -> None:
    st.markdown("### Runtime and connectivity")
    st.write(
        {
            "HKUST credentials": bool(config.HKUST_VENDOR_ID and config.HKUST_PASSWORD),
            "CUHK credentials": bool(config.CUHK_USER_ID and config.CUHK_PASSWORD),
            "CityU credentials": bool(config.CITYU_USER_ID and config.CITYU_PASSWORD),
            "Gmail": bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD),
            "BR certificate": certificate_fingerprint(
                str(config.BR_CERTIFICATE_PATH) if config.BR_CERTIFICATE_PATH else ""
            ),
            "Storage": "ephemeral — download results before the app restarts",
        }
    )

    busy = bool(_active_job_id())
    if st.button("Run diagnostics", disabled=busy):
        _start_job("diagnostic", "")
    if _active_job_id() and st.session_state.get("active_kind") == "diagnostic":
        _render_job(_active_job_id(), "", "diagnostic", "diagnostic_result")

    result = st.session_state.get("diagnostic_result")
    if result:
        st.write(result)
    error = st.session_state.get("diagnostic_result_error")
    if error:
        st.error(error)
        st.session_state.pop("diagnostic_result_error", None)

    _render_log_section("diagnostic")


def render_app(auth: AuthResult) -> None:
    st.session_state["auth_name"] = auth.name or auth.username
    st.sidebar.success(f"Signed in as {st.session_state['auth_name']}")
    if auth.logout:
        # The callable renders the sign-out control, because the authenticator's
        # logout button is what clears its cookie.
        auth.logout()
    _render_br_upload()

    st.sidebar.warning(
        "Streamlit Cloud storage is temporary. Download the files after each job; "
        "they disappear when the app restarts."
    )

    _manager().cleanup()

    st.title("Tender Portal")
    tabs = st.tabs(["HKUST", "CUHK", "CityU", "Diagnostics"])
    with tabs[0]:
        _render_portal("hkust")
    with tabs[1]:
        _render_portal("cuhk")
    with tabs[2]:
        _render_portal("cityu")
    with tabs[3]:
        _render_diagnostics()
