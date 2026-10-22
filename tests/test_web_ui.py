"""Tests for the invitation project screens shared by the three universities."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scraper.models import TenderNotice
from web.serialize import tenders_to_payloads


def _tender(**overrides):
    values = dict(
        tender_no="TNG2600120 EOI",
        description="Chip Fabrication Service",
        closing_date="29-Oct-2026 15:00:00",
        contact_person="Mimi WONG",
        contact_email="pumimi@ust.hk",
        invited=True,
    )
    values.update(overrides)
    return TenderNotice(**values)


class _Click:
    """Stand-in for the object Streamlit stores for a ButtonColumn click."""

    def __init__(self, row):
        self.row = row
        self.label = ":material/arrow_forward:"


class _UiTestCase(unittest.TestCase):

    def setUp(self):
        try:
            from web import ui
        except Exception as exc:  # streamlit extra not installed
            self.skipTest(f"web UI unavailable: {exc}")
        self.ui = ui


class TestProjectKeys(_UiTestCase):

    def test_keys_are_unique_per_university(self):
        refs = {
            self.ui._project_ref_key(university)
            for university in ("hkust", "cuhk", "cityu")
        }
        clicks = {
            self.ui._project_click_key(university)
            for university in ("hkust", "cuhk", "cityu")
        }

        self.assertEqual(len(refs), 3)
        self.assertEqual(len(clicks), 3)
        self.assertEqual(self.ui._project_ref_key("cuhk"), "cuhk_invitation_ref")
        self.assertEqual(self.ui._project_click_key("cityu"), "open_cityu_invitation")

    def test_a_click_maps_to_the_row_it_was_drawn_on(self):
        self.assertEqual(
            self.ui._project_ref_for_click(("A", "B"), _Click(1)), "B"
        )

    def test_a_click_outside_the_rows_maps_to_nothing(self):
        self.assertIsNone(self.ui._project_ref_for_click(("A",), _Click(3)))
        self.assertIsNone(self.ui._project_ref_for_click(("A",), _Click(-1)))

    def test_a_missing_click_maps_to_nothing(self):
        self.assertIsNone(self.ui._project_ref_for_click(("A",), None))

    def test_selection_writes_only_its_own_university(self):
        state = {"open_cuhk_invitation": _Click(1)}

        with patch.object(self.ui.st, "session_state", state):
            self.ui._select_invitation_project("cuhk", ("A", "B"))

        self.assertEqual(state["cuhk_invitation_ref"], "B")
        self.assertNotIn("hkust_invitation_ref", state)

    def test_each_table_owns_its_dataframe_and_arrow_keys(self):
        tenders = [_tender()]
        frames = []
        buttons = []

        def button(*args, **kwargs):
            buttons.append((args, kwargs))
            return object()

        with patch.object(
            self.ui.st, "dataframe", side_effect=lambda *a, **k: frames.append(k)
        ), patch.object(
            self.ui.st.column_config, "ButtonColumn", side_effect=button
        ):
            self.ui._invitation_table("cuhk", tenders)
            self.ui._invitation_table("cityu", tenders)

        self.assertEqual([frame["key"] for frame in frames], [
            "invitations_cuhk",
            "invitations_cityu",
        ])
        self.assertEqual(
            [entry[1]["key"] for entry in buttons],
            ["open_cuhk_invitation", "open_cityu_invitation"],
        )
        self.assertEqual(
            buttons[0][1]["args"], ("cuhk", (tenders[0].tender_no,))
        )
        self.assertEqual(
            buttons[1][1]["on_click"], self.ui._select_invitation_project
        )


class TestInvitationScreens(_UiTestCase):

    def test_cuhk_invitations_use_the_arrow_table_not_a_select_grid(self):
        result = {"tenders": tenders_to_payloads([_tender()])}

        with patch.object(self.ui, "_invitation_table") as table, patch.object(
            self.ui, "_selection_table"
        ) as selection, patch.object(
            self.ui, "_render_process_action"
        ) as process, patch.object(
            self.ui, "_active_job_id", return_value=""
        ), patch.object(
            self.ui.st, "markdown"
        ), patch.object(
            self.ui.st, "caption"
        ):
            self.ui._render_scan_result("cuhk", result)

        table.assert_called_once()
        self.assertEqual(table.call_args.args[0], "cuhk")
        selection.assert_not_called()
        process.assert_not_called()

    def test_open_tenders_keep_their_select_grid(self):
        open_tender = _tender(invited=False, needs_eoi=True)
        result = {"tenders": tenders_to_payloads([open_tender])}

        with patch.object(self.ui, "_invitation_table") as table, patch.object(
            self.ui, "_selection_table", return_value=[]
        ) as selection, patch.object(
            self.ui, "_render_process_action"
        ) as process, patch.object(
            self.ui, "_active_job_id", return_value=""
        ), patch.object(
            self.ui.st, "markdown"
        ), patch.object(
            self.ui.st, "caption"
        ):
            self.ui._render_scan_result("cuhk", result)

        table.assert_not_called()
        selection.assert_called_once()
        self.assertEqual(selection.call_args.args[1], "select_cuhk_open")
        self.assertEqual(process.call_args.kwargs["group"], "open")

    def test_a_project_screen_fetches_only_that_project_with_gemini(self):
        tender = _tender()
        state = {"scan_result_cuhk": {"tenders": tenders_to_payloads([tender])}}

        with patch.object(self.ui.st, "session_state", state), patch.object(
            self.ui.st, "button", return_value=False
        ), patch.object(self.ui.st, "subheader"), patch.object(
            self.ui.st, "markdown"
        ), patch.object(
            self.ui.st, "caption"
        ), patch.object(
            self.ui, "_render_fetch_action"
        ) as fetch:
            self.ui._render_project_screen("cuhk", tender.tender_no, False)

        fetch.assert_called_once()
        args, kwargs = fetch.call_args
        self.assertEqual(args[0], "cuhk")
        self.assertEqual([item.tender_no for item in args[1]], [tender.tender_no])
        self.assertEqual(kwargs, {"group": "project", "archive": False, "gemini": True})

    def test_clicking_fetch_passes_the_project_options(self):
        tender = _tender()

        with patch.object(self.ui.st, "session_state", {}), patch.object(
            self.ui.st, "button", return_value=True
        ), patch.object(self.ui, "_active_job_id", return_value=""), patch.object(
            self.ui, "_start_job"
        ) as start:
            self.ui._render_fetch_action(
                "cuhk",
                [tender],
                False,
                group="project",
                archive=False,
                gemini=True,
            )

        args, kwargs = start.call_args
        self.assertEqual(args, ("download", "cuhk"))
        self.assertEqual(kwargs["options"], {"archive": False, "gemini": True})
        self.assertEqual(kwargs["group"], "project")

    def test_an_invitation_that_still_needs_eoi_asks_before_submitting(self):
        tender = _tender(needs_eoi=True)

        with patch.object(self.ui.st, "session_state", {}), patch.object(
            self.ui.st, "checkbox", return_value=False
        ) as checkbox, patch.object(
            self.ui.st, "button"
        ) as button, patch.object(
            self.ui, "_active_job_id", return_value=""
        ):
            self.ui._render_fetch_action(
                "cuhk",
                [tender],
                False,
                group="project",
                archive=False,
                gemini=True,
            )

        checkbox.assert_called_once()
        self.assertEqual(
            checkbox.call_args.kwargs["key"],
            f"eoi_confirm_cuhk_project_{tender.tender_no}",
        )
        self.assertEqual(button.call_args.args[0], "Submit EOI & Fetch Documents")
        self.assertTrue(button.call_args.kwargs["disabled"])

    def test_back_clears_only_its_own_project(self):
        state = {
            "cuhk_invitation_ref": "P1",
            "hkust_invitation_ref": "H1",
            "scan_result_cuhk": {"tenders": tenders_to_payloads([_tender()])},
        }

        with patch.object(self.ui.st, "session_state", state), patch.object(
            self.ui.st, "button", return_value=True
        ), patch.object(self.ui.st, "rerun") as rerun:
            self.ui._render_project_screen("cuhk", "P1", False)

        self.assertNotIn("cuhk_invitation_ref", state)
        self.assertEqual(state["hkust_invitation_ref"], "H1")
        rerun.assert_called_once()


class TestOpenTenderEOI(_UiTestCase):

    def test_the_open_table_submits_an_eoi_only(self):
        tender = _tender(invited=False, needs_eoi=True)

        with patch.object(self.ui.st, "session_state", {}), patch.object(
            self.ui.st, "checkbox", return_value=True
        ), patch.object(self.ui.st, "button", return_value=True), patch.object(
            self.ui.st, "caption"
        ), patch.object(
            self.ui, "_active_job_id", return_value=""
        ), patch.object(
            self.ui, "_start_job"
        ) as start:
            self.ui._render_process_action("cuhk", [tender], False, group="open")

        args, kwargs = start.call_args
        self.assertEqual(args, ("download", "cuhk"))
        self.assertEqual(kwargs["options"], {"eoi_only": True})
        self.assertEqual(kwargs["group"], "open")

    def test_the_eoi_button_needs_the_confirmation(self):
        tender = _tender(invited=False, needs_eoi=True)

        with patch.object(self.ui.st, "session_state", {}), patch.object(
            self.ui.st, "checkbox", return_value=False
        ), patch.object(self.ui.st, "button") as button, patch.object(
            self.ui.st, "caption"
        ), patch.object(
            self.ui, "_active_job_id", return_value=""
        ):
            self.ui._render_process_action("cuhk", [tender], False, group="open")

        self.assertEqual(button.call_args.args[0], "Submit EOI (1)")
        self.assertTrue(button.call_args.kwargs["disabled"])

    def test_an_eoi_result_moves_the_tender_into_invitations(self):
        result = {"tenders": tenders_to_payloads([
            _tender(tender_no="T-1", invited=False, needs_eoi=True)
        ])}
        state = {
            "scan_result_download_cuhk_open": {"eoi_submitted": ["T-1"]},
            "select_cuhk_open": {0: {"Select": True}},
            "eoi_confirm_cuhk_open": True,
        }

        with patch.object(self.ui.st, "session_state", state), patch.object(
            self.ui.st, "rerun"
        ) as rerun:
            submitted = self.ui._apply_eoi_result("cuhk", result)

        self.assertEqual(submitted, ["T-1"])
        self.assertTrue(result["tenders"][0]["invited"])
        self.assertFalse(result["tenders"][0]["needs_eoi"])
        self.assertNotIn("select_cuhk_open", state)
        self.assertNotIn("eoi_confirm_cuhk_open", state)
        rerun.assert_called_once()


class TestScanCleanup(_UiTestCase):

    def test_a_scan_clears_only_its_own_project_state(self):
        state = {
            "cuhk_invitation_ref": "P1",
            "open_cuhk_invitation": _Click(0),
            "fetched_refs_cuhk_project": ["P1"],
            "scan_result_download_cuhk_project": {"zip": "x"},
            "scan_result_download_cuhk_project_error": "boom",
            "scan_result_download_cuhk_open": {"eoi_submitted": ["T-1"]},
            "hkust_invitation_ref": "H1",
            "scan_result_hkust": {"tenders": []},
        }
        manager = MagicMock()
        manager.start.return_value = MagicMock(job_id="job-1")

        with patch.object(self.ui, "_manager", return_value=manager), patch.object(
            self.ui, "_workspace", return_value=Path("/tmp")
        ), patch.object(self.ui.st, "session_state", state), patch.object(
            self.ui.st, "rerun"
        ):
            self.ui._start_job("scan", "cuhk")

        for key in (
            "cuhk_invitation_ref",
            "open_cuhk_invitation",
            "fetched_refs_cuhk_project",
            "scan_result_download_cuhk_project",
            "scan_result_download_cuhk_project_error",
            "scan_result_download_cuhk_open",
        ):
            self.assertNotIn(key, state)
        self.assertEqual(state["hkust_invitation_ref"], "H1")
        self.assertIn("scan_result_hkust", state)
        self.assertEqual(state["active_job"], "job-1")


if __name__ == "__main__":
    unittest.main()
