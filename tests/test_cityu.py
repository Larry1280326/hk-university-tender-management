"""Unit tests for CityU (ETS) authentication, parsing, EOI submission, and downloads."""

import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import MagicMock, patch

from playwright.sync_api import sync_playwright

import config
from scraper.cityu_auth import CityUAuthManager
from scraper.cityu_documents import _unique_filename, process_cityu_tenders
from scraper.cityu_parser import CityUTenderParser

BASE = "https://etender.cityu.edu.hk"


LOGIN_INDEX_HTML = """<!DOCTYPE html>
<html><body><div id="MainContent">
<form method="POST" action="/Supplier/en/Login/AcceptTerms.aspx" name="LoginForm"
      style="visibility: hidden; display: none">
  <input type="hidden" name="CSRF_Token" value="token-index" />
</form>
</div></body></html>
"""


TERMS_HTML = """<!DOCTYPE html>
<html><body><div id="MainContent">
<form method="post" name="PostForm" action="New.aspx">
  <input type="hidden" name="CSRF_Token" value="token-terms" />
  <input type="checkbox" name="terms" title="terms1">
  <input type="checkbox" name="terms" title="terms2">
  <input type="checkbox" name="terms" title="terms3">
  <input type="button" value="Accept" onclick="doAcceptTerms()" />
</form>
</div></body></html>
"""


LOGIN_NEW_HTML = """<!DOCTYPE html>
<html><body><div id="MainContent">
<form method="post" action="Submit.aspx" name="PostForm" onsubmit="return false">
  <input type="hidden" name="CSRF_Token" value="token-new" />
  <input type="text" name="username" maxlength="20">
  <input type="password" name="password" maxlength="16">
  <input type="button" value="Login" name="SubmitButton" onclick="doSubmit()">
</form>
</div></body></html>
"""


LOGIN_OK_HTML = """<!DOCTYPE html><html><body>Logged in</body></html>"""


RFQ_LIST_ALL_CLOSED_HTML = """<!DOCTYPE html>
<html><body>
<div class="headtext1">Request for Quotation - Search Result (Record 1 - 1 of 1)</div>
<table>
  <tr class="trtext1"><th></th><th>Ref</th><th>Subject</th><th>Status</th><th>Issue Date</th><th>Closing Date</th></tr>
  <tr class="trtext2">
    <td><span>1.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('9001'); return false">Q-20262875</a></span></td>
    <td><span title="CityU quotation for motors">CityU quotation for motors</span></td>
    <td><span>Closed&nbsp;</span></td>
    <td><span>2026/09/29 (Tue)</span></td>
    <td><span>2026/10/13 (Tue) 15:00</span></td>
  </tr>
</table>
<div>Page: <b>1</b></div>
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
    <td><span><a href="javascript:void()" onclick="doView('20101'); return false">T-20260001</a></span></td>
    <td><span title="Provision of Campus Network Switches">Provision of Campus Network</span></td>
    <td><span>Issued&nbsp;</span></td>
    <td><span>2026/09/30 (Wed)</span></td>
    <td><span>2026/11/05 (Thu) 14:30</span></td>
  </tr>
  <tr class="trtext3">
    <td><span>2.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('20102'); return false">T-20260002</a></span></td>
    <td><span title="Closed Tender">Closed Tender</span></td>
    <td><span>Closed&nbsp;</span></td>
    <td><span>2026/09/30 (Wed)</span></td>
    <td><span>2026/10/15 (Thu) 14:30</span></td>
  </tr>
  <tr class="trtext2">
    <td><span>3.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('20103'); return false">T-20260003</a></span></td>
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
    <td><span><a href="javascript:void()" onclick="doView('20001'); return false">T-20250001</a></span></td>
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


TENDER_LIST_PAGE3_HTML = """<!DOCTYPE html>
<html><body>
<div class="headtext1">Tender Invitation - Search Result (Record 6 - 7 of 20)</div>
<table>
  <tr class="trtext1"><th></th><th>Ref</th><th>Subject</th><th>Status</th><th>Issue Date</th><th>Closing Date</th></tr>
  <tr class="trtext2">
    <td><span>6.</span></td>
    <td><span><a href="javascript:void()" onclick="doView('20104'); return false">T-20260004</a></span></td>
    <td><span title="Provision of Lab Equipment">Provision of Lab Equipment</span></td>
    <td><span>Issued&nbsp;</span></td>
    <td><span>2026/10/01 (Thu)</span></td>
    <td><span>2026/12/01 (Tue) 14:30</span></td>
  </tr>
