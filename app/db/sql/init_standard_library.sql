CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS standards (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL,
    code_normalized text NOT NULL,
    name text NOT NULL,
    source text NOT NULL,
    source_label text NOT NULL,
    category text NOT NULL,
    category_label text,
    standard_org text,
    official_status text NOT NULL,
    official_status_raw text,
    publish_date date,
    effective_date date,
    abolish_date date,
    source_site text,
    external_id text,
    detail_url text,
    pdf_url text,
    online_url text,
    file_access_type text NOT NULL DEFAULT 'unavailable',
    source_pdf_bucket text,
    source_pdf_object_key text,
    source_pdf_hash text,
    source_pdf_size_bytes bigint,
    metadata_fingerprint text,
    file_fingerprint text,
    body_md_object_key text,
    structure_md_object_key text,
    logic_md_object_key text,
    overview_md_object_key text,
    materialize_status text NOT NULL DEFAULT 'pending',
    materialize_error text,
    materialized_at timestamptz,
    index_status text NOT NULL DEFAULT 'pending',
    index_error text,
    indexed_at timestamptz,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    last_checked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_standards_source CHECK (source in ('national', 'industry', 'local')),
    CONSTRAINT ck_standards_official_status CHECK (official_status in ('upcoming', 'current', 'updated_available', 'abolished')),
    CONSTRAINT ck_standards_file_access_type CHECK (file_access_type in ('downloadable', 'online_only', 'unavailable')),
    CONSTRAINT ck_standards_materialize_status CHECK (materialize_status in ('pending', 'materializing', 'materialized', 'failed', 'skipped')),
    CONSTRAINT ck_standards_index_status CHECK (index_status in ('pending', 'indexing', 'indexed', 'failed', 'skipped')),
    CONSTRAINT uq_standards_source_code_category UNIQUE (source, code_normalized, category)
);

