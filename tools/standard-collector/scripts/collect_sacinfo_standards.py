from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.db.standard_library import StandardLibrarySessionLocal, init_standard_library_db
from app.services.standard_library_collect import (
    find_standard,
    metadata_fingerprint,
    normalize_code,
    upsert_standard_from_raw,
    upsert_standard_library_item,
    upsert_standard_library_job,
)
from app.services.standard_library_materialize import standard_library_materialize_service
from app.services.storage import storage_service


LOGGER = logging.getLogger("collect_sacinfo_standards")
DEFAULT_LOG_FILE = PROJECT_ROOT / "tools" / "standard-collector" / "logs" / "collect_sacinfo_standards.log"

SOURCE_CONFIG = {
    "industry": {
        "base_url": "https://hbba.sacinfo.org.cn/",
        "site": "hbba.sacinfo.org.cn",
        "source_label": "Industry standards",
        "statuses": ("即将实施", "现行", "废止"),
    },
    "local": {
        "base_url": "https://dbba.sacinfo.org.cn/",
        "site": "dbba.sacinfo.org.cn",
        "source_label": "Local standards",
        "statuses": ("现行", "有更新版", "废止"),
    },
}

INDUSTRY_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("AQ", "安全生产"),
    ("BB", "包装"),
    ("CB", "船舶"),
    ("CH", "测绘"),
    ("CJ", "城镇建设"),
    ("CY", "新闻出版"),
    ("DA", "档案"),
    ("DL", "电力"),
    ("DZ", "地质矿产"),
    ("EJ", "核工业"),
    ("FZ", "纺织"),
    ("GA", "公共安全"),
    ("GH", "供销合作"),
    ("GM", "国密"),
    ("GY", "广播电影电视"),
    ("HB", "航空"),
    ("HG", "化工"),
    ("HJ", "环境保护"),
    ("HS", "海关"),
    ("HY", "海洋"),
    ("JB", "机械"),
    ("JC", "建材"),
    ("JG", "建筑工业"),
    ("JR", "金融"),
    ("JT", "交通"),
    ("JY", "教育"),
    ("LB", "旅游"),
    ("LD", "劳动和劳动安全"),
    ("LS", "粮食"),
    ("LY", "林业"),
    ("MH", "民用航空"),
    ("MZ", "民政"),
    ("NB", "能源"),
    ("NY", "农业"),
    ("QB", "轻工"),
    ("QC", "汽车"),
    ("QX", "气象"),
    ("RB", "认证认可"),
    ("SB", "国内贸易"),
    ("SC", "水产"),
    ("SH", "石油化工"),
    ("SJ", "电子"),
    ("SL", "水利"),
    ("SN", "出入境检验检疫"),
    ("SY", "石油天然气"),
    ("TB", "铁路运输"),
    ("TD", "土地管理"),
    ("TY", "体育"),
    ("WB", "物资管理"),
    ("WH", "文化"),
    ("WW", "文物保护"),
    ("XB", "稀土"),
    ("YB", "黑色冶金"),
    ("YC", "烟草"),
    ("YD", "通信"),
    ("YS", "有色金属"),
    ("YY", "医药"),
    ("YZ", "邮政"),
    ("ZY", "中医药"),
)

LOCAL_PROVINCES: tuple[str, ...] = (
    "北京市",
    "天津市",
    "河北省",
    "山西省",
    "内蒙古自治区",
    "辽宁省",
    "吉林省",
    "黑龙江省",
    "上海市",
    "江苏省",
    "浙江省",
    "安徽省",
    "福建省",
    "江西省",
    "山东省",
    "河南省",
    "湖北省",
    "湖南省",
    "广东省",
    "广西壮族自治区",
    "海南省",
    "重庆市",
    "四川省",
    "贵州省",
    "云南省",
    "西藏自治区",
    "陕西省",
    "甘肃省",
    "青海省",
    "宁夏回族自治区",
    "新疆维吾尔自治区",
)


@dataclass(frozen=True)
class CategoryScope:
    query_value: str
    category: str
    category_label: str
    ministry: str = ""


