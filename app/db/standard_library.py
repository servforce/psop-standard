from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import UserDefinedType

from app.core.standard_config import standard_settings as settings


POSTGRESQL_URL_PREFIXES = ("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw) -> str:
        return f"vector({int(self.dimensions)})"


class StandardLibraryBase(DeclarativeBase):
    pass


def _require_postgresql_database_url() -> None:
    if settings.standard_library_database_url.startswith(POSTGRESQL_URL_PREFIXES):
        return
    raise RuntimeError(
        "STANDARD_LIBRARY_DATABASE_URL must point to PostgreSQL because the standard library uses pgvector. "
        f"Current STANDARD_LIBRARY_DATABASE_URL={settings.standard_library_database_url!r}"
    )


_require_postgresql_database_url()

standard_library_engine = create_engine(
    settings.standard_library_database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    future=True,
)
StandardLibrarySessionLocal = sessionmaker(
    bind=standard_library_engine,
    autoflush=False,
    autocommit=False,
    future=True,
)


def init_standard_library_db() -> None:
    _create_required_extensions()

    from app.models import standard_library  # noqa: F401

    StandardLibraryBase.metadata.create_all(bind=standard_library_engine)
    _migrate_standard_library_tables()


def _create_required_extensions() -> None:
    statements = [
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "CREATE EXTENSION IF NOT EXISTS vector",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    ]
    with standard_library_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _migrate_standard_library_tables() -> None:
    effective_standard_filter = """
    (
      (
        source in ('national', 'industry')
        and official_status = 'current'
      )
      or (
        source = 'local'
        and official_status in ('current', 'updated_available')
      )
    )
    and materialize_status = 'materialized'
    and index_status = 'indexed'
    """
    statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_standards_source_external_id ON standards (source, external_id) WHERE external_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_standards_effective_recent ON standards (publish_date DESC, id) WHERE " + effective_standard_filter,
        "CREATE INDEX IF NOT EXISTS idx_standards_effective_source_recent ON standards (source, publish_date DESC, id) WHERE " + effective_standard_filter,
        "CREATE INDEX IF NOT EXISTS idx_standards_code_normalized_effective ON standards (code_normalized) WHERE " + effective_standard_filter,
        "CREATE INDEX IF NOT EXISTS idx_standards_name_trgm ON standards USING gin (name gin_trgm_ops) WHERE " + effective_standard_filter,
        "CREATE INDEX IF NOT EXISTS idx_standards_detail_url ON standards (detail_url)",
        "CREATE INDEX IF NOT EXISTS idx_standard_sources_enabled ON standard_sources (enabled, source)",
        "CREATE INDEX IF NOT EXISTS idx_sync_jobs_status_priority ON standard_sync_jobs (status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sync_jobs_latest_update ON standard_sync_jobs (job_type, source, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_sync_items_job ON standard_sync_items (job_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sync_items_standard ON standard_sync_items (standard_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_processing_jobs_status_priority ON standard_processing_jobs (status, priority, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_processing_jobs_standard ON standard_processing_jobs (standard_id, job_type, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_processing_jobs_heartbeat ON standard_processing_jobs (status, heartbeat_at)",
        "CREATE INDEX IF NOT EXISTS idx_standard_indexes_standard ON standard_indexes (standard_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_indexes_model ON standard_indexes (index_kind, embedding_model, embedding_dimensions)",
        "CREATE INDEX IF NOT EXISTS idx_standard_indexes_embedding_hnsw ON standard_indexes USING hnsw (embedding vector_cosine_ops)",
        "CREATE INDEX IF NOT EXISTS idx_search_queries_sort ON standard_search_queries (sort_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_search_results_query_rank ON standard_search_results (query_id, rank)",
        "CREATE INDEX IF NOT EXISTS idx_atlas_projections_current ON standard_atlas_projections (is_current, completed_at DESC) WHERE status = 'completed'",
        "CREATE INDEX IF NOT EXISTS idx_atlas_points_projection ON standard_atlas_points (projection_id, standard_id)",
        "CREATE INDEX IF NOT EXISTS idx_atlas_points_color_key ON standard_atlas_points (projection_id, color_key)",
    ]
    with standard_library_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
