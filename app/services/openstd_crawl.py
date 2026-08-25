from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.models.entities import Standard, StandardSyncItem, StandardSyncJob
from app.services.standard_library_collect import (
    mirror_standard_library_item,
    mirror_standard_library_job,
    standard_exists_in_library,
    upsert_national_standard_from_raw,
)
from app.services.standard_library_materialize import standard_library_materialize_service
from app.services.standards import safe_filename, standard_id_from_name
from app.services.storage import StorageService, storage_service


OPENSTD_SOURCE_SITE = "openstd.samr.gov.cn"
DOWNLOADABLE_STATUSES = {"pending_download", "failed"}


def normalize_source_status(raw_status: str) -> str:
    value = raw_status.strip()
    if "废" in value or "abolish" in value.lower():
        return "abolished"
    if "即将" in value or "upcoming" in value.lower():
        return "upcoming"
    if "现行" in value or "active" in value.lower():
        return "active"
    return "active"


def openstd_standard_id(standard_code: str, standard_name: str = "") -> str:
    basis = standard_code.strip() or standard_name.strip() or uuid.uuid4().hex
    return standard_id_from_name(f"{basis}.pdf")


def openstd_pdf_filename(standard_code: str, standard_name: str = "") -> str:
    base = standard_code.strip() or standard_name.strip() or "openstd"
    name = safe_filename(f"{base}.pdf")
    if Path(name).suffix.lower() != ".pdf":
        name = f"{Path(name).stem}.pdf"
    return name


def national_standard_category_from_code(standard_code: str) -> str:
    normalized = " ".join((standard_code or "").strip().upper().split())
    if normalized.startswith("GB/T"):
        return "recommended"
    if normalized.startswith("GB/Z"):
        return "guidance"
    if normalized.startswith("GB "):
        return "mandatory"
    return "national"


def object_key_for_openstd_pdf(standard_id: str, filename: str) -> str:
    return f"standards/{standard_id}/source/{filename}"


class OpenStdToolRunner:
    def __init__(self, tool_dir: str | Path | None = None) -> None:
        self.tool_dir = Path(tool_dir or settings.openstd_importer_tool_dir)
        if not self.tool_dir.is_absolute():
            self.tool_dir = Path.cwd() / self.tool_dir
        self.script = self.tool_dir / "scripts" / "openstd_importer.py"

    def discover(self, *, url: str, scope: str, max_pages: int, interval_seconds: float) -> dict[str, Any]:
        command = [
            sys.executable,
            str(self.script),
            "discover",
            "--url",
            url,
            "--scope",
            scope,
            "--allowed-statuses",
            settings.openstd_allowed_statuses,
            "--max-pages",
            str(max_pages),
            "--max-items",
            str(settings.openstd_max_items),
            "--interval",
            str(interval_seconds),
            "--output-json",
        ]
        discover_timeout = settings.standard_collector_discover_timeout_seconds
        return self._run(command, timeout_seconds=discover_timeout if discover_timeout > 0 else None)

    def download(self, *, detail_url: str, output_dir: Path) -> dict[str, Any]:
        command = [
            sys.executable,
            str(self.script),
            "download",
            "--detail-url",
            detail_url,
            "--output-dir",
            str(output_dir),
            "--timeout",
            str(settings.openstd_download_timeout_seconds),
            "--output-json",
        ]
        return self._run(command, timeout_seconds=settings.openstd_download_timeout_seconds + 60)

    def _run(self, command: list[str], *, timeout_seconds: float | None) -> dict[str, Any]:
        if not self.script.exists():
            raise FileNotFoundError(f"OpenSTD importer tool script not found: {self.script}")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_label = f"{timeout_seconds} seconds" if timeout_seconds is not None else "no timeout"
            raise RuntimeError(
                f"OpenSTD importer timed out after {timeout_label}. "
                "For full historical discovery, set STANDARD_COLLECTOR_DISCOVER_TIMEOUT_SECONDS=0."
            ) from exc
        stdout = completed.stdout.strip()
        if not stdout:
            raise RuntimeError(f"OpenSTD importer produced no JSON output: {completed.stderr.strip()}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenSTD importer returned invalid JSON: {stdout[:1000]}") from exc
        if completed.returncode not in {0, 2} and payload.get("status") == "failed":
            raise RuntimeError(payload.get("message") or payload.get("reason") or completed.stderr.strip())
        return payload