CREATE TABLE IF NOT EXISTS standard_atlas_projections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version text NOT NULL,
    algorithm text NOT NULL DEFAULT 'umap',
    distance_metric text NOT NULL DEFAULT 'cosine',
    color_by text NOT NULL DEFAULT 'source',
    embedding_model text NOT NULL,
    embedding_dimensions integer NOT NULL,
    effective_standard_count integer NOT NULL DEFAULT 0,
    projected_count integer NOT NULL DEFAULT 0,
    missing_count integer NOT NULL DEFAULT 0,
    input_hash text,
    status text NOT NULL DEFAULT 'pending',
    is_current boolean NOT NULL DEFAULT false,
    started_at timestamptz,
    completed_at timestamptz,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_atlas_projections_version UNIQUE (version),
    CONSTRAINT ck_atlas_projections_algorithm CHECK (algorithm in ('umap', 'tsne', 'pca')),
    CONSTRAINT ck_atlas_projections_distance_metric CHECK (distance_metric in ('cosine', 'l2')),
    CONSTRAINT ck_atlas_projections_status CHECK (status in ('pending', 'running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS standard_sync_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL,
    source text NOT NULL,
    trigger_type text NOT NULL DEFAULT 'system',
    status text NOT NULL DEFAULT 'pending',
    stage text,
    progress_percent numeric(5,2) NOT NULL DEFAULT 0,
    process_id integer,
    heartbeat_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms integer,
    scanned_pages integer NOT NULL DEFAULT 0,
    discovered_count integer NOT NULL DEFAULT 0,
    processed_count integer NOT NULL DEFAULT 0,
    need_download_count integer NOT NULL DEFAULT 0,
    downloaded_count integer NOT NULL DEFAULT 0,
    download_failed_count integer NOT NULL DEFAULT 0,
    new_active_count integer NOT NULL DEFAULT 0,
    expired_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_sync_jobs_job_type CHECK (job_type in ('historical_collect', 'scheduled_update')),
    CONSTRAINT ck_sync_jobs_source CHECK (source in ('national', 'industry', 'local')),
    CONSTRAINT ck_sync_jobs_trigger_type CHECK (trigger_type in ('schedule', 'system', 'admin')),
    CONSTRAINT ck_sync_jobs_status CHECK (status in ('pending', 'running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT ck_sync_jobs_progress CHECK (progress_percent >= 0 and progress_percent <= 100)
);

CREATE TABLE IF NOT EXISTS standard_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source text NOT NULL,
    source_label text NOT NULL,
    entry_url text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    historical_collect_enabled boolean NOT NULL DEFAULT true,
    scheduled_update_enabled boolean NOT NULL DEFAULT true,
    schedule_cron text,
    last_historical_job_id uuid REFERENCES standard_sync_jobs(id),
    last_update_job_id uuid REFERENCES standard_sync_jobs(id),
    last_success_at timestamptz,
    scan_watermark jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_standard_sources_source UNIQUE (source),
    CONSTRAINT ck_standard_sources_source CHECK (source in ('national', 'industry', 'local'))
);

CREATE TABLE IF NOT EXISTS standard_sync_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES standard_sync_jobs(id) ON DELETE CASCADE,
    standard_id uuid REFERENCES standards(id),
    code text,
    name text,
    source text NOT NULL,
    category text,
    external_id text,
    detail_url text,
    official_status_before text,
    official_status_after text,
    metadata_action text,
    status_change_type text,
    file_decision text,
    file_result text,
    source_pdf_bucket text,
    source_pdf_object_key text,
    source_pdf_hash text,
    source_pdf_size_bytes bigint,
    online_url text,
    retry_count integer NOT NULL DEFAULT 0,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_sync_items_job_source_external_id UNIQUE (job_id, source, external_id),
    CONSTRAINT ck_sync_items_source CHECK (source in ('national', 'industry', 'local')),
    CONSTRAINT ck_sync_items_metadata_action CHECK (metadata_action is null or metadata_action in ('new', 'changed', 'unchanged')),
    CONSTRAINT ck_sync_items_file_decision CHECK (file_decision is null or file_decision in ('download', 'redownload', 'no_download', 'online_only', 'unavailable', 'skip')),
    CONSTRAINT ck_sync_items_file_result CHECK (file_result is null or file_result in ('success', 'failed', 'skipped'))
);

CREATE TABLE IF NOT EXISTS standard_processing_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL,
    standard_id uuid REFERENCES standards(id),
    projection_id uuid REFERENCES standard_atlas_projections(id),
    source_sync_job_id uuid REFERENCES standard_sync_jobs(id),
    source_sync_item_id uuid REFERENCES standard_sync_items(id),
    status text NOT NULL DEFAULT 'pending',
    stage text,
    progress_percent numeric(5,2) NOT NULL DEFAULT 0,
    priority integer NOT NULL DEFAULT 100,
    retry_count integer NOT NULL DEFAULT 0,
    max_retries integer NOT NULL DEFAULT 3,
    process_id integer,
    heartbeat_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms integer,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_processing_jobs_job_type CHECK (job_type in ('materialize', 'index', 'atlas_projection')),
    CONSTRAINT ck_processing_jobs_status CHECK (status in ('pending', 'running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT ck_processing_jobs_progress CHECK (progress_percent >= 0 and progress_percent <= 100)
);

CREATE TABLE IF NOT EXISTS standard_indexes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    standard_id uuid NOT NULL REFERENCES standards(id) ON DELETE CASCADE,
    index_kind text NOT NULL DEFAULT 'overview',
    content text NOT NULL,
    content_hash text NOT NULL,
    embedding vector(1024) NOT NULL,
    embedding_model text NOT NULL,
    embedding_dimensions integer NOT NULL,
    schema_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_standard_indexes_standard_model UNIQUE (standard_id, index_kind, embedding_model, embedding_dimensions),
    CONSTRAINT ck_standard_indexes_kind CHECK (index_kind = 'overview'),
    CONSTRAINT ck_standard_indexes_dimensions CHECK (embedding_dimensions > 0)
);

CREATE TABLE IF NOT EXISTS standard_search_queries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text text NOT NULL,
    searched_at timestamptz NOT NULL DEFAULT now(),
    last_reused_at timestamptz,
    sort_at timestamptz NOT NULL DEFAULT now(),
    caller text NOT NULL DEFAULT 'frontend',
    "limit" integer NOT NULL DEFAULT 20,
    search_mode text NOT NULL DEFAULT 'semantic',
    embedding_model text,
    embedding_dimensions integer,
    result_count integer NOT NULL DEFAULT 0,
    latency_ms integer,
    status text NOT NULL,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_search_queries_limit CHECK ("limit" > 0),
    CONSTRAINT ck_search_queries_mode CHECK (search_mode = 'semantic'),
    CONSTRAINT ck_search_queries_status CHECK (status in ('success', 'failed'))
);

