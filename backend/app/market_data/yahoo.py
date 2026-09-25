"""Adapter Yahoo Finance (publiczne API wykresów v8).

Ograniczenia Yahoo:
  * 5M  – maks. ok. 60 dni wstecz
  * 1H  – maks. ok. 730 dni wstecz
  * dane mają kilkuminutowe opóźnienie dla części rynków
  * to NIE są ceny CFD z XTB, tylko najbliższy odpowiednik (futures / indeks kasowy)

Do dłuższych backtestów zaimportuj własne pliki CSV (patrz csv_provider.py).
"""
from __future__ import annotations

import logging

import httpx
import pandas as pd

from ..config import settings
from ..instruments import get_instrument
from .base import MarketDataError, MarketDataProvider, normalize

log = logging.getLogger(__name__)

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}
# interwał aplikacji -> (interwał Yahoo, zakres pełny, zakres przyrostowy)
_TF_MAP = {"5M": ("5m", "60d", "2d"), "1H": ("60m", "730d", "10d"), "1D": ("1d", "10y", "1mo")}


_INTERVAL_SEC = {"5m": 300, "60m": 3600, "1d": 86400}


def drop_incomplete_last(df: pd.DataFrame, interval: str, last_trade_ts: int | None,
                         now: float | None = None, grace: int = 20 * 60) -> pd.DataFrame:
    """Usuwa ostatnią świecę, jeśli jeszcze się formuje (dane Yahoo bywają opóźnione).

    Świeca jest niepełna, gdy jej koniec jest późniejszy niż czas ostatniej transakcji
    w danych, a rynek jest aktywny (ostatnia transakcja niedawno). Po zamknięciu rynku
    (np. weekend) ostatnia świeca zostaje.
    """
    if df.empty or not last_trade_ts or interval not in _INTERVAL_SEC:
        return df
    import time as _t
    now = now or _t.time()
    end = df.index[-1].timestamp() + _INTERVAL_SEC[interval]
    if end > last_trade_ts and now < end + grace:
        return df.iloc[:-1]
    return df


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    native_timeframes = ("5M", "1H", "1D")

    def __init__(self, timeout: float | None = None, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=timeout or settings.http_timeout, headers=_HEADERS,
                                              follow_redirects=True)
        self._resolved: dict[str, str] = {}   # symbol XTB -> działający symbol Yahoo

    def describe(self) -> str:
        return "Yahoo Finance (proxy: futures / indeksy kasowe)"

    def _download(self, ysym: str, interval: str, rng: str) -> pd.DataFrame:
        r = self._client.get(_BASE.format(sym=ysym),
                             params={"interval": interval, "range": rng, "includePrePost": "false"})
        if r.status_code != 200:
            raise MarketDataError(f"Yahoo {ysym} HTTP {r.status_code}")
        payload = r.json().get("chart", {})
        if payload.get("error"):
            raise MarketDataError(f"Yahoo {ysym}: {payload['error']}")
        res = (payload.get("result") or [None])[0]
        if not res or not res.get("timestamp"):
            raise MarketDataError(f"Yahoo {ysym}: brak danych")
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame(
            {k: q.get(k) for k in ("open", "high", "low", "close", "volume")},
            index=pd.to_datetime(res["timestamp"], unit="s", utc=True),
        )
        df = normalize(df)
        return drop_incomplete_last(df, interval, (res.get("meta") or {}).get("regularMarketTime"))

    def fetch_candles(self, symbol: str, tf: str, full_history: bool = True) -> pd.DataFrame:
        if tf not in _TF_MAP:
            raise MarketDataError(f"Yahoo: interwał {tf} nieobsługiwany bezpośrednio")
        inst = get_instrument(symbol)
        candidates = inst.provider_symbols.get("yahoo", [])
        if symbol in self._resolved:
            candidates = [self._resolved[symbol]] + [c for c in candidates if c != self._resolved[symbol]]
        interval, full_rng, inc_rng = _TF_MAP[tf]
        errors = []
        for ysym in candidates:
            try:
                df = self._download(ysym, interval, full_rng if full_history else inc_rng)
                if df.empty:
                    raise MarketDataError("pusty wynik")
                self._resolved[symbol] = ysym
                return df
            except (httpx.HTTPError, MarketDataError, ValueError, KeyError) as exc:
                errors.append(f"{ysym}: {exc}")
        raise MarketDataError(f"{symbol}: nie udało się pobrać danych ({'; '.join(errors)})")

    def fetch_fx(self, pair: str) -> float | None:
        try:
            df = self._download(f"{pair}=X", "1d", "5d")
            return float(df["close"].iloc[-1]) if not df.empty else None
        except Exception as exc:  # noqa: BLE001
            log.warning("FX %s niedostępny: %s", pair, exc)
            return None

    def source_symbol(self, symbol: str) -> str | None:
        return self._resolved.get(symbol)
