from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.db.standard_library import StandardLibrarySessionLocal
from app.models.standard_library import (
    StandardLibraryStandard,
    StandardProcessingJob,
)
from app.services.standards import (
    MARKDOWN_FILENAMES,
    MARKDOWN_KINDS,
    MaterializeLengthError,
    build_four_markdowns,
)
from app.services.storage import StorageService, storage_service


def standard_library_markdown_object_key(standard_id: uuid.UUID | str, kind: str) -> str:
    return f"markdown/{standard_id}/{MARKDOWN_FILENAMES[kind]}"


class StandardLibraryMaterializeService:
    def __init__(self, *, storage: StorageService = storage_service) -> None:
        self.storage = storage

    def enqueue_materialize_job(
        self,
        standard_id: uuid.UUID | str,
        *,
        source_sync_job_id: uuid.UUID | str | None = None,
        source_sync_item_id: uuid.UUID | str | None = None,
        priority: int = 100,
    ) -> dict[str, Any]:
        with StandardLibrarySessionLocal() as session:
            job = self.create_materialize_job(
                session,
                standard_id,
                source_sync_job_id=source_sync_job_id,
                source_sync_item_id=source_sync_item_id,
                priority=priority,
            )
            session.commit()
            return self.processing_job_to_dict(job)

    def create_materialize_job(
        self,
        session: Session,
        standard_id: uuid.UUID | str,
        *,
        source_sync_job_id: uuid.UUID | str | None = None,
        source_sync_item_id: uuid.UUID | str | None = None,
        priority: int = 100,
    ) -> StandardProcessingJob:
        parsed_standard_id = parse_uuid(standard_id)
        standard = session.get(StandardLibraryStandard, parsed_standard_id)
        if standard is None:
            raise ValueError(f"standard not found: {standard_id}")
        if not standard.source_pdf_object_key:
            raise ValueError(f"standard has no source PDF object key: {standard_id}")
        if standard.file_access_type != "downloadable":
            standard.materialize_status = "skipped"
            standard.materialize_error = "standard is not downloadable"
            standard.updated_at = utcnow()
            session.add(standard)
            raise ValueError(f"standard is not downloadable: {standard_id}")

        running = session.scalars(
            select(StandardProcessingJob)
            .where(
                StandardProcessingJob.job_type == "materialize",
                StandardProcessingJob.standard_id == parsed_standard_id,
                StandardProcessingJob.status.in_(("pending", "running")),
            )
            .order_by(StandardProcessingJob.created_at.desc())
            .limit(1)
        ).first()
        if running is not None:
            return running

        job = StandardProcessingJob(
            id=uuid.uuid4(),
            job_type="materialize",
            standard_id=parsed_standard_id,
            source_sync_job_id=parse_optional_uuid(source_sync_job_id),
            source_sync_item_id=parse_optional_uuid(source_sync_item_id),
            status="pending",
            stage="queued",
            progress_percent=0,
            priority=max(0, int(priority)),
            retry_count=0,
            max_retries=3,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        standard.materialize_status = "pending"
        standard.materialize_error = None
        standard.updated_at = utcnow()
        session.add(job)
        session.add(standard)
        return job

    def claim_next_job(self, session: Session) -> StandardProcessingJob | None:
        job = session.scalars(
            select(StandardProcessingJob)
            .where(
                StandardProcessingJob.job_type == "materialize",
                StandardProcessingJob.status == "pending",
            )
            .order_by(StandardProcessingJob.priority.asc(), StandardProcessingJob.created_at.asc())
            .limit(1)
        ).first()
        if job is None:
            return None
        now = utcnow()
        job.status = "running"
        job.stage = "starting"
        job.progress_percent = 1
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.updated_at = now
        standard = session.get(StandardLibraryStandard, job.standard_id)
        if standard is not None:
            standard.materialize_status = "materializing"
            standard.materialize_error = None
            standard.updated_at = now
            session.add(standard)
        session.add(job)
        session.commit()
        return job

    def run_pending_once(self, *, timeout_seconds: float | None = None) -> dict[str, Any] | None:
        with StandardLibrarySessionLocal() as session:
            job = self.claim_next_job(session)
            if job is None:
                return None
            return self.run_job(session, job.id, timeout_seconds=timeout_seconds)

    def run_job(
        self,
        session: Session,
        job_id: uuid.UUID | str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        job = session.get(StandardProcessingJob, parse_uuid(job_id))
        if job is None:
            raise ValueError(f"materialize job not found: {job_id}")
        return self.materialize(session, job.standard_id, job_id=job.id, timeout_seconds=timeout_seconds)

    def materialize(
        self,
        session: Session,
        standard_id: uuid.UUID | str,
        *,
        job_id: uuid.UUID | str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        parsed_standard_id = parse_uuid(standard_id)
        standard = session.get(StandardLibraryStandard, parsed_standard_id)
        if standard is None:
            raise ValueError(f"standard not found: {standard_id}")
        if not standard.source_pdf_object_key:
            raise ValueError(f"standard has no source PDF object key: {standard_id}")

        self._set_progress(
            session,
            standard,
            job_id,
            status="running",
            materialize_status="materializing",
            stage="starting",
            progress_percent=1,
        )
        try:
            workdir = Path(settings.standard_workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"standard_library_{standard.id}_", dir=str(workdir)) as tmp:
                pdf_path = Path(tmp) / Path(standard.source_pdf_object_key).name
                self._set_progress(
                    session,
                    standard,
                    job_id,
                    status="running",
                    materialize_status="materializing",
                    stage="downloading_pdf",
                    progress_percent=5,
                )
                self.storage.download_file(
                    bucket=standard.source_pdf_bucket or settings.standard_library_object_store_bucket,
                    object_key=standard.source_pdf_object_key,
                    path=pdf_path,
                )
                self._set_progress(
                    session,
                    standard,
                    job_id,
                    status="running",
                    materialize_status="materializing",
                    stage="generating_markdown",
                    progress_percent=10,
                )
                materialized = build_four_markdowns(
                    standard_name=standard.name,
                    source_pdf=Path(standard.source_pdf_object_key).name,
                    pdf_path=pdf_path,
                    progress=self._progress_callback(session, standard, job_id),
                    timeout_seconds=timeout_seconds,
                )

            if set(materialized.markdown) != MARKDOWN_KINDS:
                missing = MARKDOWN_KINDS - set(materialized.markdown)
                extra = set(materialized.markdown) - MARKDOWN_KINDS
                raise ValueError(f"markdown result is incomplete; missing={sorted(missing)} extra={sorted(extra)}")

            self._set_progress(
                session,
                standard,
                job_id,
                status="running",
                materialize_status="materializing",
                stage="uploading_artifacts",
                progress_percent=92,
            )
            artifacts: dict[str, str] = {}
            for kind, markdown in materialized.markdown.items():
                object_key = standard_library_markdown_object_key(standard.id, kind)
                stored = self.storage.upload_bytes(
                    object_key=object_key,
                    content=markdown.encode("utf-8"),
                    media_type="text/markdown; charset=utf-8",
                    bucket=settings.standard_library_object_store_bucket,
                )
                artifacts[kind] = stored.object_key
                setattr(standard, f"{kind}_md_object_key", stored.object_key)

            now = utcnow()
            standard.materialize_status = "materialized"
            standard.materialize_error = None
            standard.materialized_at = now
            standard.index_status = "pending"
            standard.index_error = None
            standard.indexed_at = None
            standard.updated_at = now
            session.add(standard)
            self._create_index_job(session, standard, materialize_job_id=job_id)
            self._finish_job(
                session,
                job_id,
                status="completed",
                stage="completed",
                progress_percent=100,
                error_message=None,
            )
            session.commit()
            return {
                "standard_id": str(standard.id),
                "status": standard.materialize_status,
                "artifacts": artifacts,
            }
        except Exception as exc:
            self._fail_materialize(session, standard, job_id, exc)
            raise

    def _create_index_job(
        self,
        session: Session,
        standard: StandardLibraryStandard,
        *,
        materialize_job_id: uuid.UUID | str | None,
    ) -> None:
        from app.services.standard_library_index import standard_library_index_service

        source_sync_job_id = None
        source_sync_item_id = None
        if materialize_job_id:
            job = session.get(StandardProcessingJob, parse_uuid(materialize_job_id))
            if job is not None:
                source_sync_job_id = job.source_sync_job_id
                source_sync_item_id = job.source_sync_item_id
        standard_library_index_service.create_index_job(
            session,
            standard.id,
            source_sync_job_id=source_sync_job_id,
            source_sync_item_id=source_sync_item_id,
            priority=100,
        )

    def _progress_callback(
        self,
        session: Session,
        standard: StandardLibraryStandard,
        job_id: uuid.UUID | str | None,
    ):
        def update(stage: str, progress_percent: int, message: str) -> None:
            self._set_progress(
                session,
                standard,
                job_id,
                status="running",
                materialize_status="materializing",
                stage=stage,
                progress_percent=progress_percent,
            )

        return update

    def _set_progress(
        self,
        session: Session,
        standard: StandardLibraryStandard,
        job_id: uuid.UUID | str | None,
        *,
        status: str,
        materialize_status: str,
        stage: str,
        progress_percent: int,
    ) -> None:
        now = utcnow()
        standard.materialize_status = materialize_status
        standard.materialize_error = None
        standard.updated_at = now
        session.add(standard)
        job = session.get(StandardProcessingJob, parse_optional_uuid(job_id)) if job_id else None
        if job is not None:
            job.status = status
            job.stage = stage
            job.progress_percent = max(0, min(100, int(progress_percent)))
            job.heartbeat_at = now
            job.started_at = job.started_at or now
            job.updated_at = now
            session.add(job)
        session.commit()

    def _finish_job(
        self,
        session: Session,
        job_id: uuid.UUID | str | None,
        *,
        status: str,
        stage: str,
        progress_percent: int,
        error_message: str | None,
    ) -> None:
        if not job_id:
            return
        job = session.get(StandardProcessingJob, parse_uuid(job_id))
        if job is None:
            return
        now = utcnow()
        job.status = status
        job.stage = stage
        job.progress_percent = max(0, min(100, int(progress_percent)))
        job.error_message = error_message
        job.finished_at = now
        job.heartbeat_at = now
        job.updated_at = now
        if job.started_at:
            job.duration_ms = max(0, int((job.finished_at - job.started_at).total_seconds() * 1000))
        session.add(job)

    def _fail_materialize(
        self,
        session: Session,
        standard: StandardLibraryStandard,
        job_id: uuid.UUID | str | None,
        exc: Exception,
    ) -> None:
        now = utcnow()
        message = str(exc)
        if isinstance(exc, MaterializeLengthError):
            message = f"length limit: {message}"
        standard.materialize_status = "failed"
        standard.materialize_error = message
        standard.updated_at = now
        session.add(standard)
        self._finish_job(
            session,
            job_id,
            status="failed",
            stage="failed",
            progress_percent=100,
            error_message=message,
        )
        session.commit()

    def processing_job_to_dict(self, job: StandardProcessingJob) -> dict[str, Any]:
        return {
            "job_id": str(job.id),
            "job_type": job.job_type,
            "standard_id": str(job.standard_id) if job.standard_id else None,
            "source_sync_job_id": str(job.source_sync_job_id) if job.source_sync_job_id else None,
            "source_sync_item_id": str(job.source_sync_item_id) if job.source_sync_item_id else None,
            "status": job.status,
            "stage": job.stage,
            "progress_percent": float(job.progress_percent or 0),
            "priority": job.priority,
            "retry_count": job.retry_count,
            "max_retries": job.max_retries,
            "error_message": job.error_message,
            "created_at": format_dt(job.created_at),
            "updated_at": format_dt(job.updated_at),
            "started_at": format_dt(job.started_at),
            "finished_at": format_dt(job.finished_at),
            "heartbeat_at": format_dt(job.heartbeat_at),
        }


def parse_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def parse_optional_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    return parse_uuid(value)


def format_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


standard_library_materialize_service = StandardLibraryMaterializeService()


INTERRUPTED_MATERIALIZE_MESSAGE = "service interrupted while standard library materialize job was running"


def fail_interrupted_standard_library_materialize_jobs(session: Session) -> dict[str, int]:
    now = utcnow()
    jobs = session.scalars(
        select(StandardProcessingJob).where(
            StandardProcessingJob.job_type == "materialize",
            StandardProcessingJob.status == "running",
        )
    ).all()
    for job in jobs:
        job.status = "failed"
        job.stage = "failed"
        job.progress_percent = 100
        job.error_message = INTERRUPTED_MATERIALIZE_MESSAGE
        job.finished_at = now
        job.updated_at = now
        if job.started_at:
            job.duration_ms = max(0, int((job.finished_at - job.started_at).total_seconds() * 1000))
        session.add(job)

    standards = session.scalars(
        select(StandardLibraryStandard).where(StandardLibraryStandard.materialize_status == "materializing")
    ).all()
    for standard in standards:
        standard.materialize_status = "failed"
        standard.materialize_error = INTERRUPTED_MATERIALIZE_MESSAGE
        standard.updated_at = now
        session.add(standard)

    session.commit()
    return {
        "standard_library_processing_jobs": len(jobs),
        "standard_library_standards": len(standards),
    }