</table>
<div>Page: <a href="List.aspx?Page=1">1</a> <a href="List.aspx?Page=2">2</a> <b>3</b></div>
<form method="post" action="View.aspx" name="ViewForm" style="display:none">
  <input type="hidden" name="Page" value="3" />
  <input type="hidden" name="tender_id" value="" />
</form>
</body></html>
"""


VIEW_20101_UNLOCKED_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <div class="headtext1">Tender Invitation - View Details</div>
  <table>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Name</span></td>
      <td colspan="3" class="tdtext2"><div>procurement@cityu.edu.hk</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Tel. Number</span></td>
      <td class="tdtext2"><div>34421234</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Email Address</span></td>
      <td colspan="3" class="tdtext2"><div>procurement@cityu.edu.hk</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#2 Name</span></td>
      <td colspan="3" class="tdtext2"><div>Chris Wong</div></td>
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
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=501" title="Brand_Book.pdf">Click to download</a></span></td>
            <td><span>17 MB</span></td>
          </tr>
          <tr valign="top">
            <td><span>T-20260001_TENDER SPECIFICATIONS</span></td>
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=502" title="SPECS.pdf">Click to download</a></span></td>
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
            <td><span>T-20260001_TENDER SPECIFICATIONS (Addendum)</span></td>
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=503" title="SPECS.pdf">Click to download</a></span></td>
            <td><span>3 MB</span></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
</body></html>
"""


VIEW_20103_NEEDS_EOI_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <div class="headtext1">Tender Invitation - View Details</div>
  <table>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Name</span></td>
      <td colspan="3" class="tdtext2"><div>johnny.lam@cityu.edu.hk</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#1 Email Address</span></td>
      <td colspan="3" class="tdtext2"><div>johnny.lam@cityu.edu.hk</div></td>
    </tr>
    <tr valign="top">
      <td class="tdtext1"><span>#2 Name</span></td>
      <td colspan="3" class="tdtext2"><div>Phyllis Leung</div></td>
    </tr>
  </table>
  <center>
    <input type="button" value="Expression of Interest" class="btntext2" onclick="document.NewEOI.submit()" />
    <form method="post" action="/Supplier/en/Tender/EOI/New.aspx" name="NewEOI" style="display:none;">
      <input type="hidden" name="Page" value="1" />
      <input type="hidden" name="tender_id" value="20103" />
    </form>
  </center>
</div>
</body></html>
"""


VIEW_20103_UNLOCKED_AFTER_EOI_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <table>
    <tr valign="top">
      <td class="tdtext1"><div>Document(s)</div></td>
      <td class="tdtext2">
        <table>
          <tr><td>Description</td><td>File</td><td>File Size</td></tr>
          <tr valign="top">
            <td><span>GPU Server Tender Document</span></td>
            <td><span><a href="/Supplier/en/Tender/DownloadFile.aspx?file_id=504" title="GPU_Server_Spec.pdf">Click to download</a></span></td>
            <td><span>512 KB</span></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>
</body></html>
"""


VIEW_20104_UNLOCKED_HTML = """<!DOCTYPE html>
<html><body><div id="MainContent">
  <table>
    <tr><td class="tdtext1">#1 Name</td><td class="tdtext2">Lab Office</td></tr>
    <tr><td class="tdtext1">#1 Email Address</td><td class="tdtext2">lab@cityu.edu.hk</td></tr>
  </table>
</div></body></html>
"""


