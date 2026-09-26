"""Kalendarz makro — blokada alertów w okolicy ważnych publikacji (NFP, CPI, Fed, EBC...).

Źródło: darmowy tygodniowy feed kalendarza ForexFactory (JSON). Adres można zmienić
zmienną ECON_CALENDAR_URL. Gdy feed jest niedostępny, filtr nie blokuje alertów,
a w zakładce Alerty widać błąd.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Które waluty „ruszają" danym instrumentem. USD wpływa na wszystko.
INSTRUMENT_CCY = {
    "US100": {"USD"}, "US500": {"USD"}, "US30": {"USD"}, "US2000": {"USD"},
    "GER40": {"EUR", "USD"}, "UK100": {"GBP", "USD"}, "JAP225": {"JPY", "USD"},
    "GOLD": {"USD"}, "SILVER": {"USD"}, "OIL.WTI": {"USD"},
}


class EconomicCalendar:
    def __init__(self, url: str | None = None, refresh_seconds: int = 6 * 3600):
        self.url = url or os.getenv("ECON_CALENDAR_URL", DEFAULT_URL)
        self.refresh_seconds = refresh_seconds
        self.events: list[dict] = []
        self.fetched_at: float = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()

    def _fetch(self) -> None:
        try:
            with httpx.Client(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as cli:
                r = cli.get(self.url)
            r.raise_for_status()
            raw = r.json()
            events = []
            for e in raw:
                if str(e.get("impact", "")).lower() != "high":
                    continue
                try:
                    dt = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00")).astimezone(timezone.utc)
                except (KeyError, ValueError):
                    continue
                events.append({"title": e.get("title", ""), "currency": str(e.get("country", "")).upper(),
                               "time": dt})
            self.events = sorted(events, key=lambda x: x["time"])
            self.error = None
        except Exception as exc:  # noqa: BLE001
            self.error = f"Kalendarz makro niedostępny: {exc}"
            log.warning(self.error)
        self.fetched_at = time.time()

    def ensure_fresh(self) -> None:
        with self._lock:
            if time.time() - self.fetched_at > (self.refresh_seconds if not self.error else 1800):
                self._fetch()

    def blocking_event(self, symbol: str, when: datetime | None = None, window_minutes: int = 30) -> dict | None:
        """Ważna publikacja w oknie ±window_minutes dla walut instrumentu (albo None)."""
        self.ensure_fresh()
        when = when or datetime.now(timezone.utc)
        ccys = INSTRUMENT_CCY.get(symbol, {"USD"})
        win = timedelta(minutes=window_minutes)
        for e in self.events:
            if e["currency"] in ccys and abs(e["time"] - when) <= win:
                return e
        return None

    def status(self) -> dict:
        now = datetime.now(timezone.utc)
        upcoming = [e for e in self.events if e["time"] >= now - timedelta(minutes=30)][:5]
        return {
            "ok": self.error is None and bool(self.fetched_at),
            "error": self.error,
            "events_this_week": len(self.events),
            "upcoming": [{"title": e["title"], "currency": e["currency"], "time": e["time"].isoformat()}
                         for e in upcoming],
        }