class SACInfoClient:
    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "OctopusStandardCollector/1.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            }
        )

    def query_list(
        self,
        *,
        current: int,
        size: int,
        category: CategoryScope,
        status: str,
    ) -> dict[str, Any]:
        data = {
            "current": str(current),
            "size": str(size),
            "key": "",
            "ministry": category.ministry,
            "industry": "" if category.ministry else category.query_value,
            "pubdate": "",
            "date": "",
            "status": status,
        }
        response = self.session.post(
            urljoin(self.base_url, "stdQueryList"),
            data=data,
            timeout=self.timeout_seconds,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        response.raise_for_status()
        return response.json()

    def fetch_detail(self, detail_url: str) -> dict[str, Any]:
        response = self.session.get(detail_url, timeout=self.timeout_seconds)
        response.raise_for_status()
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        detail = {
            "detail_url": detail_url,
            "download_url": find_download_url(soup, detail_url),
            "online_url": find_online_url(soup, detail_url),
            "publish_date": find_publish_date(soup),
            "effective_date": find_effective_date(soup),
        }
        online_url = str(detail.get("online_url") or "")
        if online_url and (
            not detail.get("download_url")
            or not detail.get("publish_date")
            or not detail.get("effective_date")
        ):
            online_response = self.session.get(online_url, timeout=self.timeout_seconds)
            online_response.raise_for_status()
            online_soup = BeautifulSoup(online_response.text, "html.parser")
            detail["download_url"] = detail.get("download_url") or find_download_url(online_soup, online_url)
            detail["publish_date"] = detail.get("publish_date") or find_publish_date(online_soup)
            detail["effective_date"] = detail.get("effective_date") or find_effective_date(online_soup)
        return detail

    def download_pdf(self, url: str, output_path: Path) -> None:
        with self.session.get(url, stream=True, timeout=self.timeout_seconds) as response:
            response.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        file.write(chunk)
        with output_path.open("rb") as file:
            header = file.read(5)
        if header != b"%PDF-":
            raise RuntimeError("downloaded file is not a valid PDF")

    def close(self) -> None:
        self.session.close()


def collect(args: argparse.Namespace) -> int:
    setup_logging(args.log_file)
    if args.source not in SOURCE_CONFIG:
        raise SystemExit(f"unsupported source: {args.source}")
    if args.status and args.status not in SOURCE_CONFIG[args.source]["statuses"]:
        raise SystemExit(f"unsupported status for {args.source}: {args.status}")
    config = SOURCE_CONFIG[args.source]
    client = SACInfoClient(base_url=config["base_url"], timeout_seconds=args.timeout_seconds)
    if args.dry_run:
        try:
            summary = dry_run_collect(client=client, args=args)
        finally:
            client.close()
        print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
        return 0
    if not args.dry_run:
        init_standard_library_db()

    job_token = uuid.uuid4().hex
    try:
        with StandardLibrarySessionLocal() as session:
            job = upsert_standard_library_job(
                session,
                {"id": job_token, "created_at": datetime.now(timezone.utc)},
                job_type=args.job_type,
                source=args.source,
                trigger_type="admin" if args.job_type == "historical_collect" else "schedule",
                status="running",
                stage="discovering",
                entry_url=config["base_url"],
            )
            job.started_at = job.started_at or datetime.now(timezone.utc)
            session.commit()
            summary = collect_into_job(session, client=client, job=job, args=args)
            finish_job(session, job, status="completed", summary=summary)
    except Exception as exc:
        LOGGER.exception("SACInfo collection failed: %s", exc)
        if not args.dry_run:
            with StandardLibrarySessionLocal() as session:
                job = upsert_standard_library_job(
                    session,
                    {"id": job_token},
                    job_type=args.job_type,
                    source=args.source,
                    trigger_type="admin" if args.job_type == "historical_collect" else "schedule",
                    status="failed",
                    stage="failed",
                    error_message=str(exc),
                    entry_url=config["base_url"],
                )
                finish_job(session, job, status="failed", summary={"failed_count": 1}, error_message=str(exc))
        return 1
    finally:
        client.close()
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
    return 0 if summary["failed_count"] == 0 else 1


def collect_into_job(
    session: Session,
    *,
    client: SACInfoClient,
    job: Any,
    args: argparse.Namespace,
) -> dict[str, int]:
    categories = categories_from_args(args)
    statuses = [args.status] if args.status else [""]
    summary = {
        "scanned_pages": 0,
        "discovered_count": 0,
        "processed_count": 0,
        "new_count": 0,
        "changed_count": 0,
        "unchanged_count": 0,
        "downloaded_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
    }
    seen_external_ids: set[str] = set()
    for category in categories:
        for status in statuses:
            page = 1
            while True:
                if args.max_items > 0 and summary["processed_count"] >= args.max_items:
                    return summary
                LOGGER.info(
                    "query source=%s category=%s status=%s page=%s",
                    args.source,
                    category.category_label,
                    status or "all",
                    page,
                )
                payload = client.query_list(current=page, size=args.page_size, category=category, status=status)
                records = extract_records(payload)
                total_pages = extract_total_pages(payload, page_size=args.page_size)
                summary["scanned_pages"] += 1
                summary["discovered_count"] += len(records)
                update_job_progress(session, job, stage="processing", summary=summary)
                if not records:
                    break
                for record in records:
                    if args.max_items > 0 and summary["processed_count"] >= args.max_items:
                        return summary
                    raw = normalize_record(args.source, record, category=category, base_url=client.base_url)
                    external_id = str(raw.get("external_id") or "")
                    if external_id and external_id in seen_external_ids:
                        summary["skipped_count"] += 1
                        continue
                    if external_id:
                        seen_external_ids.add(external_id)
                    result = process_record(session, client=client, job=job, raw=raw, args=args)
                    summary["processed_count"] += 1
                    summary[f"{result}_count"] = summary.get(f"{result}_count", 0) + 1
                    if result in {"new", "changed"} and raw.get("download_url") and args.download_pdfs:
                        summary["downloaded_count"] += 1
                    update_job_progress(session, job, stage="processing", summary=summary)
                    sleep_if_needed(args.request_interval)
                if args.max_pages > 0 and page >= args.max_pages:
                    break
                if total_pages and page >= total_pages:
                    break
                page += 1
    return summary


def process_record(
    session: Session,
    *,
    client: SACInfoClient,
    job: Any,
    raw: dict[str, Any],
    args: argparse.Namespace,
) -> str:
    code = str(raw.get("standard_code") or "")
    name = str(raw.get("standard_name") or "")
    category = str(raw.get("category") or "")
    external_id = str(raw.get("external_id") or "")
    LOGGER.info("process source=%s code=%s name=%s external_id=%s", args.source, code, name, external_id)
    existing = find_standard(
        session,
        source=args.source,
        code_normalized=normalize_code(code or external_id),
        category=category,
        external_id=external_id,
    )
    detail = client.fetch_detail(str(raw.get("detail_url") or ""))
    raw["download_url"] = detail.get("download_url") or ""
    raw["online_url"] = detail.get("online_url") or ""
    if detail.get("publish_date") and not raw.get("publish_date"):
        raw["publish_date"] = detail.get("publish_date")
    if detail.get("effective_date") and not raw.get("effective_date"):
        raw["effective_date"] = detail.get("effective_date")
    fingerprint = metadata_fingerprint(raw, detail=detail)
    metadata_action = "new"
    if existing is not None:
        metadata_action = "unchanged" if existing.metadata_fingerprint == fingerprint else "changed"

    if metadata_action == "unchanged":
        standard = upsert_standard_from_raw(
            session,
            raw,
            source=args.source,
            detail=detail,
            category=category,
            category_label=str(raw.get("category_label") or category),
            source_site=SOURCE_CONFIG[args.source]["site"],
            source_label=SOURCE_CONFIG[args.source]["source_label"],
            external_id=external_id,
            file_access_type=existing.file_access_type,
        )
        upsert_standard_library_item(
            session,
            job=job,
            raw=raw,
            source=args.source,
            standard_id=standard.id,
            metadata_action="unchanged",
            file_decision="no_download",
            file_result="skipped",
        )
        session.commit()
        return "unchanged"

    download_url = str(detail.get("download_url") or "")
    online_url = str(detail.get("online_url") or "")
    if download_url and args.download_pdfs:
        return process_downloadable_record(
            session,
            client=client,
            job=job,
            raw=raw,
            detail=detail,
            metadata_action=metadata_action,
            args=args,
        )

    file_access_type = "online_only" if online_url else "unavailable"
    standard = upsert_standard_from_raw(
        session,
        raw,
        source=args.source,
        detail=detail,
        category=category,
        category_label=str(raw.get("category_label") or category),
        source_site=SOURCE_CONFIG[args.source]["site"],
        source_label=SOURCE_CONFIG[args.source]["source_label"],
        external_id=external_id,
        online_url=online_url,
        file_access_type=file_access_type,
    )
    upsert_standard_library_item(
        session,
        job=job,
        raw=raw,
        source=args.source,
        standard_id=standard.id,
        metadata_action=metadata_action,
        file_decision=file_access_type,
        file_result="skipped",
        error_message="online_only" if online_url else "not_downloadable",
    )
    session.commit()
    return "skipped"


def process_downloadable_record(
    session: Session,
    *,
    client: SACInfoClient,
    job: Any,
    raw: dict[str, Any],
    detail: dict[str, Any],
    metadata_action: str,
    args: argparse.Namespace,
) -> str:
    standard_id_basis = str(raw.get("external_id") or raw.get("standard_code") or raw.get("standard_name") or uuid.uuid4().hex)
    filename = pdf_filename(str(raw.get("standard_code") or ""), str(raw.get("standard_name") or ""))
    object_key = object_key_for_pdf(args.source, standard_id_basis, filename)
    last_error = ""
    for attempt in range(1, max(1, args.max_retries) + 1):
        try:
            workdir = Path(settings.standard_workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"sacinfo_{args.source}_{safe_path_token(standard_id_basis)}_", dir=str(workdir)) as tmp:
                pdf_path = Path(tmp) / filename
                client.download_pdf(str(detail["download_url"]), pdf_path)
                stored = storage_service.upload_file(
                    object_key=object_key,
                    path=pdf_path,
                    media_type="application/pdf",
                    bucket=settings.standard_library_object_store_bucket,
                )
            standard = upsert_standard_from_raw(
                session,
                raw,
                source=args.source,
                detail=detail,
                category=str(raw.get("category") or ""),
                category_label=str(raw.get("category_label") or raw.get("category") or ""),
                source_site=SOURCE_CONFIG[args.source]["site"],
                source_label=SOURCE_CONFIG[args.source]["source_label"],
                external_id=str(raw.get("external_id") or ""),
                bucket=stored.bucket,
                object_key=stored.object_key,
                checksum=stored.checksum,
                size_bytes=stored.size_bytes,
                fingerprint=stored.checksum,
                file_access_type="downloadable",
                pdf_url=str(detail["download_url"]),
                online_url=str(detail.get("online_url") or ""),
            )
            item = upsert_standard_library_item(
                session,
                job=job,
                raw=raw,
                source=args.source,
                standard_id=standard.id,
                metadata_action=metadata_action,
                file_decision="download" if metadata_action == "new" else "redownload",
                file_result="success",
                bucket=stored.bucket,
                object_key=stored.object_key,
                checksum=stored.checksum,
                size_bytes=stored.size_bytes,
                retry_count=attempt - 1,
            )
            session.flush([item, standard])
            standard_library_materialize_service.create_materialize_job(
                session,
                standard.id,
                source_sync_job_id=job.id,
                source_sync_item_id=item.id,
            )
            session.commit()
            return "new" if metadata_action == "new" else "changed"
        except Exception as exc:
            session.rollback()
            last_error = str(exc)
            if attempt < max(1, args.max_retries):
                LOGGER.warning(
                    "retry source=%s code=%s attempt=%s/%s error=%s",
                    args.source,
                    raw.get("standard_code") or raw.get("external_id"),
                    attempt,
                    args.max_retries,
                    last_error,
                )
                time.sleep(max(0.0, args.retry_backoff_seconds * attempt))
    upsert_standard_library_item(
        session,
        job=job,
        raw=raw,
        source=args.source,
        metadata_action=metadata_action,
        file_decision="download",
        file_result="failed",
        retry_count=max(1, args.max_retries),
        error_message=last_error,
    )
    session.commit()
    return "failed"


def normalize_record(source: str, record: dict[str, Any], *, category: CategoryScope, base_url: str) -> dict[str, Any]:
    external_id = clean_text(first_value(record, "pk", "id", "uuid"))
    code = clean_text(first_value(record, "code", "stdCode", "standardCode", "standard_code"))
    name = clean_text(first_value(record, "chName", "stdName", "name", "standard_name"))
    status = clean_text(first_value(record, "status", "standardStatus", "source_status_raw"))
    publish_date = clean_text(first_value(record, "issueDate", "publishDate", "pubdate", "publish_date"))
    effective_date = clean_text(first_value(record, "actDate", "executeDate", "effectiveDate", "effective_date"))
    record_industry = clean_text(first_value(record, "industry"))
    resolved_category = category.category or record_industry or category.query_value or source
    resolved_category_label = category.category_label or resolved_category
    detail_url = urljoin(base_url, f"stdDetail/{external_id}") if external_id else ""
    return {
        "standard_code": code,
        "standard_name": name,
        "standard_status": status,
        "source_status_raw": status,
        "publish_date": publish_date,
        "effective_date": effective_date,
        "detail_url": detail_url,
        "external_id": external_id,
        "pk": external_id,
        "source": source,
        "source_scope": resolved_category,
        "source_label": SOURCE_CONFIG[source]["source_label"],
        "source_site": SOURCE_CONFIG[source]["site"],
        "category": resolved_category,
        "category_label": resolved_category_label,
        "industry": record_industry or category.query_value,
        "standard_org": clean_text(first_value(record, "publishOrg", "org", "stdOrg")),
    }


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("records", "rows", "list", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_records(value)
            if nested:
                return nested
    return []


def extract_total_pages(payload: Any, *, page_size: int) -> int:
    if not isinstance(payload, dict):
        return 0
    for key in ("pages", "totalPages", "pageCount"):
        value = coerce_int(payload.get(key))
        if value:
            return value
    for key in ("data", "page"):
        if isinstance(payload.get(key), dict):
            nested = extract_total_pages(payload[key], page_size=page_size)
            if nested:
                return nested
    total = coerce_int(payload.get("total"))
    if total:
        return max(1, (total + max(1, page_size) - 1) // max(1, page_size))
    return 0


def find_download_url(soup: BeautifulSoup, detail_url: str) -> str:
    for candidate_url, text in iter_link_candidates(soup):
        if not is_url_like(candidate_url):
            continue
        lowered = candidate_url.lower()
        if "/portal/online/" in lowered:
            continue
        if (
            "/portal/download/" in lowered
            or lowered.endswith(".pdf")
            or "download" in lowered
            or "\u4e0b\u8f7d" in text
        ):
            return urljoin(detail_url, candidate_url)
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        text = clean_text(link.get_text(" "))
        lowered = href.lower()
        if "/portal/online/" in lowered:
            continue
        if lowered.endswith(".pdf") or "download" in lowered or "下载" in text:
            return urljoin(detail_url, href)
    return ""


def find_online_url(soup: BeautifulSoup, detail_url: str) -> str:
    for candidate_url, text in iter_link_candidates(soup):
        if not is_url_like(candidate_url):
            continue
        if (
            "/portal/online/" in candidate_url
            or "\u67e5\u770b\u6587\u672c" in text
            or "\u5728\u7ebf" in text
        ):
            return urljoin(detail_url, candidate_url)
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        text = clean_text(link.get_text(" "))
        if "/portal/online/" in href or "在线" in text:
            return urljoin(detail_url, href)
    return ""


def find_publish_date(soup: BeautifulSoup) -> str:
    return find_labeled_date(soup, ("\u53d1\u5e03\u65e5\u671f", "\u6279\u51c6\u65e5\u671f"))


def find_effective_date(soup: BeautifulSoup) -> str:
    return find_labeled_date(soup, ("\u5b9e\u65bd\u65e5\u671f",))


def find_labeled_date(soup: BeautifulSoup, labels: tuple[str, ...]) -> str:
    for text in iter_candidate_text_blocks(soup):
        value = date_after_label(text, labels)
        if value:
            return value
    return date_after_label(clean_text(soup.get_text(" ")), labels)


def iter_candidate_text_blocks(soup: BeautifulSoup) -> list[str]:
    blocks: list[str] = []
    for tag in soup.find_all(["tr", "li", "p", "div", "dl", "section"]):
        text = clean_text(tag.get_text(" "))
        if text:
            blocks.append(text)
    for tag in soup.find_all(["dt", "th", "td", "span", "label"]):
        text = clean_text(tag.get_text(" "))
        if not text:
            continue
        sibling_texts = [text]
        sibling = tag.find_next_sibling()
        while sibling is not None and len(sibling_texts) < 4:
            sibling_text = clean_text(sibling.get_text(" ") if hasattr(sibling, "get_text") else sibling)
            if sibling_text:
                sibling_texts.append(sibling_text)
            sibling = sibling.find_next_sibling() if hasattr(sibling, "find_next_sibling") else None
        blocks.append(" ".join(sibling_texts))
    return blocks


def date_after_label(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        index = text.find(label)
        if index < 0:
            continue
        segment = text[index + len(label) : index + len(label) + 120]
        match = re.search(r"(\d{4})\D{0,6}(\d{1,2})\D{0,6}(\d{1,2})", segment)
        if match:
            year, month, day = (int(part) for part in match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def iter_link_candidates(soup: BeautifulSoup) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    url_attrs = ("href", "data-href", "data-url", "data-download", "value")
    for tag in soup.find_all(True):
        text = clean_text(tag.get_text(" "))
        for attr in url_attrs:
            value = tag.get(attr)
            if isinstance(value, str) and value.strip():
                candidates.append((value.strip(), text))
        for attr in ("onclick", "data-options"):
            value = tag.get(attr)
            if not isinstance(value, str):
                continue
            for match in re.finditer(r"(?P<url>/(?:portal/)?(?:download|online)/[A-Za-z0-9._~!$&()*+,;=:@%/-]+)", value):
                candidates.append((match.group("url"), text))
    return candidates


def is_url_like(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith(("http://", "https://", "/", "portal/download/", "portal/online/")) or lowered.endswith(".pdf")


def dry_run_collect(*, client: SACInfoClient, args: argparse.Namespace) -> dict[str, int]:
    categories = categories_from_args(args)
    statuses = [args.status] if args.status else [""]
    summary = {"scanned_pages": 0, "discovered_count": 0, "processed_count": 0}
    for category in categories:
        for status in statuses:
            page = 1
            while True:
                if args.max_items > 0 and summary["processed_count"] >= args.max_items:
                    return summary
                payload = client.query_list(current=page, size=args.page_size, category=category, status=status)
                records = extract_records(payload)
                total_pages = extract_total_pages(payload, page_size=args.page_size)
                summary["scanned_pages"] += 1
                summary["discovered_count"] += len(records)
                for record in records:
                    if args.max_items > 0 and summary["processed_count"] >= args.max_items:
                        return summary
                    raw = normalize_record(args.source, record, category=category, base_url=client.base_url)
                    summary["processed_count"] += 1
                    if summary["processed_count"] <= 10:
                        print(json.dumps(raw, ensure_ascii=False))
                if args.max_pages > 0 and page >= args.max_pages:
                    break
                if total_pages and page >= total_pages:
                    break
                if not records:
                    break
                page += 1
                sleep_if_needed(args.request_interval)
    return summary


def categories_from_args(args: argparse.Namespace) -> list[CategoryScope]:
    if args.category:
        return [parse_category_arg(args.source, value) for value in args.category]
    if args.source == "industry":
        return [CategoryScope(query_value="", category="", category_label="")]
    return [CategoryScope(query_value=province, category=province, category_label=province) for province in LOCAL_PROVINCES]


def parse_category_arg(source: str, value: str) -> CategoryScope:
    text = clean_text(value)
    if source == "industry":
        if ":" in text:
            code, name = [part.strip() for part in text.split(":", 1)]
            return CategoryScope(query_value=name, category=name, category_label=f"{code} {name}".strip())
        return CategoryScope(query_value=text, category=text, category_label=text)
    if "|" in text:
        province, city = [part.strip() for part in text.split("|", 1)]
        return CategoryScope(query_value=city, category=f"{province} / {city}", category_label=f"{province} / {city}")
    if ":" in text:
        ministry, province = [part.strip() for part in text.split(":", 1)]
        return CategoryScope(query_value=province, category=province, category_label=province, ministry=ministry)
    return CategoryScope(query_value=text, category=text, category_label=text)


def update_job_progress(session: Session, job: Any, *, stage: str, summary: dict[str, int]) -> None:
    job.stage = stage
    job.scanned_pages = summary["scanned_pages"]
    job.discovered_count = summary["discovered_count"]
    job.processed_count = summary["processed_count"]
    job.need_download_count = summary["new_count"] + summary["changed_count"]
    job.downloaded_count = summary["downloaded_count"]
    job.download_failed_count = summary["failed_count"]
    job.new_active_count = summary["new_count"]
    job.failed_count = summary["failed_count"]
    job.progress_percent = 50
    job.heartbeat_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()


def finish_job(session: Session, job: Any, *, status: str, summary: dict[str, int], error_message: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    job.status = status
    job.stage = "completed" if status == "completed" else "failed"
    job.progress_percent = 100
    job.finished_at = now
    job.heartbeat_at = now
    job.updated_at = now
    job.error_message = error_message
    if job.started_at:
        job.duration_ms = max(0, int((job.finished_at - job.started_at).total_seconds() * 1000))
    job.scanned_pages = summary.get("scanned_pages", job.scanned_pages)
    job.discovered_count = summary.get("discovered_count", job.discovered_count)
    job.processed_count = summary.get("processed_count", job.processed_count)
    job.downloaded_count = summary.get("downloaded_count", job.downloaded_count)
    job.failed_count = summary.get("failed_count", job.failed_count)
    session.add(job)
    session.commit()


def first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return value
    return ""


def pdf_filename(code: str, name: str) -> str:
    base = clean_text(code) or clean_text(name) or "standard"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", base).strip(" ._")
    return f"{filename or 'standard'}.pdf"


def object_key_for_pdf(source: str, identity: str, filename: str) -> str:
    token = safe_path_token(identity)
    return f"pdf/{source}/{token}/{filename}"


def safe_path_token(value: str) -> str:
    text = clean_text(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    ascii_part = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:48]
    return f"{ascii_part}-{digest}" if ascii_part else digest


def coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def sleep_if_needed(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def setup_logging(log_file: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect industry/local SACInfo standards into the new standard library.")
    parser.add_argument("--source", choices=("industry", "local"), required=True)
    parser.add_argument("--job-type", choices=("historical_collect", "scheduled_update"), default="historical_collect")
    parser.add_argument("--category", action="append", help="Limit category. Industry: 通信 or YD:通信. Local: 山西省, sxzjj14:山西省, or 山西省|太原市.")
    parser.add_argument("--status", default="", help="Optional official status filter.")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--request-interval", type=float, default=settings.standard_collector_request_interval_seconds)
    parser.add_argument("--timeout-seconds", type=float, default=max(30.0, settings.openstd_download_timeout_seconds))
    parser.add_argument("--max-retries", type=int, default=max(1, settings.standard_collector_max_retries))
    parser.add_argument("--retry-backoff-seconds", type=float, default=settings.standard_collector_retry_backoff_seconds)
    parser.add_argument("--dry-run", action="store_true", help="Discover and print records without writing DB or MinIO.")
    download_group = parser.add_mutually_exclusive_group()
    download_group.add_argument("--download-pdfs", action="store_true", default=True)
    download_group.add_argument("--no-download-pdfs", action="store_false", dest="download_pdfs")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    return parser.parse_args()


def main() -> None:
    raise SystemExit(collect(parse_args()))


if __name__ == "__main__":
    main()
