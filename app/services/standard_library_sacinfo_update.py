from __future__ import annotations

import importlib.util
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.core.standard_config import StandardSettings, standard_settings
from app.db.standard_library import StandardLibrarySessionLocal, init_standard_library_db
from app.models.standard_library import StandardProcessingJob
from app.services.standard_library_atlas import standard_library_atlas_service
from app.services.standard_library_index import standard_library_index_service
from app.services.standard_library_materialize import standard_library_materialize_service


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SACINFO_SCRIPT = PROJECT_ROOT / "tools" / "standard-collector" / "scripts" / "collect_sacinfo_standards.py"


@dataclass(frozen=True)
class SacinfoUpdateOptions:
    source: str
    categories: tuple[str, ...]
    require_categories: bool
    status: str
    page_size: int
    max_pages: int
    max_items: int
    request_interval: float
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    download_pdfs: bool
    processing_limit: int
    refresh_atlas: bool

    @classmethod
    def from_settings(cls, source: str, settings_: StandardSettings = standard_settings) -> "SacinfoUpdateOptions":
        if source == "industry":
            categories = settings_.standard_update_industry_categories
        elif source == "local":
            categories = settings_.standard_update_local_categories
        else:
            raise ValueError(f"unsupported SACInfo source: {source}")
        return cls(
            source=source,
            categories=categories,
            require_categories=settings_.standard_update_sacinfo_require_categories,
            status=settings_.standard_update_sacinfo_status,
            page_size=settings_.standard_update_sacinfo_page_size,
            max_pages=settings_.standard_update_sacinfo_max_pages,
            max_items=settings_.standard_update_sacinfo_max_items,
            request_interval=settings_.standard_update_request_interval_seconds,
            timeout_seconds=max(30.0, settings_.openstd_download_timeout_seconds),
            max_retries=settings_.standard_update_max_retries,
            retry_backoff_seconds=settings_.standard_update_retry_backoff_seconds,
            download_pdfs=settings_.standard_update_sacinfo_download_pdfs,
            processing_limit=settings_.standard_update_sacinfo_processing_limit,
            refresh_atlas=settings_.standard_update_sacinfo_refresh_atlas,
        )


