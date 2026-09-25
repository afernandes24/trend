"""Adapter XTB — miejsce na podłączenie danych bezpośrednio z XTB.

Stan na moment tworzenia aplikacji: XTB wycofało publiczne xAPI (WebSocket)
w marcu 2025 r., więc nie ma oficjalnego API do pobierania świec. Ten plik jest
gotowym punktem podłączenia, gdy dostępne będzie źródło z cenami XTB
(np. nowe API, własny eksport z xStation, zewnętrzny feed CFD).

Aby podłączyć źródło:
  1. zaimplementuj `fetch_candles()` tak, by zwracało DataFrame
     [open, high, low, close, volume] z indeksem UTC (początek świecy),
  2. ustaw w `.env`:  MARKET_DATA_PROVIDER=xtb
  3. (opcjonalnie) zaimplementuj `fetch_fx()` dla przeliczeń w kalkulatorze.

Symbole XTB są 1:1 z rejestrem w `instruments.py` (US100, GER40, GOLD, OIL.WTI, ...).
Aplikacja NIGDY nie składa zleceń — adapter służy wyłącznie do odczytu cen.
"""
from __future__ import annotations

import pandas as pd

from ..config import settings
from .base import MarketDataError, MarketDataProvider


class XtbProvider(MarketDataProvider):
    name = "xtb"
    native_timeframes = ("5M", "1H")

    def __init__(self):
        self.user = settings.xtb_user
        self.mode = settings.xtb_mode

    def describe(self) -> str:
        return "XTB (adapter — wymaga podłączenia źródła)"

    def fetch_candles(self, symbol: str, tf: str, full_history: bool = True) -> pd.DataFrame:
        # Przykładowy szkielet:
        #   raw = my_xtb_client.get_chart(symbol=symbol, period=tf_minutes(tf), start=...)
        #   df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
        #   df.index = pd.to_datetime(df.pop("time"), unit="ms", utc=True)
        #   return normalize(df)
        raise MarketDataError(
            "Adapter XTB nie jest podłączony (publiczne xAPI XTB zostało wyłączone). "
            "Użyj MARKET_DATA_PROVIDER=yahoo lub csv, albo zaimplementuj fetch_candles() w market_data/xtb.py."
        )
