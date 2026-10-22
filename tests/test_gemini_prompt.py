"""Tests for the Gemini prompt and its wiring into the download job."""

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scraper.models import TenderDocument, TenderNotice
from scraper.paths import tender_dir_name
from web.gemini import (
    GEMINI_SKILL,
    TO_CONFIRM,
    build_gemini_prompt,
    document_paths,
)


def invitation():
    return TenderNotice(
        tender_no="TNG2600120 EOI",
        description="Chip Fabrication Service",
        closing_date="29-Oct-2026 15:00:00",
        contact_person="",
        contact_email="",
        invited=True,
        documents=[
            TenderDocument(
                doc_type="Invitation",
                file_name="TNG2600120EOI_I01.pdf",
                subject="Invitation",
                updated_on="15-Oct-2026 10:20:37",
                size="415.6 KB",
            )
        ],
    )


PROMPT_PATH = "HKUST/Chip Fabrication Service/TNG2600120EOI_I01.pdf"


class TestPrompt(unittest.TestCase):

    def test_prompt_invokes_the_gemini_skill(self):
        prompt = build_gemini_prompt([invitation()], [PROMPT_PATH])

        self.assertTrue(prompt.startswith(GEMINI_SKILL + "\n"), prompt)

    def test_prompt_carries_the_tender_and_file_details(self):
        prompt = build_gemini_prompt([invitation()], [PROMPT_PATH])

        self.assertIn("TNG2600120 EOI", prompt)
        self.assertIn("Chip Fabrication Service", prompt)
        self.assertIn("29-Oct-2026 15:00:00", prompt)
        self.assertIn("TNG2600120EOI_I01.pdf", prompt)
        self.assertIn("Invitation, 415.6 KB", prompt)

    def test_the_form_filling_instructions_live_in_the_skill_not_the_prompt(self):
        prompt = build_gemini_prompt([invitation()], [PROMPT_PATH])

        self.assertNotIn("Supplier details", prompt)
        self.assertNotIn("What to produce", prompt)
        self.assertNotIn("Never invent", prompt)
        self.assertNotIn("| Document | Page | Field", prompt)

    def test_an_unset_closing_date_is_marked_to_confirm(self):
        tender = invitation()
        tender.closing_date = ""

        prompt = build_gemini_prompt([tender], [])

        self.assertIn(f"- Closing: {TO_CONFIRM}", prompt)

    def test_files_are_grouped_under_their_tender(self):
        prompt = build_gemini_prompt([invitation()], [PROMPT_PATH])

        self.assertLess(prompt.index("## Tender"), prompt.index("TNG2600120EOI_I01.pdf"))

    def test_each_tender_gets_its_own_section(self):
        second = invitation()
        second.tender_no = "TNG2600118 EOI"

        prompt = build_gemini_prompt([invitation(), second], [])

        self.assertIn("## Tender 1: TNG2600120 EOI", prompt)
        self.assertIn("## Tender 2: TNG2600118 EOI", prompt)

    def test_files_from_an_unknown_folder_are_still_listed(self):
        prompt = build_gemini_prompt([invitation()], ["HKUST/stray-notes.txt"])

        self.assertIn("Other files in this download", prompt)
        self.assertIn("stray-notes.txt", prompt)

    def test_prompt_defaults_to_hkust(self):
        prompt = build_gemini_prompt([invitation()], [PROMPT_PATH])

        self.assertIn("- University: HKUST", prompt)

    def test_prompt_names_the_university(self):
        for university, label in (
            ("hkust", "HKUST"),
            ("cuhk", "CUHK"),
            ("cityu", "CityU"),
        ):
            with self.subTest(university=university):
                prompt = build_gemini_prompt(
                    [invitation()], [PROMPT_PATH], university=university
                )
                self.assertIn(f"- University: {label}", prompt)

    def test_an_unknown_university_still_gets_a_sensible_label(self):
        prompt = build_gemini_prompt(
            [invitation()], [PROMPT_PATH], university="polyu"
        )

        self.assertIn("- University: POLYU", prompt)

    def test_cuhk_files_are_grouped_under_their_tender(self):
        path = "CUHK/Chip Fabrication Service/TNG2600120EOI_I01.pdf"

        prompt = build_gemini_prompt([invitation()], [path], university="cuhk")

        self.assertLess(prompt.index("## Tender"), prompt.index(path))


