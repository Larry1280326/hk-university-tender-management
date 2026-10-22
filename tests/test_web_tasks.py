"""Tests for the job gateway, especially the Gmail filter caption and EOI handoff."""

import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from scraper.models import TenderNotice
from web import tasks
from web.serialize import tenders_to_payloads


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


class TestRunDownload(unittest.TestCase):

    def test_eoi_only_reports_the_submitted_tenders_and_skips_the_zip(self):
        tender = TenderNotice(
            tender_no="T-20260425",
            description="Student Common Room Renovation",
            closing_date="20-Nov-2026 12:00:00",
            contact_person="Miss CHAN",
            contact_email="tender@cuhk.edu.hk",
            invited=False,
            needs_eoi=True,
            portal_id="123",
        )
        seen = {}

        def downloader(
            page,
            tenders,
            dest_root,
            *,
            eoi_only=False,
            on_eoi=None,
            log=None,
            progress_callback=None,
        ):
            seen["eoi_only"] = eoi_only
            seen["refs"] = [item.tender_no for item in tenders]
            on_eoi(tenders[0].tender_no)
            return []

        with tempfile.TemporaryDirectory() as workspace, patch.object(
            tasks, "_ensure_chromium"
        ), patch.dict(
            tasks.SESSION_FACTORIES, {"cuhk": _fake_session}
        ), patch.dict(
            tasks.DOWNLOADERS, {"cuhk": downloader}
        ):
            result = tasks.run_download(
                {
                    "university": "cuhk",
                    "workspace": workspace,
                    "options": {"eoi_only": True},
                    "tenders": tenders_to_payloads([tender]),
                },
                _FakeReporter(),
            )

        self.assertTrue(seen["eoi_only"])
        self.assertEqual(seen["refs"], ["T-20260425"])
        self.assertEqual(
            result, {"files": [], "zip": "", "eoi_submitted": ["T-20260425"]}
        )


if __name__ == "__main__":
    unittest.main()
