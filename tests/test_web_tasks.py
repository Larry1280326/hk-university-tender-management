"""Tests for the scan job gateway, especially the Gmail filter caption."""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from web import tasks


class _FakeReporter:
    def log(self, message):
        pass

    def progress(self, *args, **kwargs):
        pass

    def update(self, **kwargs):
        pass


class _FakeParser:
    skipped_handled_refs = []

    def extract_tenders(self, progress_callback=None, existing_tenders=None):
        return []


@contextmanager
def _fake_session(*args, **kwargs):
    yield (object(), _FakeParser())


class TestRunScan(unittest.TestCase):

    def scan(self, university, options=None):
        with patch.object(tasks, "_ensure_chromium"), patch.dict(
            tasks.SESSION_FACTORIES, {university: _fake_session}
        ):
            return tasks.run_scan(
                {"university": university, "options": options or {}},
                _FakeReporter(),
            )

    def test_cuhk_scan_reports_no_gmail_filter_state(self):
        # CUHK never consults Gmail, so "0 of 0 handled tender(s) hidden" would be wrong.
        self.assertIsNone(self.scan("cuhk")["hide_handled"])

    def test_cityu_scan_reports_no_gmail_filter_state(self):
        self.assertIsNone(self.scan("cityu")["hide_handled"])

    def test_hkust_scan_reports_the_filter_state(self):
        self.assertIs(self.scan("hkust", {"hide_handled": False})["hide_handled"], False)

        with patch.object(tasks, "GoogleAccountMailer") as mailer:
            mailer.return_value.is_configured.return_value = False
            result = self.scan("hkust", {"hide_handled": True})

        self.assertIs(result["hide_handled"], True)


if __name__ == "__main__":
    unittest.main()
