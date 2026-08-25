from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings

LOGGER = logging.getLogger("materialize_and_index_standards")
DEFAULT_LOG_FILE = PROJECT_ROOT / "tools" / "standard-collector" / "logs" / "materialize_and_index_standards.log"

TaskKind = Literal["materialize", "index"]
RUNTIME: dict[str, Any] = {}


def load_runtime() -> dict[str, Any]:
    if RUNTIME:
        return RUNTIME
    from sqlalchemy import select

    from app.db.session import SessionLocal, init_db
    from app.models.entities import Standard, StandardProcessingJob
    from app.services.standards import MaterializeLengthError, is_postgresql_database, standard_service

    RUNTIME.update(
        {
            "select": select,
            "SessionLocal": SessionLocal,
            "init_db": init_db,
            "Standard": Standard,
            "StandardProcessingJob": StandardProcessingJob,
            "MaterializeLengthError": MaterializeLengthError,
            "is_postgresql_database": is_postgresql_database,
            "standard_service": standard_service,
        }
    )
    return RUNTIME


@dataclass
class ClaimedTask:
    standard_id: str
    standard_code: str
    standard_name: str
    kind: TaskKind


@dataclass
class RunSummary:
    processed: int = 0
    materialized: int = 0
    indexed: int = 0
    materialize_failed: int = 0
    index_failed: int = 0
    no_task_waits: int = 0


def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, mode="a", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize standard PDFs and build overview embedding indexes.")
    parser.add_argument("--watch", action="store_true", help="Keep waiting for new standards when no pending task exists.")
    parser.add_argument("--sleep-seconds", type=float, default=60.0, help="Sleep seconds between polling attempts in watch mode.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many standards. 0 means no limit.")
    parser.add_argument("--retry-failed", action="store_true", help="Also process failed materialize/index statuses.")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=max(1, settings.standard_collector_max_retries),
        help="Retry count for transient materialize/index failures.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=settings.standard_collector_retry_backoff_seconds,
        help="Base seconds for linear retry backoff. Wait attempt * backoff_seconds between retries.",
    )
    parser.add_argument("--materialize-only", action="store_true", help="Generate markdown artifacts only; do not build indexes.")
    parser.add_argument("--index-only", action="store_true", help="Build indexes only for already materialized standards.")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE), help="Append log output to this file.")
    return parser.parse_args()


def ensure_valid_args(args: argparse.Namespace) -> None:
    runtime = load_runtime()
    if args.materialize_only and args.index_only:
        raise SystemExit("--materialize-only and --index-only cannot be used together.")
    if args.sleep_seconds < 0:
        raise SystemExit("--sleep-seconds must be >= 0.")
    if args.limit < 0:
        raise SystemExit("--limit must be >= 0.")
    if args.max_retries < 1:
        raise SystemExit("--max-retries must be >= 1.")
    if args.retry_backoff_seconds < 0:
        raise SystemExit("--retry-backoff-seconds must be >= 0.")
    if not args.materialize_only and not runtime["is_postgresql_database"]():
        raise SystemExit("Indexing requires PostgreSQL + pgvector. Use --materialize-only if you only want markdown artifacts.")


def claim_next_materialize_task(*, retry_failed: bool) -> ClaimedTask | None:
    runtime = load_runtime()
    select = runtime["select"]
    SessionLocal = runtime["SessionLocal"]
    Standard = runtime["Standard"]
    statuses = ["not_started"]
    if retry_failed:
        statuses.append("failed")
    with SessionLocal() as session:
        statement = (
            select(Standard)
            .where(
                Standard.source_pdf_object_key != "",
                Standard.materialize_status.in_(statuses),
            )
            .order_by(Standard.last_synced_at.asc().nullsfirst(), Standard.created_at.asc(), Standard.id.asc())
            .limit(1)
        )
        if runtime["is_postgresql_database"]():
            statement = statement.with_for_update(skip_locked=True)
        standard = session.scalars(statement).first()
        if standard is None:
            session.rollback()
            return None
        standard.materialize_status = "processing"
        standard.materialize_error = ""
        standard.updated_at = datetime.now(timezone.utc)
        session.add(standard)
        task = ClaimedTask(
            standard_id=standard.id,
            standard_code=standard.code,
            standard_name=standard.name,
            kind="materialize",
        )
        session.commit()
        return task


