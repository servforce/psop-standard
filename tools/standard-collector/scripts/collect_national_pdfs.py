from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import re
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.standard_library import StandardLibrarySessionLocal, init_standard_library_db
from app.models.standard_library import StandardLibraryStandard, StandardSyncItem, StandardSyncJob
from app.services.standard_library_collect import (
    external_id_from_detail_url,
    find_standard,
    metadata_fingerprint,
    national_standard_category_from_code,
    normalize_code,
    upsert_standard_from_raw,
    upsert_standard_library_item,
    upsert_standard_library_job,
)
from app.services.standard_library_materialize import standard_library_materialize_service
from app.services.storage import storage_service


LOGGER = logging.getLogger("collect_national_pdfs")
OPENSTD_SOURCE_SITE = "openstd.samr.gov.cn"
DEFAULT_LOG_FILE = PROJECT_ROOT / "tools" / "standard-collector" / "logs" / "collect_national_pdfs.log"


class OpenStdImporterClient:
    def __init__(self, tool_dir: str | Path | None = None) -> None:
        self.tool_dir = Path(tool_dir or settings.openstd_importer_tool_dir)
        if not self.tool_dir.is_absolute():
            self.tool_dir = PROJECT_ROOT / self.tool_dir
        self.script = self.tool_dir / "scripts" / "openstd_importer.py"
        if not self.script.exists():
            raise FileNotFoundError(f"OpenSTD importer tool script not found: {self.script}")
        spec = importlib.util.spec_from_file_location("openstd_importer_tool", self.script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load OpenSTD importer module from: {self.script}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("openstd_importer_tool", module)
        spec.loader.exec_module(module)
        module.progress = lambda message: LOGGER.info("%s", message)
        self.module = module

    def new_http_client(self):
        return self.module.OpenStdHttpClient(timeout_seconds=settings.openstd_download_timeout_seconds)

    def resolve_sources(self, scope: str, url: str) -> list[dict[str, str]]:
        return self.module.resolve_sources(scope, url)

    def build_page_url(self, source_url: str, page: int) -> str:
        return self.module.build_page_url(source_url, page)

    def parse_total_pages(self, html: str) -> int:
        return self.module.parse_total_pages(html)

    def parse_list_items(
        self,
        html: str,
        page_url: str,
        *,
        source_scope: str = "",
        source_label: str = "",
        source_url: str = "",
        allowed_statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.module.parse_list_items(
            html,
            page_url,
            source_scope=source_scope,
            source_label=source_label,
            source_url=source_url,
            allowed_statuses=allowed_statuses,
        )

    def download(self, *, detail_url: str, output_dir: Path) -> dict[str, Any]:
        return self.module.download(
            detail_url,
            output_dir=output_dir,
            timeout_seconds=settings.openstd_download_timeout_seconds,
        )


def source_pdf_fingerprint(*, checksum: str, code: str, source_status_raw: str, publish_date: str) -> str:
    payload = json.dumps(
        {
            "checksum": checksum,
            "code": code,
            "source_status_raw": source_status_raw,
            "publish_date": publish_date,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_sync_job(session: Session, *, args: argparse.Namespace, trigger_type: str) -> StandardSyncJob:
    job = upsert_standard_library_job(
        session,
        {"id": f"{trigger_type}:{datetime.now(timezone.utc).isoformat()}", "created_at": datetime.now(timezone.utc)},
        job_type="historical_collect",
        source="national",
        trigger_type="admin",
        status="running",
        stage="retrying" if args.retry_failed else "discovering",
        entry_url=args.source_url,
    )
    job.started_at = job.started_at or datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    return job


def existing_standard(session: Session, *, code: str, detail_url: str) -> StandardLibraryStandard | None:
    external_id = external_id_from_detail_url(detail_url)
    return find_standard(
        session,
        source="national",
        code_normalized=normalize_code(code),
        category=national_standard_category_from_code(code),
        external_id=external_id,
    )


def failed_items(session: Session, *, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(StandardSyncItem)
        .where(
            StandardSyncItem.source == "national",
            StandardSyncItem.file_result == "failed",
        )
        .order_by(StandardSyncItem.updated_at.asc(), StandardSyncItem.created_at.asc())
    )
    if limit > 0:
        statement = statement.limit(limit)
    rows = session.scalars(statement).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        if row.standard_id:
            standard = session.get(StandardLibraryStandard, row.standard_id)
            if standard is not None and standard.source_pdf_object_key:
                continue
        items.append(
            {
                "source_scope": row.category or "",
                "source_label": "National standards",
                "source_site": OPENSTD_SOURCE_SITE,
                "standard_code": row.code or "",
                "standard_name": row.name or "",
                "standard_status": row.official_status_after or "",
                "source_status_raw": row.official_status_after or "",
                "detail_url": row.detail_url or "",
                "external_id": row.external_id or "",
            }
        )
    return items


def process_one(
    *,
    session: Session,
    importer: OpenStdImporterClient,
    job: StandardSyncJob,
    raw: dict[str, Any],
    args: argparse.Namespace,
) -> str:
    code = clean_text(raw.get("standard_code"))
    name = clean_text(raw.get("standard_name"))
    detail_url = clean_text(raw.get("detail_url"))
    LOGGER.info("standard=%s name=%s detail=%s", code, name, detail_url)

    existing = existing_standard(session, code=code, detail_url=detail_url)
    if existing is not None and existing.source_pdf_object_key:
        LOGGER.info("skip existing standard=%s name=%s", code or existing.id, name)
        upsert_standard_library_item(
            session,
            job=job,
            raw=raw,
            source="national",
            standard_id=existing.id,
            metadata_action="unchanged",
            file_decision="no_download",
            file_result="skipped",
        )
        update_job_counts(session, job, processed_delta=1)
        return "unchanged"

    workdir = Path(settings.standard_workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    last_error = ""
    phase = "download"
    payload: dict[str, Any] = {}
    for attempt in range(1, args.max_retries + 1):
        try:
            with tempfile.TemporaryDirectory(prefix=f"national_pdf_{safe_token(code or name)}_", dir=str(workdir)) as tmp:
                phase = "download"
                payload = importer.download(detail_url=detail_url, output_dir=Path(tmp))
                detail = payload.get("detail") or {}
                download_url = clean_text(detail.get("download_url"))
                download_method = clean_text(payload.get("download_method"))
                if payload.get("status") == "skipped":
                    standard = upsert_standard_from_raw(
                        session,
                        raw,
                        source="national",
                        detail=detail,
                        file_access_type="unavailable",
                    )
                    upsert_standard_library_item(
                        session,
                        job=job,
                        raw=raw,
                        source="national",
                        standard_id=standard.id,
                        metadata_action="new" if existing is None else "changed",
                        file_decision="unavailable",
                        file_result="skipped",
                        retry_count=attempt - 1,
                        error_message=clean_text(payload.get("reason") or "not_downloadable"),
                    )
                    update_job_counts(session, job, processed_delta=1)
                    return "skipped"
                if payload.get("status") != "downloaded":
                    raise RuntimeError(clean_text(payload.get("reason") or "download_failed"))
                pdf_path = Path(clean_text(payload.get("pdf_path")))
                validate_pdf_file(pdf_path)

                filename = openstd_pdf_filename(code, name)
                object_key = object_key_for_openstd_pdf(code or name or detail_url, filename)
                phase = "upload"
                stored = storage_service.upload_file(
                    object_key=object_key,
                    path=pdf_path,
                    media_type="application/pdf",
                    bucket=settings.standard_library_object_store_bucket,
                )
                fingerprint = source_pdf_fingerprint(
                    checksum=stored.checksum,
                    code=code,
                    source_status_raw=clean_text(raw.get("standard_status") or raw.get("source_status_raw")),
                    publish_date=clean_text(raw.get("publish_date")),
                )
                standard = upsert_standard_from_raw(
                    session,
                    raw,
                    source="national",
                    detail=detail,
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    checksum=stored.checksum,
                    size_bytes=stored.size_bytes,
                    fingerprint=fingerprint,
                    file_access_type="downloadable",
                )
                item = upsert_standard_library_item(
                    session,
                    job=job,
                    raw=raw,
                    source="national",
                    standard_id=standard.id,
                    metadata_action="new" if existing is None else "changed",
                    file_decision="download" if existing is None else "redownload",
                    file_result="success",
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    checksum=stored.checksum,
                    size_bytes=stored.size_bytes,
                    retry_count=attempt - 1,
                )
                session.flush([standard, item])
                standard_library_materialize_service.create_materialize_job(
                    session,
                    standard.id,
                    source_sync_job_id=job.id,
                    source_sync_item_id=item.id,
                )
                update_job_counts(session, job, processed_delta=1, downloaded_delta=1)
                LOGGER.info(
                    "registered standard=%s name=%s bucket=%s object_key=%s size_bytes=%s download_method=%s",
                    code,
                    name,
                    stored.bucket,
                    stored.object_key,
                    stored.size_bytes,
                    download_method,
                )
                return "registered"
        except Exception as exc:
            session.rollback()
            last_error = str(exc)
            if attempt < args.max_retries:
                sleep_seconds = max(0.0, args.retry_backoff_seconds * attempt)
                LOGGER.warning(
                    "retrying standard=%s name=%s attempt=%s/%s phase=%s error=%s sleep=%.1fs",
                    code,
                    name,
                    attempt,
                    args.max_retries,
                    phase,
                    last_error,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue

    upsert_standard_library_item(
        session,
        job=job,
        raw=raw,
        source="national",
        metadata_action="new" if existing is None else "changed",
        file_decision="download",
        file_result="failed",
        retry_count=args.max_retries,
        error_message=last_error,
    )
    update_job_counts(session, job, processed_delta=1, failed_delta=1)
    LOGGER.error("failed standard=%s name=%s phase=%s error=%s", code, name, phase, last_error)
    return "failed"


def stream_discovery(
    importer: OpenStdImporterClient,
    args: argparse.Namespace,
    *,
    page_handler,
) -> tuple[int, int]:
    client = importer.new_http_client()
    total_seen = 0
    pages_processed = 0
    try:
        for source in importer.resolve_sources(args.scope, args.source_url):
            LOGGER.info("discovering source=%s scope=%s url=%s", source["label"], source["scope"], source["url"])
            page = 1
            source_pages_processed = 0
            while True:
                if args.max_items > 0 and total_seen >= args.max_items:
                    return total_seen, pages_processed
                page_url = importer.build_page_url(source["url"], page)
                LOGGER.info("[discover] source=%s page=%s url=%s", source["label"], page, page_url)
                html, final_url = client.get_html(page_url, referer=source["url"] if page > 1 else "")
                source_total_pages = importer.parse_total_pages(html)
                discovered_items = importer.parse_list_items(
                    html,
                    final_url,
                    source_scope=source["scope"],
                    source_label=source["label"],
                    source_url=source["url"],
                    allowed_statuses={item.strip() for item in settings.openstd_allowed_statuses.split(",") if item.strip()},
                )
                page_items = [asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item) for item in discovered_items]
                pages_processed += 1
                source_pages_processed += 1
                total_seen += len(page_items)
                should_stop = page_handler(
                    source=source,
                    page=page,
                    page_url=page_url,
                    final_url=final_url,
                    source_total_pages=source_total_pages,
                    page_items=page_items,
                    pages_processed=pages_processed,
                    total_seen=total_seen,
                )
                if should_stop:
                    return total_seen, pages_processed
                if args.max_pages > 0 and source_pages_processed >= args.max_pages:
                    break
                if source_total_pages and page >= source_total_pages:
                    break
                if not page_items and not source_total_pages:
                    break
                page += 1
                sleep_if_needed(args.request_interval)
            if args.max_items > 0 and total_seen >= args.max_items:
                break
            sleep_if_needed(args.request_interval)
    finally:
        client.close()
    return total_seen, pages_processed


def run_collection(args: argparse.Namespace) -> int:
    args.max_retries = max(1, args.max_retries)
    setup_logging(args.log_file)
    LOGGER.info(
        "===== BEGIN RUN %s mode=%s scope=%s max_pages=%s max_items=%s db=standard_library bucket=%s =====",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retry_failed" if args.retry_failed else "initial",
        args.scope,
        args.max_pages,
        args.max_items,
        settings.standard_library_object_store_bucket,
    )
    importer = OpenStdImporterClient()

    if args.dry_run:
        preview: list[dict[str, Any]] = []

        def collect_preview(**kwargs: Any) -> bool:
            for raw in kwargs["page_items"]:
                if len(preview) < 5:
                    preview.append(raw)
            return False

        total_seen, _ = stream_discovery(importer, args, page_handler=collect_preview)
        print(json.dumps({"dry_run": True, "count": total_seen, "items": preview}, ensure_ascii=False, indent=2))
        return 0

    init_standard_library_db()
    with StandardLibrarySessionLocal() as session:
        job = create_sync_job(session, args=args, trigger_type="retry_failed" if args.retry_failed else "initial")
        processed_items = 0

        if args.retry_failed:
            items = failed_items(session, limit=args.failed_limit)
            job.stage = "retrying"
            job.discovered_count = len(items)
            job.need_download_count = len(items)
            session.add(job)
            session.commit()
            for index, raw in enumerate(items, start=1):
                LOGGER.info("[%s/%s] retrying %s %s", index, len(items), raw.get("standard_code"), raw.get("standard_name"))
                process_one(session=session, importer=importer, job=job, raw=raw, args=args)
                processed_items += 1
                sleep_if_needed(args.request_interval)
        else:

            def handle_page(**kwargs: Any) -> bool:
                nonlocal processed_items
                page_items = kwargs["page_items"]
                pages_processed = int(kwargs["pages_processed"] or 0)
                total_seen = int(kwargs["total_seen"] or 0)
                job.scanned_pages = pages_processed
                job.discovered_count = total_seen
                job.need_download_count = total_seen
                job.stage = "processing"
                job.heartbeat_at = datetime.now(timezone.utc)
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
                for raw in page_items:
                    if args.max_items > 0 and processed_items >= args.max_items:
                        return True
                    result = process_one(session=session, importer=importer, job=job, raw=raw, args=args)
                    processed_items += 1
                    LOGGER.info("[%s/%s] %s -> %s", processed_items, args.max_items if args.max_items > 0 else total_seen, job.stage, result)
                    sleep_if_needed(args.request_interval)
                return False

            stream_discovery(importer, args, page_handler=handle_page)

        finish_job(session, job)
        emit_run_summary(build_run_summary(job=job, total_items=processed_items, mode="retry_failed" if args.retry_failed else "initial"))
        return 0 if int(job.failed_count or 0) == 0 else 1


def update_job_counts(
    session: Session,
    job: StandardSyncJob,
    *,
    processed_delta: int = 0,
    downloaded_delta: int = 0,
    failed_delta: int = 0,
) -> None:
    now = datetime.now(timezone.utc)
    job.processed_count = int(job.processed_count or 0) + processed_delta
    job.downloaded_count = int(job.downloaded_count or 0) + downloaded_delta
    job.failed_count = int(job.failed_count or 0) + failed_delta
    job.download_failed_count = int(job.download_failed_count or 0) + failed_delta
    job.heartbeat_at = now
    job.updated_at = now
    session.add(job)
    session.commit()


def finish_job(session: Session, job: StandardSyncJob) -> None:
    now = datetime.now(timezone.utc)
    job.status = "completed" if int(job.failed_count or 0) == 0 else "failed"
    job.stage = "completed" if job.status == "completed" else "completed_with_errors"
    job.progress_percent = 100
    job.finished_at = now
    job.heartbeat_at = now
    job.updated_at = now
    if job.started_at:
        job.duration_ms = max(0, int((job.finished_at - job.started_at).total_seconds() * 1000))
    session.add(job)
    session.commit()


def build_run_summary(*, job: StandardSyncJob, total_items: int, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "job_id": str(job.id),
        "status": job.status,
        "total_items": total_items,
        "processed_items": int(job.processed_count or 0),
        "downloaded_success": int(job.downloaded_count or 0),
        "download_failed": int(job.download_failed_count or 0),
        "failed_total": int(job.failed_count or 0),
        "database": "octopus_standard_library",
        "bucket": settings.standard_library_object_store_bucket,
    }


def emit_run_summary(summary: dict[str, Any]) -> None:
    LOGGER.info("summary: %s", summary)
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))


def openstd_pdf_filename(standard_code: str, standard_name: str = "") -> str:
    base = clean_text(standard_code) or clean_text(standard_name) or "openstd"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", base).strip(" ._")
    return f"{filename or 'openstd'}.pdf"


def object_key_for_openstd_pdf(identity: str, filename: str) -> str:
    return f"pdf/national/{safe_token(identity)}/{filename}"


def validate_pdf_file(pdf_path: Path) -> None:
    if not pdf_path.exists():
        raise RuntimeError("downloaded PDF path does not exist")
    with pdf_path.open("rb") as file:
        header = file.read(5)
    if header != b"%PDF-":
        raise RuntimeError("downloaded file is not a valid PDF")


def safe_token(value: str) -> str:
    text = clean_text(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    ascii_part = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:48]
    return f"{ascii_part}-{digest}" if ascii_part else digest


def setup_logging(log_file: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        path = Path(log_file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def sleep_if_needed(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect national standard PDFs into the new standard library database.")
    parser.add_argument("--scope", default=settings.openstd_crawl_scope or "all_national_standards")
    parser.add_argument("--source-url", default=settings.openstd_source_url)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--request-interval", type=float, default=settings.standard_collector_request_interval_seconds)
    parser.add_argument("--max-retries", type=int, default=max(1, settings.standard_collector_max_retries))
    parser.add_argument("--retry-backoff-seconds", type=float, default=settings.standard_collector_retry_backoff_seconds)
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed download/upload items in the new standard library.")
    parser.add_argument("--failed-limit", type=int, default=0, help="Max failed items to retry; 0 means no limit.")
    parser.add_argument("--dry-run", action="store_true", help="Discover without downloading or writing DB.")
    parser.add_argument("--log-file", default=settings.standard_collector_log_file or str(DEFAULT_LOG_FILE))
    return parser.parse_args()


def main() -> None:
    raise SystemExit(run_collection(parse_args()))


if __name__ == "__main__":
    main()
