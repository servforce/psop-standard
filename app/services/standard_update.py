from __future__ import annotations

import importlib.util
import json
import logging
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.db.standard_library import StandardLibrarySessionLocal
from app.db.session import SessionLocal
from app.models.entities import Standard, StandardProcessingJob, StandardSyncItem, StandardSyncJob
from app.services.openstd_crawl import (
    OPENSTD_SOURCE_SITE,
    national_standard_category_from_code,
    object_key_for_openstd_pdf,
    openstd_pdf_filename,
    openstd_standard_id,
)
from app.services.standard_library_collect import (
    mirror_standard_library_item,
    mirror_standard_library_job,
    upsert_national_standard_from_raw,
)
from app.services.standard_library_atlas import standard_library_atlas_service
from app.services.standard_library_index import standard_library_index_service
from app.services.standard_library_materialize import standard_library_materialize_service
from app.services.standards import is_postgresql_database, standard_service
from app.services.storage import storage_service


LOGGER = logging.getLogger(__name__)
ADVISORY_LOCK_KEY = 2026080601


@dataclass(frozen=True)
class NationalUpdateOptions:
    request_interval_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    max_pages_safety: int
    known_page_stop_count: int
    check_upcoming: bool
    upcoming_limit: int
    active_check_limit: int
    new_materialize_limit: int
    dry_run: bool = False

    @classmethod
    def from_settings(cls, *, dry_run: bool = False) -> "NationalUpdateOptions":
        return cls(
            request_interval_seconds=settings.standard_update_request_interval_seconds,
            max_retries=settings.standard_update_max_retries,
            retry_backoff_seconds=settings.standard_update_retry_backoff_seconds,
            max_pages_safety=settings.standard_update_max_pages_safety,
            known_page_stop_count=settings.standard_update_known_page_stop_count,
            check_upcoming=settings.standard_update_check_upcoming,
            upcoming_limit=settings.standard_update_upcoming_limit,
            active_check_limit=settings.standard_update_active_check_limit,
            new_materialize_limit=settings.standard_update_new_materialize_limit,
            dry_run=dry_run,
        )


@dataclass
class NationalUpdateSummary:
    status: str = "completed"
    scanned_pages: int = 0
    discovered_count: int = 0
    new_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    downloaded_count: int = 0
    materialized_count: int = 0
    indexed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    download_failed_count: int = 0
    upload_failed_count: int = 0
    materialize_failed_count: int = 0
    index_failed_count: int = 0
    status_checked_count: int = 0
    lock_skipped: bool = False


