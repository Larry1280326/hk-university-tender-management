"""The browser-side half of the Gemini handoff.

Streamlit renders components in a sandboxed iframe with an opaque origin, where Chrome
does not expose ``chrome.runtime``. So the component does not talk to the extension
directly: it opens the top-level page in ``web/static/gemini_bridge.html`` (served by
Streamlit's static file support) and passes the prompt to it with ``postMessage``. That
page calls the Send to Gemini extension and reports back.

The prompt therefore never appears in a URL, the browser history, or storage.
"""

import json

BRIDGE_PAGE_PATH = "/app/static/gemini_bridge.html"
# Just the button; the bridge page reports success and failure in its own tab.
BRIDGE_HEIGHT = 44


def bridge_url(app_url: str = "") -> str:
    """Absolute URL of the bridge page, or a root-relative one when it is unknown.

    A root-relative URL still resolves correctly from the sandboxed component because
    the browser resolves it against the embedding Streamlit page.
    """
    if not app_url:
        return BRIDGE_PAGE_PATH
    return app_url.rstrip("/") + BRIDGE_PAGE_PATH


def render_gemini_bridge(prompt: str, *, app_url: str = "") -> str:
    """HTML for ``st.components.v1.html``: just the button.

    The component itself never calls the extension: it only passes the prompt to the
    bridge page, which owns the extension call.
    """
    # ``</`` must not appear verbatim inside the script element, or the browser ends the
    # script early, no matter what JSON quoting says.
    prompt_js = json.dumps(prompt).replace("</", "<\\/")
    bridge_js = json.dumps(bridge_url(app_url))
    return f"""<style>
  body {{ font-family: "Source Sans Pro", system-ui, -apple-system, sans-serif;
         font-size: 14px; margin: 0; color: #31333f; }}
  button {{ font: inherit; padding: 0 1rem; border-radius: 0.5rem;
            border: 1px solid rgba(49, 51, 63, 0.2); background: #ffffff;
            color: #31333f; cursor: pointer; height: 2.5rem;
            display: inline-flex; align-items: center; justify-content: center;
            text-align: center; white-space: nowrap; }}
  button:hover {{ border-color: #ff4b4b; color: #ff4b4b; }}
</style>
<button id="send" type="button">Gemini</button>
<script>
const PROMPT = {prompt_js};
const BRIDGE_URL = {bridge_js};
let bridge = null;

document.getElementById("send").addEventListener("click", () => {{
  bridge = window.open(BRIDGE_URL, "gemini-bridge");
}});

window.addEventListener("message", (event) => {{
  const data = event.data || {{}};
  if (data.type === "bridge-ready" && data.extension && bridge) {{
    event.source.postMessage({{type: "autofill", prompt: PROMPT}}, "*");
  }}
}});
</script>
"""