CREATE TABLE IF NOT EXISTS standard_search_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id uuid NOT NULL REFERENCES standard_search_queries(id) ON DELETE CASCADE,
    standard_id uuid NOT NULL REFERENCES standards(id),
    index_id uuid REFERENCES standard_indexes(id),
    rank integer NOT NULL,
    score numeric(8,6) NOT NULL,
    match_level text,
    reason text,
    evidence text,
    snapshot_code text NOT NULL,
    snapshot_name text NOT NULL,
    snapshot_source text NOT NULL,
    snapshot_category text,
    snapshot_publish_date date,
    snapshot_effective_date date,
    snapshot_detail_url text,
    snapshot_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_standard_search_results_query_rank UNIQUE (query_id, rank),
    CONSTRAINT ck_search_results_rank CHECK (rank > 0),
    CONSTRAINT ck_search_results_score CHECK (score >= 0)
);

CREATE TABLE IF NOT EXISTS standard_atlas_points (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projection_id uuid NOT NULL REFERENCES standard_atlas_projections(id) ON DELETE CASCADE,
    standard_id uuid NOT NULL REFERENCES standards(id) ON DELETE CASCADE,
    x double precision NOT NULL,
    y double precision NOT NULL,
    color_key text,
    source text NOT NULL,
    category text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_atlas_points_projection_standard UNIQUE (projection_id, standard_id),
    CONSTRAINT ck_atlas_points_coordinates_not_nan CHECK (x = x and y = y)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_standards_source_external_id
ON standards (source, external_id)
WHERE external_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_standards_effective_recent
ON standards (publish_date DESC, id)
WHERE (((source in ('national', 'industry') and official_status = 'current') or (source = 'local' and official_status in ('current', 'updated_available'))) and materialize_status = 'materialized' and index_status = 'indexed');

CREATE INDEX IF NOT EXISTS idx_standards_effective_source_recent
ON standards (source, publish_date DESC, id)
WHERE (((source in ('national', 'industry') and official_status = 'current') or (source = 'local' and official_status in ('current', 'updated_available'))) and materialize_status = 'materialized' and index_status = 'indexed');

CREATE INDEX IF NOT EXISTS idx_standards_code_normalized_effective
ON standards (code_normalized)
WHERE (((source in ('national', 'industry') and official_status = 'current') or (source = 'local' and official_status in ('current', 'updated_available'))) and materialize_status = 'materialized' and index_status = 'indexed');

CREATE INDEX IF NOT EXISTS idx_standards_name_trgm
ON standards
USING gin (name gin_trgm_ops)
WHERE (((source in ('national', 'industry') and official_status = 'current') or (source = 'local' and official_status in ('current', 'updated_available'))) and materialize_status = 'materialized' and index_status = 'indexed');

CREATE INDEX IF NOT EXISTS idx_standards_detail_url ON standards (detail_url);
CREATE INDEX IF NOT EXISTS idx_standard_sources_enabled ON standard_sources (enabled, source);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_status_priority ON standard_sync_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_latest_update ON standard_sync_jobs (job_type, source, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_items_job ON standard_sync_items (job_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sync_items_standard ON standard_sync_items (standard_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_status_priority ON standard_processing_jobs (status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_standard ON standard_processing_jobs (standard_id, job_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_heartbeat ON standard_processing_jobs (status, heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_standard_indexes_standard ON standard_indexes (standard_id);
CREATE INDEX IF NOT EXISTS idx_standard_indexes_model ON standard_indexes (index_kind, embedding_model, embedding_dimensions);
CREATE INDEX IF NOT EXISTS idx_standard_indexes_embedding_hnsw ON standard_indexes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_search_queries_sort ON standard_search_queries (sort_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_search_results_query_rank ON standard_search_results (query_id, rank);
CREATE INDEX IF NOT EXISTS idx_atlas_projections_current ON standard_atlas_projections (is_current, completed_at DESC) WHERE status = 'completed';
CREATE INDEX IF NOT EXISTS idx_atlas_points_projection ON standard_atlas_points (projection_id, standard_id);
CREATE INDEX IF NOT EXISTS idx_atlas_points_color_key ON standard_atlas_points (projection_id, color_key);
