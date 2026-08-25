from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.db.session import init_db
from app.services.standard_update import NationalUpdateOptions, standard_update_service


LOGGER = logging.getLogger("sync_national_updates")
DEFAULT_LOG_FILE = PROJECT_ROOT / "tools" / "standard-collector" / "logs" / "sync_national_updates.log"


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
    parser = argparse.ArgumentParser(description="Run national standard scheduled updates.")
    parser.add_argument("--watch", action="store_true", help="Keep running updates on a fixed interval.")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=settings.standard_update_interval_seconds,
        help="Seconds between update runs in --watch mode. Default comes from STANDARD_UPDATE_INTERVAL_SECONDS.",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=settings.standard_update_request_interval_seconds,
        help="Sleep seconds between OpenSTD requests.",
    )
    parser.add_argument("--max-retries", type=int, default=settings.standard_update_max_retries)
    parser.add_argument("--retry-backoff-seconds", type=float, default=settings.standard_update_retry_backoff_seconds)
    parser.add_argument(
        "--max-pages-safety",
        type=int,
        default=settings.standard_update_max_pages_safety,
        help="Safety cap for recent-page scanning. 0 means no cap; negative disables recent scan.",
    )
    parser.add_argument(
        "--known-page-stop-count",
        type=int,
        default=settings.standard_update_known_page_stop_count,
        help="Stop recent scan after this many consecutive pages with no new standards.",
    )
    upcoming_group = parser.add_mutually_exclusive_group()
    upcoming_group.add_argument("--check-upcoming", action="store_true", default=settings.standard_update_check_upcoming)
    upcoming_group.add_argument("--no-check-upcoming", action="store_false", dest="check_upcoming")
    parser.add_argument("--upcoming-limit", type=int, default=settings.standard_update_upcoming_limit, help="Max due upcoming standards to check. 0 means no limit; negative disables.")
    parser.add_argument("--active-check-limit", type=int, default=settings.standard_update_active_check_limit, help="Max active standards to check. 0 means no limit; negative disables.")
    parser.add_argument(
        "--new-materialize-limit",
        type=int,
        default=settings.standard_update_new_materialize_limit,
        help="Max new standards to materialize and index in one run. 0 means no limit.",
    )
    parser.add_argument("--log-file", default=settings.standard_update_log_file or str(DEFAULT_LOG_FILE))
    return parser.parse_args()


def options_from_args(args: argparse.Namespace) -> NationalUpdateOptions:
    return NationalUpdateOptions(
        request_interval_seconds=args.request_interval,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        max_pages_safety=args.max_pages_safety,
        known_page_stop_count=args.known_page_stop_count,
        check_upcoming=args.check_upcoming,
        upcoming_limit=args.upcoming_limit,
        active_check_limit=args.active_check_limit,
        new_materialize_limit=args.new_materialize_limit,
        dry_run=False,
    )


def run_once(args: argparse.Namespace) -> int:
    init_db()
    options = options_from_args(args)
    LOGGER.info(
        "BEGIN RUN %s max_pages_safety=%s known_page_stop_count=%s check_upcoming=%s "
        "upcoming_limit=%s active_check_limit=%s new_materialize_limit=%s dry_run=%s",
        datetime.now().isoformat(timespec="seconds"),
        options.max_pages_safety,
        options.known_page_stop_count,
        options.check_upcoming,
        options.upcoming_limit,
        options.active_check_limit,
        options.new_materialize_limit,
        options.dry_run,
    )
    summary = standard_update_service.run_national_update(options)
    LOGGER.info("SUMMARY %s", asdict(summary))
    if summary.lock_skipped:
        return 0
    return 0 if summary.failed_count == 0 else 1


def run(args: argparse.Namespace) -> int:
    if args.interval_seconds < 0:
        raise SystemExit("--interval-seconds must be >= 0.")
    if args.watch:
        while True:
            run_once(args)
            LOGGER.info("sleeping %.1fs before next national update", args.interval_seconds)
            time.sleep(args.interval_seconds)
    return run_once(args)


def main() -> None:
    args = parse_args()
    configure_logging(Path(args.log_file))
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
