from __future__ import annotations

import asyncio
import logging

from app.core.standard_config import StandardSettings, standard_settings
from app.db.session import SessionLocal, init_db
from app.services.openstd_crawl import openstd_crawl_service


logger = logging.getLogger(__name__)


class OpenStdWorker:
    def __init__(self, settings_: StandardSettings = standard_settings) -> None:
        self.settings = settings_
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        init_db()
        while not self._stop.is_set():
            job_id = await asyncio.to_thread(self._claim_next_job_id)
            if not job_id:
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)
                continue
            try:
                await asyncio.to_thread(self._run_job, job_id)
            except Exception:
                logger.exception("openstd crawl job failed: %s", job_id)

    def stop(self) -> None:
        self._stop.set()

    def _claim_next_job_id(self) -> str | None:
        with SessionLocal() as session:
            job = openstd_crawl_service.claim_next_job(session)
            return job.id if job else None

    def _run_job(self, job_id: str) -> None:
        with SessionLocal() as session:
            openstd_crawl_service.run_job(session, job_id)


async def main_async() -> None:
    logging.basicConfig(level=logging.INFO)
    worker = OpenStdWorker()
    await worker.run_forever()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