class TestDocumentPaths(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "HKUST"
        self.folder = self.root / tender_dir_name(invitation())
        self.folder.mkdir(parents=True)

    def write(self, name, data=b"%PDF-1.4 test"):
        path = self.folder / name
        path.write_bytes(data)
        return path

    def test_paths_match_the_downloaded_zip_layout(self):
        pdf = self.write("TNG2600120EOI_I01.pdf")

        self.assertEqual(
            document_paths([pdf], self.root),
            [f"HKUST/{tender_dir_name(invitation())}/TNG2600120EOI_I01.pdf"],
        )

    def test_non_pdf_files_are_listed_too(self):
        xlsx = self.write("Annex-A.xlsx", b"PK\x03\x04 spreadsheet")

        self.assertIn("Annex-A.xlsx", document_paths([xlsx], self.root)[0])

    def test_missing_files_and_symlinks_are_skipped(self):
        pdf = self.write("TNG2600120EOI_I01.pdf")
        link = self.folder / "linked.pdf"
        link.symlink_to(pdf)

        self.assertEqual(document_paths([link, self.folder / "absent.pdf"], self.root), [])

    def test_duplicates_are_listed_once(self):
        pdf = self.write("TNG2600120EOI_I01.pdf")

        self.assertEqual(len(document_paths([pdf, pdf], self.root)), 1)

    def test_names_never_climb_out_of_the_root(self):
        pdf = self.write("TNG2600120EOI_I01.pdf")

        for path in document_paths([pdf], self.root):
            self.assertNotIn("..", Path(path).parts)
            self.assertFalse(Path(path).is_absolute())


class _FakeReporter:
    def __init__(self):
        self.lines = []

    def log(self, message):
        self.lines.append(message)

    def progress(self, *args, **kwargs):
        pass

    def update(self, **kwargs):
        pass


def _fake_downloader(page, tenders, dest_root, *, log=print, progress_callback=None):
    """Write one PDF per tender the way a real downloader would."""
    saved = []
    for tender in tenders:
        folder = Path(dest_root) / tender_dir_name(tender)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{tender.tender_no.split()[0]}.pdf"
        path.write_bytes(b"%PDF-1.4 " + tender.tender_no.encode())
        saved.append(path)
    return saved


def _empty_downloader(page, tenders, dest_root, *, log=print, progress_callback=None):
    return []


@contextmanager
def _fake_session(*args, **kwargs):
    yield (object(), object())


def _download_request(university, workspace, tenders, options=None):
    from web.serialize import tenders_to_payloads

    return {
        "university": university,
        "options": options or {},
        "tenders": tenders_to_payloads(tenders),
        "workspace": str(workspace),
    }


class TestDownloadIntegration(unittest.TestCase):
    """The prompt is an add-on the project screens ask for; it must never cost a download."""

    def workspace(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return Path(self.tmp.name)

    def run_download(
        self, university, workspace, downloader=_fake_downloader, options=None
    ):
        from web import tasks

        reporter = _FakeReporter()
        with patch.object(tasks, "_ensure_chromium"), patch.dict(
            tasks.SESSION_FACTORIES, {university: _fake_session}
        ), patch.dict(tasks.DOWNLOADERS, {university: downloader}):
            result = tasks.run_download(
                _download_request(university, workspace, [invitation()], options),
                reporter,
            )
        return result, reporter

    def test_hkust_project_download_carries_the_prompt(self):
        result, _ = self.run_download(
            "hkust", self.workspace(), options={"gemini": True}
        )

        self.assertTrue(Path(result["zip"]).is_file())
        self.assertIn("gemini", result)
        self.assertIn("TNG2600120 EOI", result["gemini"]["prompt"])
        self.assertIn("HKUST/", result["gemini"]["prompt"])
        self.assertNotIn("pack", result["gemini"])

    def test_a_project_download_can_skip_the_zip(self):
        workspace = self.workspace()
        result, _ = self.run_download(
            "hkust", workspace, options={"archive": False, "gemini": True}
        )

        self.assertEqual(result["zip"], "")
        self.assertTrue(result["files"])
        for relative in result["files"]:
            self.assertTrue((workspace / relative).is_file(), relative)
        self.assertIn("TNG2600120 EOI", result["gemini"]["prompt"])

    def test_an_open_tender_download_gets_no_prompt(self):
        # The open-tender tables keep their ZIP and never show Gemini.
        result, _ = self.run_download("hkust", self.workspace())

        self.assertTrue(Path(result["zip"]).is_file())
        self.assertNotIn("gemini", result)

    def test_cuhk_project_download_carries_a_cuhk_prompt(self):
        workspace = self.workspace()
        result, _ = self.run_download(
            "cuhk", workspace, options={"archive": False, "gemini": True}
        )

        self.assertEqual(result["zip"], "")
        self.assertTrue(result["files"])
        prompt = result["gemini"]["prompt"]
        self.assertIn("- University: CUHK", prompt)
        self.assertNotIn("HKUST", prompt)
        self.assertNotIn("CityU", prompt)

    def test_cityu_project_download_carries_a_cityu_prompt(self):
        result, _ = self.run_download(
            "cityu", self.workspace(), options={"archive": False, "gemini": True}
        )

        prompt = result["gemini"]["prompt"]
        self.assertIn("- University: CityU", prompt)
        self.assertNotIn("HKUST", prompt)
        self.assertNotIn("CUHK", prompt)

    def test_the_prompt_builder_receives_the_university(self):
        from web import tasks

        with patch.object(
            tasks, "build_gemini_prompt", return_value="brief"
        ) as builder:
            result, _ = self.run_download(
                "cuhk", self.workspace(), options={"archive": False, "gemini": True}
            )

        self.assertEqual(result["gemini"]["prompt"], "brief")
        self.assertEqual(builder.call_args.kwargs.get("university"), "cuhk")

    def test_nothing_downloaded_means_no_gemini_component(self):
        result, _ = self.run_download(
            "hkust", self.workspace(), _empty_downloader, options={"gemini": True}
        )

        self.assertNotIn("gemini", result)

    def test_a_failing_prompt_does_not_fail_the_download(self):
        from web import tasks

        workspace = self.workspace()
        reporter = _FakeReporter()
        with patch.object(tasks, "_ensure_chromium"), patch.dict(
            tasks.SESSION_FACTORIES, {"hkust": _fake_session}
        ), patch.dict(tasks.DOWNLOADERS, {"hkust": _fake_downloader}), patch.object(
            tasks, "build_gemini_prompt", side_effect=OSError("disk full")
        ):
            result = tasks.run_download(
                _download_request(
                    "hkust", workspace, [invitation()], {"gemini": True}
                ),
                reporter,
            )

        self.assertTrue(Path(result["zip"]).is_file())
        self.assertNotIn("gemini", result)
        self.assertTrue(
            any("Gemini prompt not built" in line for line in reporter.lines),
            reporter.lines,
        )


if __name__ == "__main__":
    unittest.main()