EOI_NEW_HTML = """<!DOCTYPE html>
<html><body>
<div id="MainContent">
  <form method="post" action="/Supplier/en/Tender/EOI/Create.aspx" name="PostForm">
    <input type="hidden" name="Page" value="1">
    <input type="hidden" name="tender_id" value="20103">
    <input type="radio" name="response_type" value="1"> Yes
    <input type="radio" name="response_type" value="2"> No
    <input type="button" value="Submit" name="SubmitButton">
  </form>
</div>
</body></html>
"""


class TestCityULogin(unittest.TestCase):
    """Drive the index -> declarations -> login form -> Submit.aspx flow offline."""

    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.context = CityUAuthManager().get_context(self._browser)
        self.page = self.context.new_page()
        self.submitted = []

        def handle(route, request):
            url = request.url
            if "Login/index.aspx" in url:
                body = LOGIN_INDEX_HTML
            elif "Login/AcceptTerms.aspx" in url:
                body = TERMS_HTML
            elif "Login/New.aspx" in url:
                body = LOGIN_NEW_HTML
            elif "Login/Submit.aspx" in url:
                self.submitted.append(parse_qs(request.post_data or ""))
                route.fulfill(
                    status=302,
                    headers={"Location": "/Supplier/en/index.htm"},
                    body="",
                )
                return
            elif "Supplier/en/index.htm" in url:
                body = LOGIN_OK_HTML
            else:
                route.fulfill(status=404, body="Not found")
                return
            route.fulfill(status=200, content_type="text/html", body=body)

        self.page.route("**/*", handle)

    def tearDown(self):
        self.context.close()

    def test_login_accepts_declarations_and_submits_credentials(self):
        auth = CityUAuthManager()
        with patch.object(config, "CITYU_USER_ID", "U100"), patch.object(
            config, "CITYU_PASSWORD", "secret"
        ):
            self.assertTrue(auth.perform_login(self.page))

        self.assertIn("/Supplier/en/index.htm", self.page.url)
        self.assertEqual(len(self.submitted), 1)
        self.assertEqual(self.submitted[0].get("username"), ["U100"])
        self.assertEqual(self.submitted[0].get("password"), ["secret"])

    def test_missing_credentials_raises_value_error(self):
        auth = CityUAuthManager()
        with patch.object(config, "CITYU_USER_ID", ""), patch.object(
            config, "CITYU_PASSWORD", ""
        ):
            with self.assertRaises(ValueError):
                auth.perform_login(MagicMock(), user_id="", password="")


