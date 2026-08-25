from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def beijing_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


class Standard(Base):
    __tablename__ = "standards"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), default="")
    code: Mapped[str] = mapped_column(String(128), default="", index=True)
    standard_type: Mapped[str] = mapped_column(String(64), default="national", index=True)
    standard_category: Mapped[str] = mapped_column(String(64), default="")
    standard_org: Mapped[str] = mapped_column(String(64), default="")
    source_status: Mapped[str] = mapped_column(String(64), default="active", index=True)
    source_status_raw: Mapped[str] = mapped_column(String(128), default="")
    publish_date: Mapped[str] = mapped_column(String(64), default="")
    effective_date: Mapped[str] = mapped_column(String(64), default="")
    source_site: Mapped[str] = mapped_column(String(255), default="")
    source_scope: Mapped[str] = mapped_column(String(128), default="")
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    detail_url: Mapped[str] = mapped_column(String(2048), default="")
    external_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    source_pdf_bucket: Mapped[str] = mapped_column(String(255), default="")
    source_pdf_object_key: Mapped[str] = mapped_column(String(1024), default="")
    source_pdf_hash: Mapped[str] = mapped_column(String(64), default="")
    source_pdf_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    overview_md_object_key: Mapped[str] = mapped_column(String(1024), default="")
    structure_md_object_key: Mapped[str] = mapped_column(String(1024), default="")
    logic_md_object_key: Mapped[str] = mapped_column(String(1024), default="")
    body_md_object_key: Mapped[str] = mapped_column(String(1024), default="")
    materialize_status: Mapped[str] = mapped_column(String(64), default="not_started", index=True)
    materialize_error: Mapped[str] = mapped_column(Text, default="")
    materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_status: Mapped[str] = mapped_column(String(64), default="not_indexed", index=True)
    index_error: Mapped[str] = mapped_column(Text, default="")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StandardSyncJob(Base):
    __tablename__ = "standard_sync_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trigger_type: Mapped[str] = mapped_column(String(64), default="scheduled")
    source_scope: Mapped[str] = mapped_column(String(128), default="configured_sources")
    source_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    source_site: Mapped[str] = mapped_column(String(255), default="")
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    crawl_scope: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(128), default="queued")
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    current_page: Mapped[int] = mapped_column(Integer, default=0)
    total_discovered: Mapped[int] = mapped_column(Integer, default=0)
    total_downloadable: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_unavailable_count: Mapped[int] = mapped_column(Integer, default=0)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    download_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    upload_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    materialize_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    index_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    current_item: Mapped[str] = mapped_column(String(512), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StandardSyncItem(Base):
    __tablename__ = "standard_sync_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    standard_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    action: Mapped[str] = mapped_column(String(64), default="new", index=True)
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    source_type: Mapped[str] = mapped_column(String(64), default="national")
    source_site: Mapped[str] = mapped_column(String(255), default="")
    source_scope: Mapped[str] = mapped_column(String(128), default="")
    source_label: Mapped[str] = mapped_column(String(128), default="")
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    external_id: Mapped[str] = mapped_column(String(255), default="")
    standard_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    standard_name: Mapped[str] = mapped_column(String(512), default="")
    standard_status: Mapped[str] = mapped_column(String(64), default="")
    source_status_raw: Mapped[str] = mapped_column(String(128), default="")
    publish_date: Mapped[str] = mapped_column(String(64), default="")
    effective_date: Mapped[str] = mapped_column(String(64), default="")
    detail_url: Mapped[str] = mapped_column(String(2048), default="")
    download_url: Mapped[str] = mapped_column(String(2048), default="")
    download_method: Mapped[str] = mapped_column(String(64), default="")
    old_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    new_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    skip_reason: Mapped[str] = mapped_column(String(255), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    source_pdf_bucket: Mapped[str] = mapped_column(String(255), default="")
    source_pdf_object_key: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StandardProcessingJob(Base):
    __tablename__ = "standard_processing_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    standard_id: Mapped[str] = mapped_column(String(128), index=True)
    sync_job_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    job_type: Mapped[str] = mapped_column(String(64), default="materialize_and_index")
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(128), default="starting")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StandardSearchQuery(Base):
    __tablename__ = "standard_search_queries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_text: Mapped[str] = mapped_column(Text, default="")
    query_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    caller_type: Mapped[str] = mapped_column(String(64), default="web", index=True)
    video_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    limit: Mapped[int] = mapped_column(Integer, default=5)
    mode: Mapped[str] = mapped_column(String(64), default="pgvector_overview")
    embedding_model: Mapped[str] = mapped_column(String(128), default="")
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=0)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(64), default="success", index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StandardSearchResult(Base):
    __tablename__ = "standard_search_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_id: Mapped[str] = mapped_column(String(64), index=True)
    standard_id: Mapped[str] = mapped_column(String(128), index=True)
    index_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    match_level: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