def claim_next_index_task(*, retry_failed: bool) -> ClaimedTask | None:
    runtime = load_runtime()
    select = runtime["select"]
    SessionLocal = runtime["SessionLocal"]
    Standard = runtime["Standard"]
    statuses = ["not_indexed"]
    if retry_failed:
        statuses.append("failed")
    with SessionLocal() as session:
        statement = (
            select(Standard)
            .where(
                Standard.materialize_status == "materialized",
                Standard.index_status.in_(statuses),
            )
            .order_by(Standard.materialized_at.asc().nullsfirst(), Standard.created_at.asc(), Standard.id.asc())
            .limit(1)
        )
        if runtime["is_postgresql_database"]():
            statement = statement.with_for_update(skip_locked=True)
        standard = session.scalars(statement).first()
        if standard is None:
            session.rollback()
            return None
        standard.index_status = "indexing"
        standard.index_error = ""
        standard.updated_at = datetime.now(timezone.utc)
        session.add(standard)
        task = ClaimedTask(
            standard_id=standard.id,
            standard_code=standard.code,
            standard_name=standard.name,
            kind="index",
        )
        session.commit()
        return task


def claim_next_task(args: argparse.Namespace) -> ClaimedTask | None:
    if args.index_only:
        return claim_next_index_task(retry_failed=args.retry_failed)
    task = claim_next_materialize_task(retry_failed=args.retry_failed)
    if task is not None or args.materialize_only:
        return task
    return claim_next_index_task(retry_failed=args.retry_failed)


