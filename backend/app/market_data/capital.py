"""Adapter Capital.com (REST API) — ceny CFD na żywo.

Wymaga (Railway → Variables albo .env):
    MARKET_DATA_PROVIDER=capital
    CAPITAL_API_KEY=...          (Ustawienia → API integrations → Generate new key)
    CAPITAL_IDENTIFIER=...       (e-mail / login do Capital.com)
    CAPITAL_API_PASSWORD=...     (hasło ustawione przy generowaniu klucza)
    CAPITAL_ENV=demo | live
    CAPITAL_EPICS=US2000=RTY;JAP225=J225   (opcjonalnie — ręczne nazwy instrumentów)

Ceny: BID (tak jak wykresy w xStation). Świece 5M i 1H, reszta interwałów z resamplingu.
Ostatnia świeca może się jeszcze formować — analiza i tak używa tylko zamkniętych świec.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from ..config import settings
from ..instruments import get_instrument
from .base import MarketDataError, MarketDataProvider, normalize

log = logging.getLogger(__name__)

URLS = {"demo": "https://demo-api-capital.backend-capital.com",
        "live": "https://api-capital.backend-capital.com"}

# symbol XTB -> (kandydaci „epic" w Capital.com, fraza do wyszukiwarki)
EPICS: dict[str, tuple[list[str], str]] = {
    "US100": (["US100"], "US Tech 100"),
    "US500": (["US500"], "US 500"),
    "US30": (["US30"], "US Wall Street 30"),
    "US2000": (["US2000", "RTY"], "Russell 2000"),
    "GER40": (["DE40", "GER40"], "Germany 40"),
    "UK100": (["UK100"], "UK 100"),
    "JAP225": (["J225", "JP225", "JPN225"], "Japan 225"),
    "GOLD": (["GOLD"], "Gold"),
    "SILVER": (["SILVER"], "Silver"),
    "OIL.WTI": (["OIL_CRUDE", "USOIL", "WTI"], "US Crude Oil"),
}
RESOLUTION = {"5M": ("MINUTE_5", 5), "1H": ("HOUR", 60), "1D": ("DAY", 1440)}
MAX_BARS = 1000                       # limit API na jedno zapytanie
FULL_PAGES = {"5M": 6, "1H": 5, "1D": 2}   # ile stron historii przy pierwszym pobraniu


def _parse_overrides(raw: str) -> dict[str, str]:
    out = {}
    for part in (raw or "").replace(",", ";").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip().upper()] = v.strip()
    return out


class CapitalProvider(MarketDataProvider):
    name = "capital"
    native_timeframes = ("5M", "1H", "1D")

    def __init__(self, client: httpx.Client | None = None, env: str | None = None,
                 api_key: str | None = None, identifier: str | None = None, password: str | None = None):
        self.env = (env or os.getenv("CAPITAL_ENV", "demo")).lower()
        if self.env not in URLS:
            raise MarketDataError("CAPITAL_ENV musi być: demo albo live")
        self.base = URLS[self.env]
        self.api_key = api_key if api_key is not None else os.getenv("CAPITAL_API_KEY", "")
        self.identifier = identifier if identifier is not None else os.getenv("CAPITAL_IDENTIFIER", "")
        self.password = password if password is not None else os.getenv("CAPITAL_API_PASSWORD", "")
        self._client = client or httpx.Client(timeout=settings.http_timeout)
        self._tokens: dict[str, str] | None = None
        self._login_lock = threading.Lock()
        self._resolved: dict[str, str] = dict(_parse_overrides(os.getenv("CAPITAL_EPICS", "")))
        self._last_call = 0.0

    def describe(self) -> str:
        return f"Capital.com ({self.env}) — ceny CFD na żywo"

    def missing(self) -> list[str]:
        req = {"CAPITAL_API_KEY": self.api_key, "CAPITAL_IDENTIFIER": self.identifier,
               "CAPITAL_API_PASSWORD": self.password}
        return [k for k, v in req.items() if not v]

    # ------------------------------------------------------------ http
    def _login(self) -> None:
        if self.missing():
            raise MarketDataError(f"Capital.com: brak zmiennych {', '.join(self.missing())}")
        with self._login_lock:
            r = self._client.post(f"{self.base}/api/v1/session",
                                  headers={"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"},
                                  json={"identifier": self.identifier, "password": self.password,
                                        "encryptedPassword": False})
            if r.status_code != 200:
                raise MarketDataError(f"Capital.com: logowanie nieudane (HTTP {r.status_code}: {_err(r)})")
            cst, sec = r.headers.get("CST"), r.headers.get("X-SECURITY-TOKEN")
            if not cst or not sec:
                raise MarketDataError("Capital.com: brak tokenów sesji w odpowiedzi")
            self._tokens = {"CST": cst, "X-SECURITY-TOKEN": sec}

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        if self._tokens is None:
            self._login()
        # limit API: 10 zapytań / s
        wait = 0.12 - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()
        r = self._client.get(f"{self.base}{path}", params=params, headers=self._tokens)
        if r.status_code == 401:            # sesja wygasła (10 min bez aktywności) — logujemy ponownie
            self._login()
            r = self._client.get(f"{self.base}{path}", params=params, headers=self._tokens)
        return r

    # ------------------------------------------------------------ instruments
    def resolve_epic(self, symbol: str) -> str:
        if symbol in self._resolved:
            return self._resolved[symbol]
        candidates, term = EPICS.get(symbol, ([symbol], symbol))
        for epic in candidates:
            r = self._get(f"/api/v1/prices/{epic}", {"resolution": "HOUR", "max": 1})
            if r.status_code == 200 and r.json().get("prices") is not None:
                self._resolved[symbol] = epic
                return epic
        r = self._get("/api/v1/markets", {"searchTerm": term})
        if r.status_code == 200:
            inst = get_instrument(symbol)
            want = {"index": "INDICES", "commodity": "COMMODITIES"}.get(inst.category)
            markets = r.json().get("markets") or []
            pick = next((m for m in markets if m.get("instrumentType") == want), None) or (markets[0] if markets else None)
            if pick and pick.get("epic"):
                self._resolved[symbol] = pick["epic"]
                log.info("Capital.com: %s -> %s (%s)", symbol, pick["epic"], pick.get("instrumentName"))
                return pick["epic"]
        raise MarketDataError(f"Capital.com: nie znaleziono instrumentu dla {symbol}. "
                              f"Ustaw ręcznie CAPITAL_EPICS, np. {symbol}=EPIC")

    def source_symbol(self, symbol: str) -> str | None:
        return self._resolved.get(symbol)

    # ------------------------------------------------------------ prices
    def _page(self, epic: str, resolution: str, max_bars: int, frm: datetime | None = None,
              to: datetime | None = None) -> pd.DataFrame:
        params: dict = {"resolution": resolution, "max": max_bars}
        if frm is not None and to is not None:
            params["from"] = frm.strftime("%Y-%m-%dT%H:%M:%S")
            params["to"] = to.strftime("%Y-%m-%dT%H:%M:%S")
        r = self._get(f"/api/v1/prices/{epic}", params)
        if r.status_code != 200:
            raise MarketDataError(f"Capital.com {epic}: HTTP {r.status_code} ({_err(r)})")
        return parse_prices(r.json())

    def fetch_candles(self, symbol: str, tf: str, full_history: bool = True) -> pd.DataFrame:
        if tf not in RESOLUTION:
            raise MarketDataError(f"Capital.com: interwał {tf} nieobsługiwany bezpośrednio")
        epic = self.resolve_epic(get_instrument(symbol).symbol)
        res, minutes = RESOLUTION[tf]
        if not full_history:
            return self._page(epic, res, 12)
        frames = [self._page(epic, res, MAX_BARS)]
        span = timedelta(minutes=minutes * (MAX_BARS - 1))
        for _ in range(FULL_PAGES[tf] - 1):
            first = frames[-1]
            if first.empty:
                break
            to = first.index[0].to_pydatetime().astimezone(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)
            try:
                page = self._page(epic, res, MAX_BARS, to - span, to)
            except MarketDataError as exc:
                log.info("Capital.com %s %s: koniec historii (%s)", symbol, tf, exc)
                break
            if page.empty or page.index[0] >= first.index[0]:
                break
            frames.append(page)
        df = pd.concat(frames[::-1])
        return normalize(df)

    def fetch_fx(self, pair: str) -> float | None:
        try:
            df = self._page(pair, "DAY", 1)
            return float(df["close"].iloc[-1]) if not df.empty else None
        except Exception:  # noqa: BLE001
            return None


def _err(r: httpx.Response) -> str:
    try:
        return str(r.json().get("errorCode") or r.text[:120])
    except ValueError:
        return r.text[:120]


def _side(p: dict | None) -> float | None:
    """Cena BID (jak wykres w xStation); gdy brak — środek."""
    if not p:
        return None
    bid, ask = p.get("bid"), p.get("ask")
    if bid is not None:
        return float(bid)
    return float(ask) if ask is not None else None


def parse_prices(payload: dict) -> pd.DataFrame:
    rows, idx = [], []
    for p in payload.get("prices") or []:
        t = p.get("snapshotTimeUTC") or p.get("snapshotTime")
        if not t:
            continue
        o, h, l, c = (_side(p.get(k)) for k in ("openPrice", "highPrice", "lowPrice", "closePrice"))
        if None in (o, h, l, c):
            continue
        idx.append(pd.Timestamp(t, tz="UTC") if pd.Timestamp(t).tzinfo is None else pd.Timestamp(t).tz_convert("UTC"))
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": float(p.get("lastTradedVolume") or 0)})
    if not rows:
        return normalize(pd.DataFrame())
    return normalize(pd.DataFrame(rows, index=pd.DatetimeIndex(idx)))
