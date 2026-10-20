"""Unit tests for the CUHK (CUPro) authentication, parser, EOI submission, and downloader."""

import contextlib
import io
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import MagicMock, patch

from playwright.sync_api import sync_playwright

import app
import config
from scraper.cuhk_auth import CUHKAuthManager
from scraper.cuhk_documents import _unique_filename, process_cuhk_tenders
from scraper.cuhk_parser import CUHKTenderParser
from scraper.tender_parser import TenderDocument, TenderNotice


RFQ_LIST_ALL_CLOSED_HTML = """<!DOCTYPE html>
<html><body>
<div class="headtext1">Request for Quotation - Search Result (Record 1 - 1 of 1)</div>
<table>
  <tr class="trtext1"><th></th><th>Ref</th><th>Subject</th><th>Status</th><th>Issue Date</th><th>Closing Date</th></tr>
  <tr class="trtext2">
    <td><span>1.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('13540'); return false">Q-20262875</a></span></td>
    <td><span title="CURI quotation for motors">CURI quotation for motors</span></td>
    <td><span>Closed&nbsp;</span></td>
    <td><span>2026/09/29 (Tue)</span></td>
    <td><span>2026/10/13 (Tue) 15:00</span></td>
  </tr>
</table>
<div>Page: <b>1</b> <a href="List.aspx?Page=2">2</a></div>
<form method="post" action="View.aspx" name="ViewForm" style="display:none">
  <input type="hidden" name="Page" value="1" />
  <input type="hidden" name="tender_id" value="" />
</form>
</body></html>
"""

TENDER_LIST_PAGE1_HTML = """<!DOCTYPE html>
<html><body>
<div class="headtext1">Tender Invitation - Search Result (Record 1 - 3 of 20)</div>
<table>
  <tr class="trtext1"><th></th><th>Ref</th><th>Subject</th><th>Status</th><th>Issue Date</th><th>Closing Date</th></tr>
  <tr class="trtext2">
    <td><span>1.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('13299'); return false">T-20260393</a></span></td>
    <td><span title="CUHK Business School Social Media Marketing Project 2026-2027">CUHK Business School Social Media</span></td>
    <td><span>Issued&nbsp;</span></td>
    <td><span>2026/09/30 (Wed)</span></td>
    <td><span>2026/11/05 (Thu) 14:30</span></td>
  </tr>
  <tr class="trtext3">
    <td><span>2.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('13211'); return false">T-20260386</a></span></td>
    <td><span title="Tender for Polyethylene Bags">Tender for Polyethylene Bags</span></td>
    <td><span>Closed&nbsp;</span></td>
    <td><span>2026/09/30 (Wed)</span></td>
    <td><span>2026/10/15 (Thu) 14:30</span></td>
  </tr>
  <tr class="trtext2">
    <td><span>3.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('13603'); return false">T-20260422</a></span></td>
    <td><span title="Provision of GPU Server">Provision of GPU Server</span></td>
    <td><span>Issued&nbsp;</span></td>
    <td><span>2026/10/09 (Fri)</span></td>
    <td><span>2026/10/22 (Thu) 14:30</span></td>
  </tr>
</table>
<div>Page: <b>1</b> <a href="List.aspx?Page=2">2</a> <a href="List.aspx?Page=3">3</a></div>
<form method="post" action="View.aspx" name="ViewForm" style="display:none">
  <input type="hidden" name="Page" value="1" />
  <input type="hidden" name="tender_id" value="" />
</form>
</body></html>
"""

TENDER_LIST_PAGE2_ALL_CLOSED_HTML = """<!DOCTYPE html>
<html><body>
<div class="headtext1">Tender Invitation - Search Result (Record 4 - 5 of 20)</div>
<table>
  <tr class="trtext1"><th></th><th>Ref</th><th>Subject</th><th>Status</th><th>Issue Date</th><th>Closing Date</th></tr>
  <tr class="trtext2">
    <td><span>4.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('11000'); return false">T-20260100</a></span></td>
    <td><span title="Old Closed Tender">Old Closed Tender</span></td>
    <td><span>Closed&nbsp;</span></td>
    <td><span>2026/08/01 (Sat)</span></td>
    <td><span>2026/08/15 (Sat) 14:30</span></td>
  </tr>
</table>
<div>Page: <a href="List.aspx?Page=1">1</a> <b>2</b> <a href="List.aspx?Page=3">3</a></div>
<form method="post" action="View.aspx" name="ViewForm" style="display:none">
  <input type="hidden" name="Page" value="2" />
  <input type="hidden" name="tender_id" value="" />
</form>
</body></html>
"""

