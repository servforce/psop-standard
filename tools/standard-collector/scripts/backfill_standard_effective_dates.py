from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.models.entities import Standard
from app.services.openstd_crawl import OPENSTD_SOURCE_SITE


LOGGER = logging.getLogger("backfill_standard_effective_dates")
DEFAULT_LOG_FILE = PROJECT_ROOT / "tools" / "standard-collector" / "logs" / "backfill_standard_effective_dates.log"


class OpenStdImporterClient:
    def __init__(self, tool_dir: str | Path | None = None) -> None:
        self.tool_dir = Path(tool_dir or settings.openstd_importer_tool_dir)
        if not self.tool_dir.is_absolute():
            self.tool_dir = PROJECT_ROOT / self.tool_dir
        self.script = self.tool_dir / "scripts" / "openstd_importer.py"
        if not self.script.exists():
            raise FileNotFoundError(f"OpenSTD importer tool script not found: {self.script}")
        spec = importlib.util.spec_from_file_location("openstd_importer_backfill_tool", self.script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load OpenSTD importer module from: {self.script}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("openstd_importer_backfill_tool", module)
        spec.loader.exec_module(module)
        self.module = module

    def new_http_client(self):
        return self.module.OpenStdHttpClient(timeout_seconds=settings.openstd_download_timeout_seconds)

    def parse_detail(self, html: str, detail_url: str):
        return self.module.parse_detail(html, detail_url)


def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, mode="a", encoding="utf-8")]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill missing effective_date values for standards.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many rows. 0 means all.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and log values without writing to the database.")
    parser.add_argument(
        "--request-interval",
        type=float,
        default=settings.standard_collector_request_interval_seconds,
        help="Sleep seconds between detail page requests.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=max(1, settings.standard_collector_max_retries),
        help="Retry count for one standard when fetching detail pages fails.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=settings.standard_collector_retry_backoff_seconds,
        help="Backoff seconds between retries.",
    )
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE), help="Append log output to this file.")
    return parser.parse_args()


def candidate_standard_ids(limit: int) -> list[str]:
    with SessionLocal() as session:
        statement = (
            select(Standard.id)
            .where(
                Standard.standard_type == "national",
                Standard.source_site == OPENSTD_SOURCE_SITE,
                Standard.effective_date == "",
                Standard.source_pdf_object_key != "",
                Standard.detail_url != "",
            )
            .order_by(Standard.updated_at.asc(), Standard.created_at.asc(), Standard.id.asc())
        )
        if limit > 0:
            statement = statement.limit(limit)
        return list(session.scalars(statement).all())


def run_backfill(args: argparse.Namespace) -> int:
    init_db()
    importer = OpenStdImporterClient()
    candidate_ids = candidate_standard_ids(args.limit)
    total = len(candidate_ids)
    LOGGER.info("BEGIN RUN %s", datetime.now(timezone.utc).isoformat())
    LOGGER.info("candidates=%s dry_run=%s request_interval=%.1fs max_retries=%s", total, args.dry_run, args.request_interval, args.max_retries)

    updated = 0
    preview_updated = 0
    skipped_no_detail = 0
    skipped_no_effective_date = 0
    failed = 0
    unchanged = 0

    client = importer.new_http_client()
    try:
        for index, standard_id in enumerate(candidate_ids, start=1):
            with SessionLocal() as session:
                standard = session.get(Standard, standard_id)
                if standard is None:
                    unchanged += 1
                    LOGGER.warning("[%s/%s] standard missing id=%s", index, total, standard_id)
                    continue

                detail_url = (standard.detail_url or standard.source_url or "").strip()
                if not detail_url:
                    skipped_no_detail += 1
                    LOGGER.warning(
                        "[%s/%s] skip no detail_url code=%s name=%s id=%s",
                        index,
                        total,
                        standard.code,
                        standard.name,
                        standard.id,
                    )
                    continue

                LOGGER.info(
                    "[%s/%s] fetching code=%s name=%s detail=%s",
                    index,
                    total,
                    standard.code,
                    standard.name,
                    detail_url,
                )

                last_error = ""
                effective_date = ""
                for attempt in range(1, args.max_retries + 1):
                    try:
                        html, final_url = client.get_html(detail_url, referer=standard.source_url or detail_url)
                        inspection = importer.parse_detail(html, final_url)
                        effective_date = str(inspection.effective_date or "").strip()
                        if not effective_date:
                            skipped_no_effective_date += 1
                            LOGGER.warning(
                                "[%s/%s] no effective_date code=%s name=%s detail=%s",
                                index,
                                total,
                                standard.code,
                                standard.name,
                                final_url,
                            )
                            break

                        if args.dry_run:
                            preview_updated += 1
                            LOGGER.info(
                                "[%s/%s] dry-run update code=%s name=%s effective_date=%s",
                                index,
                                total,
                                standard.code,
                                standard.name,
                                effective_date,
                            )
                        else:
                            now = datetime.now(timezone.utc)
                            standard.effective_date = effective_date
                            standard.last_synced_at = now
                            standard.updated_at = now
                            session.add(standard)
                            session.commit()
                            LOGGER.info(
                                "[%s/%s] updated code=%s name=%s effective_date=%s",
                                index,
                                total,
                                standard.code,
                                standard.name,
                                effective_date,
                            )
                            updated += 1
                        break
                    except Exception as exc:  # noqa: BLE001
                        session.rollback()
                        last_error = str(exc)
                        if attempt < args.max_retries:
                            sleep_seconds = max(0.0, args.retry_backoff_seconds * attempt)
                            LOGGER.warning(
                                "[%s/%s] retry code=%s name=%s attempt=%s/%s error=%s sleep=%.1fs",
                                index,
                                total,
                                standard.code,
                                standard.name,
                                attempt,
                                args.max_retries,
                                last_error,
                                sleep_seconds,
                            )
                            time.sleep(sleep_seconds)
                            continue
                        failed += 1
                        LOGGER.error(
                            "[%s/%s] failed code=%s name=%s error=%s",
                            index,
                            total,
                            standard.code,
                            standard.name,
                            last_error,
                        )
                        break

                if args.request_interval > 0:
                    time.sleep(args.request_interval)
    finally:
        client.close()

    LOGGER.info(
        "SUMMARY total=%s updated=%s preview_updated=%s skipped_no_detail=%s skipped_no_effective_date=%s failed=%s unchanged=%s dry_run=%s",
        total,
        updated,
        preview_updated,
        skipped_no_detail,
        skipped_no_effective_date,
        failed,
        unchanged,
        args.dry_run,
    )
    LOGGER.info("END RUN %s", datetime.now(timezone.utc).isoformat())
    return 0 if failed == 0 else 1


def main() -> None:
    args = parse_args()
    configure_logging(Path(args.log_file))
    raise SystemExit(run_backfill(args))


if __name__ == "__main__":
    main()