def create_processing_job(*, standard_id: str, job_type: str, stage: str) -> str:
    runtime = load_runtime()
    SessionLocal = runtime["SessionLocal"]
    StandardProcessingJob = runtime["StandardProcessingJob"]
    with SessionLocal() as session:
        job = StandardProcessingJob(
            id=uuid.uuid4().hex,
            standard_id=standard_id,
            job_type=job_type,
            status="queued",
            stage=stage,
            progress_percent=0,
            message="queued",
            error_message="",
            updated_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.commit()
        return job.id


def update_processing_job(
    job_id: str,
    *,
    status: str,
    stage: str,
    progress_percent: int,
    message: str,
    error_message: str = "",
) -> None:
    if not job_id:
        return
    runtime = load_runtime()
    SessionLocal = runtime["SessionLocal"]
    StandardProcessingJob = runtime["StandardProcessingJob"]
    with SessionLocal() as session:
        job = session.get(StandardProcessingJob, job_id)
        if job is None:
            return
        job.status = status
        job.stage = stage
        job.progress_percent = max(0, min(100, int(progress_percent)))
        job.message = message
        job.error_message = error_message
        job.updated_at = datetime.now(timezone.utc)
        if status == "running" and job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        if status == "running":
            job.completed_at = None
        if status in {"completed", "failed"}:
            job.completed_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()


def retry_sleep_seconds(*, attempt: int, backoff_seconds: float) -> float:
    return max(0.0, backoff_seconds * attempt)


def materialize_timeout_seconds_for_attempt(*, attempt: int, base_timeout_seconds: float) -> float:
    return max(1.0, base_timeout_seconds + (max(1, attempt) - 1) * 600.0)


def process_materialize_task(task: ClaimedTask, args: argparse.Namespace, summary: RunSummary) -> None:
    job_type = "materialize_only" if args.materialize_only else "materialize_and_index"
    job_id = create_processing_job(standard_id=task.standard_id, job_type=job_type, stage="materialize_queued")
    runtime = load_runtime()
    SessionLocal = runtime["SessionLocal"]
    standard_service = runtime["standard_service"]
    MaterializeLengthError = runtime["MaterializeLengthError"]
    max_retries = max(1, int(args.max_retries))
    retry_backoff_seconds = max(0.0, float(args.retry_backoff_seconds))
    base_timeout_seconds = max(1.0, float(settings.qwen_text_timeout_seconds))
    LOGGER.info(
        "materializing standard=%s code=%s name=%s job_id=%s",
        task.standard_id,
        task.standard_code,
        task.standard_name,
        job_id,
    )
    for attempt in range(1, max_retries + 1):
        attempt_timeout_seconds = materialize_timeout_seconds_for_attempt(
            attempt=attempt,
            base_timeout_seconds=base_timeout_seconds,
        )
        update_processing_job(
            job_id,
            status="running",
            stage="materializing" if attempt == 1 else "materialize_retrying",
            progress_percent=0 if attempt == 1 else 1,
            message=f"materialize attempt {attempt}/{max_retries}, timeout={int(attempt_timeout_seconds)}s",
            error_message="",
        )
        try:
            with SessionLocal() as session:
                standard_service.materialize(
                    session,
                    task.standard_id,
                    job_id=job_id,
                    timeout_seconds=attempt_timeout_seconds,
                )
            summary.materialized += 1
            LOGGER.info(
                "materialized standard=%s code=%s name=%s attempt=%s/%s timeout=%ss",
                task.standard_id,
                task.standard_code,
                task.standard_name,
                attempt,
                max_retries,
                int(attempt_timeout_seconds),
            )
            if args.materialize_only:
                return
            process_index_for_task(
                task,
                summary,
                existing_job_id=job_id,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            return
        except Exception as exc:
            retryable = not isinstance(exc, MaterializeLengthError)
            if retryable and attempt < max_retries:
                LOGGER.warning(
                    "materialize retry standard=%s code=%s name=%s attempt=%s/%s timeout=%ss error=%s",
                    task.standard_id,
                    task.standard_code,
                    task.standard_name,
                    attempt,
                    max_retries,
                    int(attempt_timeout_seconds),
                    exc,
                )
                update_processing_job(
                    job_id,
                    status="running",
                    stage="materialize_retrying",
                    progress_percent=0,
                    message=f"retrying materialize {attempt}/{max_retries}, next timeout={int(materialize_timeout_seconds_for_attempt(attempt=attempt + 1, base_timeout_seconds=base_timeout_seconds))}s",
                    error_message=str(exc),
                )
                continue
            summary.materialize_failed += 1
            LOGGER.exception(
                "materialize failed standard=%s code=%s name=%s attempt=%s/%s timeout=%ss error=%s",
                task.standard_id,
                task.standard_code,
                task.standard_name,
                attempt,
                max_retries,
                int(attempt_timeout_seconds),
                exc,
            )
            update_processing_job(
                job_id,
                status="failed",
                stage="length_error" if isinstance(exc, MaterializeLengthError) else "materialize_failed",
                progress_percent=100,
                message="materialize failed",
                error_message=str(exc),
            )
            return

def process_index_for_task(
    task: ClaimedTask,
    summary: RunSummary,
    *,
    existing_job_id: str = "",
    max_retries: int = 1,
    retry_backoff_seconds: float = 0.0,
) -> None:
    job_id = existing_job_id or create_processing_job(standard_id=task.standard_id, job_type="index_only", stage="index_queued")
    LOGGER.info(
        "indexing standard=%s code=%s name=%s job_id=%s",
        task.standard_id,
        task.standard_code,
        task.standard_name,
        job_id,
    )
    runtime = load_runtime()
    SessionLocal = runtime["SessionLocal"]
    standard_service = runtime["standard_service"]
    for attempt in range(1, max_retries + 1):
        update_processing_job(
            job_id,
            status="running",
            stage="indexing" if attempt == 1 else "index_retrying",
            progress_percent=90,
            message=f"index attempt {attempt}/{max_retries}",
            error_message="",
        )
        try:
            with SessionLocal() as session:
                standard_service.index_standard(session, task.standard_id)
            summary.indexed += 1
            update_processing_job(
                job_id,
                status="completed",
                stage="completed",
                progress_percent=100,
                message="materialize/index completed" if existing_job_id else "index completed",
            )
            LOGGER.info(
                "indexed standard=%s code=%s name=%s attempt=%s/%s",
                task.standard_id,
                task.standard_code,
                task.standard_name,
                attempt,
                max_retries,
            )
            return
        except Exception as exc:
            if attempt < max_retries:
                sleep_seconds = retry_sleep_seconds(attempt=attempt, backoff_seconds=retry_backoff_seconds)
                LOGGER.warning(
                    "index retry standard=%s code=%s name=%s attempt=%s/%s error=%s sleep=%.1fs",
                    task.standard_id,
                    task.standard_code,
                    task.standard_name,
                    attempt,
                    max_retries,
                    exc,
                    sleep_seconds,
                )
                update_processing_job(
                    job_id,
                    status="running",
                    stage="index_retrying",
                    progress_percent=90,
                    message=f"retrying index {attempt}/{max_retries}",
                    error_message=str(exc),
                )
                time.sleep(sleep_seconds)
                continue
            summary.index_failed += 1
            LOGGER.exception(
                "index failed standard=%s code=%s name=%s attempt=%s/%s error=%s",
                task.standard_id,
                task.standard_code,
                task.standard_name,
                attempt,
                max_retries,
                exc,
            )
            update_processing_job(
                job_id,
                status="failed",
                stage="index_failed",
                progress_percent=100,
                message="index failed",
                error_message=str(exc),
            )
            return


def process_task(task: ClaimedTask, args: argparse.Namespace, summary: RunSummary) -> None:
    summary.processed += 1
    LOGGER.info(
        "[%s] claimed kind=%s standard=%s code=%s name=%s",
        summary.processed,
        task.kind,
        task.standard_id,
        task.standard_code,
        task.standard_name,
    )
    if task.kind == "materialize":
        process_materialize_task(task, args, summary)
        return
    process_index_for_task(
        task,
        summary,
        max_retries=max(1, int(args.max_retries)),
        retry_backoff_seconds=max(0.0, float(args.retry_backoff_seconds)),
    )


def emit_summary(summary: RunSummary, *, final: bool = True) -> None:
    prefix = "SUMMARY" if final else "summary"
    LOGGER.info(
        "%s processed=%s materialized=%s indexed=%s materialize_failed=%s index_failed=%s no_task_waits=%s",
        prefix,
        summary.processed,
        summary.materialized,
        summary.indexed,
        summary.materialize_failed,
        summary.index_failed,
        summary.no_task_waits,
    )


def run(args: argparse.Namespace) -> int:
    runtime = load_runtime()
    ensure_valid_args(args)
    runtime["init_db"]()
    from app.db.session import SessionLocal
    from app.services.job_recovery import fail_interrupted_background_jobs

    with SessionLocal() as session:
        recovered = fail_interrupted_background_jobs(session)
    if any(recovered.values()):
        LOGGER.warning("marked interrupted jobs as failed at startup: %s", recovered)
    summary = RunSummary()
    LOGGER.info(
        "BEGIN RUN %s watch=%s limit=%s retry_failed=%s materialize_only=%s index_only=%s sleep_seconds=%.1f max_retries=%s retry_backoff_seconds=%.1f",
        datetime.now(timezone.utc).isoformat(),
        args.watch,
        args.limit,
        args.retry_failed,
        args.materialize_only,
        args.index_only,
        args.sleep_seconds,
        args.max_retries,
        args.retry_backoff_seconds,
    )
    try:
        while True:
            if args.limit > 0 and summary.processed >= args.limit:
                LOGGER.info("limit reached: %s", args.limit)
                break
            task = claim_next_task(args)
            if task is None:
                if not args.watch:
                    LOGGER.info("no pending standards")
                    break
                summary.no_task_waits += 1
                emit_summary(summary, final=False)
                LOGGER.info("no pending standards, sleep %.1fs", args.sleep_seconds)
                time.sleep(args.sleep_seconds)
                continue
            process_task(task, args, summary)
    except KeyboardInterrupt:
        LOGGER.warning("interrupted by user")
    finally:
        emit_summary(summary)
        LOGGER.info("END RUN %s", datetime.now(timezone.utc).isoformat())
    return 0 if summary.materialize_failed == 0 and summary.index_failed == 0 else 1


def main() -> None:
    args = parse_args()
    configure_logging(Path(args.log_file))
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
