"""Metryki wyników backtestu. Wynik transakcji liczony w R (wielokrotność ryzyka)."""
from __future__ import annotations

import math

import numpy as np


def _streak(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def _r(x, nd=3):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(float(x), nd)


def compute_metrics(trades: list[dict], initial_capital: float = 10_000.0, risk_pct: float = 1.0) -> dict:
    n = len(trades)
    base = {"trades": n, "period_from": trades[0]["entry_time"] if n else None,
            "period_to": trades[-1]["exit_time"] if n else None}
    if n == 0:
        return {**base, "win_rate": None, "avg_win_r": None, "avg_loss_r": None, "profit_factor": None,
                "expectancy_r": None, "total_r": 0.0, "max_drawdown_r": 0.0, "max_drawdown_pct": 0.0,
                "max_loss_streak": 0, "max_win_streak": 0, "long": _side([]), "short": _side([]),
                "avg_win_points": None, "avg_loss_points": None, "final_equity": initial_capital,
                "return_pct": 0.0, "avg_bars_held": None}
    r = np.array([t["r"] for t in trades], dtype=float)
    pts = np.array([t["pnl_points"] for t in trades], dtype=float)
    wins, losses = r > 0, r < 0
    gross_win, gross_loss = r[wins].sum(), -r[losses].sum()
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd_r = float((peak - cum).max()) if n else 0.0
    eq = initial_capital * np.cumprod(1 + (risk_pct / 100.0) * r)
    eq_peak = np.maximum.accumulate(np.concatenate([[initial_capital], eq]))[1:]
    dd_pct = float(((eq_peak - eq) / eq_peak).max() * 100)
    return {
        **base,
        "win_rate": _r(wins.mean() * 100, 1),
        "avg_win_r": _r(r[wins].mean()) if wins.any() else None,
        "avg_loss_r": _r(r[losses].mean()) if losses.any() else None,
        "avg_win_points": _r(pts[wins].mean(), 5) if wins.any() else None,
        "avg_loss_points": _r(pts[losses].mean(), 5) if losses.any() else None,
        "profit_factor": _r(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy_r": _r(r.mean()),
        "total_r": _r(r.sum(), 2),
        "max_drawdown_r": _r(dd_r, 2),
        "max_drawdown_pct": _r(dd_pct, 2),
        "max_loss_streak": _streak(list(losses)),
        "max_win_streak": _streak(list(wins)),
        "long": _side([t for t in trades if t["direction"] == "LONG"]),
        "short": _side([t for t in trades if t["direction"] == "SHORT"]),
        "final_equity": _r(eq[-1], 2),
        "return_pct": _r((eq[-1] / initial_capital - 1) * 100, 2),
        "avg_bars_held": _r(np.mean([t["bars_held"] for t in trades]), 1),
    }


def _side(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": None, "total_r": 0.0, "expectancy_r": None}
    r = np.array([t["r"] for t in trades], dtype=float)
    return {"trades": len(trades), "win_rate": _r((r > 0).mean() * 100, 1),
            "total_r": _r(r.sum(), 2), "expectancy_r": _r(r.mean())}


def equity_curve(trades: list[dict], initial_capital: float = 10_000.0, risk_pct: float = 1.0) -> list[dict]:
    out, cum, eq = [], 0.0, initial_capital
    for t in trades:
        cum += t["r"]
        eq *= 1 + (risk_pct / 100.0) * t["r"]
        out.append({"time": t["exit_ts"], "r": round(cum, 3), "equity": round(eq, 2)})
    return out
