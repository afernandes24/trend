"""Pętla w tle: pobranie danych → skan → alerty → aktualizacja wyników sygnałów."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from ..market_data.service import MarketDataService
from .analysis import AnalysisService

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, market: MarketDataService, analysis: AnalysisService, interval: int,
                 h1_interval: int = 300, offset_seconds: int = 20):
        self.offset = max(0, offset_seconds)
        self.market = market
        self.analysis = analysis
        self.interval = max(30, interval)
        self.h1_interval = max(60, h1_interval)
        self._last_h1 = 0.0
        self._task: asyncio.Task | None = None
        self.last_cycle: str | None = None
        self.last_error: str | None = None
        self.cycles = 0

    def cycle(self) -> dict:
        self.analysis.scan_running = True
        try:
            # 5M co cykl (szybkie alerty), 1H rzadziej (trend 1H/4H zmienia się wolno)
            now = time.time()
            tfs = ("5M", "1H") if now - self._last_h1 >= self.h1_interval else ("5M",)
            fetched = self.market.refresh_all(tfs)
            if "1H" in tfs:
                self._last_h1 = now
            self.analysis.scan_all(dispatch_alerts=True)
            closed = self.analysis.update_outcomes()
            try:
                self.analysis.notifier.weekly_summary()
            except Exception:  # noqa: BLE001
                log.exception("Podsumowanie tygodnia nieudane")
            self.last_cycle = datetime.now(timezone.utc).isoformat()
            errors = [info["error"] for res in fetched.values() for info in res.values() if not info["ok"]]
            ok_count = sum(1 for res in fetched.values() for info in res.values() if info["ok"])
            self.last_error = (f"Nie pobrano żadnych danych: {errors[0]}" if errors and not ok_count
                               else None)
            self.cycles += 1
            return {"ok": self.last_error is None, "error": self.last_error, "closed_signals": closed,
                    "fetch_errors": len(errors), "at": self.last_cycle}
        except Exception as exc:  # noqa: BLE001
            log.exception("Cykl skanera nieudany")
            self.last_error = str(exc)
            return {"ok": False, "error": str(exc)}
        finally:
            self.analysis.scan_running = False

    async def _loop(self) -> None:
        # cykle wyrównane do pełnych minut + offset (Yahoo: 20 s, dane na żywo: 3 s)
        while True:
            await asyncio.to_thread(self.cycle)
            now = time.time()
            nxt = (now // self.interval + 1) * self.interval + self.offset
            await asyncio.sleep(max(5.0, nxt - now))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def status(self) -> dict:
        return {"running": self._task is not None, "interval_seconds": self.interval,
                "last_cycle": self.last_cycle, "last_error": self.last_error, "cycles": self.cycles,
                "scan_in_progress": self.analysis.scan_running}
