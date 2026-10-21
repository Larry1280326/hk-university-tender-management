"""Application-level login for a small group of colleagues.

Streamlit Cloud private apps already restrict who can open the URL; this is the second
gate, and it also records who started each job. ``streamlit-authenticator`` is used when
configured; a single shared password is the fallback so a deployment without the package
still cannot be opened anonymously.
"""

import hmac
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 60


@dataclass
class AuthResult:
    ok: bool
    username: str = ""
    name: str = ""
    logout: Optional[Callable[[], None]] = None


def _secrets(secrets: Optional[Mapping] = None) -> Mapping:
    if secrets is not None:
        return secrets
    import streamlit as st

    try:
        # Accessing the object lazily parses the file; force that here so a missing
        # secrets.toml is handled rather than raised later.
        return dict(st.secrets)
    except Exception as exc:
        if type(exc).__name__ == "StreamlitSecretNotFoundError":
            # No secrets file at all is a valid local state; the caller reports the
            # missing login. A parse error is different and must surface as one.
            return {}
        raise


def authenticate(secrets: Optional[Mapping] = None) -> AuthResult:
    """Render the login UI if needed and stop the script when not authenticated."""
    import streamlit as st

    try:
        values = _plain(_secrets(secrets))
    except Exception as exc:
        # A malformed secrets.toml otherwise looks exactly like "no login configured",
        # which sends people hunting for the wrong problem.
        st.error(
            f"Could not read app secrets ({type(exc).__name__}): {exc}"
        )
        st.stop()

    if not values:
        # Streamlit returns an empty mapping when the file is malformed, so a broken
        # TOML file otherwise looks identical to "no login configured".
        problem = _secrets_file_error()
        if problem:
            st.error(f"Could not read app secrets: {problem}")
            st.stop()

    auth_config = values.get("auth", {}) if hasattr(values, "get") else {}
    username = _authenticator_login(st, auth_config)
    if username is not None:
        return username
    return _password_login(st, values)


def _secrets_file_error() -> str:
    """Parse the candidate secrets files so a TOML error is not silently swallowed."""
    project_root = Path(__file__).resolve().parent.parent
    candidates = [
        Path.cwd() / ".streamlit" / "secrets.toml",
        project_root / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"{path}: {type(exc).__name__}: {exc}"
        return ""
    return ""


def _plain(value):
    """Streamlit secrets are AttrDict-like; the authenticator wants plain mappings."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _authenticator_login(st, auth_config) -> Optional[AuthResult]:
    """Return an AuthResult when streamlit-authenticator handled the session."""
    if not auth_config or "credentials" not in auth_config:
        return None
    try:
        import streamlit_authenticator as stauth
    except Exception:
        return None

    try:
        authenticator = stauth.Authenticate(
            auth_config["credentials"],
            auth_config.get("cookie_name", "tender_app"),
            auth_config.get("cookie_key", ""),
            auth_config.get("cookie_expiry_days", 1),
        )
        authenticator.login(location="main")
    except Exception as exc:
        st.error(f"Login is misconfigured: {type(exc).__name__}: {exc}")
        st.stop()

    status = st.session_state.get("authentication_status")
    if status:
        return AuthResult(
            ok=True,
            username=str(st.session_state.get("username") or ""),
            name=str(st.session_state.get("name") or ""),
            # Renders the library's sign-out control (which also clears its cookie).
            logout=lambda: authenticator.logout(location="sidebar"),
        )
    if status is False:
        st.error("Username/password is incorrect")
    else:
        st.warning("Please enter your username and password")
    st.stop()
    return AuthResult(ok=False)


def _password_login(st, values: Mapping) -> AuthResult:
    """Fallback: one shared password with a small in-session lockout."""
    expected = str(values.get("WEB_PASSWORD", "") or "")
    if not expected:
        st.error(
            "No login is configured. Add either an [auth] section or WEB_PASSWORD to "
            "the app secrets before using this deployment."
        )
        st.stop()

    if st.session_state.get("authenticated"):
        username = str(st.session_state.get("username") or "operator")
        return AuthResult(
            ok=True,
            username=username,
            name=username,
            logout=lambda: _logout_control(st),
        )

    locked_until = float(st.session_state.get("login_locked_until") or 0)
    if time.time() < locked_until:
        st.error("Too many failed attempts. Try again in a minute.")
        st.stop()

    with st.form("login"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        if hmac.compare_digest(entered, expected):
            st.session_state["authenticated"] = True
            st.session_state["username"] = str(values.get("WEB_USERNAME", "operator"))
            st.session_state["login_failures"] = 0
            st.rerun()
        failures = int(st.session_state.get("login_failures") or 0) + 1
        st.session_state["login_failures"] = failures
        if failures >= MAX_ATTEMPTS:
            st.session_state["login_locked_until"] = time.time() + LOCKOUT_SECONDS
            st.session_state["login_failures"] = 0
        st.error("Incorrect password")

    st.stop()
    return AuthResult(ok=False)


def _logout_control(st) -> None:
    """Render the fallback sign-out control; clicking it clears the session."""
    if st.sidebar.button("Sign out", key="fallback_sign_out"):
        st.session_state.pop("authenticated", None)
        st.session_state.pop("username", None)
        st.rerun()
