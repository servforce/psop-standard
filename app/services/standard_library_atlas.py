from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.db.standard_library import StandardLibrarySessionLocal
from app.models.standard_library import (
    StandardAtlasPoint,
    StandardAtlasProjection,
    StandardLibraryStandard,
    StandardProcessingJob,
)


ATLAS_ALGORITHM = "pca"
ATLAS_DISTANCE_METRIC = "cosine"


class StandardLibraryAtlasService:
    def enqueue_atlas_job(self, *, priority: int = 500) -> dict[str, Any]:
        with StandardLibrarySessionLocal() as session:
            job = self.create_atlas_job(session, priority=priority)
            session.commit()
            return self.processing_job_to_dict(job)

    def create_atlas_job(self, session: Session, *, priority: int = 500) -> StandardProcessingJob:
        running = session.scalars(
            select(StandardProcessingJob)
            .where(
                StandardProcessingJob.job_type == "atlas_projection",
                StandardProcessingJob.status.in_(("pending", "running")),
            )
            .order_by(StandardProcessingJob.created_at.desc())
            .limit(1)
        ).first()
        if running is not None:
            return running

        now = utcnow()
        job = StandardProcessingJob(
            id=uuid.uuid4(),
            job_type="atlas_projection",
            status="pending",
            stage="queued",
            progress_percent=0,
            priority=max(0, int(priority)),
            retry_count=0,
            max_retries=1,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        return job

    def claim_next_job(self, session: Session) -> StandardProcessingJob | None:
        job = session.scalars(
            select(StandardProcessingJob)
            .where(
                StandardProcessingJob.job_type == "atlas_projection",
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
            raise ValueError(f"atlas projection job not found: {job_id}")
        return self.build_current_projection(session, job_id=job.id)

    def build_current_projection(
        self,
        session: Session,
        *,
        job_id: uuid.UUID | str | None = None,
        color_by: str = "source",
    ) -> dict[str, Any]:
        if color_by not in {"source", "category"}:
            raise ValueError("unsupported color_by")

        self._set_job_progress(session, job_id, stage="reading_indexes", progress_percent=5)
        effective_count = self._effective_standard_count(session)
        rows = self._projectable_rows(session)
        input_hash = build_input_hash(rows)
        version = atlas_version(input_hash)
        projection = StandardAtlasProjection(
            id=uuid.uuid4(),
            version=version,
            algorithm=ATLAS_ALGORITHM,
            distance_metric=ATLAS_DISTANCE_METRIC,
            color_by=color_by,
            embedding_model=settings.standard_embedding_model,
            embedding_dimensions=settings.standard_embedding_dimensions,
            effective_standard_count=effective_count,
            projected_count=0,
            missing_count=max(0, effective_count - len(rows)),
            input_hash=input_hash,
            status="running",
            is_current=False,
            started_at=utcnow(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(projection)
        session.flush()
        self._attach_projection_to_job(session, job_id, projection.id)
        session.commit()

        try:
            self._set_job_progress(session, job_id, stage="projecting", progress_percent=35)
            vectors = [parse_vector(row["embedding"]) for row in rows]
            coordinates = project_embeddings(vectors)

            self._set_job_progress(session, job_id, stage="writing_points", progress_percent=70)
            for row, (x, y) in zip(rows, coordinates, strict=True):
                session.add(
                    StandardAtlasPoint(
                        id=uuid.uuid4(),
                        projection_id=projection.id,
                        standard_id=parse_uuid(row["standard_id"]),
                        x=float(x),
                        y=float(y),
                        color_key=self._color_key(row, color_by=color_by),
                        source=row.get("source") or "",
                        category=row.get("category") or None,
                    )
                )

            now = utcnow()
            session.execute(
                text(
                    """
                    UPDATE standard_atlas_projections
                    SET is_current = false, updated_at = now()
                    WHERE is_current = true
                    """
                )
            )
            projection.projected_count = len(rows)
            projection.missing_count = max(0, effective_count - len(rows))
            projection.status = "completed"
            projection.is_current = True
            projection.completed_at = now
            projection.updated_at = now
            session.add(projection)
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
                "projection_id": str(projection.id),
                "version": projection.version,
                "status": projection.status,
                "algorithm": projection.algorithm,
                "distance_metric": projection.distance_metric,
                "effective_standard_count": effective_count,
                "projected_count": len(rows),
                "missing_count": max(0, effective_count - len(rows)),
                "input_hash": input_hash,
            }
        except Exception as exc:
            self._fail_projection(session, projection, job_id, exc)
            raise

    def _effective_standard_count(self, session: Session) -> int:
        return session.scalar(
            select(func.count())
            .select_from(StandardLibraryStandard)
            .where(
                or_(
                    StandardLibraryStandard.source.in_(("national", "industry"))
                    & (StandardLibraryStandard.official_status == "current"),
                    (StandardLibraryStandard.source == "local")
                    & StandardLibraryStandard.official_status.in_(("current", "updated_available")),
                )
            )
            .where(
                StandardLibraryStandard.materialize_status == "materialized",
                StandardLibraryStandard.index_status == "indexed",
            )
        ) or 0

    def _projectable_rows(self, session: Session) -> list[dict[str, Any]]:
        rows = session.execute(
            text(
                """
                SELECT
                    s.id AS standard_id,
                    s.code AS code,
                    s.name AS name,
                    s.source AS source,
                    s.source_label AS source_label,
                    s.category AS category,
                    s.category_label AS category_label,
                    i.id AS index_id,
                    i.content_hash AS content_hash,
                    i.embedding::text AS embedding
                FROM standard_indexes i
                JOIN standards s ON s.id = i.standard_id
                WHERE (
                    (
                        s.source in ('national', 'industry')
                        AND s.official_status = 'current'
                    )
                    OR (
                        s.source = 'local'
                        AND s.official_status in ('current', 'updated_available')
                    )
                )
                  AND s.materialize_status = 'materialized'
                  AND s.index_status = 'indexed'
                  AND i.index_kind = 'overview'
                  AND i.embedding_model = :embedding_model
                  AND i.embedding_dimensions = :embedding_dimensions
                ORDER BY s.source ASC, s.category ASC, s.code ASC, s.id ASC
                """
            ),
            {
                "embedding_model": settings.standard_embedding_model,
                "embedding_dimensions": settings.standard_embedding_dimensions,
            },
        ).mappings().all()
        return [dict(row) for row in rows]

    def _color_key(self, row: dict[str, Any], *, color_by: str) -> str:
        if color_by == "category":
            return row.get("category_label") or row.get("category") or "uncategorized"
        return row.get("source_label") or row.get("source") or "unknown"

    def _set_job_progress(
        self,
        session: Session,
        job_id: uuid.UUID | str | None,
        *,
        stage: str,
        progress_percent: int,
    ) -> None:
        if not job_id:
            return
        job = session.get(StandardProcessingJob, parse_uuid(job_id))
        if job is None:
            return
        now = utcnow()
        job.status = "running"
        job.stage = stage
        job.progress_percent = max(0, min(100, int(progress_percent)))
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.updated_at = now
        session.add(job)
        session.commit()

    def _attach_projection_to_job(
        self,
        session: Session,
        job_id: uuid.UUID | str | None,
        projection_id: uuid.UUID,
    ) -> None:
        if not job_id:
            return
        job = session.get(StandardProcessingJob, parse_uuid(job_id))
        if job is None:
            return
        job.projection_id = projection_id
        job.updated_at = utcnow()
        session.add(job)

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

    def _fail_projection(
        self,
        session: Session,
        projection: StandardAtlasProjection,
        job_id: uuid.UUID | str | None,
        exc: Exception,
    ) -> None:
        now = utcnow()
        message = str(exc)
        projection.status = "failed"
        projection.error_message = message
        projection.completed_at = now
        projection.updated_at = now
        session.add(projection)
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
            "projection_id": str(job.projection_id) if job.projection_id else None,
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


def parse_vector(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    text_value = str(value).strip()
    if text_value.startswith("[") and text_value.endswith("]"):
        text_value = text_value[1:-1]
    if not text_value:
        return []
    return [float(part.strip()) for part in text_value.split(",") if part.strip()]


def project_embeddings(vectors: list[list[float]]) -> list[tuple[float, float]]:
    count = len(vectors)
    if count == 0:
        return []
    if count == 1:
        return [(0.0, 0.0)]
    if count == 2:
        return [(-1.0, 0.0), (1.0, 0.0)]

    width = min(len(vector) for vector in vectors) if vectors else 0
    if width <= 0:
        return circular_layout(count)
    normalized = [normalize_vector(vector[:width]) for vector in vectors]
    variances = []
    for index in range(width):
        values = [vector[index] for vector in normalized]
        mean = sum(values) / count
        variance = sum((value - mean) ** 2 for value in values) / count
        variances.append((variance, index))
    ordered = sorted(variances, key=lambda item: (-item[0], item[1]))
    if len(ordered) < 2 or ordered[0][0] <= 0:
        return circular_layout(count)

    x_index = ordered[0][1]
    y_index = ordered[1][1] if len(ordered) > 1 and ordered[1][0] > 0 else None
    xs = [vector[x_index] for vector in normalized]
    ys = [vector[y_index] for vector in normalized] if y_index is not None else [0.0] * count
    projected = list(zip(scale_axis(xs), scale_axis(ys), strict=True))
    if all(abs(x) < 1e-12 and abs(y) < 1e-12 for x, y in projected):
        return circular_layout(count)
    return projected


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return [0.0 for _ in vector]
    return [value / norm for value in vector]


def scale_axis(values: list[float]) -> list[float]:
    if not values:
        return []
    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    max_abs = max(abs(value) for value in centered)
    if max_abs <= 0:
        return [0.0 for _ in values]
    return [max(-1.0, min(1.0, value / max_abs)) for value in centered]


def circular_layout(count: int) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    return [
        (
            math.cos((2 * math.pi * index) / count),
            math.sin((2 * math.pi * index) / count),
        )
        for index in range(count)
    ]


def build_input_hash(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(settings.standard_embedding_model.encode("utf-8"))
    digest.update(str(settings.standard_embedding_dimensions).encode("utf-8"))
    for row in rows:
        digest.update(str(row.get("standard_id") or "").encode("utf-8"))
        digest.update(str(row.get("index_id") or "").encode("utf-8"))
        digest.update(str(row.get("content_hash") or "").encode("utf-8"))
        digest.update(str(row.get("embedding") or "").encode("utf-8"))
    return digest.hexdigest()


def atlas_version(input_hash: str) -> str:
    timestamp = utcnow().strftime("%Y%m%d%H%M%S%f")
    return f"atlas-{timestamp}-{input_hash[:12]}"


def parse_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def format_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


standard_library_atlas_service = StandardLibraryAtlasService()


INTERRUPTED_ATLAS_MESSAGE = "service interrupted while standard library atlas projection job was running"


def fail_interrupted_standard_library_atlas_jobs(session: Session) -> dict[str, int]:
    now = utcnow()
    jobs = session.scalars(
        select(StandardProcessingJob).where(
            StandardProcessingJob.job_type == "atlas_projection",
            StandardProcessingJob.status == "running",
        )
    ).all()
    projection_ids = [job.projection_id for job in jobs if job.projection_id]
    for job in jobs:
        job.status = "failed"
        job.stage = "failed"
        job.progress_percent = 100
        job.error_message = INTERRUPTED_ATLAS_MESSAGE
        job.finished_at = now
        job.updated_at = now
        if job.started_at:
            job.duration_ms = max(0, int((job.finished_at - job.started_at).total_seconds() * 1000))
        session.add(job)

    projections = []
    if projection_ids:
        projections = session.scalars(
            select(StandardAtlasProjection).where(
                StandardAtlasProjection.id.in_(projection_ids),
                StandardAtlasProjection.status == "running",
            )
        ).all()
    for projection in projections:
        projection.status = "failed"
        projection.error_message = INTERRUPTED_ATLAS_MESSAGE
        projection.completed_at = now
        projection.updated_at = now
        session.add(projection)

    session.commit()
    return {
        "standard_library_atlas_jobs": len(jobs),
        "standard_library_atlas_projections": len(projections),
    }
