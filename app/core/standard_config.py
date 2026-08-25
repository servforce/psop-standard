from __future__ import annotations

from dataclasses import dataclass

from app.core.env import (
    DEFAULT_MODEL_OPENAI_BASE_URL,
    env,
    env_bool,
    env_list,
    load_dotenv,
)


@dataclass(frozen=True)
class StandardSettings:
    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/servforce_material_workbench"
    standard_library_database_url: str = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/octopus_standard_library"
    )

    storage_backend: str = "minio"
    object_store_endpoint: str = "http://10.0.0.20:9000"
    object_store_access_key: str = "minioadmin"
    object_store_secret_key: str = "minioadmin"
    object_store_bucket: str = "servforce-materials"
    object_store_standard_bucket: str = "servforce-standards"
    standard_library_object_store_bucket: str = "octopus-standard-library"
    object_store_region: str = "us-east-1"
    object_store_secure: bool = False

    qwen_text_api_key: str = ""
    qwen_text_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL
    qwen_text_model: str = "qwen3.7-plus"
    qwen_text_temperature: float = 0.1
    qwen_text_top_p: float = 0.8
    qwen_text_max_tokens: int = 24000
    qwen_standard_body_max_tokens: int = 96000
    qwen_standard_structure_max_tokens: int = 32000
    qwen_standard_logic_max_tokens: int = 32000
    qwen_standard_overview_max_tokens: int = 16000
    qwen_text_timeout_seconds: float = 900.0
    qwen_text_max_input_chars: int = 600000
    qwen_text_file_upload_purpose: str = "file-extract"

    standard_embedding_api_key: str = ""
    standard_embedding_base_url: str = DEFAULT_MODEL_OPENAI_BASE_URL
    standard_embedding_model: str = "text-embedding-v4"
    standard_embedding_dimensions: int = 1024
    standard_embedding_timeout_seconds: float = 120.0
    standard_vector_search_min_score: float = 0.0
    standard_search_result_limit: int = 5

    standard_workdir: str = "./work/standards"
    openstd_importer_tool_dir: str = "./tools/openstd-importer"
    openstd_object_store_bucket: str = "openstd"
    openstd_source_url: str = "https://openstd.samr.gov.cn/bzgk/std/"
    openstd_crawl_scope: str = "all_national_standards"
    openstd_allowed_statuses: str = "鐜拌,鍗冲皢瀹炴柦"
    openstd_request_interval_seconds: float = 3.0
    openstd_max_retries: int = 2
    openstd_max_pages: int = 0
    openstd_max_items: int = 0
    openstd_download_timeout_seconds: float = 180.0

    standard_collector_request_interval_seconds: float = 3.0
    standard_collector_max_retries: int = 2
    standard_collector_retry_backoff_seconds: float = 3.0
    standard_collector_discover_timeout_seconds: float = 0.0
    standard_collector_log_file: str = "./tools/standard-collector/logs/collect_national_pdfs.log"

    standard_update_scheduler_enabled: bool = False
    standard_update_national_enabled: bool = True
    standard_update_industry_enabled: bool = False
    standard_update_local_enabled: bool = False
    standard_update_industry_categories: tuple[str, ...] = ()
    standard_update_local_categories: tuple[str, ...] = ()
    standard_update_sacinfo_require_categories: bool = True
    standard_update_sacinfo_status: str = ""
    standard_update_sacinfo_page_size: int = 50
    standard_update_sacinfo_max_pages: int = 1
    standard_update_sacinfo_max_items: int = 50
    standard_update_sacinfo_download_pdfs: bool = True
    standard_update_sacinfo_processing_limit: int = 0
    standard_update_sacinfo_refresh_atlas: bool = True
    standard_update_interval_seconds: float = 1800.0
    standard_update_request_interval_seconds: float = 3.0
    standard_update_max_retries: int = 2
    standard_update_retry_backoff_seconds: float = 3.0
    standard_update_max_pages_safety: int = 0
    standard_update_known_page_stop_count: int = 2
    standard_update_check_upcoming: bool = True
    standard_update_upcoming_limit: int = 0
    standard_update_active_check_limit: int = 0
    standard_update_new_materialize_limit: int = 0
    standard_update_log_file: str = "./tools/standard-collector/logs/sync_national_updates.log"
    standard_library_processing_worker_enabled: bool = False
    worker_poll_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> "StandardSettings":
        load_dotenv()
        model_api_key = (
            env("MODEL_API_KEY", "")
            or env("QWEN_TEXT_API_KEY", "")
            or env("DASHSCOPE_API_KEY", "")
        )
        model_openai_base_url = (
            env("MODEL_OPENAI_BASE_URL", "")
            or env("QWEN_TEXT_BASE_URL", "")
            or env("STANDARD_EMBEDDING_BASE_URL", "")
            or DEFAULT_MODEL_OPENAI_BASE_URL
        )
        qwen_text_api_key = env("QWEN_TEXT_API_KEY", "") or model_api_key
        return cls(
            app_env=env("APP_ENV", "dev"),
            database_url=env(
                "DATABASE_URL",
                "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/servforce_material_workbench",
            ),
            standard_library_database_url=env(
                "STANDARD_LIBRARY_DATABASE_URL",
                "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/octopus_standard_library",
            ),
            storage_backend=env("STORAGE_BACKEND", "minio").lower(),
            object_store_endpoint=env("OBJECT_STORE_ENDPOINT", "http://10.0.0.20:9000"),
            object_store_access_key=env("OBJECT_STORE_ACCESS_KEY", "minioadmin"),
            object_store_secret_key=env("OBJECT_STORE_SECRET_KEY", "minioadmin"),
            object_store_bucket=env("OBJECT_STORE_BUCKET", "servforce-materials"),
            object_store_standard_bucket=env("OBJECT_STORE_STANDARD_BUCKET", "servforce-standards"),
            standard_library_object_store_bucket=env(
                "STANDARD_LIBRARY_OBJECT_STORE_BUCKET",
                "octopus-standard-library",
            ),
            object_store_region=env("OBJECT_STORE_REGION", "us-east-1"),
            object_store_secure=env_bool("OBJECT_STORE_SECURE", False),
            qwen_text_api_key=qwen_text_api_key,
            qwen_text_base_url=env("QWEN_TEXT_BASE_URL", model_openai_base_url),
            qwen_text_model=env("QWEN_TEXT_MODEL", "qwen3.7-plus"),
            qwen_text_temperature=float(env("QWEN_TEXT_TEMPERATURE", "0.1")),
            qwen_text_top_p=float(env("QWEN_TEXT_TOP_P", "0.8")),
            qwen_text_max_tokens=int(env("QWEN_TEXT_MAX_TOKENS", "24000")),
            qwen_standard_body_max_tokens=int(env("QWEN_STANDARD_BODY_MAX_TOKENS", "96000")),
            qwen_standard_structure_max_tokens=int(env("QWEN_STANDARD_STRUCTURE_MAX_TOKENS", "32000")),
            qwen_standard_logic_max_tokens=int(env("QWEN_STANDARD_LOGIC_MAX_TOKENS", "32000")),
            qwen_standard_overview_max_tokens=int(env("QWEN_STANDARD_OVERVIEW_MAX_TOKENS", "16000")),
            qwen_text_timeout_seconds=float(env("QWEN_TEXT_TIMEOUT_SECONDS", "1800")),
            qwen_text_max_input_chars=int(env("QWEN_TEXT_MAX_INPUT_CHARS", "600000")),
            qwen_text_file_upload_purpose=env("QWEN_TEXT_FILE_UPLOAD_PURPOSE", "file-extract"),
            standard_embedding_api_key=env("STANDARD_EMBEDDING_API_KEY", "") or model_api_key,
            standard_embedding_base_url=env("STANDARD_EMBEDDING_BASE_URL", model_openai_base_url),
            standard_embedding_model=env("STANDARD_EMBEDDING_MODEL", "text-embedding-v4"),
            standard_embedding_dimensions=int(env("STANDARD_EMBEDDING_DIMENSIONS", "1024")),
            standard_embedding_timeout_seconds=float(env("STANDARD_EMBEDDING_TIMEOUT_SECONDS", "120")),
            standard_vector_search_min_score=float(env("STANDARD_VECTOR_SEARCH_MIN_SCORE", "0")),
            standard_search_result_limit=int(env("STANDARD_SEARCH_RESULT_LIMIT", "5")),
            standard_workdir=env("STANDARD_WORKDIR", "./work/standards"),
            openstd_importer_tool_dir=env("OPENSTD_IMPORTER_TOOL_DIR", "./tools/openstd-importer"),
            openstd_object_store_bucket=env("OPENSTD_OBJECT_STORE_BUCKET", "openstd"),
            openstd_source_url=env("OPENSTD_SOURCE_URL", "https://openstd.samr.gov.cn/bzgk/std/"),
            openstd_crawl_scope=env("OPENSTD_CRAWL_SCOPE", "all_national_standards"),
            openstd_allowed_statuses=env("OPENSTD_ALLOWED_STATUSES", "鐜拌,鍗冲皢瀹炴柦"),
            openstd_request_interval_seconds=float(env("OPENSTD_REQUEST_INTERVAL_SECONDS", "3")),
            openstd_max_retries=int(env("OPENSTD_MAX_RETRIES", "2")),
            openstd_max_pages=int(env("OPENSTD_MAX_PAGES", "0")),
            openstd_max_items=int(env("OPENSTD_MAX_ITEMS", "0")),
            openstd_download_timeout_seconds=float(env("OPENSTD_DOWNLOAD_TIMEOUT_SECONDS", "180")),
            standard_collector_request_interval_seconds=float(
                env("STANDARD_COLLECTOR_REQUEST_INTERVAL_SECONDS", "3")
            ),
            standard_collector_max_retries=int(env("STANDARD_COLLECTOR_MAX_RETRIES", "2")),
            standard_collector_retry_backoff_seconds=float(
                env("STANDARD_COLLECTOR_RETRY_BACKOFF_SECONDS", "3")
            ),
            standard_collector_discover_timeout_seconds=float(
                env("STANDARD_COLLECTOR_DISCOVER_TIMEOUT_SECONDS", "0")
            ),
            standard_collector_log_file=env(
                "STANDARD_COLLECTOR_LOG_FILE",
                "./tools/standard-collector/logs/collect_national_pdfs.log",
            ),
            standard_update_scheduler_enabled=env_bool("STANDARD_UPDATE_SCHEDULER_ENABLED", False),
            standard_update_national_enabled=env_bool("STANDARD_UPDATE_NATIONAL_ENABLED", True),
            standard_update_industry_enabled=env_bool("STANDARD_UPDATE_INDUSTRY_ENABLED", False),
            standard_update_local_enabled=env_bool("STANDARD_UPDATE_LOCAL_ENABLED", False),
            standard_update_industry_categories=env_list("STANDARD_UPDATE_INDUSTRY_CATEGORIES"),
            standard_update_local_categories=env_list("STANDARD_UPDATE_LOCAL_CATEGORIES"),
            standard_update_sacinfo_require_categories=env_bool("STANDARD_UPDATE_SACINFO_REQUIRE_CATEGORIES", True),
            standard_update_sacinfo_status=env("STANDARD_UPDATE_SACINFO_STATUS", ""),
            standard_update_sacinfo_page_size=int(env("STANDARD_UPDATE_SACINFO_PAGE_SIZE", "50")),
            standard_update_sacinfo_max_pages=int(env("STANDARD_UPDATE_SACINFO_MAX_PAGES", "1")),
            standard_update_sacinfo_max_items=int(env("STANDARD_UPDATE_SACINFO_MAX_ITEMS", "50")),
            standard_update_sacinfo_download_pdfs=env_bool("STANDARD_UPDATE_SACINFO_DOWNLOAD_PDFS", True),
            standard_update_sacinfo_processing_limit=int(env("STANDARD_UPDATE_SACINFO_PROCESSING_LIMIT", "0")),
            standard_update_sacinfo_refresh_atlas=env_bool("STANDARD_UPDATE_SACINFO_REFRESH_ATLAS", True),
            standard_update_interval_seconds=float(env("STANDARD_UPDATE_INTERVAL_SECONDS", "1800")),
            standard_update_request_interval_seconds=float(env("STANDARD_UPDATE_REQUEST_INTERVAL_SECONDS", "3")),
            standard_update_max_retries=int(env("STANDARD_UPDATE_MAX_RETRIES", "2")),
            standard_update_retry_backoff_seconds=float(env("STANDARD_UPDATE_RETRY_BACKOFF_SECONDS", "3")),
            standard_update_max_pages_safety=int(env("STANDARD_UPDATE_MAX_PAGES_SAFETY", "0")),
            standard_update_known_page_stop_count=int(env("STANDARD_UPDATE_KNOWN_PAGE_STOP_COUNT", "2")),
            standard_update_check_upcoming=env_bool("STANDARD_UPDATE_CHECK_UPCOMING", True),
            standard_update_upcoming_limit=int(env("STANDARD_UPDATE_UPCOMING_LIMIT", "0")),
            standard_update_active_check_limit=int(env("STANDARD_UPDATE_ACTIVE_CHECK_LIMIT", "0")),
            standard_update_new_materialize_limit=int(env("STANDARD_UPDATE_NEW_MATERIALIZE_LIMIT", "0")),
            standard_update_log_file=env(
                "STANDARD_UPDATE_LOG_FILE",
                "./tools/standard-collector/logs/sync_national_updates.log",
            ),
            standard_library_processing_worker_enabled=env_bool("STANDARD_LIBRARY_PROCESSING_WORKER_ENABLED", False),
            worker_poll_interval_seconds=float(env("WORKER_POLL_INTERVAL_SECONDS", "1")),
        )


standard_settings = StandardSettings.from_env()