class OpenStdCrawlService:
    def __init__(
        self,
        *,
        storage: StorageService = storage_service,
        tool_runner: OpenStdToolRunner | None = None,
    ) -> None:
        self.storage = storage
        self.tool_runner = tool_runner or OpenStdToolRunner()

    def create_job(self, session: Session) -> dict[str, Any]:
        running = session.scalars(
            select(StandardSyncJob)
            .where(StandardSyncJob.status.in_(["queued", "running"]))
            .order_by(StandardSyncJob.created_at.desc())
        ).first()
        if running is not None:
            result = self.job_to_dict(session, running)
            result["created"] = False
            return result
        job = StandardSyncJob(
            id=uuid.uuid4().hex,
            source_site=OPENSTD_SOURCE_SITE,
            source_url=settings.openstd_source_url,
            crawl_scope=settings.openstd_crawl_scope,
            status="queued",
        )
        session.add(job)
        session.commit()
        mirror_standard_library_job(job, job_type="historical_collect", trigger_type="admin", status="pending")
        result = self.job_to_dict(session, job)
        result["created"] = True
        return result

    def latest_job(self, session: Session) -> dict[str, Any] | None:
        job = session.scalars(select(StandardSyncJob).order_by(StandardSyncJob.created_at.desc())).first()
        return self.job_to_dict(session, job) if job else None

    def claim_next_job(self, session: Session) -> StandardSyncJob | None:
        job = session.scalars(
            select(StandardSyncJob)
            .where(StandardSyncJob.status.in_(["queued", "running"]))
            .order_by(StandardSyncJob.created_at.asc())
        ).first()
        if job is None:
            return None
        if job.status == "queued":
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
            mirror_standard_library_job(job, job_type="historical_collect", trigger_type="admin", status="running")
        return job

    def run_job(self, session: Session, job_id: str) -> dict[str, Any]:
        job = session.get(StandardSyncJob, job_id)
        if job is None:
            raise ValueError(f"OpenSTD crawl job not found: {job_id}")
        job.status = "running"
        job.started_at = job.started_at or datetime.now(timezone.utc)
        job.error_message = ""
        session.add(job)
        session.commit()
        mirror_standard_library_job(job, job_type="historical_collect", trigger_type="admin", status="running")
        try:
            if not self._has_items(session, job_id):
                self._discover_items(session, job)
            self._download_items(session, job)
            self._refresh_counts(session, job)
            job.status = "completed_with_errors" if job.failed_count else "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
            mirror_standard_library_job(job, job_type="historical_collect", trigger_type="admin", status=job.status)
            return self.job_to_dict(session, job)
        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
            mirror_standard_library_job(
                job,
                job_type="historical_collect",
                trigger_type="admin",
                status="failed",
                error_message=str(exc),
            )
            raise

    def _has_items(self, session: Session, job_id: str) -> bool:
        count = session.scalar(select(func.count()).select_from(StandardSyncItem).where(StandardSyncItem.job_id == job_id))
        return bool(count)

    def _discover_items(self, session: Session, job: StandardSyncJob) -> None:
        payload = self.tool_runner.discover(
            url=job.source_url or settings.openstd_source_url,
            scope=job.crawl_scope or settings.openstd_crawl_scope,
            max_pages=settings.openstd_max_pages,
            interval_seconds=settings.openstd_request_interval_seconds,
        )
        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []
        job.total_pages = int(payload.get("total_pages") or 0)
        job.current_page = int(payload.get("pages_processed") or 0)
        job.total_discovered = len(items)
        job.scanned_count = len(items)
        session.add(job)
        seen_codes: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("standard_code") or "").strip()
            detail_url = str(raw.get("detail_url") or "").strip()
            if not code and not detail_url:
                continue
            is_duplicate_in_job = bool(code and code in seen_codes)
            if code:
                seen_codes.add(code)
            is_existing_standard = self._is_duplicate_standard(session, code)
            is_existing_in_library = standard_exists_in_library(code=code, detail_url=detail_url)
            if is_existing_standard and is_existing_in_library:
                job.unchanged_count += 1
                mirror_standard_library_item(
                    job,
                    raw,
                    job_type="historical_collect",
                    metadata_action="unchanged",
                    file_decision="no_download",
                    file_result="skipped",
                )
                continue
            if is_duplicate_in_job:
                job.skipped_count += 1
                mirror_standard_library_item(
                    job,
                    raw,
                    job_type="historical_collect",
                    metadata_action="unchanged",
                    file_decision="skip",
                    file_result="skipped",
                    error_message="duplicate_in_job",
                )
                continue
            item = StandardSyncItem(
                id=uuid.uuid4().hex,
                job_id=job.id,
                standard_id=openstd_standard_id(code, str(raw.get("standard_name") or "")),
                action="new",
                standard_code=code,
                standard_name=str(raw.get("standard_name") or ""),
                standard_status=str(raw.get("standard_status") or ""),
                source_status_raw=str(raw.get("standard_status") or ""),
                publish_date=str(raw.get("publish_date") or ""),
                effective_date=str(raw.get("effective_date") or ""),
                source_type="national",
                source_site=OPENSTD_SOURCE_SITE,
                source_scope=str(raw.get("source_scope") or ""),
                source_label=str(raw.get("source_label") or ""),
                source_url=str(raw.get("source_url") or ""),
                detail_url=detail_url,
                status="pending_download",
                skip_reason="",
            )
            session.add(item)
            mirror_standard_library_item(
                job,
                raw,
                job_type="historical_collect",
                legacy_standard_id=item.standard_id,
                metadata_action="new",
                file_decision="download",
            )
        session.commit()
        self._refresh_counts(session, job)

    def _download_items(self, session: Session, job: StandardSyncJob) -> None:
        while True:
            item = session.scalars(
                select(StandardSyncItem)
                .where(
                    StandardSyncItem.job_id == job.id,
                    StandardSyncItem.status.in_(list(DOWNLOADABLE_STATUSES)),
                    StandardSyncItem.retry_count < settings.openstd_max_retries,
                )
                .order_by(StandardSyncItem.created_at.asc())
            ).first()
            if item is None:
                return
            if self._is_duplicate_standard(session, item.standard_code) and standard_exists_in_library(
                code=item.standard_code,
                detail_url=item.detail_url,
            ):
                item.status = "skipped_duplicate"
                item.skip_reason = "standard_code_exists"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()
                mirror_standard_library_item(
                    job,
                    raw_from_openstd_item(item),
                    job_type="historical_collect",
                    legacy_standard_id=item.standard_id or "",
                    metadata_action="unchanged",
                    file_decision="no_download",
                    file_result="skipped",
                    error_message=item.skip_reason,
                )
                self._refresh_counts(session, job)
                continue
            self._download_one(session, job, item)
            time.sleep(max(0.0, settings.openstd_request_interval_seconds))

    def _download_one(self, session: Session, job: StandardSyncJob, item: StandardSyncItem) -> None:
        item.status = "downloading"
        item.retry_count += 1
        item.error_message = ""
        item.updated_at = datetime.now(timezone.utc)
        job.current_item = f"{item.standard_code} {item.standard_name}".strip()
        job.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.add(job)
        session.commit()
        try:
            workdir = Path(settings.standard_workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"openstd_{item.id}_", dir=str(workdir)) as tmp:
                payload = self.tool_runner.download(detail_url=item.detail_url, output_dir=Path(tmp))
                item.download_url = str((payload.get("detail") or {}).get("download_url") or "")
                item.download_method = str(payload.get("download_method") or "")
                if payload.get("status") == "skipped":
                    item.status = "skipped_unavailable"
                    item.skip_reason = str(payload.get("reason") or "not_downloadable")
                    session.add(item)
                    session.commit()
                    library_standard_id = upsert_national_standard_from_raw(
                        raw_from_openstd_item(item),
                        detail=payload.get("detail") or {},
                        legacy_standard_id=item.standard_id or "",
                        file_access_type="unavailable",
                    )
                    mirror_standard_library_item(
                        job,
                        raw_from_openstd_item(item),
                        job_type="historical_collect",
                        legacy_standard_id=item.standard_id or "",
                        standard_id=library_standard_id,
                        metadata_action="new",
                        file_decision="unavailable",
                        file_result="skipped",
                        retry_count=item.retry_count,
                        error_message=item.skip_reason,
                    )
                    self._refresh_counts(session, job)
                    return
                if payload.get("status") != "downloaded":
                    raise RuntimeError(str(payload.get("reason") or "download_failed"))
                pdf_path = Path(str(payload.get("pdf_path") or ""))
                if not pdf_path.exists():
                    raise RuntimeError("downloaded PDF path does not exist")
                effective_date = str((payload.get("detail") or {}).get("effective_date") or item.effective_date or "")
                standard_id = item.standard_id or openstd_standard_id(item.standard_code, item.standard_name)
                filename = openstd_pdf_filename(item.standard_code, item.standard_name)
                object_key = object_key_for_openstd_pdf(standard_id, filename)
                stored = self.storage.upload_file(
                    object_key=object_key,
                    path=pdf_path,
                    media_type="application/pdf",
                    bucket=settings.openstd_object_store_bucket,
                )
                standard = Standard(
                    id=standard_id,
                    name=item.standard_name or Path(filename).stem,
                    code=item.standard_code,
                    standard_type="national",
                    standard_category=national_standard_category_from_code(item.standard_code),
                    standard_org=(item.standard_code.split(" ", 1)[0] if item.standard_code else ""),
                    source_status=normalize_source_status(item.standard_status),
                    source_status_raw=item.standard_status,
                    publish_date=item.publish_date,
                    effective_date=effective_date,
                    source_site=OPENSTD_SOURCE_SITE,
                    source_scope=item.source_scope,
                    source_url=item.source_url,
                    detail_url=item.detail_url,
                    source_pdf_bucket=stored.bucket,
                    source_pdf_object_key=stored.object_key,
                    source_pdf_hash=stored.checksum,
                    source_pdf_size_bytes=stored.size_bytes,
                    materialize_status="not_started",
                    materialize_error="",
                    index_status="not_indexed",
                    index_error="",
                    fingerprint=stored.checksum,
                    last_synced_at=datetime.now(timezone.utc),
                )
                session.merge(standard)
                item.standard_id = standard_id
                item.source_pdf_bucket = stored.bucket
                item.source_pdf_object_key = stored.object_key
                item.status = "registered"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()
                library_standard_id = upsert_national_standard_from_raw(
                    raw_from_openstd_item(item),
                    detail=payload.get("detail") or {},
                    legacy_standard_id=standard_id,
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    checksum=stored.checksum,
                    size_bytes=stored.size_bytes,
                    fingerprint=stored.checksum,
                    file_access_type="downloadable",
                )
                mirror_standard_library_item(
                    job,
                    raw_from_openstd_item(item),
                    job_type="historical_collect",
                    legacy_standard_id=standard_id,
                    standard_id=library_standard_id,
                    metadata_action="new",
                    file_decision="download",
                    file_result="success",
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    checksum=stored.checksum,
                    size_bytes=stored.size_bytes,
                    retry_count=item.retry_count,
                )
                standard_library_materialize_service.enqueue_materialize_job(library_standard_id)
                self._refresh_counts(session, job)
        except Exception as exc:
            item.status = "failed"
            item.error_message = str(exc)
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            mirror_standard_library_item(
                job,
                raw_from_openstd_item(item),
                job_type="historical_collect",
                legacy_standard_id=item.standard_id or "",
                metadata_action="new",
                file_decision="download",
                file_result="failed",
                retry_count=item.retry_count,
                error_message=str(exc),
            )
            self._refresh_counts(session, job)

    def _is_duplicate_standard(self, session: Session, standard_code: str) -> bool:
        code = standard_code.strip()
        if not code:
            return False
        standard_id = openstd_standard_id(code)
        return bool(
            session.scalars(
                select(Standard).where((Standard.code == code) | (Standard.id == standard_id))
            ).first()
        )

    def _refresh_counts(self, session: Session, job: StandardSyncJob) -> None:
        rows = session.execute(
            select(StandardSyncItem.status, func.count())
            .where(StandardSyncItem.job_id == job.id)
            .group_by(StandardSyncItem.status)
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        item_total = sum(counts.values())
        job.total_discovered = max(job.total_discovered, job.scanned_count, item_total)
        job.scanned_count = max(job.scanned_count, job.total_discovered)
        job.total_downloadable = (
            counts.get("pending_download", 0)
            + counts.get("downloading", 0)
            + counts.get("registered", 0)
            + counts.get("failed", 0)
        )
        job.uploaded_count = counts.get("registered", 0)
        job.skipped_duplicate_count = max(job.skipped_duplicate_count, counts.get("skipped_duplicate", 0))
        job.skipped_unavailable_count = max(job.skipped_unavailable_count, counts.get("skipped_unavailable", 0))
        job.failed_count = max(job.failed_count, counts.get("failed", 0))
        job.new_count = counts.get("registered", 0)
        job.download_failed_count = counts.get("failed", 0)
        job.skipped_count = max(job.skipped_count, counts.get("skipped_duplicate", 0) + counts.get("skipped_unavailable", 0))
        job.updated_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()

    def _standard_status_counts(self, session: Session, job_id: str) -> dict[str, int]:
        rows = session.execute(
            select(StandardSyncItem.standard_status, func.count())
            .where(StandardSyncItem.job_id == job_id)
            .group_by(StandardSyncItem.standard_status)
        ).all()
        counts = {"current": 0, "upcoming": 0, "scrapped": 0, "other": 0}
        for status, count in rows:
            label = str(status or "").strip()
            value = int(count)
            if "废止" in label:
                counts["scrapped"] += value
            elif label == "现行":
                counts["current"] += value
            elif label == "即将实施":
                counts["upcoming"] += value
            else:
                counts["other"] += value
        return counts

    def job_to_dict(self, session: Session, job: StandardSyncJob) -> dict[str, Any]:
        self._refresh_counts(session, job)
        standard_status_counts = self._standard_status_counts(session, job.id)
        return {
            "id": job.id,
            "source_site": job.source_site,
            "source_url": job.source_url,
            "crawl_scope": job.crawl_scope,
            "status": job.status,
            "stage": job.stage,
            "total_pages": job.total_pages,
            "current_page": job.current_page,
            "scanned_count": job.scanned_count,
            "new_count": job.new_count,
            "updated_count": job.updated_count,
            "unchanged_count": job.unchanged_count,
            "download_failed_count": job.download_failed_count,
            "upload_failed_count": job.upload_failed_count,
            "materialize_failed_count": job.materialize_failed_count,
            "index_failed_count": job.index_failed_count,
            "skipped_count": job.skipped_count,
            "total_discovered": job.total_discovered,
            "total_downloadable": job.total_downloadable,
            "uploaded_count": job.uploaded_count,
            "skipped_duplicate_count": job.skipped_duplicate_count,
            "skipped_unavailable_count": job.skipped_unavailable_count,
            "failed_count": job.failed_count,
            "standard_status_counts": standard_status_counts,
            "current_item": job.current_item,
            "error_message": job.error_message,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def item_to_dict(self, item: StandardSyncItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "job_id": item.job_id,
            "standard_id": item.standard_id,
            "action": item.action,
            "standard_code": item.standard_code,
            "standard_name": item.standard_name,
            "standard_status": item.standard_status,
            "source_status_raw": item.source_status_raw,
            "publish_date": item.publish_date,
            "effective_date": item.effective_date,
            "source_type": item.source_type,
            "source_site": item.source_site,
            "source_scope": item.source_scope,
            "source_label": item.source_label,
            "source_url": item.source_url,
            "detail_url": item.detail_url,
            "download_url": item.download_url,
            "download_method": item.download_method,
            "status": item.status,
            "skip_reason": item.skip_reason,
            "error_message": item.error_message,
            "retry_count": item.retry_count,
            "source_pdf_bucket": item.source_pdf_bucket,
            "source_pdf_object_key": item.source_pdf_object_key,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }


def raw_from_openstd_item(item: StandardSyncItem) -> dict[str, Any]:
    return {
        "standard_code": item.standard_code,
        "standard_name": item.standard_name,
        "standard_status": item.standard_status or item.source_status_raw,
        "source_status_raw": item.source_status_raw,
        "publish_date": item.publish_date,
        "effective_date": item.effective_date,
        "source_scope": item.source_scope,
        "source_label": item.source_label,
        "source_url": item.source_url,
        "detail_url": item.detail_url,
        "download_url": item.download_url,
        "online_url": "",
    }


openstd_crawl_service = OpenStdCrawlService()
