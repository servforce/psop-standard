from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.standard_library import init_standard_library_db
from app.services.standard_library_atlas import (
    fail_interrupted_standard_library_atlas_jobs,
    standard_library_atlas_service,
)
from app.services.standard_library_index import (
    fail_interrupted_standard_library_index_jobs,
    standard_library_index_service,
)
from app.services.standard_library_materialize import (
    fail_interrupted_standard_library_materialize_jobs,
    standard_library_materialize_service,
)


LOGGER = logging.getLogger("process_standard_library_jobs")
DEFAULT_LOG_FILE = PROJECT_ROOT / "tools" / "standard-collector" / "logs" / "process_standard_library_jobs.log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process new standard library materialize and index jobs.")
    parser.add_argument("--watch", action="store_true", help="Keep waiting for new jobs when no pending job exists.")
    parser.add_argument("--sleep-seconds", type=float, default=60.0, help="Sleep seconds between polling attempts in watch mode.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many jobs. 0 means no limit.")
    parser.add_argument("--materialize-only", action="store_true", help="Only process materialize jobs.")
    parser.add_argument("--index-only", action="store_true", help="Only process index jobs.")
    parser.add_argument("--atlas", action="store_true", help="Build one Atlas projection after materialize/index jobs finish.")
    parser.add_argument("--atlas-only", action="store_true", help="Only build one Atlas projection.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override one materialize job timeout.")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE), help="Append log output to this file.")
    return parser.parse_args()


def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
        ],
    )


def run_once(args: argparse.Namespace) -> dict | None:
    if not args.index_only:
        result = standard_library_materialize_service.run_pending_once(timeout_seconds=args.timeout_seconds)
        if result is not None:
            return result
    if not args.materialize_only:
        result = standard_library_index_service.run_pending_once()
        if result is not None:
            return result
    if not args.materialize_only and not args.index_only:
        return standard_library_atlas_service.run_pending_once()
    return None


def build_atlas_once() -> dict:
    from app.db.standard_library import StandardLibrarySessionLocal

    with StandardLibrarySessionLocal() as session:
        job = standard_library_atlas_service.create_atlas_job(session)
        session.commit()
        return standard_library_atlas_service.run_job(session, job.id)


def run(args: argparse.Namespace) -> int:
    if args.materialize_only and args.index_only:
        raise SystemExit("--materialize-only and --index-only cannot be used together.")
    if args.atlas_only and (args.materialize_only or args.index_only):
        raise SystemExit("--atlas-only cannot be used with --materialize-only or --index-only.")
    if args.atlas and args.atlas_only:
        raise SystemExit("--atlas and --atlas-only cannot be used together.")
    if args.atlas and args.watch:
        raise SystemExit("--atlas cannot be used with --watch. Run --atlas-only after the watcher is stopped.")
    if args.sleep_seconds < 0:
        raise SystemExit("--sleep-seconds must be >= 0.")
    if args.limit < 0:
        raise SystemExit("--limit must be >= 0.")

    init_standard_library_db()
    from app.db.standard_library import StandardLibrarySessionLocal

    with StandardLibrarySessionLocal() as session:
        recovered = fail_interrupted_standard_library_materialize_jobs(session)
    with StandardLibrarySessionLocal() as session:
        recovered.update(fail_interrupted_standard_library_index_jobs(session))
    with StandardLibrarySessionLocal() as session:
        recovered.update(fail_interrupted_standard_library_atlas_jobs(session))
    if any(recovered.values()):
        LOGGER.warning("marked interrupted standard library jobs as failed: %s", recovered)

    if args.atlas_only:
        try:
            result = build_atlas_once()
        except Exception:
            LOGGER.exception("standard library atlas projection failed")
            return 1
        LOGGER.info("built standard library atlas result=%s", result)
        return 0

    processed = 0
    failed = 0
    LOGGER.info(
        "begin processing standard library jobs watch=%s limit=%s materialize_only=%s index_only=%s atlas=%s",
        args.watch,
        args.limit,
        args.materialize_only,
        args.index_only,
        args.atlas,
    )
    while True:
        if args.limit > 0 and processed >= args.limit:
            LOGGER.info("limit reached: %s", args.limit)
            break
        try:
            result = run_once(args)
        except Exception:
            failed += 1
            processed += 1
            LOGGER.exception("standard library job failed")
            continue
        if result is None:
            if not args.watch:
                LOGGER.info("no pending standard library jobs")
                break
            LOGGER.info("no pending standard library jobs, sleep %.1fs", args.sleep_seconds)
            time.sleep(args.sleep_seconds)
            continue
        processed += 1
        LOGGER.info("processed job result=%s", result)
    if args.atlas:
        try:
            result = build_atlas_once()
        except Exception:
            failed += 1
            LOGGER.exception("standard library atlas projection failed")
        else:
            LOGGER.info("built standard library atlas result=%s", result)
    LOGGER.info("summary processed=%s failed=%s", processed, failed)
    return 0 if failed == 0 else 1


def main() -> None:
    args = parse_args()
    configure_logging(Path(args.log_file))
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