VIEW_13299_UNLOCKED_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <div class="headtext1">Tender Invitation - View Details</div>
  <table>
    <tr><td class="tdtext1">Ref</td><td class="tdtext2">T-20260393</td></tr>
    <tr valign="top">
      <td class="tdtext1"><span>Registration Form for Briefing / Site Visit</span></td>
      <td colspan="3" class="tdtext2">
        <div><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=47115">Click to download</a></div>
      </td>
    </tr>
  </table>
  <table>
    <tr valign="top">
      <td class="tdtext1"><div>Document(s)</div></td>
      <td class="tdtext2">
        <table>
          <tr><td>Description</td><td>File</td><td>File Size</td></tr>
          <tr valign="top">
            <td><span>Appendix: Brand Guidelines</span></td>
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=46772" title="Brand_Book.pdf">Click to download</a></span></td>
            <td><span>17 MB</span></td>
          </tr>
          <tr valign="top">
            <td><span>T-20260393_TENDER SPECIFICATIONS</span></td>
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=47454" title="SPECS.pdf">Click to download</a></span></td>
            <td><span>3 MB</span></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
  <table>
    <tr valign="top">
      <td class="tdtext1"><div>TA1</div></td>
      <td colspan="3" class="tdtext2">
        <table>
          <tr><td>Description</td><td>File</td><td>File Size</td></tr>
          <tr valign="top">
            <td><span>T-20260393_TENDER SPECIFICATIONS (Addendum)</span></td>
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=47537" title="SPECS.pdf">Click to download</a></span></td>
            <td><span>3 MB</span></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
  <table>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Name</span></td>
      <td colspan="3" class="tdtext2"><div>vanessaliu@cuhk.edu.hk</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Tel. Number</span></td>
      <td class="tdtext2"><div>39431780</div></td>
      <td class="tdtext1"><span>#1 Fax. Number</span></td>
      <td class="tdtext2"><div></div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Email Address</span></td>
      <td colspan="3" class="tdtext2"><div>vanessaliu@cuhk.edu.hk</div></td>
    </tr>
  </table>
  <center>
    <input type="button" value="Tender Submission" class="btntext2" />
    <input type="button" value="Back" class="btntext" />
  </center>
</div>
</body></html>
"""

VIEW_13603_NEEDS_EOI_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <div class="headtext1">Tender Invitation - View Details</div>
  <table>
    <tr><td class="tdtext1">Ref</td><td class="tdtext2">T-20260422</td></tr>
  </table>
  <table>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Name</span></td>
      <td colspan="3" class="tdtext2"><div>johnny.cw.lam@cuhk.edu.hk</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Tel. Number</span></td>
      <td class="tdtext2"><div>39438494</div></td>
      <td class="tdtext1"><span>#1 Fax. Number</span></td>
      <td class="tdtext2"><div></div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Email Address</span></td>
      <td colspan="3" class="tdtext2"><div>johnny.cw.lam@cuhk.edu.hk</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#2 Name</span></td>
      <td colspan="3" class="tdtext2"><div>Phyllis HM Leung</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#2 Email Address</span></td>
      <td colspan="3" class="tdtext2"><div>pyhllis@cuhk.edu.hk</div></td>
    </tr>
  </table>
  <center>
    <input type="button" value="Expression of Interest" class="btntext2" onclick="document.NewEOI.submit()" />
    <form method="post" action="/Supplier/en/Tender/EOI/New.aspx" name="NewEOI" style="display:none;">
      <input type="hidden" name="Page" value="1" />
      <input type="hidden" name="tender_id" value="13603" />
    </form>
  </center>
</div>
</body></html>
"""

VIEW_13603_UNLOCKED_AFTER_EOI_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <div class="headtext1">Tender Invitation - View Details</div>
  <table>
    <tr valign="top">
      <td class="tdtext1"><div>Document(s)</div></td>
      <td class="tdtext2">
        <table>
          <tr><td>Description</td><td>File</td><td>File Size</td></tr>
          <tr valign="top">
            <td><span>GPU Server Tender Document</span></td>
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=48001" title="GPU_Server_Spec.pdf">Click to download</a></span></td>
            <td><span>512 KB</span></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
</body></html>
"""

EOI_NEW_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <div class="headtext1">Tender Invitation - Expression of Interest</div>
  <form method="post" action="/Supplier/en/Tender/EOI/Create.aspx" name="PostForm">
    <input type="hidden" name="Page" value="1">
    <input type="hidden" name="tender_id" value="13603">
    <input type="radio" name="response_type" value="1"> Yes
    <input type="radio" name="response_type" value="2"> No
    <input type="button" value="Submit" name="SubmitButton" onclick="document.PostForm.submit()">
  </form>
</div>
</body></html>
"""


class TestUniqueFilename(unittest.TestCase):

    def test_first_occurrence_keeps_original_name(self):
        used = set()
        self.assertEqual(
            _unique_filename("SPECS.pdf", "/DownloadFile.aspx?file_id=47454", used),
            "SPECS.pdf",
        )

    def test_duplicate_filename_is_disambiguated_by_file_id(self):
        used = {"SPECS.pdf"}
        self.assertEqual(
            _unique_filename("SPECS.pdf", "/DownloadFile.aspx?file_id=47537", used),
            "SPECS_47537.pdf",
        )


