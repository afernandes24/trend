"""Backtesting Engine.

Zasady symulacji (konserwatywne, bez zaglądania w przyszłość):
  * sygnał powstaje na ZAMKNIĘCIU świecy t (ten sam score co w skanerze),
  * wejście po cenie OPEN świecy t+1 (LONG kupuje po ask = open + spread),
  * SL wyznaczany ze struktury do świecy t, TP = wielokrotności faktycznego ryzyka,
  * jeśli w tej samej świecy dotknięto SL i TP — liczymy SL (najgorszy przypadek),
  * luka cenowa przez SL -> wyjście po cenie open (poślizg),
  * jedna pozycja naraz; sygnały w trakcie pozycji są ignorowane,
  * tryb `single`: całość na TP = rr_target;
    tryb `scaled`: 1/3 na TP1, TP2, TP3, po TP1 SL przesuwany na BE.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..instruments import Instrument
from ..strategy.engine import Prepared, prepare
from ..strategy.levels import compute_levels
from ..strategy.params import StrategyParams
from ..strategy.scoring import signal_events
from .metrics import compute_metrics, equity_curve


def _first_ready(pr: Prepared) -> int:
    ok = pr.scores["long_score"].notna().values
    idx = np.argmax(ok) if ok.any() else len(ok)
    return int(idx)


def simulate(pr: Prepared, p: StrategyParams, spread: float, i_start: int, i_end: int,
             events: pd.Series | None = None) -> list[dict]:
    """Symulacja transakcji dla sygnałów w [i_start, i_end). Pozycje zamykane najpóźniej na i_end-1."""
    ind = pr.ind
    ev = (events if events is not None else signal_events(pr.scores, p.min_score)).values
    o, h, l, c = (ind[k].values for k in ("open", "high", "low", "close"))
    times = ind.index
    sp = spread if p.use_spread else 0.0
    trades: list[dict] = []
    i = max(i_start, _first_ready(pr))
    i_end = min(i_end, len(ind))
    while i < i_end - 1:
        if ev[i] == 0:
            i += 1
            continue
        long = ev[i] > 0
        direction = "LONG" if long else "SHORT"
        j = i + 1
        fill = o[j] + sp if long else o[j]
        lv = compute_levels(ind, i, direction, p, entry=fill)
        if lv is None:
            i += 1
            continue
        sgn = 1 if long else -1
        risk = lv["risk"]
        sl = lv["sl"]
        if p.exit_mode == "single":
            targets = [(1.0, fill + sgn * p.rr_target * risk)]
        else:
            targets = [(1 / 3, lv["tp1"]), (1 / 3, lv["tp2"]), (1 / 3, lv["tp3"])]
        remaining = 1.0
        realized = 0.0
        hit_tp = 0
        exit_reason, k = None, j
        exit_price = None
        for k in range(j, i_end):
            # ceny, po których realnie zamykamy: LONG -> bid (dane), SHORT -> ask (dane + spread)
            lo_x = l[k] if long else l[k] + sp
            hi_x = h[k] if long else h[k] + sp
            op_x = o[k] if long else o[k] + sp
            # 1) Stop Loss (najpierw — konserwatywnie)
            sl_hit = (lo_x <= sl) if long else (hi_x >= sl)
            if sl_hit:
                px = min(sl, op_x) if long else max(sl, op_x)
                realized += remaining * sgn * (px - fill) / risk
                remaining = 0.0
                exit_reason = "BE" if hit_tp and abs(sl - fill) < 1e-12 else "SL"
                exit_price = px
                break
            # 2) Take Profit(y)
            while targets and ((hi_x >= targets[0][1]) if long else (lo_x <= targets[0][1])):
                frac, tp = targets.pop(0)
                px = max(tp, op_x) if long else min(tp, op_x)
                realized += frac * sgn * (px - fill) / risk
                remaining -= frac
                hit_tp += 1
                exit_price = px
                if p.exit_mode == "scaled" and hit_tp == 1:
                    sl = fill                                  # SL na break-even
            if remaining <= 1e-9:
                exit_reason = "TP" if p.exit_mode == "single" else "TP3"
                break
            # 3) limit czasu
            if p.max_bars_in_trade and (k - j + 1) >= p.max_bars_in_trade:
                px = c[k] if long else c[k] + sp
                realized += remaining * sgn * (px - fill) / risk
                remaining, exit_reason, exit_price = 0.0, "CZAS", px
                break
        if remaining > 1e-9:          # koniec danych/okresu
            px = c[k] if long else c[k] + sp
            realized += remaining * sgn * (px - fill) / risk
            exit_reason, exit_price = "KONIEC OKRESU", px
        if exit_reason in ("SL", "BE") and hit_tp:
            exit_reason = f"TP{hit_tp}→{exit_reason}"
        score = float(pr.scores[f"{'long' if long else 'short'}_score"].iloc[i])
        trades.append({
            "direction": direction,
            "signal_time": times[i].isoformat(),
            "entry_time": times[j].isoformat(), "entry_ts": int(times[j].timestamp()),
            "exit_time": times[k].isoformat(), "exit_ts": int(times[k].timestamp()),
            "entry": float(fill), "sl": float(lv["sl"]), "tp1": float(lv["tp1"]), "tp2": float(lv["tp2"]),
            "tp3": float(lv["tp3"]), "exit": float(exit_price), "score": int(score),
            "r": round(float(realized), 4), "pnl_points": float(realized * risk),
            "risk_points": float(risk), "bars_held": int(k - j + 1), "exit_reason": exit_reason,
        })
        i = k + 1
    return trades


def index_range(ind: pd.DataFrame, date_from: str | None, date_to: str | None) -> tuple[int, int]:
    idx = ind.index
    s = 0 if not date_from else int(idx.searchsorted(pd.Timestamp(date_from, tz="UTC")))
    e = len(idx) if not date_to else int(idx.searchsorted(pd.Timestamp(date_to, tz="UTC") + pd.Timedelta(days=1)))
    return s, e


def run_backtest(df: pd.DataFrame, tf: str, p: StrategyParams, inst: Instrument,
                 htf_frames: dict[str, pd.DataFrame] | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 split: float | None = None, capital: float = 10_000.0, risk_pct: float = 1.0,
                 prepared: Prepared | None = None) -> dict:
    """Backtest pełny + opcjonalny podział TRAINING / TESTING (split = udział części treningowej)."""
    if df.empty or len(df) < p.sma_slow + 50:
        raise ValueError(f"Za mało danych do backtestu ({len(df)} świec). Pobierz dane lub zaimportuj CSV.")
    pr = prepared or prepare(df, tf, p, htf_frames)
    s, e = index_range(pr.ind, date_from, date_to)
    s = max(s, _first_ready(pr))
    if e - s < 50:
        raise ValueError("Wybrany okres jest zbyt krótki (po odliczeniu rozgrzewki wskaźników).")
    ev = signal_events(pr.scores, p.min_score)
    out: dict = {"symbol": inst.symbol, "tf": tf, "params": p.to_dict(), "spread": inst.spread,
                 "bars": e - s, "from": pr.ind.index[s].isoformat(), "to": pr.ind.index[e - 1].isoformat(),
                 "capital": capital, "risk_pct": risk_pct}
    trades = simulate(pr, p, inst.spread, s, e, ev)
    out["all"] = compute_metrics(trades, capital, risk_pct)
    out["equity"] = equity_curve(trades, capital, risk_pct)
    out["trades"] = trades
    if split and 0.1 <= split <= 0.9:
        cut = s + int((e - s) * split)
        tr_train = simulate(pr, p, inst.spread, s, cut, ev)
        tr_test = simulate(pr, p, inst.spread, cut, e, ev)
        out["split"] = {
            "ratio": split,
            "cut_time": pr.ind.index[cut].isoformat(),
            "train": {**compute_metrics(tr_train, capital, risk_pct), "from": out["from"],
                      "to": pr.ind.index[cut - 1].isoformat()},
            "test": {**compute_metrics(tr_test, capital, risk_pct), "from": pr.ind.index[cut].isoformat(),
                     "to": out["to"]},
            "equity_train": equity_curve(tr_train, capital, risk_pct),
            "equity_test": equity_curve(tr_test, capital, risk_pct),
        }
    return out

