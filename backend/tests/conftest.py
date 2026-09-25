"""Fixture'y testowe.

Testy nie mają dostępu do internetu, więc używamy PRAWDZIWYCH historycznych danych
EURUSD 1H dołączonych do pakietu `backtesting` (5000 świec, 2017–2018).
Na potrzeby testów przesuwamy znaczniki czasu tak, by dane kończyły się „teraz",
i skalujemy poziom ceny do instrumentu. To wyłącznie fixture testowy — aplikacja
w normalnym trybie pobiera dane z prawdziwego źródła (Yahoo / CSV / adapter).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Database  # noqa: E402
from app.instruments import get_instrument  # noqa: E402
from app.market_data.base import MarketDataProvider, normalize  # noqa: E402

LEVEL = {"US100": 24000, "US500": 6500, "US30": 45000, "US2000": 2300, "GER40": 23500, "UK100": 9200,
         "JAP225": 44000, "GOLD": 3600, "SILVER": 42, "OIL.WTI": 64}


def eurusd() -> pd.DataFrame:
    from backtesting.test import EURUSD
    return normalize(EURUSD.rename(columns=str.lower))


class FixtureProvider(MarketDataProvider):
    """Dostawca testowy: rzeczywisty kształt ceny EURUSD, przeskalowany i przesunięty w czasie."""
    name = "fixture"

    def __init__(self, end: pd.Timestamp | None = None):
        self.base = eurusd()
        self.end = end or pd.Timestamp.now(tz="UTC").floor("5min") - pd.Timedelta(minutes=5)

    def fetch_candles(self, symbol: str, tf: str, full_history: bool = True) -> pd.DataFrame:
        get_instrument(symbol)
        seed = sum(map(ord, symbol))
        df = self.base.iloc[: len(self.base) - (seed * 37) % 1500].copy()
        if symbol in ("GOLD", "US2000", "JAP225"):
            # odwrócona seria — żeby w testach były też trendy spadkowe
            df[["open", "high", "low", "close"]] = 2 * df["close"].mean() - df[["open", "low", "high", "close"]].values
        scale = LEVEL[symbol] / df["close"].iloc[-1]
        df[["open", "high", "low", "close"]] *= scale
        step = pd.Timedelta(minutes=5 if tf == "5M" else 60)
        end = self.end if tf == "5M" else self.end.floor("h") - pd.Timedelta(hours=1)
        df.index = pd.DatetimeIndex([end - step * k for k in range(len(df) - 1, -1, -1)])
        return normalize(df)

    def fetch_fx(self, pair):
        return None


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def fixture_provider():
    return FixtureProvider()
