from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.db.standard_library import StandardLibrarySessionLocal
from app.models.standard_library import StandardLibraryStandard, StandardProcessingJob
from app.services.standards import (
    STANDARD_SEARCH_INDEX_KIND,
    StandardEmbeddingClient,
    build_standard_search_content,
    content_hash,
    vector_literal,
)
from app.services.storage import StorageService, storage_service


class EmbeddingClient(Protocol):
    def embed(self, text_value: str) -> list[float]:
        ...


class StandardLibraryIndexService:
    def __init__(
        self,
        *,
        storage: StorageService = storage_service,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.storage = storage
        self.embedding_client = embedding_client

    def enqueue_index_job(
        self,
        standard_id: uuid.UUID | str,
        *,
        source_sync_job_id: uuid.UUID | str | None = None,
        source_sync_item_id: uuid.UUID | str | None = None,
        priority: int = 100,
    ) -> dict[str, Any]:
        with StandardLibrarySessionLocal() as session:
            job = self.create_index_job(
                session,
                standard_id,
                source_sync_job_id=source_sync_job_id,
                source_sync_item_id=source_sync_item_id,
                priority=priority,
            )
            session.commit()
            return self.processing_job_to_dict(job)

    def create_index_job(
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
        if standard.materialize_status != "materialized":
            raise ValueError(f"standard is not materialized: {standard_id}")
        if not standard.overview_md_object_key:
            raise ValueError(f"standard has no overview markdown object key: {standard_id}")

        running = session.scalars(
            select(StandardProcessingJob)
            .where(
                StandardProcessingJob.job_type == "index",
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
            job_type="index",
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
        standard.index_status = "pending"
        standard.index_error = None
        standard.updated_at = utcnow()
        session.add(job)
        session.add(standard)
        return job

    def claim_next_job(self, session: Session) -> StandardProcessingJob | None:
        job = session.scalars(
            select(StandardProcessingJob)
            .where(
                StandardProcessingJob.job_type == "index",
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
            standard.index_status = "indexing"
            standard.index_error = None
            standard.updated_at = now
            session.add(standard)
        session.add(job)
        session.commit()
        return job

    def run_pending_once(self) -> dict[str, Any] | None:
        with StandardLibrarySessionLocal() as session:
            job = self.claim_next_job(session)
            if job is None:
                return None
            return self.run_job(session, job.id)

    def run_job(self, session: Session, job_id: uuid.UUID | str) -> dict[str, Any]:
        job = session.get(StandardProcessingJob, parse_uuid(job_id))
        if job is None:
            raise ValueError(f"index job not found: {job_id}")
        if job.standard_id is None:
            raise ValueError(f"index job has no standard_id: {job_id}")
        return self.index_standard(session, job.standard_id, job_id=job.id)

    def index_standard(
        self,
        session: Session,
        standard_id: uuid.UUID | str,
        *,
        job_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        parsed_standard_id = parse_uuid(standard_id)
        standard = session.get(StandardLibraryStandard, parsed_standard_id)
        if standard is None:
            raise ValueError(f"standard not found: {standard_id}")
        if standard.materialize_status != "materialized":
            raise ValueError(f"standard is not materialized: {standard_id}")
        if not standard.overview_md_object_key:
            raise ValueError(f"standard has no overview markdown object key: {standard_id}")

        self._set_progress(
            session,
            standard,
            job_id,
            status="running",
            index_status="indexing",
            stage="reading_overview",
            progress_percent=5,
        )
        try:
            overview = self.storage.get_bytes(
                bucket=settings.standard_library_object_store_bucket,
                object_key=standard.overview_md_object_key,
            ).decode("utf-8")
            search_content = build_standard_search_content(standard=standard, overview_markdown=overview)
            if not search_content.strip():
                raise ValueError("standard_overview.md produced empty search content")

            self._set_progress(
                session,
                standard,
                job_id,
                status="running",
                index_status="indexing",
                stage="embedding_overview",
                progress_percent=35,
            )
            embedding = self._embedding_client().embed(search_content)
            embedding_literal = vector_literal(embedding)
            search_hash = content_hash(search_content)

            self._set_progress(
                session,
                standard,
                job_id,
                status="running",
                index_status="indexing",
                stage="writing_index",
                progress_percent=80,
            )
            session.execute(
                text(
                    """
                    DELETE FROM standard_indexes
                    WHERE standard_id = :standard_id AND index_kind = :index_kind
                    """
                ),
                {"standard_id": str(parsed_standard_id), "index_kind": STANDARD_SEARCH_INDEX_KIND},
            )
            index_id = uuid.uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO standard_indexes (
                        id,
                        standard_id,
                        index_kind,
                        content,
                        content_hash,
                        embedding,
                        embedding_model,
                        embedding_dimensions,
                        schema_version,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        CAST(:id AS uuid),
                        CAST(:standard_id AS uuid),
                        :index_kind,
                        :content,
                        :content_hash,
                        CAST(:embedding AS vector),
                        :embedding_model,
                        :embedding_dimensions,
                        :schema_version,
                        now(),
                        now()
                    )
                    """
                ),
                {
                    "id": str(index_id),
                    "standard_id": str(parsed_standard_id),
                    "index_kind": STANDARD_SEARCH_INDEX_KIND,
                    "content": search_content,
                    "content_hash": search_hash,
                    "embedding": embedding_literal,
                    "embedding_model": settings.standard_embedding_model,
                    "embedding_dimensions": settings.standard_embedding_dimensions,
                    "schema_version": "overview-1.0",
                },
            )
            now = utcnow()
            standard.index_status = "indexed"
            standard.index_error = None
            standard.indexed_at = now
            standard.updated_at = now
            session.add(standard)
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
                "status": standard.index_status,
                "index_id": str(index_id),
                "index_kind": STANDARD_SEARCH_INDEX_KIND,
                "embedding_model": settings.standard_embedding_model,
                "embedding_dimensions": settings.standard_embedding_dimensions,
                "content_hash": search_hash,
                "content_chars": len(search_content),
            }
        except Exception as exc:
            self._fail_index(session, standard, job_id, exc)
            raise

    def _embedding_client(self) -> EmbeddingClient:
        return self.embedding_client or StandardEmbeddingClient.from_settings()

    def _set_progress(
        self,
        session: Session,
        standard: StandardLibraryStandard,
        job_id: uuid.UUID | str | None,
        *,
        status: str,
        index_status: str,
        stage: str,
        progress_percent: int,
    ) -> None:
        now = utcnow()
        standard.index_status = index_status
        standard.index_error = None
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

    def _fail_index(
        self,
        session: Session,
        standard: StandardLibraryStandard,
        job_id: uuid.UUID | str | None,
        exc: Exception,
    ) -> None:
        now = utcnow()
        message = str(exc)
        standard.index_status = "failed"
        standard.index_error = message
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


standard_library_index_service = StandardLibraryIndexService()


INTERRUPTED_INDEX_MESSAGE = "service interrupted while standard library index job was running"


def fail_interrupted_standard_library_index_jobs(session: Session) -> dict[str, int]:
    now = utcnow()
    jobs = session.scalars(
        select(StandardProcessingJob).where(
            StandardProcessingJob.job_type == "index",
            StandardProcessingJob.status == "running",
        )
    ).all()
    for job in jobs:
        job.status = "failed"
        job.stage = "failed"
        job.progress_percent = 100
        job.error_message = INTERRUPTED_INDEX_MESSAGE
        job.finished_at = now
        job.updated_at = now
        if job.started_at:
            job.duration_ms = max(0, int((job.finished_at - job.started_at).total_seconds() * 1000))
        session.add(job)

    standards = session.scalars(
        select(StandardLibraryStandard).where(StandardLibraryStandard.index_status == "indexing")
    ).all()
    for standard in standards:
        standard.index_status = "failed"
        standard.index_error = INTERRUPTED_INDEX_MESSAGE
        standard.updated_at = now
        session.add(standard)

    session.commit()
    return {
        "standard_library_index_jobs": len(jobs),
        "standard_library_index_standards": len(standards),
    }
