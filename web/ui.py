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
from web.jobs import JobError, JobManager, TERMINAL_STATUSES
from web.select_logic import (
    draftable_tenders,
    eoi_candidates,
    row_label,
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


def _render_br_upload() -> None:
    st.sidebar.subheader("BR certificate")
    if _certificate_ready():
        st.sidebar.success("Ready for Gmail drafts")
    else:
        st.sidebar.warning("Not uploaded — drafts are disabled")

    with st.sidebar.form("br_upload_form", clear_on_submit=True):
        uploaded = st.file_uploader("Upload BR certificate (PDF)", type=["pdf"])
        submitted = st.form_submit_button("Save certificate")

    if submitted:
        if uploaded is None:
            st.sidebar.error("Choose a PDF first.")
        else:
            path, error = _store_uploaded_certificate(uploaded)
            if path:
                st.sidebar.success(f"Saved {path.name} for this runtime.")
                st.rerun()
            else:
                st.sidebar.error(error)

    st.sidebar.caption(
        "The upload lives in the runtime directory and is lost when the app "
        "restarts or redeploys. Upload it again after a wake-up."
    )


def _start_job(
    kind: str,
    university: str,
    *,
    options: Optional[Dict] = None,
    tenders: Optional[List] = None,
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
        # A fresh scan must not inherit ticks from the previous table.
        st.session_state.pop(f"scan_result_{university}", None)
        for group in ("invited", "open"):
            st.session_state.pop(f"select_{university}_{group}", None)
    st.session_state["active_job"] = job.job_id
    st.session_state["active_university"] = university
    st.session_state["active_kind"] = kind
    return True


@st.fragment(run_every=2)
def _render_job(job_id: str, university: str, kind: str, result_key: str) -> None:
    manager = _manager()
    state = manager.poll(job_id)
    status = str(state.get("status") or "unknown")
    progress = state.get("progress") or {}
    current = int(progress.get("current") or 0)
    total = int(progress.get("total") or 0)
    fraction = min(1.0, current / total) if total else 0.0
    st.progress(
        fraction,
        text=f"{status} · {state.get('stage', '')} · {progress.get('message', '')}".strip(),
    )

    if status not in TERMINAL_STATUSES:
        if st.button("Cancel job", key=f"cancel_{job_id}"):
            manager.cancel(job_id)
            st.rerun()

    job = manager.get(job_id)
    with st.expander("Job log", expanded=status not in TERMINAL_STATUSES):
        st.code(job.log_tail() if job else "", language="text")

    if status in TERMINAL_STATUSES:
        st.session_state[result_key] = state.get("result")
        st.session_state[f"{result_key}_error"] = state.get("error") or ""
        st.session_state.pop("active_job", None)
        # Default rerun scope is "fragment" inside a fragment, which would re-render
        # this finished job instead of the results below it.
        st.rerun(scope="app")


def _render_download_result(result: Optional[Dict], key: str) -> None:
    if not result:
        return
    files = result.get("files") or []
    zip_path = result.get("zip") or ""
    st.success(f"Downloaded {len(files)} file(s).")
    if zip_path and Path(zip_path).is_file():
        with open(zip_path, "rb") as handle:
            st.download_button(
                "Download documents (ZIP)",
                handle,
                file_name=Path(zip_path).name,
                key=f"zip_{key}",
            )
    with st.expander("Files", expanded=False):
        for path in files:
            st.text(path)


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
    rows = [
        {
            "Select": False,
            "Tender": tender.tender_no,
            "Description": tender.description,
            "Closing": tender.closing_date,
            "Details": row_label(tender),
        }
        for tender in tenders
    ]
    edited = st.data_editor(
        rows,
        key=key,
        hide_index=True,
        width="stretch",
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", default=False),
            "Details": st.column_config.TextColumn("Details", width="large"),
        },
        disabled=["Tender", "Description", "Closing", "Details"],
    )
    return [row["Tender"] for row in edited if row.get("Select")]


def _render_scan_result(university: str, result: Dict) -> None:
    label = UNIVERSITIES[university]
    tenders = tenders_from_payloads(result.get("tenders") or [])
    if not tenders:
        st.info("No active tenders were found.")
        return

    invited, open_tenders = split_groups(tenders)
    handled = int(result.get("handled_count") or 0)
    st.caption(
        f"{len(tenders)} tender(s) · {len(invited)} invitation(s) · "
        f"{len(open_tenders)} open · {handled} handled reference(s) hidden"
    )

    selected_refs: List[str] = []
    if invited:
        st.markdown("**My Tender Invitation / Interested Tender(s)**")
        selected_refs += _selection_table(invited, f"select_{university}_invited")
    if open_tenders:
        st.markdown("**Tender Notice**")
        selected_refs += _selection_table(open_tenders, f"select_{university}_open")

    selected = selected_tenders(tenders, selected_refs)
    if not selected:
        st.info("Tick the tenders you want to act on.")
        return

    selected_invited = [tender for tender in selected if tender.invited]
    selected_open = [tender for tender in selected if not tender.invited]
    busy = bool(_active_job_id())

    if university == "hkust":
        if selected_invited:
            st.caption(f"{len(selected_invited)} invited tender(s) selected.")
            if st.button(
                "Download documents for selected invitations",
                key=f"download_{university}",
                disabled=busy,
            ):
                _start_job(
                    "download",
                    university,
                    tenders=tenders_to_payloads(selected_invited),
                )

        if selected_open:
            draftable = draftable_tenders(selected_open)
            skipped = skipped_for_missing_email(selected_open)
            certificate_ready = _certificate_ready()
            st.caption(f"{len(draftable)} draftable open tender(s) selected.")
            if not certificate_ready:
                st.warning(
                    "Upload the BR certificate in the sidebar before creating drafts."
                )
            if skipped:
                st.warning("No contact email, will be skipped: " + ", ".join(skipped))
            if draftable:
                sample = draftable[0]
                draft = generate_tender_email(
                    tender_no=sample.tender_no,
                    description=sample.description,
                    recipient_name=sample.contact_person,
                    recipient_email=sample.contact_email,
                )
                with st.expander("Draft preview (first selected tender)"):
                    st.text(f"To: {draft.recipient_email}")
                    st.text(f"Subject: {draft.subject}")
                    st.text(draft.body)
                if st.button(
                    f"Create {len(draftable)} Gmail draft(s)",
                    key=f"draft_{university}",
                    disabled=busy or not certificate_ready,
                ):
                    _start_job(
                        "draft",
                        university,
                        tenders=tenders_to_payloads(draftable),
                    )
    else:
        candidates = eoi_candidates(selected)
        if candidates:
            confirmed = st.checkbox(
                f"I understand an Expression of Interest will be submitted for "
                f"{len(candidates)} tender(s).",
                key=f"eoi_confirm_{university}",
            )
            disabled = busy or not confirmed
        else:
            disabled = busy
        st.caption(
            f"{len(selected)} tender(s) selected · {len(candidates)} need EOI submitted."
        )
        if st.button(
            f"Submit EOI where needed and download {len(selected)} tender(s)",
            key=f"process_{university}",
            disabled=disabled,
        ):
            _start_job(
                "download",
                university,
                tenders=tenders_to_payloads(selected),
            )


def _render_portal(university: str) -> None:
    label = UNIVERSITIES[university]
    busy = bool(_active_job_id())

    if university == "hkust":
        hide_handled = st.checkbox(
            "Hide tenders already handled in Gmail",
            value=True,
            key=f"hide_handled_{university}",
        )
        options = {"hide_handled": hide_handled}
    else:
        user_id = config.CUHK_USER_ID if university == "cuhk" else config.CITYU_USER_ID
        password = config.CUHK_PASSWORD if university == "cuhk" else config.CITYU_PASSWORD
        if not user_id or not password:
            st.warning(
                f"{label} credentials are not configured. Add "
                f"{label.upper()}_USER_ID and {label.upper()}_PASSWORD to the app secrets."
            )
            return
        options = {}

    if st.button(f"Scan {label} tenders", key=f"scan_{university}", disabled=busy):
        _start_job("scan", university, options=options)

    active_kind = str(st.session_state.get("active_kind") or "")
    result_key = {
        "scan": f"scan_result_{university}",
        "download": f"download_result_{university}",
        "draft": f"draft_result_{university}",
    }.get(active_kind, f"scan_result_{university}")
    active_for_tab = (
        _active_job_id()
        and st.session_state.get("active_university") == university
    )
    if active_for_tab:
        _render_job(
            _active_job_id(),
            university,
            active_kind,
            result_key,
        )

    error_key = f"{result_key}_error"
    if st.session_state.get(error_key):
        st.error(st.session_state[error_key])
        st.session_state.pop(error_key, None)

    result = st.session_state.get(result_key)
    if result:
        _render_scan_result(university, result)

    scan_download_key = f"scan_result_download_{university}"
    if st.session_state.get(scan_download_key):
        _render_download_result(st.session_state[scan_download_key], scan_download_key)

    scan_draft_key = f"scan_result_draft_{university}"
    if st.session_state.get(scan_draft_key):
        _render_draft_result(st.session_state[scan_draft_key], scan_draft_key)

    # Results of jobs started from this tab are stored under kind-specific keys by the
    # fragment callback, so read them once they appear.
    _consume_action_result(university, "download", scan_download_key)
    _consume_action_result(university, "draft", scan_draft_key)


def _consume_action_result(university: str, kind: str, target_key: str) -> None:
    source = f"{kind}_result_{university}"
    if st.session_state.get(source):
        st.session_state[target_key] = st.session_state.pop(source)
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


def render_app(auth: AuthResult) -> None:
    st.session_state["auth_name"] = auth.name or auth.username
    st.sidebar.success(f"Signed in as {st.session_state['auth_name']}")
    if auth.logout:
        # The callable renders the sign-out control, because the authenticator's
        # logout button is what clears its cookie.
        auth.logout()
    _render_br_upload()

    st.sidebar.warning(
        "Streamlit Cloud storage is temporary. Download the ZIP after each job; "
        "files disappear when the app restarts."
    )
    st.sidebar.caption(f"Workspace: {_workspace()}")

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
