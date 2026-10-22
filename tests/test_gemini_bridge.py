"""Tests for the Gemini bridge: the component HTML and the static page it opens."""

import json
import unittest
from pathlib import Path

from web.gemini import GEMINI_EXTENSION_ID
from web.gemini_bridge import bridge_url, render_gemini_bridge

BRIDGE_PAGE = (
    Path(__file__).resolve().parent.parent / "web" / "static" / "gemini_bridge.html"
)


class TestComponentHtml(unittest.TestCase):

    def test_prompt_is_embedded_as_json(self):
        prompt = 'TNG2600120 EOI\nLine "two" from Larry'

        html = render_gemini_bridge(prompt, app_url="https://example.streamlit.app")

        self.assertIn(json.dumps(prompt), html)

    def test_prompt_cannot_end_the_script_element(self):
        html = render_gemini_bridge(
            "</script><script>alert(1)</script>", app_url="https://example.streamlit.app"
        )

        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("<\\/script>", html)

    def test_prompt_never_appears_in_a_url(self):
        prompt = "PROMPT-MARKER-123"

        html = render_gemini_bridge(prompt, app_url="https://example.streamlit.app")

        for line in html.splitlines():
            if "href=" in line or "BRIDGE_URL" in line or "window.open" in line:
                self.assertNotIn(prompt, line)
        self.assertIn(
            '"https://example.streamlit.app/app/static/gemini_bridge.html"', html
        )

    def test_bridge_url_falls_back_to_root_relative(self):
        self.assertEqual(bridge_url(""), "/app/static/gemini_bridge.html")
        self.assertEqual(
            bridge_url("https://example.streamlit.app/"),
            "https://example.streamlit.app/app/static/gemini_bridge.html",
        )

    def test_component_is_only_the_button(self):
        html = render_gemini_bridge("prompt", app_url="https://example.streamlit.app")

        self.assertIn(">Gemini</button>", html)
        self.assertNotIn("<a ", html)
        self.assertNotIn("Open Gemini", html)
        self.assertNotIn("Requires the Send to Gemini extension", html)
        self.assertNotIn("not submitted", html)
        self.assertNotIn("id=\"status\"", html)

    def test_button_label_is_centred(self):
        html = render_gemini_bridge("prompt", app_url="https://example.streamlit.app")

        self.assertIn("align-items: center", html)
        self.assertIn("justify-content: center", html)
        self.assertIn("text-align: center", html)


class TestBridgePage(unittest.TestCase):
    """The page that does have chrome.runtime: it must stay fill-only and leak-free."""

    @classmethod
    def setUpClass(cls):
        cls.text = BRIDGE_PAGE.read_text(encoding="utf-8")

    def test_calls_the_expected_extension_without_submitting(self):
        self.assertIn(GEMINI_EXTENSION_ID, self.text)
        self.assertIn("autoSubmit: false", self.text)

    def test_accepts_a_prompt_only_from_its_opener(self):
        self.assertIn("event.source !== window.opener", self.text)

    def test_never_persists_the_prompt_or_puts_it_in_the_url(self):
        for forbidden in ("localStorage", "sessionStorage", "URLSearchParams"):
            self.assertNotIn(forbidden, self.text)
        self.assertNotIn("location.href =", self.text)
        self.assertNotIn("location.search", self.text)

    def test_reports_readiness_and_the_result(self):
        self.assertIn('type: "bridge-ready"', self.text)
        self.assertIn('type: "autofill-result"', self.text)

    def test_reports_a_missing_extension_in_its_own_tab(self):
        self.assertIn("not installed or is disabled", self.text)


if __name__ == "__main__":
    unittest.main()