class OpenStdImporterModule:
    def __init__(self, tool_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.tool_dir = Path(tool_dir or settings.openstd_importer_tool_dir)
        if not self.tool_dir.is_absolute():
            self.tool_dir = project_root / self.tool_dir
        self.script = self.tool_dir / "scripts" / "openstd_importer.py"
        if not self.script.exists():
            raise FileNotFoundError(f"OpenSTD importer tool script not found: {self.script}")
        spec = importlib.util.spec_from_file_location("openstd_update_importer", self.script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load OpenSTD importer module from: {self.script}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("openstd_update_importer", module)
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
        source_scope: str,
        source_label: str,
        source_url: str,
    ) -> list[dict[str, Any]]:
        return self.module.parse_list_items(
            html,
            page_url,
            source_scope=source_scope,
            source_label=source_label,
            source_url=source_url,
            allowed_statuses=set(),
        )

    def inspect_detail(self, detail_url: str) -> dict[str, Any]:
        return self.module.inspect_detail(
            detail_url,
            timeout_seconds=settings.openstd_download_timeout_seconds,
        )

    def download(self, *, detail_url: str, output_dir: Path) -> dict[str, Any]:
        return self.module.download(
            detail_url,
            output_dir=output_dir,
            timeout_seconds=settings.openstd_download_timeout_seconds,
        )


class StandardUpdateService:
    def __init__(self, importer: OpenStdImporterModule | None = None) -> None:
        self.importer = importer or OpenStdImporterModule()

    def run_national_update(self, options: NationalUpdateOptions) -> NationalUpdateSummary:
        summary = NationalUpdateSummary()
        with SessionLocal() as session:
            if not acquire_update_lock(session):
                LOGGER.warning("another national standard update is already running; skip this run")
                summary.status = "skipped_locked"
                summary.lock_skipped = True
                return summary

            job = create_sync_job(session, trigger_type="scheduled", options=options)
            try:
                self.scan_new_until_known(session, job=job, options=options, summary=summary)
                if options.check_upcoming:
                    self.check_due_upcoming(session, job=job, options=options, summary=summary)
                self.rotate_check_active(session, job=job, options=options, summary=summary)
                self.refresh_atlas_after_update(session, job=job, options=options, summary=summary)
                finish_sync_job(session, job, summary=summary)
                return summary
            except Exception as exc:
                LOGGER.exception("national standard update failed: %s", exc)
                summary.failed_count += 1
                summary.status = "failed"
                job.status = "failed"
                job.stage = "failed"
                job.error_message = str(exc)
                job.failed_count = summary.failed_count
                job.completed_at = datetime.now(timezone.utc)
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
                mirror_standard_library_job(
                    job,
                    job_type="scheduled_update",
                    status="failed",
                    stage="failed",
                    error_message=str(exc),
                )
                return summary
            finally:
                release_update_lock(session)

    def scan_new_until_known(
        self,
        session: Session,
        *,
        job: StandardSyncJob,
        options: NationalUpdateOptions,
        summary: NationalUpdateSummary,
    ) -> None:
        if options.max_pages_safety < 0:
            LOGGER.info("recent scan disabled because max_pages_safety=%s", options.max_pages_safety)
            return

        known_page_stop_count = max(1, options.known_page_stop_count)
        known_pages = 0
        scanned_pages = 0
        client = self.importer.new_http_client()
        try:
            for source in self.importer.resolve_sources(settings.openstd_crawl_scope, settings.openstd_source_url):
                page = 1
                known_pages = 0
                LOGGER.info("recent scan source=%s scope=%s", source["label"], source["scope"])
                while True:
                    if options.max_pages_safety > 0 and scanned_pages >= options.max_pages_safety:
                        LOGGER.info("recent scan stopped by max_pages_safety=%s", options.max_pages_safety)
                        return
                    page_url = self.importer.build_page_url(source["url"], page)
                    LOGGER.info("[recent] source=%s page=%s url=%s", source["label"], page, page_url)
                    html, final_url = client.get_html(page_url, referer=source["url"] if page > 1 else "")
                    total_pages = self.importer.parse_total_pages(html)
                    raw_items = self.importer.parse_list_items(
                        html,
                        final_url,
                        source_scope=source["scope"],
                        source_label=source["label"],
                        source_url=source["url"],
                    )
                    scanned_pages += 1
                    summary.scanned_pages += 1
                    job.current_page = scanned_pages
                    job.total_pages = max(job.total_pages or 0, total_pages or 0)
                    job.stage = "recent_scan"
                    page_new = 0
                    page_existing = 0
                    for raw in raw_items:
                        summary.discovered_count += 1
                        job.total_discovered += 1
                        standard_id = standard_id_from_raw(raw)
                        existing = find_existing_standard(session, raw=raw, standard_id=standard_id)
                        if existing is not None:
                            page_existing += 1
                            summary.unchanged_count += 1
                            job.unchanged_count += 1
                            library_standard_id = upsert_national_standard_from_raw(
                                raw,
                                legacy_standard_id=existing.id,
                                bucket=existing.source_pdf_bucket or "",
                                object_key=existing.source_pdf_object_key or "",
                                checksum=existing.source_pdf_hash or "",
                                size_bytes=existing.source_pdf_size_bytes,
                                fingerprint=existing.fingerprint or "",
                                file_access_type="downloadable" if existing.source_pdf_object_key else "unavailable",
                            )
                            mirror_standard_library_item(
                                job,
                                raw,
                                job_type="scheduled_update",
                                legacy_standard_id=existing.id,
                                standard_id=library_standard_id,
                                metadata_action="unchanged",
                                file_decision="no_download",
                                file_result="skipped",
                            )
                            continue
                        page_new += 1
                        self.process_new_standard(
                            session,
                            job=job,
                            raw=raw,
                            standard_id=standard_id,
                            options=options,
                            summary=summary,
                        )
                    session.add(job)
                    session.commit()
                    LOGGER.info(
                        "[recent] source=%s page=%s items=%s new=%s existing=%s known_pages=%s",
                        source["label"],
                        page,
                        len(raw_items),
                        page_new,
                        page_existing,
                        known_pages,
                    )
                    if page_new == 0:
                        known_pages += 1
                    else:
                        known_pages = 0
                    if known_pages >= known_page_stop_count:
                        LOGGER.info(
                            "recent scan stopped after %s consecutive known pages for source=%s",
                            known_pages,
                            source["label"],
                        )
                        break
                    if total_pages and page >= total_pages:
                        break
                    if not raw_items and not total_pages:
                        break
                    page += 1
                    sleep_if_needed(options.request_interval_seconds)
                sleep_if_needed(options.request_interval_seconds)
        finally:
            client.close()

    def process_new_standard(
        self,
        session: Session,
        *,
        job: StandardSyncJob,
        raw: dict[str, Any],
        standard_id: str,
        options: NationalUpdateOptions,
        summary: NationalUpdateSummary,
    ) -> None:
        code = clean_text(item_value(raw, "standard_code"))
        name = clean_text(item_value(raw, "standard_name"))
        detail_url = clean_text(item_value(raw, "detail_url"))
        job.current_item = f"{code} {name}".strip()
        LOGGER.info("[new] standard=%s code=%s name=%s", standard_id, code, name)
        if options.dry_run:
            summary.new_count += 1
            job.new_count += 1
            return

        payload: dict[str, Any] = {}
        phase = "download"
        last_error = ""
        for attempt in range(1, max(1, options.max_retries) + 1):
            try:
                workdir = Path(settings.standard_workdir)
                workdir.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=f"update_pdf_{standard_id}_", dir=str(workdir)) as tmp:
                    phase = "download"
                    payload = self.importer.download(detail_url=detail_url, output_dir=Path(tmp))
                    download_url = clean_text((payload.get("detail") or {}).get("download_url"))
                    download_method = clean_text(payload.get("download_method"))
                    if payload.get("status") == "skipped":
                        summary.skipped_count += 1
                        job.skipped_count += 1
                        job.skipped_unavailable_count += 1
                        record_sync_item(
                            session,
                            job=job,
                            raw=raw,
                            standard_id=standard_id,
                            action="not_downloadable",
                            status="skipped",
                            skip_reason=clean_text(payload.get("reason") or "not_downloadable"),
                            download_url=download_url,
                            download_method=download_method,
                            retry_count=attempt - 1,
                        )
                        library_standard_id = upsert_national_standard_from_raw(
                            raw,
                            detail=payload.get("detail") or {},
                            legacy_standard_id=standard_id,
                            file_access_type="unavailable",
                        )
                        mirror_standard_library_item(
                            job,
                            raw,
                            job_type="scheduled_update",
                            legacy_standard_id=standard_id,
                            standard_id=library_standard_id,
                            metadata_action="new",
                            file_decision="unavailable",
                            file_result="skipped",
                            retry_count=attempt - 1,
                            error_message=clean_text(payload.get("reason") or "not_downloadable"),
                        )
                        return
                    if payload.get("status") != "downloaded":
                        raise RuntimeError(clean_text(payload.get("reason") or "download_failed"))
                    pdf_path = Path(clean_text(payload.get("pdf_path")))
                    validate_pdf_file(pdf_path)
                    filename = openstd_pdf_filename(code, name)
                    object_key = object_key_for_openstd_pdf(standard_id, filename)
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
                        source_status_raw=clean_text(item_value(raw, "standard_status")),
                        publish_date=clean_text(item_value(raw, "publish_date")),
                    )
                    write_new_standard(
                        session,
                        raw=raw,
                        standard_id=standard_id,
                        bucket=stored.bucket,
                        object_key=stored.object_key,
                        checksum=stored.checksum,
                        size_bytes=stored.size_bytes,
                        fingerprint=fingerprint,
                        detail=payload.get("detail") or {},
                    )
                    record_sync_item(
                        session,
                        job=job,
                        raw=raw,
                        standard_id=standard_id,
                        action="new",
                        status="registered",
                        download_url=download_url,
                        download_method=download_method,
                        bucket=stored.bucket,
                        object_key=stored.object_key,
                        new_fingerprint=fingerprint,
                        retry_count=attempt - 1,
                    )
                    library_standard_id = upsert_national_standard_from_raw(
                        raw,
                        detail=payload.get("detail") or {},
                        legacy_standard_id=standard_id,
                        bucket=stored.bucket,
                        object_key=stored.object_key,
                        checksum=stored.checksum,
                        size_bytes=stored.size_bytes,
                        fingerprint=fingerprint,
                        file_access_type="downloadable",
                    )
                    mirror_standard_library_item(
                        job,
                        raw,
                        job_type="scheduled_update",
                        legacy_standard_id=standard_id,
                        standard_id=library_standard_id,
                        metadata_action="new",
                        file_decision="download",
                        file_result="success",
                        bucket=stored.bucket,
                        object_key=stored.object_key,
                        checksum=stored.checksum,
                        size_bytes=stored.size_bytes,
                        retry_count=attempt - 1,
                    )
                    summary.new_count += 1
                    summary.downloaded_count += 1
                    job.new_count += 1
                    job.uploaded_count += 1
                    session.add(job)
                    session.commit()
                    self.materialize_and_index_new_standard(
                        session,
                        job=job,
                        standard_id=standard_id,
                        library_standard_id=library_standard_id,
                        options=options,
                        summary=summary,
                    )
                    return
            except Exception as exc:
                session.rollback()
                last_error = str(exc)
                if attempt < max(1, options.max_retries):
                    sleep_seconds = max(0.0, options.retry_backoff_seconds * attempt)
                    LOGGER.warning(
                        "[new] retry standard=%s code=%s attempt=%s/%s phase=%s error=%s sleep=%.1fs",
                        standard_id,
                        code,
                        attempt,
                        options.max_retries,
                        phase,
                        last_error,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue

        action = "upload_failed" if phase == "upload" else "download_failed"
        summary.failed_count += 1
        job.failed_count += 1
        if action == "upload_failed":
            summary.upload_failed_count += 1
            job.upload_failed_count += 1
        else:
            summary.download_failed_count += 1
            job.download_failed_count += 1
        record_sync_item(
            session,
            job=job,
            raw=raw,
            standard_id=standard_id,
            action=action,
            status="failed",
            error_message=last_error,
            retry_count=max(1, options.max_retries),
            download_url=clean_text((payload.get("detail") or {}).get("download_url")) if payload else "",
            download_method=clean_text(payload.get("download_method")) if payload else "",
        )
        mirror_standard_library_item(
            job,
            raw,
            job_type="scheduled_update",
            legacy_standard_id=standard_id,
            metadata_action="new",
            file_decision="download",
            file_result="failed",
            retry_count=max(1, options.max_retries),
            error_message=last_error,
        )

    def materialize_and_index_new_standard(
        self,
        session: Session,
        *,
        job: StandardSyncJob,
        standard_id: str,
        library_standard_id: Any,
        options: NationalUpdateOptions,
        summary: NationalUpdateSummary,
    ) -> None:
        processing_job = standard_library_materialize_service.enqueue_materialize_job(library_standard_id)
        if options.new_materialize_limit > 0 and summary.materialized_count >= options.new_materialize_limit:
            LOGGER.info(
                "[new] standard-library materialize limit reached standard=%s limit=%s; leave as pending",
                library_standard_id,
                options.new_materialize_limit,
            )
            return
        phase = "materialize"
        try:
            with StandardLibrarySessionLocal() as library_session:
                standard_library_materialize_service.run_job(
                    library_session,
                    processing_job["job_id"],
                )
                summary.materialized_count += 1
                phase = "index"
                index_job = standard_library_index_service.create_index_job(library_session, library_standard_id)
                standard_library_index_service.run_job(library_session, index_job.id)
            summary.indexed_count += 1
        except Exception as exc:
            failure_action = "index_failed" if phase == "index" else "materialize_failed"
            if failure_action == "index_failed":
                summary.index_failed_count += 1
                job.index_failed_count += 1
            else:
                summary.materialize_failed_count += 1
                job.materialize_failed_count += 1
            summary.failed_count += 1
            job.failed_count += 1
            record_sync_item(
                session,
                job=job,
                raw=raw_from_standard(session, standard_id),
                standard_id=standard_id,
                action=failure_action,
                status="failed",
                error_message=str(exc),
            )
            return

    def refresh_atlas_after_update(
        self,
        session: Session,
        *,
        job: StandardSyncJob,
        options: NationalUpdateOptions,
        summary: NationalUpdateSummary,
    ) -> None:
        if options.dry_run or summary.indexed_count <= 0:
            return
        job.stage = "atlas_projection"
        session.add(job)
        session.commit()
        try:
            with StandardLibrarySessionLocal() as library_session:
                atlas_job = standard_library_atlas_service.create_atlas_job(library_session, priority=500)
                library_session.commit()
                result = standard_library_atlas_service.run_job(library_session, atlas_job.id)
            LOGGER.info("[atlas] refreshed standard-library atlas result=%s", result)
        except Exception as exc:
            LOGGER.exception("standard library atlas projection failed after scheduled update: %s", exc)
            summary.failed_count += 1
            job.failed_count += 1
            job.error_message = str(exc)
            session.add(job)
            session.commit()

    def check_due_upcoming(
        self,
        session: Session,
        *,
        job: StandardSyncJob,
        options: NationalUpdateOptions,
        summary: NationalUpdateSummary,
    ) -> None:
        if options.upcoming_limit < 0:
            return
        today = date.today()
        candidates = session.scalars(
            select(Standard)
            .where(Standard.source_status == "upcoming")
            .order_by(Standard.effective_date.asc(), Standard.last_status_checked_at.asc().nullsfirst())
        ).all()
        due = [standard for standard in candidates if parse_date(standard.effective_date) and parse_date(standard.effective_date) <= today]
        selected = due if options.upcoming_limit == 0 else due[: options.upcoming_limit]
        for standard in selected:
            self.check_standard_status(session, job=job, standard=standard, summary=summary)
            sleep_if_needed(options.request_interval_seconds)

    def rotate_check_active(
        self,
        session: Session,
        *,
        job: StandardSyncJob,
        options: NationalUpdateOptions,
        summary: NationalUpdateSummary,
    ) -> None:
        if options.active_check_limit < 0:
            return
        statement = (
            select(Standard)
            .where(Standard.source_status == "active")
            .order_by(Standard.last_status_checked_at.asc().nullsfirst(), Standard.last_synced_at.asc().nullsfirst())
        )
        if options.active_check_limit > 0:
            statement = statement.limit(options.active_check_limit)
        candidates = session.scalars(statement).all()
        for standard in candidates:
            self.check_standard_status(session, job=job, standard=standard, summary=summary)
            sleep_if_needed(options.request_interval_seconds)

    def check_standard_status(
        self,
        session: Session,
        *,
        job: StandardSyncJob,
        standard: Standard,
        summary: NationalUpdateSummary,
    ) -> None:
        if not standard.detail_url:
            standard.last_status_checked_at = datetime.now(timezone.utc)
            session.add(standard)
            session.commit()
            return
        job.stage = "status_check"
        job.current_item = f"{standard.code} {standard.name}".strip()
        session.add(job)
        session.commit()
        try:
            payload = self.importer.inspect_detail(standard.detail_url)
            detail = payload.get("detail") or {}
            latest_raw = clean_text(detail.get("standard_status")) or standard.source_status_raw
            latest_status = normalize_source_status(latest_raw)
            now = datetime.now(timezone.utc)
            summary.status_checked_count += 1
            if latest_status != standard.source_status and latest_raw:
                old_status = standard.source_status
                old_raw = standard.source_status_raw
                standard.source_status = latest_status
                standard.source_status_raw = latest_raw
                standard.effective_date = clean_text(detail.get("effective_date")) or standard.effective_date
                standard.last_synced_at = now
                standard.last_status_checked_at = now
                standard.updated_at = now
                summary.updated_count += 1
                job.updated_count += 1
                record_sync_item(
                    session,
                    job=job,
                    raw=raw_from_standard_object(standard),
                    standard_id=standard.id,
                    action="status_updated",
                    status="updated",
                    error_message=f"status {old_status}({old_raw}) -> {latest_status}({latest_raw})",
                )
                library_standard_id = upsert_national_standard_from_raw(
                    raw_from_standard_object(standard),
                    detail=detail,
                    legacy_standard_id=standard.id,
                    bucket=standard.source_pdf_bucket or "",
                    object_key=standard.source_pdf_object_key or "",
                    checksum=standard.source_pdf_hash or "",
                    size_bytes=standard.source_pdf_size_bytes,
                    fingerprint=standard.fingerprint or "",
                    file_access_type="downloadable" if standard.source_pdf_object_key else "unavailable",
                )
                mirror_standard_library_item(
                    job,
                    raw_from_standard_object(standard),
                    job_type="scheduled_update",
                    legacy_standard_id=standard.id,
                    standard_id=library_standard_id,
                    metadata_action="changed",
                    status_change_type="official_status",
                    file_decision="no_download",
                    file_result="skipped",
                    official_status_before=old_status,
                    official_status_after=latest_status,
                    error_message=f"status {old_status}({old_raw}) -> {latest_status}({latest_raw})",
                )
                LOGGER.info(
                    "[status] updated standard=%s code=%s %s -> %s",
                    standard.id,
                    standard.code,
                    old_status,
                    latest_status,
                )
            else:
                standard.last_status_checked_at = now
                standard.updated_at = now
                session.add(standard)
                session.commit()
        except Exception as exc:
            session.rollback()
            summary.failed_count += 1
            job.failed_count += 1
            record_sync_item(
                session,
                job=job,
                raw=raw_from_standard_object(standard),
                standard_id=standard.id,
                action="status_check_failed",
                status="failed",
                error_message=str(exc),
            )
            mirror_standard_library_item(
                job,
                raw_from_standard_object(standard),
                job_type="scheduled_update",
                legacy_standard_id=standard.id,
                metadata_action="unchanged",
                file_decision="no_download",
                file_result="failed",
                error_message=str(exc),
            )


def create_sync_job(session: Session, *, trigger_type: str, options: NationalUpdateOptions) -> StandardSyncJob:
    now = datetime.now(timezone.utc)
    job = StandardSyncJob(
        id=uuid.uuid4().hex,
        trigger_type=trigger_type,
        source_scope="national_updates",
        source_summary_json=json.dumps(
            {
                "recent_until_known": True,
                "max_pages_safety": options.max_pages_safety,
                "known_page_stop_count": options.known_page_stop_count,
                "check_upcoming": options.check_upcoming,
                "upcoming_limit": options.upcoming_limit,
                "active_check_limit": options.active_check_limit,
                "new_materialize_limit": options.new_materialize_limit,
                "dry_run": options.dry_run,
            },
            ensure_ascii=False,
        ),
        source_site=OPENSTD_SOURCE_SITE,
        source_url=settings.openstd_source_url,
        crawl_scope=settings.openstd_crawl_scope,
        status="running",
        stage="starting",
        started_at=now,
        updated_at=now,
    )
    session.add(job)
    session.commit()
    mirror_standard_library_job(job, job_type="scheduled_update", trigger_type="schedule", status="running")
    return job


def finish_sync_job(session: Session, job: StandardSyncJob, *, summary: NationalUpdateSummary) -> None:
    job.status = "completed_with_errors" if summary.failed_count else "completed"
    summary.status = job.status
    job.stage = "completed"
    job.total_pages = summary.scanned_pages
    job.total_discovered = summary.discovered_count
    job.new_count = summary.new_count
    job.updated_count = summary.updated_count
    job.unchanged_count = summary.unchanged_count
    job.uploaded_count = summary.downloaded_count
    job.download_failed_count = summary.download_failed_count
    job.upload_failed_count = summary.upload_failed_count
    job.materialize_failed_count = summary.materialize_failed_count
    job.index_failed_count = summary.index_failed_count
    job.failed_count = summary.failed_count
    job.skipped_count = summary.skipped_count
    job.completed_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    mirror_standard_library_job(job, job_type="scheduled_update", trigger_type="schedule", status=job.status)


def latest_standard_update_job(session: Session) -> StandardSyncJob | None:
    statement = (
        select(StandardSyncJob)
        .where(
            StandardSyncJob.trigger_type == "scheduled",
            StandardSyncJob.source_scope == "national_updates",
            StandardSyncJob.source_site == OPENSTD_SOURCE_SITE,
        )
        .order_by(StandardSyncJob.created_at.desc())
    )
    return session.scalars(statement).first()


def standard_update_job_to_dict(job: StandardSyncJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "trigger_type": job.trigger_type,
        "source_site": job.source_site,
        "source_scope": job.source_scope,
        "crawl_scope": job.crawl_scope,
        "status": job.status,
        "stage": job.stage,
        "total_pages": job.total_pages,
        "current_page": job.current_page,
        "total_discovered": job.total_discovered,
        "new_count": job.new_count,
        "updated_count": job.updated_count,
        "unchanged_count": job.unchanged_count,
        "uploaded_count": job.uploaded_count,
        "download_failed_count": job.download_failed_count,
        "upload_failed_count": job.upload_failed_count,
        "materialize_failed_count": job.materialize_failed_count,
        "index_failed_count": job.index_failed_count,
        "skipped_count": job.skipped_count,
        "failed_count": job.failed_count,
        "current_item": job.current_item,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def find_existing_standard(session: Session, *, raw: Any, standard_id: str) -> Standard | None:
    code = clean_text(item_value(raw, "standard_code"))
    detail_url = clean_text(item_value(raw, "detail_url"))
    external_id = external_id_from_detail_url(detail_url)
    conditions = []
    if code:
        conditions.append(Standard.code == code)
    if standard_id:
        conditions.append(Standard.id == standard_id)
    if external_id:
        conditions.append(Standard.external_id == external_id)
    if not conditions:
        return None
    return session.scalars(select(Standard).where(or_(*conditions))).first()


def write_new_standard(
    session: Session,
    *,
    raw: Any,
    standard_id: str,
    bucket: str,
    object_key: str,
    checksum: str,
    size_bytes: int,
    fingerprint: str,
    detail: dict[str, Any],
) -> Standard:
    now = datetime.now(timezone.utc)
    code = clean_text(item_value(raw, "standard_code"))
    raw_status = clean_text(detail.get("standard_status")) or clean_text(item_value(raw, "standard_status"))
    standard = Standard(
        id=standard_id,
        name=clean_text(item_value(raw, "standard_name")),
        code=code,
        standard_type="national",
        standard_category=national_standard_category_from_code(code),
        standard_org=standard_org_from_code(code),
        source_status=normalize_source_status(raw_status),
        source_status_raw=raw_status,
        publish_date=clean_text(item_value(raw, "publish_date")),
        effective_date=clean_text(detail.get("effective_date")) or clean_text(item_value(raw, "effective_date")),
        source_site=OPENSTD_SOURCE_SITE,
        source_scope=clean_text(item_value(raw, "source_scope")),
        source_url=clean_text(item_value(raw, "source_url")),
        detail_url=clean_text(item_value(raw, "detail_url")),
        external_id=external_id_from_detail_url(clean_text(item_value(raw, "detail_url"))),
        source_pdf_bucket=bucket,
        source_pdf_object_key=object_key,
        source_pdf_hash=checksum,
        source_pdf_size_bytes=size_bytes,
        materialize_status="not_started",
        materialize_error="",
        index_status="not_indexed",
        index_error="",
        fingerprint=fingerprint,
        last_synced_at=now,
        last_status_checked_at=now,
        updated_at=now,
    )
    session.add(standard)
    session.commit()
    return standard


def record_sync_item(
    session: Session,
    *,
    job: StandardSyncJob,
    raw: Any,
    standard_id: str,
    action: str,
    status: str,
    retry_count: int = 0,
    skip_reason: str = "",
    error_message: str = "",
    download_url: str = "",
    download_method: str = "",
    bucket: str = "",
    object_key: str = "",
    new_fingerprint: str = "",
) -> StandardSyncItem:
    item = StandardSyncItem(
        id=uuid.uuid4().hex,
        job_id=job.id,
        standard_id=standard_id,
        action=action,
        status=status,
        source_type="national",
        source_site=OPENSTD_SOURCE_SITE,
        source_scope=clean_text(item_value(raw, "source_scope")),
        source_label=clean_text(item_value(raw, "source_label")),
        source_url=clean_text(item_value(raw, "source_url")),
        external_id=external_id_from_detail_url(clean_text(item_value(raw, "detail_url"))),
        standard_code=clean_text(item_value(raw, "standard_code")),
        standard_name=clean_text(item_value(raw, "standard_name")),
        standard_status=normalize_source_status(clean_text(item_value(raw, "standard_status"))),
        source_status_raw=clean_text(item_value(raw, "standard_status")),
        publish_date=clean_text(item_value(raw, "publish_date")),
        effective_date=clean_text(item_value(raw, "effective_date")),
        detail_url=clean_text(item_value(raw, "detail_url")),
        download_url=download_url,
        download_method=download_method,
        new_fingerprint=new_fingerprint,
        skip_reason=skip_reason,
        error_message=error_message,
        retry_count=retry_count,
        source_pdf_bucket=bucket,
        source_pdf_object_key=object_key,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.add(job)
    session.commit()
    return item


def create_processing_job(session: Session, *, standard_id: str, job_type: str) -> str:
    job = StandardProcessingJob(
        id=uuid.uuid4().hex,
        standard_id=standard_id,
        job_type=job_type,
        status="queued",
        stage="materialize_queued",
        progress_percent=0,
        message="scheduled update queued",
        error_message="",
        updated_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    return job.id


def finish_processing_job(
    session: Session,
    job_id: str,
    *,
    status: str,
    stage: str,
    message: str,
    error: str = "",
) -> None:
    job = session.get(StandardProcessingJob, job_id)
    if job is None:
        return
    job.status = status
    job.stage = stage
    job.progress_percent = 100
    job.message = message
    job.error_message = error
    job.completed_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()


def acquire_update_lock(session: Session) -> bool:
    if not is_postgresql_database():
        return True
    return bool(session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}).scalar())


def release_update_lock(session: Session) -> None:
    if not is_postgresql_database():
        return
    session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
    session.commit()


def standard_id_from_raw(raw: Any) -> str:
    return openstd_standard_id(clean_text(item_value(raw, "standard_code")), clean_text(item_value(raw, "standard_name")))


def external_id_from_detail_url(detail_url: str) -> str:
    parsed = urlparse(detail_url or "")
    query = parse_qs(parsed.query)
    for key in ("hcno", "id"):
        values = query.get(key)
        if values:
            return values[0]
    return ""


def source_pdf_fingerprint(*, checksum: str, code: str, source_status_raw: str, publish_date: str) -> str:
    import hashlib

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


def normalize_source_status(raw_status: str) -> str:
    value = clean_text(raw_status)
    lowered = value.lower()
    if "废止" in value or "废" in value or "abolish" in lowered or "scrap" in lowered:
        return "abolished"
    if "即将" in value or "upcoming" in lowered:
        return "upcoming"
    if "现行" in value or "active" in lowered:
        return "active"
    return "active"


def standard_org_from_code(code: str) -> str:
    value = clean_text(code)
    return value.split(" ", 1)[0] if value else ""


def parse_date(value: str) -> date | None:
    import re

    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", value or "")
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def item_value(item: Any, key: str) -> Any:
    if item is None:
        return ""
    if isinstance(item, dict):
        return item.get(key, "")
    if is_dataclass(item):
        return getattr(item, key, "")
    return getattr(item, key, "")


def item_to_dict(item: Any) -> dict[str, Any]:
    if item is None:
        return {}
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "__dict__"):
        return dict(vars(item))
    return {}


def validate_pdf_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError("downloaded PDF path does not exist")
    with path.open("rb") as file:
        if file.read(5) != b"%PDF-":
            raise RuntimeError("downloaded file is not a valid PDF")


def raw_from_standard(session: Session, standard_id: str) -> dict[str, Any]:
    standard = session.get(Standard, standard_id)
    if standard is None:
        return {}
    return raw_from_standard_object(standard)


def raw_from_standard_object(standard: Standard) -> dict[str, Any]:
    return {
        "source_scope": standard.source_scope,
        "source_label": "",
        "source_url": standard.source_url,
        "standard_code": standard.code,
        "standard_name": standard.name,
        "standard_status": standard.source_status_raw,
        "publish_date": standard.publish_date,
        "effective_date": standard.effective_date,
        "detail_url": standard.detail_url,
    }


def sleep_if_needed(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


standard_update_service = StandardUpdateService()