class TestCUHKAuthManager(unittest.TestCase):

    def test_missing_credentials_raises_value_error(self):
        auth = CUHKAuthManager()
        with patch.object(config, "CUHK_USER_ID", ""), patch.object(
            config, "CUHK_PASSWORD", ""
        ):
            with self.assertRaises(ValueError):
                auth.perform_login(MagicMock(), user_id="", password="")


class TestCUHKParserAndEOIDownload(unittest.TestCase):
    """End-to-end tests of CUHKTenderParser and process_cuhk_tenders against routed CUPro HTML."""

    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.context = CUHKAuthManager().get_context(self._browser)
        self.page = self.context.new_page()
        self.eoi_submitted_ids = set()
        self.visited_urls = []

        def handle_route(route, request):
            url = request.url
            self.visited_urls.append(url)

            if "RFQ/Search.aspx" in url or "RFQ/List.aspx" in url:
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=RFQ_LIST_ALL_CLOSED_HTML,
                )
            elif "Tender/Search.aspx" in url or (
                "Tender/List.aspx" in url and "Page=2" not in url and "Page=3" not in url
            ):
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=TENDER_LIST_PAGE1_HTML,
                )
            elif "Tender/List.aspx?Page=2" in url:
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=TENDER_LIST_PAGE2_ALL_CLOSED_HTML,
                )
            elif "Tender/View.aspx" in url:
                post_data = parse_qs(request.post_data or "")
                tid = post_data.get("tender_id", [""])[0]
                if tid == "13299":
                    body = VIEW_13299_UNLOCKED_HTML
                elif tid == "13603":
                    body = (
                        VIEW_13603_UNLOCKED_AFTER_EOI_HTML
                        if "13603" in self.eoi_submitted_ids
                        else VIEW_13603_NEEDS_EOI_HTML
                    )
                else:
                    body = "<html><body><div id='MainContent'>Not found</div></body></html>"
                route.fulfill(status=200, content_type="text/html", body=body)
            elif "Tender/EOI/New.aspx" in url:
                route.fulfill(status=200, content_type="text/html", body=EOI_NEW_HTML)
            elif "Tender/EOI/Create.aspx" in url:
                post_data = parse_qs(request.post_data or "")
                if post_data.get("response_type") == ["1"]:
                    tid = post_data.get("tender_id", [""])[0]
                    self.eoi_submitted_ids.add(tid)
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<html><body><div id='MainContent'>EOI Created</div></body></html>",
                )
            elif "DownloadFile.aspx" in url:
                file_map = {
                    "47115": ("Registration_Form.pdf", b"%PDF-reg"),
                    "46772": ("Brand_Book.pdf", b"%PDF-brand"),
                    "47454": ("SPECS.pdf", b"%PDF-specs-v1"),
                    "47537": ("SPECS.pdf", b"%PDF-specs-v2-addendum"),
                    "48001": ("GPU_Server_Spec.pdf", b"%PDF-gpu"),
                }
                fid = parse_qs(url.split("?", 1)[-1]).get("file_id", [""])[0]
                fname, content = file_map.get(fid, ("doc.pdf", b"%PDF"))
                route.fulfill(
                    status=200,
                    headers={
                        "Content-Type": "application/pdf",
                        "Content-Disposition": f'attachment; filename="{fname}"',
                    },
                    body=content,
                )
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("**/*", handle_route)

    def tearDown(self):
        self.context.close()

    def test_extract_tenders_filters_issued_and_stops_pagination_early(self):
        parser = CUHKTenderParser(self.page)
        tenders = parser.extract_tenders()

        # Page 1 had 2 Issued rows (T-20260393 and T-20260422) and 1 Closed row.
        # Page 2 had 0 Issued rows, so Page 3 must never be requested.
        self.assertEqual([t.tender_no for t in tenders], ["T-20260393", "T-20260422"])
        self.assertFalse(any("Page=3" in u for u in self.visited_urls))

        # 1. T-20260393: EOI already submitted -> invited=True, needs_eoi=False, 4 documents
        t_unlocked = tenders[0]
        self.assertTrue(t_unlocked.invited)
        self.assertFalse(t_unlocked.needs_eoi)
        self.assertEqual(t_unlocked.portal_id, "13299")
        self.assertEqual(t_unlocked.contact_email, "vanessaliu@cuhk.edu.hk")
        self.assertEqual(t_unlocked.contact_phone, "39431780")
        # #1 Name was an email address, so contact_person falls back to Sir/Madam
        self.assertEqual(t_unlocked.contact_person, "Sir/Madam")
        self.assertEqual(len(t_unlocked.documents), 4)

        # 2. T-20260422: Open tender -> invited=False, needs_eoi=True
        t_open = tenders[1]
        self.assertFalse(t_open.invited)
        self.assertTrue(t_open.needs_eoi)
        self.assertEqual(t_open.portal_id, "13603")
        self.assertEqual(t_open.contact_person, "Phyllis HM Leung")
        self.assertEqual(t_open.contact_email, "johnny.cw.lam@cuhk.edu.hk")
        self.assertEqual(t_open.documents, [])

    def test_process_cuhk_tenders_submits_eoi_and_downloads_disambiguated_files(self):
        parser = CUHKTenderParser(self.page)
        tenders = parser.extract_tenders()

        with tempfile.TemporaryDirectory() as tmpdir:
            doc_dir = Path(tmpdir)
            with patch.object(config, "DOCUMENT_DIR", doc_dir):
                saved = process_cuhk_tenders(self.page, tenders)

            # T-20260393 has 4 files (including two named SPECS.pdf from Document(s) and Addendum)
            # T-20260422 had EOI submitted automatically, unlocking 1 file (GPU_Server_Spec.pdf)
            self.assertIn("13603", self.eoi_submitted_ids)
            self.assertTrue(tenders[1].invited)
            self.assertFalse(tenders[1].needs_eoi)

            saved_names = [p.name for p in saved]
            self.assertEqual(
                saved_names,
                [
                    "Registration_Form.pdf",
                    "Brand_Book.pdf",
                    "SPECS.pdf",
                    "SPECS_47537.pdf",
                    "GPU_Server_Spec.pdf",
                ],
            )
            # Verify files are saved under <DOCUMENT_DIR>/CUHK/<project_name>/ and addendum did not overwrite
            t1_dir = (
                doc_dir
                / "CUHK"
                / "CUHK Business School Social Media Marketing Project 2026-2027"
            )
            t2_dir = doc_dir / "CUHK" / "Provision of GPU Server"
            self.assertTrue((t2_dir / "GPU_Server_Spec.pdf").is_file())
            self.assertEqual((t1_dir / "SPECS.pdf").read_bytes(), b"%PDF-specs-v1")
            self.assertEqual(
                (t1_dir / "SPECS_47537.pdf").read_bytes(), b"%PDF-specs-v2-addendum"
            )


