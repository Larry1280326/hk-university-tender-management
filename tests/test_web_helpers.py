"""Unit tests for the pure helpers behind the Streamlit layer."""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scraper.models import TenderDocument, TenderNotice
from web.bootstrap import bootstrap_environment
from web.select_logic import (
    draftable_tenders,
    eoi_candidates,
    selected_tenders,
    skipped_for_missing_email,
    split_groups,
)
from web.secrets_bridge import (
    CertificateError,
    scalar_secrets,
    validate_certificate_bytes,
)
from web.serialize import tender_from_payload, tender_to_payload
from web.zip_utils import zip_directory

PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"


def _tender(**overrides):
    values = dict(
        tender_no="TNP2600099",
        description="Fitting Out Works",
        closing_date="2026-10-15",
        contact_person="Mimi WONG",
        contact_email="pumimi@ust.hk",
    )
    values.update(overrides)
    return TenderNotice(**values)


class TestSerialize(unittest.TestCase):
    def test_round_trip_including_documents(self):
        tender = _tender(
            invited=True,
            documents=[
                TenderDocument(
                    doc_type="Document",
                    file_name="spec.pdf",
                    subject="Spec",
                    updated_on="2026-10-01",
                    size="12 KB",
                )
            ],
        )
        rebuilt = tender_from_payload(tender_to_payload(tender))
        self.assertEqual(rebuilt, tender)
        self.assertEqual(rebuilt.documents[0].file_name, "spec.pdf")

    def test_unknown_fields_are_ignored(self):
        payload = tender_to_payload(_tender())
        payload["added_by_a_newer_ui"] = True
        self.assertEqual(tender_from_payload(payload), _tender())


class TestSecretsBridge(unittest.TestCase):
    def test_scalar_secrets_skip_nested_tables(self):
        values = {"A": "1", "nested": {"B": "2"}, "flag": True}
        self.assertEqual(scalar_secrets(values), {"A": "1", "flag": "True"})

    def test_accepts_a_pdf(self):
        self.assertEqual(validate_certificate_bytes(PDF_BYTES), PDF_BYTES)

    def test_rejects_a_non_document(self):
        with self.assertRaises(CertificateError):
            validate_certificate_bytes(b"not a document")

    def test_bootstrap_sets_env_and_runtime_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {}
            bootstrap_environment(
                {"HKUST_VENDOR_ID": "V1"},
                environ=env,
                runtime_dir=Path(tmp),
            )
            self.assertEqual(env["HKUST_VENDOR_ID"], "V1")
            self.assertTrue((Path(tmp) / "assets").is_dir())
            self.assertIn("--no-sandbox", env["PLAYWRIGHT_LAUNCH_ARGS"])

    def test_legacy_br_secret_is_not_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {}
            bootstrap_environment(
                {
                    "BR_CERTIFICATE_B64": "x" * 1000,
                    "HKUST_VENDOR_ID": "V1",
                },
                environ=env,
                runtime_dir=Path(tmp),
            )
            self.assertNotIn("BR_CERTIFICATE_B64", env)
            self.assertEqual(env["HKUST_VENDOR_ID"], "V1")

    def test_shell_environment_wins(self):
        env = {"HKUST_VENDOR_ID": "from-shell"}
        bootstrap_environment(
            {"HKUST_VENDOR_ID": "from-secrets"},
            environ=env,
            runtime_dir=Path(tempfile.mkdtemp()),
        )
        self.assertEqual(env["HKUST_VENDOR_ID"], "from-shell")


class TestUploadedCertificate(unittest.TestCase):
    def setUp(self):
        try:
            from web import ui
        except Exception as exc:  # streamlit extra not installed
            self.skipTest(f"web UI unavailable: {exc}")
        self.ui = ui
        self.original_path = ui.config.BR_CERTIFICATE_PATH

    def tearDown(self):
        if hasattr(self, "ui"):
            self.ui.config.BR_CERTIFICATE_PATH = self.original_path
            os.environ.pop("BR_CERTIFICATE_PATH", None)

    def test_pdf_is_saved_and_selected(self):
        class Uploaded:
            def getvalue(self):
                return PDF_BYTES

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.ui.config, "ASSETS_DIR", Path(tmp)):
                path, error = self.ui._store_uploaded_certificate(Uploaded())
            self.assertEqual(error, "")
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), PDF_BYTES)
            self.assertEqual(self.ui.config.BR_CERTIFICATE_PATH, path)

    def test_non_pdf_is_rejected(self):
        class Uploaded:
            def getvalue(self):
                return b"not a pdf"

        path, error = self.ui._store_uploaded_certificate(Uploaded())
        self.assertIsNone(path)
        self.assertIn("PDF", error)


class TestSelectLogic(unittest.TestCase):
    def test_grouping_and_selection(self):
        invited = _tender(tender_no="INV1", invited=True)
        open_with_email = _tender(tender_no="OPEN1")
        open_without_email = _tender(tender_no="OPEN2", contact_email="")
        tenders = [invited, open_with_email, open_without_email]

        self.assertEqual(split_groups(tenders), ([invited], [open_with_email, open_without_email]))
        self.assertEqual(
            selected_tenders(tenders, ["INV1", "OPEN2"]),
            [invited, open_without_email],
        )
        self.assertEqual(draftable_tenders(tenders), [open_with_email])
        self.assertEqual(skipped_for_missing_email(tenders), ["OPEN2"])

    def test_eoi_candidates(self):
        tender = _tender(needs_eoi=True)
        self.assertEqual(eoi_candidates([tender]), [tender])


class TestZipUtils(unittest.TestCase):
    def test_zips_relative_names_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "docs"
            (root / "tender a").mkdir(parents=True)
            (root / "tender a" / "spec.pdf").write_bytes(b"hello")
            archive = zip_directory(root, Path(tmp) / "out.zip")
            self.assertIsNotNone(archive)
            with zipfile.ZipFile(archive) as handle:
                names = handle.namelist()
            self.assertEqual(names, ["tender a/spec.pdf"])
            self.assertFalse(any(name.startswith("/") or ".." in Path(name).parts for name in names))

    def test_empty_directory_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "empty"
            root.mkdir()
            self.assertIsNone(zip_directory(root, Path(tmp) / "out.zip"))


if __name__ == "__main__":
    unittest.main()
