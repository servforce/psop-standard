from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.standard_config import StandardSettings, standard_settings
from app.services.standard_library_sacinfo_update import (
    SacinfoUpdateOptions,
    standard_library_sacinfo_update_service,
)
from app.services.standard_update import NationalUpdateOptions, standard_update_service


logger = logging.getLogger(__name__)


class StandardUpdateScheduler:
    def __init__(self, settings_: StandardSettings = standard_settings) -> None:
        self.settings = settings_
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        interval = max(0.0, float(self.settings.standard_update_interval_seconds))
        self._emit(
            f"standard update scheduler started interval_seconds={interval:.1f} "
            f"enabled={self.settings.standard_update_scheduler_enabled}"
        )
        try:
            while not self._stop.is_set():
                await self._run_once()
                if self._stop.is_set():
                    break
                await self._sleep(interval)
        except asyncio.CancelledError:
            self._emit("standard update scheduler cancelled")
            raise
        except Exception:
            logger.exception("standard update scheduler crashed")
            self._emit("standard update scheduler crashed")
            raise
        finally:
            self._emit("standard update scheduler stopped")

    async def _run_once(self) -> None:
        if self._run_lock.locked():
            self._emit("standard update scheduler skipped: previous run still in progress")
            return
        async with self._run_lock:
            started_at = datetime.now(timezone.utc)
            self._emit(f"standard update scheduler cycle started at {started_at.isoformat()}")
            try:
                summary = await asyncio.to_thread(self._run_update_once)
            except Exception as exc:
                logger.exception("standard update scheduler cycle failed: %s", exc)
                self._emit(f"standard update scheduler cycle failed: {exc}")
                return
            self._emit(f"standard update scheduler cycle summary={self._summary_payload(summary)}")

    def _run_update_once(self) -> dict[str, Any]:
        cycle: dict[str, Any] = {
            "status": "completed",
            "national": None,
            "industry": None,
            "local": None,
        }
        if self.settings.standard_update_national_enabled:
            national_options = NationalUpdateOptions.from_settings()
            cycle["national"] = asdict(standard_update_service.run_national_update(national_options))
        else:
            cycle["national"] = {"status": "disabled"}

        for source in ("industry", "local"):
            enabled = (
                self.settings.standard_update_industry_enabled
                if source == "industry"
                else self.settings.standard_update_local_enabled
            )
            if not enabled:
                cycle[source] = {"status": "disabled"}
                continue
            options = SacinfoUpdateOptions.from_settings(source, self.settings)
            cycle[source] = standard_library_sacinfo_update_service.run_source_update(options)

        statuses = [value.get("status") for value in cycle.values() if isinstance(value, dict)]
        if any(status in {"failed", "completed_with_failures"} for status in statuses):
            cycle["status"] = "completed_with_failures"
        if all(status == "disabled" for status in statuses):
            cycle["status"] = "disabled"
        return cycle

    async def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    def _emit(self, message: str) -> None:
        logger.info(message)
        print(message, flush=True)

    def _summary_payload(self, summary: Any) -> Any:
        if is_dataclass(summary):
            return asdict(summary)
        return summary
