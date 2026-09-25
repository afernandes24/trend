"""Import danych z plików CSV.

Użycie:
  * wrzuć plik do `data/import/` o nazwie `<SYMBOL>_<TF>.csv`, np. `US100_5M.csv`,
    `GOLD_1H.csv`, `OIL.WTI_1H.csv`, albo
  * wgraj plik w aplikacji (Ustawienia → Dane → Import CSV).

Akceptowane kolumny (wielkość liter bez znaczenia, separator `,` lub `;`):
  time|date|datetime|timestamp, open, high, low, close, [volume]
Czas: ISO 8601, 'YYYY-MM-DD HH:MM', albo epoch (s/ms). Bez strefy = UTC,
chyba że podasz parametr tz (np. 'Europe/Warsaw' dla eksportów z xStation).
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from ..config import settings
from .base import MarketDataError, MarketDataProvider, normalize

_TIME_COLS = ("time", "date", "datetime", "timestamp", "gmt time", "czas", "data")


def parse_csv(content: bytes | str, tz: str = "UTC") -> pd.DataFrame:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    first = text.splitlines()[0] if text else ""
    sep = ";" if first.count(";") > first.count(",") else ","
    df = pd.read_csv(io.StringIO(text), sep=sep)
    df.columns = [str(c).strip().lower() for c in df.columns]
    tcol = next((c for c in df.columns if c in _TIME_COLS), None)
    if tcol is None:
        raise MarketDataError(f"Brak kolumny czasu. Kolumny: {list(df.columns)}")
    for c in ("open", "high", "low", "close", "volume"):
        if c not in df.columns:
            if c == "volume":
                continue
            raise MarketDataError(f"Brak kolumny '{c}'")
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ".").str.replace(" ", ""), errors="coerce")
    t = df[tcol]
    if pd.api.types.is_numeric_dtype(t):
        unit = "ms" if t.max() > 1e11 else "s"
        idx = pd.to_datetime(t, unit=unit, utc=True)
    else:
        idx = pd.to_datetime(t, dayfirst=False, errors="coerce")
        if getattr(idx.dt, "tz", None) is None:
            idx = idx.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
        idx = idx.dt.tz_convert("UTC")
    df.index = pd.DatetimeIndex(idx)
    df = df[df.index.notna()]
    return normalize(df)


class CsvProvider(MarketDataProvider):
    name = "csv"
    native_timeframes = ("5M", "15M", "1H", "4H", "1D")

    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory or settings.import_dir)

    def describe(self) -> str:
        return f"Pliki CSV ({self.dir})"

    def fetch_candles(self, symbol: str, tf: str, full_history: bool = True) -> pd.DataFrame:
        path = self.dir / f"{symbol}_{tf}.csv"
        if not path.exists():
            raise MarketDataError(f"Brak pliku {path.name} w {self.dir}")
        return parse_csv(path.read_bytes())
