"""Wspólny interfejs warstwy Market Data.

Każdy dostawca danych implementuje `MarketDataProvider.fetch_candles()` i zwraca
DataFrame z kolumnami: open, high, low, close, volume, indeksowany czasem UTC
(początek świecy, tz-aware). Nic więcej nie jest potrzebne, żeby podłączyć nowe
źródło (np. XTB, broker API, płatny feed).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

# Interwały widoczne w aplikacji -> długość w minutach
TIMEFRAMES: dict[str, int] = {"5M": 5, "15M": 15, "1H": 60, "4H": 240, "1D": 1440}
UI_TIMEFRAMES = ["5M", "15M", "1H", "4H"]

# Z jakiego interwału źródłowego budujemy dany interwał (resampling).
# Pobieramy tylko 5M i 1H — mniej zapytań do dostawcy, spójne dane.
SOURCE_TF: dict[str, str] = {"5M": "5M", "15M": "5M", "1H": "1H", "4H": "1H", "1D": "1H"}

# Interwały wyższe używane jako kontekst trendu (MTF) dla danego interwału
HIGHER_TFS: dict[str, list[str]] = {
    "5M": ["15M", "1H"],
    "15M": ["1H", "4H"],
    "1H": ["4H", "1D"],
    "4H": ["1D"],
}

COLUMNS = ["open", "high", "low", "close", "volume"]


def tf_minutes(tf: str) -> int:
    if tf not in TIMEFRAMES:
        raise ValueError(f"Nieobsługiwany interwał {tf}. Dostępne: {', '.join(TIMEFRAMES)}")
    return TIMEFRAMES[tf]


def empty_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=COLUMNS, dtype=float)
    df.index = pd.DatetimeIndex([], tz="UTC", name="time")
    return df


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Sprząta dane: UTC, sortowanie, bez duplikatów, bez pustych świec."""
    if df is None or df.empty:
        return empty_frame()
    out = df.copy()
    out.columns = [c.lower() for c in out.columns]
    for c in COLUMNS:
        if c not in out.columns:
            out[c] = 0.0 if c == "volume" else float("nan")
    out = out[COLUMNS].astype(float)
    idx = pd.DatetimeIndex(out.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out.index = idx.rename("time")
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["volume"] = out["volume"].fillna(0.0)
    # Naprawa ewentualnie niespójnych świec (high < max(open, close) itp.)
    out["high"] = out[["high", "open", "close"]].max(axis=1)
    out["low"] = out[["low", "open", "close"]].min(axis=1)
    return out


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Buduje świece wyższego interwału z niższego. Etykieta = początek świecy (UTC)."""
    if df.empty:
        return empty_frame()
    rule = f"{tf_minutes(tf)}min"
    agg = df.resample(rule, label="left", closed="left", origin="epoch").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["open", "high", "low", "close"])


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(ABC):
    """Interfejs dostawcy danych rynkowych."""

    name: str = "base"
    #: interwały, które dostawca potrafi zwrócić bezpośrednio
    native_timeframes: tuple[str, ...] = ("5M", "1H")

    @abstractmethod
    def fetch_candles(self, symbol: str, tf: str, full_history: bool = True) -> pd.DataFrame:
        """Zwraca świece dla symbolu XTB i interwału (`5M` lub `1H`).

        full_history=True  -> możliwie długa historia (pierwsze ładowanie)
        full_history=False -> tylko ostatnie dni (aktualizacja przyrostowa)
        """

    def fetch_fx(self, pair: str) -> float | None:  # np. "USDPLN"
        return None

    def describe(self) -> str:
        return self.name


def epoch_seconds(idx: pd.DatetimeIndex) -> list[int]:
    """Znaczniki czasu w sekundach UTC — niezależnie od rozdzielczości indeksu (ns/us/ms)."""
    return (idx.as_unit("s").asi8).tolist() if len(idx) else []
