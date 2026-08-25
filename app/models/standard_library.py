from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.standard_library import StandardLibraryBase, Vector
from app.core.standard_config import standard_settings as settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_pk() -> Any:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def created_at_column() -> Any:
    return mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))


def updated_at_column() -> Any:
    return mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=text("now()"),
    )


class StandardLibraryStandard(StandardLibraryBase):
    __tablename__ = "standards"
    __table_args__ = (
        UniqueConstraint("source", "code_normalized", "category", name="uq_standards_source_code_category"),
        CheckConstraint("source in ('national', 'industry', 'local')", name="ck_standards_source"),
        CheckConstraint(
            "official_status in ('upcoming', 'current', 'updated_available', 'abolished')",
            name="ck_standards_official_status",
        ),
        CheckConstraint(
            "file_access_type in ('downloadable', 'online_only', 'unavailable')",
            name="ck_standards_file_access_type",
        ),
        CheckConstraint(
            "materialize_status in ('pending', 'materializing', 'materialized', 'failed', 'skipped')",
            name="ck_standards_materialize_status",
        ),
        CheckConstraint(
            "index_status in ('pending', 'indexing', 'indexed', 'failed', 'skipped')",
            name="ck_standards_index_status",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(Text)
    code_normalized: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_label: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    category_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    standard_org: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_status: Mapped[str] = mapped_column(Text)
    official_status_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    abolish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_site: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    online_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_access_type: Mapped[str] = mapped_column(Text, default="unavailable", server_default=text("'unavailable'"))
    source_pdf_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_md_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    structure_md_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    logic_md_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    overview_md_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    materialize_status: Mapped[str] = mapped_column(Text, default="pending", server_default=text("'pending'"))
    materialize_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_status: Mapped[str] = mapped_column(Text, default="pending", server_default=text("'pending'"))
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardSource(StandardLibraryBase):
    __tablename__ = "standard_sources"
    __table_args__ = (
        UniqueConstraint("source", name="uq_standard_sources_source"),
        CheckConstraint("source in ('national', 'industry', 'local')", name="ck_standard_sources_source"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source: Mapped[str] = mapped_column(Text)
    source_label: Mapped[str] = mapped_column(Text)
    entry_url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    historical_collect_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    scheduled_update_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    schedule_cron: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_historical_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_sync_jobs.id"),
        nullable=True,
    )
    last_update_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_sync_jobs.id"),
        nullable=True,
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_watermark: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardSyncJob(StandardLibraryBase):
    __tablename__ = "standard_sync_jobs"
    __table_args__ = (
        CheckConstraint("job_type in ('historical_collect', 'scheduled_update')", name="ck_sync_jobs_job_type"),
        CheckConstraint("source in ('national', 'industry', 'local')", name="ck_sync_jobs_source"),
        CheckConstraint("trigger_type in ('schedule', 'system', 'admin')", name="ck_sync_jobs_trigger_type"),
        CheckConstraint(
            "status in ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_sync_jobs_status",
        ),
        CheckConstraint("progress_percent >= 0 and progress_percent <= 100", name="ck_sync_jobs_progress"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(Text, default="system", server_default=text("'system'"))
    status: Mapped[str] = mapped_column(Text, default="pending", server_default=text("'pending'"))
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default=text("0"))
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scanned_pages: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    discovered_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    processed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    need_download_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    downloaded_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    download_failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    new_active_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    expired_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardSyncItem(StandardLibraryBase):
    __tablename__ = "standard_sync_items"
    __table_args__ = (
        UniqueConstraint("job_id", "source", "external_id", name="uq_sync_items_job_source_external_id"),
        CheckConstraint("source in ('national', 'industry', 'local')", name="ck_sync_items_source"),
        CheckConstraint(
            "metadata_action is null or metadata_action in ('new', 'changed', 'unchanged')",
            name="ck_sync_items_metadata_action",
        ),
        CheckConstraint(
            "file_decision is null or file_decision in ('download', 'redownload', 'no_download', 'online_only', 'unavailable', 'skip')",
            name="ck_sync_items_file_decision",
        ),
        CheckConstraint(
            "file_result is null or file_result in ('success', 'failed', 'skipped')",
            name="ck_sync_items_file_result",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_sync_jobs.id", ondelete="CASCADE"),
    )
    standard_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standards.id"),
        nullable=True,
    )
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_status_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_status_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_change_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_pdf_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    online_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardProcessingJob(StandardLibraryBase):
    __tablename__ = "standard_processing_jobs"
    __table_args__ = (
        CheckConstraint("job_type in ('materialize', 'index', 'atlas_projection')", name="ck_processing_jobs_job_type"),
        CheckConstraint(
            "status in ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_processing_jobs_status",
        ),
        CheckConstraint("progress_percent >= 0 and progress_percent <= 100", name="ck_processing_jobs_progress"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_type: Mapped[str] = mapped_column(Text)
    standard_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standards.id"),
        nullable=True,
    )
    projection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_atlas_projections.id"),
        nullable=True,
    )
    source_sync_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_sync_jobs.id"),
        nullable=True,
    )
    source_sync_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_sync_items.id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, default="pending", server_default=text("'pending'"))
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, server_default=text("0"))
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_retries: Mapped[int] = mapped_column(Integer, default=3, server_default=text("3"))
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardIndex(StandardLibraryBase):
    __tablename__ = "standard_indexes"
    __table_args__ = (
        UniqueConstraint(
            "standard_id",
            "index_kind",
            "embedding_model",
            "embedding_dimensions",
            name="uq_standard_indexes_standard_model",
        ),
        CheckConstraint("index_kind = 'overview'", name="ck_standard_indexes_kind"),
        CheckConstraint("embedding_dimensions > 0", name="ck_standard_indexes_dimensions"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    standard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standards.id", ondelete="CASCADE"),
    )
    index_kind: Mapped[str] = mapped_column(Text, default="overview", server_default=text("'overview'"))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    embedding: Mapped[str] = mapped_column(Vector(settings.standard_embedding_dimensions))
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_dimensions: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardSearchQuery(StandardLibraryBase):
    __tablename__ = "standard_search_queries"
    __table_args__ = (
        CheckConstraint('"limit" > 0', name="ck_search_queries_limit"),
        CheckConstraint("search_mode = 'semantic'", name="ck_search_queries_mode"),
        CheckConstraint("status in ('success', 'failed')", name="ck_search_queries_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    query_text: Mapped[str] = mapped_column(Text)
    searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    last_reused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sort_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text("now()"))
    caller: Mapped[str] = mapped_column(Text, default="frontend", server_default=text("'frontend'"))
    limit: Mapped[int] = mapped_column(Integer, default=20, server_default=text("20"))
    search_mode: Mapped[str] = mapped_column(Text, default="semantic", server_default=text("'semantic'"))
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardSearchResult(StandardLibraryBase):
    __tablename__ = "standard_search_results"
    __table_args__ = (
        UniqueConstraint("query_id", "rank", name="uq_standard_search_results_query_rank"),
        CheckConstraint("rank > 0", name="ck_search_results_rank"),
        CheckConstraint("score >= 0", name="ck_search_results_score"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_search_queries.id", ondelete="CASCADE"),
    )
    standard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("standards.id"))
    index_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("standard_indexes.id"), nullable=True)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    match_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_code: Mapped[str] = mapped_column(Text)
    snapshot_name: Mapped[str] = mapped_column(Text)
    snapshot_source: Mapped[str] = mapped_column(Text)
    snapshot_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    snapshot_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    snapshot_detail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class StandardAtlasProjection(StandardLibraryBase):
    __tablename__ = "standard_atlas_projections"
    __table_args__ = (
        UniqueConstraint("version", name="uq_atlas_projections_version"),
        CheckConstraint("algorithm in ('umap', 'tsne', 'pca')", name="ck_atlas_projections_algorithm"),
        CheckConstraint("distance_metric in ('cosine', 'l2')", name="ck_atlas_projections_distance_metric"),
        CheckConstraint(
            "status in ('pending', 'running', 'completed', 'failed')",
            name="ck_atlas_projections_status",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[str] = mapped_column(Text)
    algorithm: Mapped[str] = mapped_column(Text, default="umap", server_default=text("'umap'"))
    distance_metric: Mapped[str] = mapped_column(Text, default="cosine", server_default=text("'cosine'"))
    color_by: Mapped[str] = mapped_column(Text, default="source", server_default=text("'source'"))
    embedding_model: Mapped[str] = mapped_column(Text)
    embedding_dimensions: Mapped[int] = mapped_column(Integer)
    effective_standard_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    projected_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    missing_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    input_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default=text("'pending'"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class StandardAtlasPoint(StandardLibraryBase):
    __tablename__ = "standard_atlas_points"
    __table_args__ = (
        UniqueConstraint("projection_id", "standard_id", name="uq_atlas_points_projection_standard"),
        CheckConstraint("x = x and y = y", name="ck_atlas_points_coordinates_not_nan"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    projection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standard_atlas_projections.id", ondelete="CASCADE"),
    )
    standard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("standards.id", ondelete="CASCADE"),
    )
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    color_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