class TestCityUParserAndEOIDownload(unittest.TestCase):
    """End-to-end parser and downloader tests against routed ETS HTML."""

    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.context = CityUAuthManager().get_context(self._browser)
        self.page = self.context.new_page()
        self.eoi_submitted_ids = set()
        self.visited_urls = []

        def handle_route(route, request):
            url = request.url
            self.visited_urls.append(url)

            if "RFQ/Search.aspx" in url or "RFQ/List.aspx" in url:
                body = RFQ_LIST_ALL_CLOSED_HTML
            elif "Tender/Search.aspx" in url or (
                "Tender/List.aspx" in url
                and "Page=2" not in url
                and "Page=3" not in url
            ):
                body = TENDER_LIST_PAGE1_HTML
            elif "Tender/List.aspx?Page=2" in url:
                body = TENDER_LIST_PAGE2_ALL_CLOSED_HTML
            elif "Tender/List.aspx?Page=3" in url:
                body = TENDER_LIST_PAGE3_HTML
            elif "Tender/View.aspx" in url:
                tid = parse_qs(request.post_data or "").get("tender_id", [""])[0]
                if tid == "20101":
                    body = VIEW_20101_UNLOCKED_HTML
                elif tid == "20103":
                    body = (
                        VIEW_20103_UNLOCKED_AFTER_EOI_HTML
                        if "20103" in self.eoi_submitted_ids
                        else VIEW_20103_NEEDS_EOI_HTML
                    )
                elif tid == "20104":
                    body = VIEW_20104_UNLOCKED_HTML
                else:
                    body = "<html><body><div id='MainContent'>Not found</div></body></html>"
            elif "Tender/EOI/New.aspx" in url:
                body = EOI_NEW_HTML
            elif "Tender/EOI/Create.aspx" in url:
                post_data = parse_qs(request.post_data or "")
                if post_data.get("response_type") == ["1"]:
                    self.eoi_submitted_ids.add(
                        post_data.get("tender_id", [""])[0]
                    )
                body = "<html><body><div id='MainContent'>EOI Created</div></body></html>"
            elif "DownloadFile.aspx" in url:
                file_map = {
                    "501": ("Brand_Book.pdf", b"%PDF-brand"),
                    "502": ("SPECS.pdf", b"%PDF-specs-v1"),
                    "503": ("SPECS.pdf", b"%PDF-specs-v2-addendum"),
                    "504": ("GPU_Server_Spec.pdf", b"%PDF-gpu"),
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
                return
            else:
                route.fulfill(status=404, body="Not found")
                return

            route.fulfill(status=200, content_type="text/html", body=body)

        self.page.route("**/*", handle_route)

    def tearDown(self):
        self.context.close()

    def test_extract_tenders_collects_issued_rows_across_all_pages(self):
        tenders = CityUTenderParser(self.page).extract_tenders()

        self.assertEqual(
            [t.tender_no for t in tenders],
            ["T-20260001", "T-20260003", "T-20260004"],
        )
        self.assertTrue(any("Page=3" in u for u in self.visited_urls))

        unlocked = tenders[0]
        self.assertTrue(unlocked.invited)
        self.assertFalse(unlocked.needs_eoi)
        self.assertEqual(unlocked.portal_id, "20101")
        self.assertEqual(unlocked.contact_email, "procurement@cityu.edu.hk")
        self.assertEqual(unlocked.contact_phone, "34421234")
        # #1 Name is an email address, so the parser falls back to #2 Name.
        self.assertEqual(unlocked.contact_person, "Chris Wong")
        self.assertEqual(len(unlocked.documents), 3)

        needs_eoi = tenders[1]
        self.assertFalse(needs_eoi.invited)
        self.assertTrue(needs_eoi.needs_eoi)
        self.assertEqual(needs_eoi.contact_person, "Phyllis Leung")
        self.assertEqual(needs_eoi.contact_email, "johnny.lam@cityu.edu.hk")
        self.assertEqual(needs_eoi.documents, [])

    def test_process_cityu_tenders_submits_eoi_and_downloads_documents(self):
        tenders = CityUTenderParser(self.page).extract_tenders()

        with tempfile.TemporaryDirectory() as tmpdir:
            doc_dir = Path(tmpdir)
            with patch.object(config, "DOCUMENT_DIR", doc_dir):
                saved = process_cityu_tenders(self.page, tenders)

            self.assertIn("20103", self.eoi_submitted_ids)
            self.assertTrue(tenders[1].invited)
            self.assertFalse(tenders[1].needs_eoi)

            self.assertEqual(
                [p.name for p in saved],
                [
                    "Brand_Book.pdf",
                    "SPECS.pdf",
                    "SPECS_503.pdf",
                    "GPU_Server_Spec.pdf",
                ],
            )
            t1_dir = doc_dir / "CityU" / "Provision of Campus Network Switches"
            t2_dir = doc_dir / "CityU" / "Provision of GPU Server"
            self.assertTrue((t1_dir / "SPECS.pdf").is_file())
            self.assertEqual(
                (t1_dir / "SPECS_503.pdf").read_bytes(), b"%PDF-specs-v2-addendum"
            )
            self.assertEqual((t2_dir / "GPU_Server_Spec.pdf").read_bytes(), b"%PDF-gpu")


class TestCityUUniqueFilename(unittest.TestCase):

    def test_first_occurrence_keeps_original_name(self):
        self.assertEqual(
            _unique_filename("SPECS.pdf", "/DownloadFile.aspx?file_id=502", set()),
            "SPECS.pdf",
        )

    def test_duplicate_filename_is_disambiguated_by_file_id(self):
        self.assertEqual(
            _unique_filename(
                "SPECS.pdf", "/DownloadFile.aspx?file_id=503", {"SPECS.pdf"}
            ),
            "SPECS_503.pdf",
        )


if __name__ == "__main__":
    unittest.main()
