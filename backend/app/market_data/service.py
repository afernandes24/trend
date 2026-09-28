"""Serwis danych rynkowych: dostawca -> baza (cache) -> interwały aplikacji.

Pobieramy tylko świece źródłowe (5M i 1H), zapisujemy w SQLite, a 15M / 4H / 1D
budujemy resamplingiem. Dzięki temu backtest i scanner używają tych samych danych.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import pandas as pd

from ..config import settings
from ..database import Database
from ..instruments import INSTRUMENTS, get_instrument
from .base import SOURCE_TF, MarketDataError, MarketDataProvider, resample, tf_minutes
from .csv_provider import CsvProvider, parse_csv
from .xtb import XtbProvider
from .yahoo import YahooProvider

log = logging.getLogger(__name__)

DEFAULT_FX = {"USDPLN": 3.65, "EURPLN": 4.25, "GBPPLN": 4.90, "JPYPLN": 0.025}


def make_provider(name: str) -> MarketDataProvider:
    name = (name or "yahoo").lower()
    if name == "yahoo":
        return YahooProvider()
    if name == "csv":
        return CsvProvider()
    if name == "xtb":
        return XtbProvider()
    if name == "capital":
        from .capital import CapitalProvider
        return CapitalProvider()
    raise ValueError(f"Nieznany dostawca danych: {name}")


class MarketDataService:
    def __init__(self, db: Database, provider: MarketDataProvider | None = None):
        self.db = db
        self.provider = provider or make_provider(settings.market_data_provider)
        self.status: dict[str, dict] = {}      # symbol -> {tf: {...}}
        self._cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self._lock = threading.RLock()
        self._fx: dict[str, float] = {}
        self._fx_ts = 0.0
        self._offsets: dict | None = None
        self._handle_provider_switch()

    def _handle_provider_switch(self) -> None:
        """Zmiana źródła danych = inna skala cen. Czyścimy świece i korektę, zamykamy stare otwarte sygnały."""
        prev = self.db.get_setting("candles_provider") or ("yahoo" if self.db.has_candles() else None)
        cur = self.provider.name
        if prev and prev != cur:
            log.warning("Zmiana źródła danych %s -> %s: czyszczę świece i korektę cen", prev, cur)
            self.db.delete_all_candles()
            self.db.set_setting("price_offsets", {})
            for sig in self.db.open_signals():
                self.db.update_signal(sig["id"], status="EXPIRED", result="ZMIANA ŹRÓDŁA", result_r=None,
                                      closed_at=int(time.time()))
            for p in self.db.list_positions("OPEN"):
                self.db.update_position(p["id"], ref_price=None)   # zostanie ustawiona od nowa
        self.db.set_setting("candles_provider", cur)

    # ------------------------------------------------------------------ refresh
    def refresh(self, symbol: str, tfs: tuple[str, ...] = ("5M", "1H")) -> dict:
        """Pobiera świeże dane źródłowe (domyślnie 5M i 1H) dla instrumentu."""
        result = dict(self.status.get(symbol) or {})
        for src_tf in tfs:
            existing = self.db.load_candles(symbol, src_tf)
            # pełne pobranie, gdy brak danych lub ostatnia świeca starsza niż zakres aktualizacji przyrostowej
            stale_after = pd.Timedelta(days=4 if src_tf == "5M" else 9)
            full = (existing.empty or len(existing) < 300
                    or pd.Timestamp.now(tz="UTC") - existing.index[-1] > stale_after)
            try:
                df = self.provider.fetch_candles(symbol, src_tf, full_history=full)
                n = self.db.upsert_candles(symbol, src_tf, df)
                info = {"ok": True, "rows": n, "last": _iso(df.index[-1]) if n else None,
                        "updated": _now_iso(), "error": None}
            except (MarketDataError, Exception) as exc:  # noqa: BLE001
                log.warning("Refresh %s %s: %s", symbol, src_tf, exc)
                info = {"ok": False, "rows": 0, "last": None, "updated": _now_iso(), "error": str(exc)}
            result[src_tf] = info
        with self._lock:
            self.status[symbol] = result
            for key in list(self._cache):
                if key[0] == symbol:
                    self._cache.pop(key, None)
        return result

    def refresh_all(self, tfs: tuple[str, ...] = ("5M", "1H")) -> dict:
        return {i.symbol: self.refresh(i.symbol, tfs) for i in INSTRUMENTS}

    # ------------------------------------------------------------------ read
    # ------------------------------------------------------------------ korekta cen do XTB
    def offsets(self) -> dict:
        if self._offsets is None:
            self._offsets = self.db.get_setting("price_offsets", {}) or {}
        return dict(self._offsets)

    def offset(self, symbol: str) -> float:
        return float((self.offsets().get(symbol) or {}).get("offset") or 0.0)

    def raw_last(self, symbol: str) -> tuple[float | None, str | None]:
        """Ostatnia cena ze źródła danych BEZ korekty (i czas świecy)."""
        for tf in ("5M", "1H"):
            df = self.get_candles(symbol, tf, raw=True)
            if not df.empty:
                return float(df["close"].iloc[-1]), df.index[-1].isoformat()
        return None, None

    def set_offsets(self, xtb_prices: dict[str, float | None]) -> dict[str, float]:
        """Ustawia korektę = cena w XTB − cena ze źródła. None = usuń korektę.

        Zwraca zmianę korekty (delta) dla każdego instrumentu — do przesunięcia otwartych sygnałów/pozycji.
        """
        cur = self.offsets()
        deltas: dict[str, float] = {}
        for symbol, xtb in xtb_prices.items():
            inst = get_instrument(symbol)
            old = float((cur.get(inst.symbol) or {}).get("offset") or 0.0)
            if xtb in (None, ""):
                cur.pop(inst.symbol, None)
                new = 0.0
            else:
                raw, raw_time = self.raw_last(inst.symbol)
                if raw is None:
                    raise ValueError(f"{inst.symbol}: brak danych ze źródła — nie można policzyć korekty")
                new = round(float(xtb) - raw, 6)
                cur[inst.symbol] = {"offset": new, "xtb_price": float(xtb), "src_price": raw,
                                    "src_time": raw_time, "ts": int(time.time())}
            if abs(new - old) > 1e-12:
                deltas[inst.symbol] = new - old
        self.db.set_setting("price_offsets", cur)
        with self._lock:
            self._offsets = cur
            self._cache.clear()
        return deltas

    def get_candles(self, symbol: str, tf: str, raw: bool = False) -> pd.DataFrame:
        """Świece danego interwału. Domyślnie z korektą do XTB (raw=True — surowe dane źródła)."""
        get_instrument(symbol)
        tf_minutes(tf)
        if not raw:
            off = self.offset(symbol)
            if off:
                key = (symbol, tf, "adj")
                with self._lock:
                    hit = self._cache.get(key)
                    if hit and time.time() - hit[0] < 30:
                        return hit[1]
                base_df = self.get_candles(symbol, tf, raw=True)
                df = base_df.copy()
                if not df.empty:
                    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]] + off
                df.attrs = dict(base_df.attrs)
                with self._lock:
                    self._cache[key] = (time.time(), df)
                return df
        key = (symbol, tf)
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < 30:
                return hit[1]
        src = SOURCE_TF[tf]
        base = self.db.load_candles(symbol, src)
        df = base if tf == src else resample(base, tf)
        complete = None
        if tf != src and not df.empty and not base.empty:
            # ostatnia świeca wyższego TF jest pełna dopiero, gdy są wszystkie świece składowe
            src_end = base.index[-1] + pd.Timedelta(minutes=tf_minutes(src))
            htf_end = df.index[-1] + pd.Timedelta(minutes=tf_minutes(tf))
            complete = bool(src_end >= htf_end)
        if tf != src:
            direct = self.db.load_candles(symbol, tf)   # np. zaimportowane 4H/15M z CSV
            if not direct.empty:
                df = df.combine_first(direct) if not df.empty else direct
        if complete is not None:
            df.attrs["last_complete"] = complete
        with self._lock:
            self._cache[key] = (time.time(), df)
        return df

    def import_csv(self, symbol: str, tf: str, content: bytes, tz: str = "UTC") -> int:
        get_instrument(symbol)
        tf_minutes(tf)
        df = parse_csv(content, tz=tz)
        n = self.db.upsert_candles(symbol, tf, df)
        with self._lock:
            for key in list(self._cache):
                if key[0] == symbol:
                    self._cache.pop(key, None)
        return n

    # ------------------------------------------------------------------ fx
    def fx_rates(self) -> dict:
        if time.time() - self._fx_ts > 3600:
            live = {}
            for pair in DEFAULT_FX:
                v = self.provider.fetch_fx(pair)
                if v:
                    live[pair] = v
            self._fx = live
            self._fx_ts = time.time()
        rates = {**DEFAULT_FX, **self._fx}
        return {"rates": rates, "live": sorted(self._fx), "defaults_used": sorted(set(DEFAULT_FX) - set(self._fx))}

    def data_status(self) -> dict:
        stats = {(r["symbol"], r["tf"]): r for r in self.db.candle_stats()}
        out = []
        for inst in INSTRUMENTS:
            row = {"symbol": inst.symbol, "proxy_note": inst.proxy_note,
                   "source_symbol": getattr(self.provider, "source_symbol", lambda s: None)(inst.symbol),
                   "fetch": self.status.get(inst.symbol)}
            for tf in ("5M", "1H"):
                s = stats.get((inst.symbol, tf))
                row[tf] = ({"bars": s["n"], "from": _iso_ts(s["first"]), "to": _iso_ts(s["last"])} if s else None)
            out.append(row)
        return {"provider": self.provider.name, "provider_description": self.provider.describe(), "instruments": out}


def _iso(ts: pd.Timestamp) -> str:
    return ts.isoformat()


def _iso_ts(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
