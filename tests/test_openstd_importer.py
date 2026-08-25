from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "openstd-importer" / "scripts" / "openstd_importer.py"
spec = importlib.util.spec_from_file_location("openstd_importer", SCRIPT_PATH)
openstd_importer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["openstd_importer"] = openstd_importer
spec.loader.exec_module(openstd_importer)


def test_parse_list_items_extracts_gbz_rows():
    html = """
    <table>
      <tbody>
        <tr>
          <td>1</td>
          <td><a href="javascript:void(0)" onclick="showInfo('abc');">GB/Z 179-2026</a></td>
          <td><span>采</span></td>
          <td><a href="javascript:void(0)" onclick="showInfo('abc');">恒定湿热</a></td>
          <td><span>现行</span></td>
          <td>2026-07-02</td>
          <td><button onclick="showInfo('abc');">查看详细</button></td>
        </tr>
      </tbody>
    </table>
    """

    items = openstd_importer.parse_list_items(html, "https://openstd.samr.gov.cn/bzgk/std/std_list_type")

    assert len(items) == 1
    assert items[0].standard_code == "GB/Z 179-2026"
    assert items[0].standard_name == "恒定湿热"
    assert items[0].standard_status == "现行"
    assert items[0].publish_date == "2026-07-02"
    assert items[0].is_adopted is True
    assert items[0].detail_url == "https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=abc"


def test_discover_preserves_duplicate_standard_codes(monkeypatch):
    html = """
    <script>pages: 1</script>
    <table>
      <tbody>
        <tr>
          <td>1</td>
          <td><a href="javascript:void(0)" onclick="showInfo('aaa');">GB/Z 190-2026</a></td>
          <td></td>
          <td><a href="javascript:void(0)" onclick="showInfo('aaa');">标准 A</a></td>
          <td><span>现行</span></td>
          <td>2026-07-02</td>
          <td><button onclick="showInfo('aaa');">查看详细</button></td>
        </tr>
        <tr>
          <td>2</td>
          <td><a href="javascript:void(0)" onclick="showInfo('bbb');">GB/Z 190-2026</a></td>
          <td></td>
          <td><a href="javascript:void(0)" onclick="showInfo('bbb');">标准 B</a></td>
          <td><span>现行</span></td>
          <td>2026-07-03</td>
          <td><button onclick="showInfo('bbb');">查看详细</button></td>
        </tr>
      </tbody>
    </table>
    """

    class FakeClient:
        def get_html(self, url, *, referer=""):
            return html, url

        def close(self):
            pass

    monkeypatch.setattr(openstd_importer, "OpenStdHttpClient", FakeClient)

    result = openstd_importer.discover(
        "https://openstd.samr.gov.cn/bzgk/std/std_list_type",
        scope="custom",
        max_pages=1,
        max_items=0,
        interval_seconds=0,
        allowed_statuses={"现行", "即将实施"},
    )

    assert result["total_seen"] == 2
    assert [item["standard_name"] for item in result["items"]] == ["标准 A", "标准 B"]


def test_parse_list_items_accepts_three_national_standard_types_and_filters_scrapped():
    html = """
    <table>
      <tbody>
        <tr>
          <td>1</td><td><a onclick="showInfo('gb');">GB 1-2026</a></td><td></td>
          <td>强制性</td><td>即将实施</td><td>2026-07-02</td><td>查看详细</td>
        </tr>
        <tr>
          <td>2</td><td><a onclick="showInfo('gbt');">GB/T 2-2026</a></td><td></td>
          <td>推荐性</td><td>现行</td><td>2026-07-02</td><td>查看详细</td>
        </tr>
        <tr>
          <td>3</td><td><a onclick="showInfo('gbz');">GB/Z 3-2026</a></td><td></td>
          <td>指导性</td><td>现行</td><td>2026-07-02</td><td>查看详细</td>
        </tr>
        <tr>
          <td>4</td><td><a onclick="showInfo('old');">GB/T 4-2020</a></td><td></td>
          <td>废止推荐性</td><td>废止</td><td>2020-01-01</td><td>查看详细</td>
        </tr>
      </tbody>
    </table>
    """

    items = openstd_importer.parse_list_items(
        html,
        "https://openstd.samr.gov.cn/bzgk/std/std_list_type",
        source_scope="test_scope",
        source_label="测试分类",
        source_url="https://openstd.samr.gov.cn/bzgk/std/std_list_type",
        allowed_statuses={"现行", "即将实施"},
    )

    assert [item.standard_code for item in items] == ["GB 1-2026", "GB/T 2-2026", "GB/Z 3-2026"]
    assert {item.source_scope for item in items} == {"test_scope"}


def test_build_page_url_adds_page_parameters():
    url = "https://openstd.samr.gov.cn/bzgk/std/std_list_type?p.p1=3&p.p90=circulation_date&p.p91=desc"

    page_url = openstd_importer.build_page_url(url, 2)

    assert "page=2" in page_url
    assert "pageSize=10" in page_url
    assert "p.p1=3" in page_url
    assert "p.p90=circulation_date" in page_url
    assert "p.p91=desc" in page_url


def test_parse_detail_extracts_download_button():
    html = """
    <body>
      <h1>标准号： GB/Z 188-2026</h1>
      <p>中文标准名称： 空间碎片在轨清除指南</p>
      <button class="btn xz_btn btn-sm btn-warning app-hide" data-value="hcno123">下载标准</button>
    </body>
    """

    detail = openstd_importer.parse_detail(html, "https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=hcno123")

    assert detail.downloadable is True
    assert detail.standard_code == "GB/Z 188-2026"
    assert detail.standard_name == "空间碎片在轨清除指南"
    assert detail.download_url == "https://openstd.samr.gov.cn/bzgk/std/viewGb?hcno=hcno123"


def test_validate_pdf_rejects_non_pdf(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_text("not a pdf", encoding="utf-8")

    valid, reason = openstd_importer.validate_pdf(bad)

    assert valid is False
    assert reason == "pdf_invalid_header"


def test_validate_pdf_accepts_pdf_header(tmp_path):
    pdf = tmp_path / "good.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    valid, reason = openstd_importer.validate_pdf(pdf)

    assert valid is True
    assert reason == ""
