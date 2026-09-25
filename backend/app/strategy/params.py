"""Parametry strategii — jedno źródło prawdy dla scannera, wykresu i backtestu."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields


@dataclass
class StrategyParams:
    # Średnie kroczące
    sma_fast: int = 20
    sma_mid: int = 50
    sma_slow: int = 200
    # Oscylatory / siła trendu
    rsi_period: int = 14
    rsi_long_min: float = 50.0     # strefa RSI zgodna z trendem wzrostowym
    rsi_long_max: float = 70.0
    rsi_short_min: float = 30.0    # strefa RSI zgodna z trendem spadkowym
    rsi_short_max: float = 50.0
    atr_period: int = 14
    adx_period: int = 14
    adx_min: float = 20.0          # minimalna siła trendu (warunek konieczny)
    adx_strong: float = 25.0       # silny trend (pełne punkty)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    momentum_period: int = 10
    swing_bars: int = 3            # świece z każdej strony, by uznać swing high/low
    # Sygnał
    min_score: int = 75
    require_mtf: bool = True       # czy wyższe interwały muszą być zgodne (skaner/alerty)
    # Stop Loss
    sl_atr_buffer: float = 0.3     # bufor ATR za swingiem
    sl_min_atr: float = 0.8        # minimalna odległość SL (w ATR)
    sl_max_atr: float = 3.0        # maksymalna odległość SL (w ATR)
    sl_swing_lookback: int = 30    # ile świec wstecz szukamy swingu
    # Take Profit (wielokrotności R)
    tp_r: list[float] = field(default_factory=lambda: [1.5, 2.0, 3.0])
    # Backtest
    rr_target: float = 2.0         # wyjście w trybie "single"
    exit_mode: str = "single"      # single | scaled (1/3 na TP1/TP2/TP3, SL na BE po TP1)
    max_bars_in_trade: int = 0     # 0 = bez limitu
    use_spread: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "StrategyParams":
        d = d or {}
        names = {f.name for f in fields(cls)}
        clean = {k: v for k, v in d.items() if k in names and v is not None}
        p = cls(**clean)
        p.validate()
        return p

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> None:
        if not (2 <= self.sma_fast < self.sma_mid < self.sma_slow <= 400):
            raise ValueError("Wymagane: 2 ≤ SMA szybka < SMA średnia < SMA wolna ≤ 400")
        for name in ("rsi_period", "atr_period", "adx_period", "momentum_period"):
            if not 2 <= getattr(self, name) <= 100:
                raise ValueError(f"{name} poza zakresem 2–100")
        if not 0 <= self.min_score <= 100:
            raise ValueError("min_score musi być w zakresie 0–100")
        if self.exit_mode not in ("single", "scaled"):
            raise ValueError("exit_mode: single | scaled")
        if not (0 < self.sl_min_atr <= self.sl_max_atr):
            raise ValueError("Wymagane: 0 < sl_min_atr ≤ sl_max_atr")
        if len(self.tp_r) != 3 or any(r <= 0 for r in self.tp_r):
            raise ValueError("tp_r: trzy dodatnie wartości (np. 1.5, 2, 3)")
        self.tp_r = sorted(float(r) for r in self.tp_r)
        if self.rr_target <= 0:
            raise ValueError("Risk/Reward musi być > 0")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("MACD: szybka EMA musi być krótsza od wolnej")

    def key(self) -> str:
        return hashlib.sha1(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]

    def indicator_key(self) -> tuple:
        """Parametry, od których zależą wskaźniki (cache)."""
        return (self.sma_fast, self.sma_mid, self.sma_slow, self.rsi_period, self.atr_period,
                self.adx_period, self.macd_fast, self.macd_slow, self.macd_signal,
                self.momentum_period, self.swing_bars)
