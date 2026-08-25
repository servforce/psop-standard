from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.standard_config import standard_settings as settings


POSTGRESQL_URL_PREFIXES = ("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")


class Base(DeclarativeBase):
    pass


def _require_postgresql_database_url() -> None:
    if settings.database_url.startswith(POSTGRESQL_URL_PREFIXES):
        return
    raise RuntimeError(
        "DATABASE_URL must point to PostgreSQL because the standard search index requires pgvector. "
        f"Current DATABASE_URL={settings.database_url!r}"
    )


_require_postgresql_database_url()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_postgresql_tables()


def _migrate_postgresql_tables() -> None:
    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "DROP TABLE IF EXISTS audio_chunks",
        "DROP TABLE IF EXISTS test_runs",
        "DROP TABLE IF EXISTS video_artifacts",
        f"""
        CREATE TABLE IF NOT EXISTS standard_indexes (
            id UUID PRIMARY KEY,
            standard_id VARCHAR(128) NOT NULL REFERENCES standards(id) ON DELETE CASCADE,
            index_kind VARCHAR(64) NOT NULL DEFAULT 'overview',
            content TEXT NOT NULL,
            embedding vector({settings.standard_embedding_dimensions}) NOT NULL,
            embedding_model VARCHAR(128) NOT NULL,
            embedding_dimensions INTEGER NOT NULL DEFAULT {settings.standard_embedding_dimensions},
            content_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_standard_indexes_standard_id ON standard_indexes (standard_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_indexes_kind ON standard_indexes (index_kind)",
        "CREATE INDEX IF NOT EXISTS idx_standard_indexes_embedding ON standard_indexes USING hnsw (embedding vector_cosine_ops)",
        "CREATE INDEX IF NOT EXISTS idx_standards_code ON standards (code)",
        "CREATE INDEX IF NOT EXISTS idx_standards_source_status ON standards (source_status)",
        "CREATE INDEX IF NOT EXISTS idx_standards_materialize_status ON standards (materialize_status)",
        "CREATE INDEX IF NOT EXISTS idx_standards_index_status ON standards (index_status)",
        "ALTER TABLE standards ADD COLUMN IF NOT EXISTS last_status_checked_at TIMESTAMPTZ",
        "ALTER TABLE standard_sync_items ADD COLUMN IF NOT EXISTS effective_date VARCHAR(64) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_standard_sync_items_job_id ON standard_sync_items (job_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_sync_items_standard_id ON standard_sync_items (standard_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_sync_items_standard_code ON standard_sync_items (standard_code)",
        "CREATE INDEX IF NOT EXISTS idx_standard_sync_items_status ON standard_sync_items (status)",
        "CREATE INDEX IF NOT EXISTS idx_standard_processing_jobs_standard_id ON standard_processing_jobs (standard_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_search_results_query_id ON standard_search_results (query_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_search_results_standard_id ON standard_search_results (standard_id)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
