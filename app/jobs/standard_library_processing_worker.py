from __future__ import annotations

import asyncio
import logging

from app.core.standard_config import StandardSettings, standard_settings
from app.db.standard_library import init_standard_library_db
from app.services.standard_library_atlas import standard_library_atlas_service
from app.services.standard_library_index import standard_library_index_service
from app.services.standard_library_materialize import standard_library_materialize_service


logger = logging.getLogger(__name__)


class StandardLibraryProcessingWorker:
    def __init__(self, settings_: StandardSettings = standard_settings) -> None:
        self.settings = settings_
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        init_standard_library_db()
        while not self._stop.is_set():
            try:
                result = await asyncio.to_thread(standard_library_materialize_service.run_pending_once)
                if result is None:
                    result = await asyncio.to_thread(standard_library_index_service.run_pending_once)
                if result is None:
                    result = await asyncio.to_thread(standard_library_atlas_service.run_pending_once)
            except Exception:
                logger.exception("standard library processing job failed")
                result = {"status": "failed"}
            if result is None:
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)

    def stop(self) -> None:
        self._stop.set()


async def main_async() -> None:
    logging.basicConfig(level=logging.INFO)
    worker = StandardLibraryProcessingWorker()
    await worker.run_forever()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