class StandardLibrarySacinfoUpdateService:
    def __init__(self) -> None:
        self._module: ModuleType | None = None

    def run_source_update(self, options: SacinfoUpdateOptions) -> dict[str, Any]:
        if options.source not in {"industry", "local"}:
            raise ValueError(f"unsupported SACInfo source: {options.source}")
        if options.require_categories and not options.categories:
            return {
                "source": options.source,
                "status": "skipped_no_categories",
                "message": (
                    f"STANDARD_UPDATE_{options.source.upper()}_ENABLED=true but no categories are configured"
                ),
            }

        module = self._load_module()
        config = module.SOURCE_CONFIG[options.source]
        args = self._args(options)
        client = module.SACInfoClient(base_url=config["base_url"], timeout_seconds=options.timeout_seconds)
        job_token = uuid.uuid4().hex
        init_standard_library_db()
        try:
            with StandardLibrarySessionLocal() as session:
                job = module.upsert_standard_library_job(
                    session,
                    {"id": job_token, "created_at": datetime.now(timezone.utc)},
                    job_type="scheduled_update",
                    source=options.source,
                    trigger_type="schedule",
                    status="running",
                    stage="discovering",
                    entry_url=config["base_url"],
                )
                job.started_at = job.started_at or datetime.now(timezone.utc)
                session.commit()
                summary = module.collect_into_job(session, client=client, job=job, args=args)
                module.finish_job(session, job, status="completed", summary=summary)
                sync_job_id = job.id
        except Exception as exc:
            LOGGER.exception("SACInfo scheduled update failed source=%s: %s", options.source, exc)
            with StandardLibrarySessionLocal() as session:
                job = module.upsert_standard_library_job(
                    session,
                    {"id": job_token, "created_at": datetime.now(timezone.utc)},
                    job_type="scheduled_update",
                    source=options.source,
                    trigger_type="schedule",
                    status="failed",
                    stage="failed",
                    error_message=str(exc),
                    entry_url=config["base_url"],
                )
                module.finish_job(session, job, status="failed", summary={"failed_count": 1}, error_message=str(exc))
                sync_job_id = job.id
            return {"source": options.source, "status": "failed", "sync_job_id": str(sync_job_id), "error": str(exc)}
        finally:
            client.close()

        processing = self._consume_processing_jobs(sync_job_id, limit=options.processing_limit)
        atlas = None
        if options.refresh_atlas and processing["indexed_count"] > 0:
            atlas = self._refresh_atlas()
        status = "completed" if summary.get("failed_count", 0) == 0 and processing["failed_count"] == 0 else "completed_with_failures"
        return {
            "source": options.source,
            "status": status,
            "sync_job_id": str(sync_job_id),
            "collect": summary,
            "processing": processing,
            "atlas": atlas,
        }

    def _consume_processing_jobs(self, sync_job_id: uuid.UUID, *, limit: int) -> dict[str, int]:
        processed = 0
        materialized = 0
        indexed = 0
        failed = 0
        while limit <= 0 or processed < limit:
            job = self._next_processing_job(sync_job_id, job_type="materialize")
            if job is not None:
                try:
                    with StandardLibrarySessionLocal() as session:
                        standard_library_materialize_service.run_job(session, job.id)
                    materialized += 1
                except Exception:
                    failed += 1
                    LOGGER.exception("SACInfo materialize job failed job_id=%s", job.id)
                processed += 1
                continue

            job = self._next_processing_job(sync_job_id, job_type="index")
            if job is not None:
                try:
                    with StandardLibrarySessionLocal() as session:
                        standard_library_index_service.run_job(session, job.id)
                    indexed += 1
                except Exception:
                    failed += 1
                    LOGGER.exception("SACInfo index job failed job_id=%s", job.id)
                processed += 1
                continue
            break

        return {
            "processed_count": processed,
            "materialized_count": materialized,
            "indexed_count": indexed,
            "failed_count": failed,
        }

    def _next_processing_job(self, sync_job_id: uuid.UUID, *, job_type: str) -> StandardProcessingJob | None:
        with StandardLibrarySessionLocal() as session:
            return session.scalars(
                select(StandardProcessingJob)
                .where(
                    StandardProcessingJob.source_sync_job_id == sync_job_id,
                    StandardProcessingJob.job_type == job_type,
                    StandardProcessingJob.status == "pending",
                )
                .order_by(StandardProcessingJob.priority.asc(), StandardProcessingJob.created_at.asc())
                .limit(1)
            ).first()

    def _refresh_atlas(self) -> dict[str, Any]:
        with StandardLibrarySessionLocal() as session:
            job = standard_library_atlas_service.create_atlas_job(session, priority=500)
            session.commit()
            return standard_library_atlas_service.run_job(session, job.id)

    def _args(self, options: SacinfoUpdateOptions) -> SimpleNamespace:
        return SimpleNamespace(
            source=options.source,
            job_type="scheduled_update",
            category=list(options.categories),
            status=options.status,
            page_size=max(1, options.page_size),
            max_pages=max(0, options.max_pages),
            max_items=max(0, options.max_items),
            request_interval=max(0.0, options.request_interval),
            timeout_seconds=max(30.0, options.timeout_seconds),
            max_retries=max(1, options.max_retries),
            retry_backoff_seconds=max(0.0, options.retry_backoff_seconds),
            dry_run=False,
            download_pdfs=options.download_pdfs,
            log_file="",
        )

    def _load_module(self) -> ModuleType:
        if self._module is not None:
            return self._module
        if not SACINFO_SCRIPT.exists():
            raise FileNotFoundError(f"SACInfo collector script not found: {SACINFO_SCRIPT}")
        spec = importlib.util.spec_from_file_location("collect_sacinfo_standards_scheduler", SACINFO_SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load SACInfo collector script: {SACINFO_SCRIPT}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("collect_sacinfo_standards_scheduler", module)
        spec.loader.exec_module(module)
        self._module = module
        return module


standard_library_sacinfo_update_service = StandardLibrarySacinfoUpdateService()
