"""Rejestr instrumentów — WYŁĄCZNIE symbole dostępne na XTB.

Dodanie nowego instrumentu = dopisanie jednego wpisu `Instrument(...)` do listy
`INSTRUMENTS`. Reszta aplikacji (scanner, dashboard, backtest) pobiera listę stąd.

UWAGA: parametry kontraktu (wartość punktu, spread) to wartości domyślne do
kalkulatora i backtestu. Przed użyciem porównaj je z tabelą specyfikacji
instrumentów w xStation (Specyfikacja instrumentu) — XTB może je zmieniać.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Instrument:
    symbol: str                 # symbol XTB (tak jak w xStation)
    name: str
    category: str               # "index" | "commodity"
    currency: str               # waluta kwotowania / wyceny punktu
    digits: int                 # liczba miejsc po przecinku do wyświetlania
    value_per_point: float      # wartość zmiany ceny o 1.0 dla 1 lota (w walucie `currency`)
    spread: float               # typowy spread w jednostkach ceny (do kosztów backtestu)
    min_lot: float = 0.01
    lot_step: float = 0.01
    # Symbole u dostawców danych. Pierwszy działający jest używany.
    provider_symbols: dict[str, list[str]] = field(default_factory=dict)
    # Czy dane dostawcy są proxy (futures/cash), a nie ceną CFD XTB
    proxy_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


INSTRUMENTS: list[Instrument] = [
    # ---------------- Indeksy ----------------
    Instrument("US100", "Nasdaq 100", "index", "USD", 2, 20.0, 1.0,
               provider_symbols={"yahoo": ["NQ=F", "^NDX"]},
               proxy_note="Yahoo: kontrakt futures E-mini Nasdaq (NQ). CFD XTB może różnić się o bazę."),
    Instrument("US500", "S&P 500", "index", "USD", 2, 50.0, 0.5,
               provider_symbols={"yahoo": ["ES=F", "^GSPC"]},
               proxy_note="Yahoo: futures E-mini S&P 500 (ES)."),
    Instrument("US30", "Dow Jones 30", "index", "USD", 1, 5.0, 2.0,
               provider_symbols={"yahoo": ["YM=F", "^DJI"]},
               proxy_note="Yahoo: futures E-mini Dow (YM)."),
    Instrument("US2000", "Russell 2000", "index", "USD", 2, 50.0, 0.3,
               provider_symbols={"yahoo": ["RTY=F", "^RUT"]},
               proxy_note="Yahoo: futures E-mini Russell 2000 (RTY)."),
    Instrument("GER40", "DAX 40", "index", "EUR", 1, 25.0, 1.0,
               provider_symbols={"yahoo": ["^GDAXI"]},
               proxy_note="Yahoo: indeks kasowy DAX (tylko sesja Xetra)."),
    Instrument("UK100", "FTSE 100", "index", "GBP", 1, 10.0, 1.0,
               provider_symbols={"yahoo": ["^FTSE"]},
               proxy_note="Yahoo: indeks kasowy FTSE 100 (tylko sesja LSE)."),
    Instrument("JAP225", "Nikkei 225", "index", "JPY", 0, 1000.0, 8.0,
               provider_symbols={"yahoo": ["NIY=F", "^N225"]},
               proxy_note="Yahoo: futures Nikkei (JPY) lub indeks kasowy."),
    # ---------------- Surowce ----------------
    Instrument("GOLD", "Złoto", "commodity", "USD", 2, 100.0, 0.35,
               provider_symbols={"yahoo": ["GC=F"]},
               proxy_note="Yahoo: futures COMEX Gold (GC)."),
    Instrument("SILVER", "Srebro", "commodity", "USD", 3, 5000.0, 0.03,
               provider_symbols={"yahoo": ["SI=F"]},
               proxy_note="Yahoo: futures COMEX Silver (SI)."),
    Instrument("OIL.WTI", "Ropa WTI", "commodity", "USD", 2, 1000.0, 0.03,
               provider_symbols={"yahoo": ["CL=F"]},
               proxy_note="Yahoo: futures NYMEX WTI (CL)."),
]

_BY_SYMBOL = {i.symbol: i for i in INSTRUMENTS}


def get_instrument(symbol: str) -> Instrument:
    try:
        return _BY_SYMBOL[symbol.upper()]
    except KeyError as exc:
        raise KeyError(f"Nieznany instrument: {symbol}. Dostępne: {', '.join(_BY_SYMBOL)}") from exc


def all_symbols() -> list[str]:
    return [i.symbol for i in INSTRUMENTS]
