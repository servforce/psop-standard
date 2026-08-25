from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.services.standard_library_collect import external_id_from_detail_url, metadata_fingerprint, official_status_from_raw
from app.services.standard_library_sacinfo_update import SacinfoUpdateOptions, StandardLibrarySacinfoUpdateService


def load_sacinfo_module():
    script = Path(__file__).resolve().parents[1] / "tools" / "standard-collector" / "scripts" / "collect_sacinfo_standards.py"
    spec = importlib.util.spec_from_file_location("collect_sacinfo_standards", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_standard_library_status_supports_local_updated_available():
    assert official_status_from_raw("有更新版") == "updated_available"
    assert official_status_from_raw("现行") == "current"
    assert official_status_from_raw("废止") == "abolished"


def test_external_id_from_sacinfo_detail_path():
    assert external_id_from_detail_url("https://hbba.sacinfo.org.cn/stdDetail/abc123") == "abc123"
    assert external_id_from_detail_url("https://dbba.sacinfo.org.cn/portal/online/local-9") == "local-9"


def test_sacinfo_record_normalization_and_pagination():
    module = load_sacinfo_module()
    category = module.CategoryScope(query_value="通信", category="通信", category_label="YD 通信")
    raw = module.normalize_record(
        "industry",
        {
            "pk": "PK001",
            "code": "YD/T 123-2026",
            "chName": "通信设备测试规范",
            "industry": "通信",
            "status": "现行",
            "issueDate": "2026-01-02",
            "actDate": "2026-03-01",
        },
        category=category,
        base_url="https://hbba.sacinfo.org.cn/",
    )
    assert raw["external_id"] == "PK001"
    assert raw["detail_url"] == "https://hbba.sacinfo.org.cn/stdDetail/PK001"
    assert raw["category"] == "通信"
    assert raw["category_label"] == "YD 通信"

    payload = {"data": {"records": [raw], "pages": 3, "total": 120}}
    assert module.extract_records(payload) == [raw]
    assert module.extract_total_pages(payload, page_size=50) == 3


def test_sacinfo_detail_follows_online_page_for_download_and_dates():
    module = load_sacinfo_module()
    detail_url = "https://hbba.sacinfo.org.cn/stdDetail/abc123"
    online_url = "https://hbba.sacinfo.org.cn/portal/online/abc123"
    detail_html = """
    <html><body>
      <a href="/portal/online/abc123">\u67e5\u770b\u6587\u672c</a>
      <dl><dt>\u6279\u51c6\u65e5\u671f</dt><dd>2026-05-25</dd></dl>
    </body></html>
    """
    online_html = """
    <html><body>
      <div><span>\u5b9e\u65bd\u65e5\u671f</span><span>2026\u5e7411\u67081\u65e5</span></div>
      <button onclick="window.location.href='/portal/download/abc123'">\u4e0b\u8f7d\u6807\u51c6</button>
    </body></html>
    """

    class FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, timeout: float) -> FakeResponse:
            self.calls.append(url)
            return FakeResponse(online_html if url == online_url else detail_html)

    client = module.SACInfoClient(base_url="https://hbba.sacinfo.org.cn/", timeout_seconds=30)
    fake_session = FakeSession()
    client.session = fake_session

    detail = client.fetch_detail(detail_url)

    assert fake_session.calls == [detail_url, online_url]
    assert detail["online_url"] == online_url
    assert detail["download_url"] == "https://hbba.sacinfo.org.cn/portal/download/abc123"
    assert detail["publish_date"] == "2026-05-25"
    assert detail["effective_date"] == "2026-11-01"


def test_sacinfo_helpers_extract_online_download_and_labeled_dates():
    module = load_sacinfo_module()
    soup = module.BeautifulSoup(
        """
        <html><body>
          <a href="/portal/online/abc123">\u67e5\u770b\u6587\u672c</a>
          <button data-url="/portal/download/abc123">\u4e0b\u8f7d\u6807\u51c6</button>
          <table>
            <tr><th>\u53d1\u5e03\u65e5\u671f</th><td>2026/05/25</td></tr>
            <tr><th>\u5b9e\u65bd\u65e5\u671f</th><td>2026.11.01</td></tr>
          </table>
        </body></html>
        """,
        "html.parser",
    )

    assert module.find_online_url(soup, "https://hbba.sacinfo.org.cn/stdDetail/abc123") == (
        "https://hbba.sacinfo.org.cn/portal/online/abc123"
    )
    assert module.find_download_url(soup, "https://hbba.sacinfo.org.cn/portal/online/abc123") == (
        "https://hbba.sacinfo.org.cn/portal/download/abc123"
    )
    assert module.find_publish_date(soup) == "2026-05-25"
    assert module.find_effective_date(soup) == "2026-11-01"


def test_metadata_fingerprint_changes_when_sacinfo_file_links_appear():
    raw = {
        "standard_code": "WS/T 886-2026",
        "standard_name": "\u4e34\u5e8a\u68c0\u9a8c\u5e38\u7528\u9879\u76ee\u540d\u79f0\u53ca\u4ee3\u7801",
        "detail_url": "https://hbba.sacinfo.org.cn/stdDetail/abc123",
        "external_id": "abc123",
        "category": "WS",
    }

    before = metadata_fingerprint(raw, detail={"detail_url": raw["detail_url"]})
    after = metadata_fingerprint(
        raw,
        detail={
            "detail_url": raw["detail_url"],
            "online_url": "https://hbba.sacinfo.org.cn/portal/online/abc123",
            "download_url": "https://hbba.sacinfo.org.cn/portal/download/abc123",
        },
    )

    assert after != before


def test_sacinfo_scheduler_requires_categories_by_default():
    service = StandardLibrarySacinfoUpdateService()
    result = service.run_source_update(
        SacinfoUpdateOptions(
            source="local",
            categories=(),
            require_categories=True,
            status="",
            page_size=50,
            max_pages=1,
            max_items=50,
            request_interval=0,
            timeout_seconds=30,
            max_retries=1,
            retry_backoff_seconds=0,
            download_pdfs=True,
            processing_limit=0,
            refresh_atlas=True,
        )
    )
    assert result["status"] == "skipped_no_categories"
    assert result["source"] == "local"