@contextmanager
def fake_cuhk_session(page):
    with patch.object(app, "cuhk_portal_session") as session:
        session.return_value.__enter__.return_value = (page, MagicMock())
        yield session


class TestCUHKCLIFlow(unittest.TestCase):

    def test_cuhk_selection_prompts_with_eoi_count_and_downloads(self):
        open_cuhk = TenderNotice(
            tender_no="T-20260422",
            description="Provision of GPU Server",
            closing_date="2026/10/22 (Thu) 14:30",
            contact_person="Phyllis HM Leung",
            contact_email="johnny.cw.lam@cuhk.edu.hk",
            invited=False,
            needs_eoi=True,
            portal_id="13603",
        )
        page = MagicMock()
        saved_file = Path("/tmp/T-20260422/GPU_Server_Spec.pdf")

        with patch.object(
            app, "select_suitable_tenders", return_value=[open_cuhk]
        ), patch.object(app, "questionary") as q, patch.object(
            app, "process_cuhk_tenders", return_value=[saved_file]
        ) as proc, fake_cuhk_session(page):
            q.confirm.return_value.ask.return_value = True

            app.handle_cuhk_selection_and_download([open_cuhk], headless=True)

        confirm_prompt = q.confirm.call_args.args[0]
        self.assertIn("Submit EOI for 1 open tender(s)", confirm_prompt)
        proc.assert_called_once_with(page, [open_cuhk])

    def test_declining_cuhk_confirmation_opens_no_browser(self):
        invited_cuhk = TenderNotice(
            tender_no="T-20260393",
            description="CUHK Business School Social Media Marketing Project",
            closing_date="2026/11/05 (Thu) 14:30",
            contact_person="Sir/Madam",
            contact_email="vanessaliu@cuhk.edu.hk",
            invited=True,
            needs_eoi=False,
            portal_id="13299",
            documents=[
                TenderDocument("Document", "SPECS.pdf", "SPECS", "", "3 MB")
            ],
        )

        with patch.object(
            app, "select_suitable_tenders", return_value=[invited_cuhk]
        ), patch.object(app, "questionary") as q, patch.object(
            app, "process_cuhk_tenders"
        ) as proc, fake_cuhk_session(MagicMock()) as session:
            q.confirm.return_value.ask.return_value = False

            app.handle_cuhk_selection_and_download([invited_cuhk], headless=True)

        proc.assert_not_called()
        session.assert_not_called()


if __name__ == "__main__":
    unittest.main()

