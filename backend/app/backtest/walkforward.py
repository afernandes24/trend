"""Walk-forward testing — ograniczanie overfittingu.

Dane dzielimy na (folds + 1) kolejnych segmentów. W każdym kroku:
  1. na oknie TRENINGOWYM wybieramy najlepszą konfigurację z siatki
     (min_score × Risk/Reward) wg expectancy (min. `min_trades` transakcji),
  2. tę konfigurację stosujemy BEZ ZMIAN na następnym segmencie TESTOWYM (out-of-sample).
Wynik końcowy = wyłącznie połączone transakcje out-of-sample.

Okno: `rolling` (stała długość) lub `anchored` (rośnie od początku danych).
Parametry wskaźników (SMA/RSI/ATR/ADX) są brane z formularza i nie są optymalizowane.
"""
from __future__ import annotations

import itertools
from dataclasses import replace

import pandas as pd

from ..instruments import Instrument
from ..strategy.engine import prepare
from ..strategy.params import StrategyParams
from ..strategy.scoring import signal_events
from .engine import _first_ready, index_range, simulate
from .metrics import compute_metrics, equity_curve

DEFAULT_SCORE_GRID = [60, 65, 70, 75, 80, 85]
DEFAULT_RR_GRID = [1.5, 2.0, 2.5, 3.0]


def run_walkforward(df: pd.DataFrame, tf: str, p: StrategyParams, inst: Instrument,
                    htf_frames: dict[str, pd.DataFrame] | None = None,
                    folds: int = 4, mode: str = "rolling", min_trades: int = 5,
                    score_grid: list[int] | None = None, rr_grid: list[float] | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    capital: float = 10_000.0, risk_pct: float = 1.0) -> dict:
    if not 2 <= folds <= 12:
        raise ValueError("Liczba kroków walk-forward: 2–12")
    if df.empty or len(df) < p.sma_slow + 50:
        raise ValueError(f"Za mało danych ({len(df)} świec).")
    pr = prepare(df, tf, p, htf_frames)
    s, e = index_range(pr.ind, date_from, date_to)
    s = max(s, _first_ready(pr))
    seg = (e - s) // (folds + 1)
    if seg < 100:
        raise ValueError("Za mało świec na segment walk-forward (min. 100). Zmniejsz liczbę kroków lub wydłuż okres.")
    score_grid = score_grid or DEFAULT_SCORE_GRID
    rr_grid = rr_grid or DEFAULT_RR_GRID
    if p.exit_mode == "scaled":
        rr_grid = [p.rr_target]     # w trybie scaled RR nie wpływa na wyjście
    events_cache = {ms: signal_events(pr.scores, ms) for ms in score_grid}

    fold_rows, oos_trades = [], []
    for f in range(1, folds + 1):
        tr_s = s if mode == "anchored" else s + (f - 1) * seg
        tr_e = s + f * seg
        te_s, te_e = tr_e, (s + (f + 1) * seg if f < folds else e)
        best, best_key = None, None
        for ms, rr in itertools.product(score_grid, rr_grid):
            cand = replace(p, min_score=ms, rr_target=rr)
            m = compute_metrics(simulate(pr, cand, inst.spread, tr_s, tr_e, events_cache[ms]), capital, risk_pct)
            if m["trades"] < min_trades:
                continue
            key = (m["expectancy_r"] or -9, m["profit_factor"] or 0, m["trades"])
            if best_key is None or key > best_key:
                best, best_key = (ms, rr, m), key
        if best is None:
            fold_rows.append({"fold": f, "train_from": pr.ind.index[tr_s].isoformat(),
                              "train_to": pr.ind.index[tr_e - 1].isoformat(),
                              "test_from": pr.ind.index[te_s].isoformat(),
                              "test_to": pr.ind.index[te_e - 1].isoformat(),
                              "chosen": None, "note": f"Brak konfiguracji z ≥ {min_trades} transakcjami — pominięto",
                              "train": None, "test": None})
            continue
        ms, rr, m_train = best
        chosen = replace(p, min_score=ms, rr_target=rr)
        t_test = simulate(pr, chosen, inst.spread, te_s, te_e, events_cache[ms])
        oos_trades.extend(t_test)
        fold_rows.append({
            "fold": f,
            "train_from": pr.ind.index[tr_s].isoformat(), "train_to": pr.ind.index[tr_e - 1].isoformat(),
            "test_from": pr.ind.index[te_s].isoformat(), "test_to": pr.ind.index[te_e - 1].isoformat(),
            "chosen": {"min_score": ms, "rr_target": rr},
            "train": _brief(m_train), "test": _brief(compute_metrics(t_test, capital, risk_pct)),
        })
    oos_trades.sort(key=lambda t: t["entry_ts"])
    return {
        "symbol": inst.symbol, "tf": tf, "mode": mode, "folds": fold_rows,
        "score_grid": score_grid, "rr_grid": rr_grid, "min_trades": min_trades,
        "oos": compute_metrics(oos_trades, capital, risk_pct),
        "equity": equity_curve(oos_trades, capital, risk_pct),
        "trades": oos_trades,
    }


def _brief(m: dict) -> dict:
    return {k: m.get(k) for k in ("trades", "win_rate", "expectancy_r", "profit_factor", "total_r", "max_drawdown_r")}
