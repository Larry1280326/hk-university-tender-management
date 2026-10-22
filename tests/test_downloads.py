"""Tests for turning a download job's file list into per-file downloads."""

import tempfile
import unittest
from pathlib import Path

from web.downloads import DEFAULT_MIME, download_mime, downloadable_files


class TestDownloadableFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.folder = self.workspace / "documents" / "HKUST" / "Project"
        self.folder.mkdir(parents=True)
        self.pdf = self.folder / "form.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 test")

    def relative(self, path):
        return path.relative_to(self.workspace).as_posix()

    def outside_file(self):
        outside = tempfile.TemporaryDirectory(dir=self.workspace.parent)
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "secret.pdf"
        secret.write_bytes(b"%PDF-1.4 secret")
        return secret

    def test_existing_files_are_returned_in_order(self):
        second = self.folder / "annex.xlsx"
        second.write_bytes(b"PK\x03\x04")

        self.assertEqual(
            downloadable_files(
                [self.relative(self.pdf), self.relative(second)], self.workspace
            ),
            [self.pdf.resolve(), second.resolve()],
        )

    def test_duplicates_are_returned_once(self):
        relative = self.relative(self.pdf)

        self.assertEqual(
            downloadable_files([relative, relative], self.workspace),
            [self.pdf.resolve()],
        )

    def test_missing_files_are_skipped(self):
        self.assertEqual(
            downloadable_files(
                [self.relative(self.folder / "absent.pdf")], self.workspace
            ),
            [],
        )

    def test_directories_are_skipped(self):
        self.assertEqual(
            downloadable_files([self.relative(self.folder)], self.workspace), []
        )

    def test_symlinks_are_skipped(self):
        link = self.folder / "linked.pdf"
        link.symlink_to(self.pdf)

        self.assertEqual(
            downloadable_files([self.relative(link)], self.workspace), []
        )

    def test_absolute_paths_outside_the_workspace_are_skipped(self):
        secret = self.outside_file()

        self.assertEqual(downloadable_files([str(secret)], self.workspace), [])

    def test_relative_paths_climbing_out_are_skipped(self):
        secret = self.outside_file()
        climbing = Path("..") / secret.parent.name / secret.name

        self.assertEqual(
            downloadable_files([climbing.as_posix()], self.workspace), []
        )


class TestDownloadMime(unittest.TestCase):

    def test_known_types(self):
        self.assertEqual(download_mime(Path("form.pdf")), "application/pdf")

    def test_unknown_types_fall_back_to_binary(self):
        self.assertEqual(download_mime(Path("form.qqq")), DEFAULT_MIME)


if __name__ == "__main__":
    unittest.main()
