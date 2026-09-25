"""Wyznaczanie Stop Loss i Take Profit.

SL (LONG): poniżej niższego z [ostatni potwierdzony swing low (≤ sl_swing_lookback świec),
najniższe low z 5 ostatnich świec], minus bufor `sl_atr_buffer × ATR`.
Odległość jest ograniczana do przedziału [sl_min_atr, sl_max_atr] × ATR — dzięki
temu SL dopasowuje się do zmienności KAŻDEGO instrumentu (brak stałego SL).
SHORT lustrzanie (swing high + bufor).

TP: wielokrotności ryzyka R (domyślnie 1.5R / 2R / 3R). Dodatkowo zwracamy najbliższy
opór/wsparcie strukturalne i ostrzeżenie, gdy leży przed TP1.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .params import StrategyParams


def compute_levels(ind: pd.DataFrame, i: int, direction: str, p: StrategyParams,
                   entry: float | None = None, sr: dict | None = None) -> dict | None:
    row = ind.iloc[i]
    a = float(row["atr"])
    if not math.isfinite(a) or a <= 0:
        return None
    entry = float(row["close"]) if entry is None else float(entry)
    long = direction == "LONG"
    recent = ind.iloc[max(0, i - 4): i + 1]

    swing_col, pos_col = ("last_swing_low", "last_swing_low_pos") if long else ("last_swing_high", "last_swing_high_pos")
    swing = row[swing_col]
    swing_pos = row[pos_col]
    swing_ok = bool(pd.notna(swing) and pd.notna(swing_pos) and i - swing_pos <= p.sl_swing_lookback)
    extreme = float(recent["low"].min()) if long else float(recent["high"].max())
    if swing_ok:
        anchor = min(float(swing), extreme) if long else max(float(swing), extreme)
        method = "swing low + ATR" if long else "swing high + ATR"
    else:
        anchor = extreme
        method = "lokalne minimum + ATR" if long else "lokalne maksimum + ATR"
    raw_sl = anchor - p.sl_atr_buffer * a if long else anchor + p.sl_atr_buffer * a
    dist = (entry - raw_sl) if long else (raw_sl - entry)
    lo, hi = p.sl_min_atr * a, p.sl_max_atr * a
    if dist < lo:
        dist, method = lo, f"min {p.sl_min_atr}×ATR (struktura zbyt blisko)"
    elif dist > hi:
        dist, method = hi, f"max {p.sl_max_atr}×ATR (struktura zbyt daleko)"
    sgn = 1 if long else -1
    sl = entry - sgn * dist
    tps = [entry + sgn * r * dist for r in p.tp_r]

    out = {
        "direction": direction, "entry": entry, "sl": sl,
        "tp1": tps[0], "tp2": tps[1], "tp3": tps[2],
        "tp_r": list(p.tp_r), "risk": dist, "risk_atr": dist / a, "atr": a,
        "sl_method": method, "structure_target": None, "warnings": [],
    }
    if sr:
        side = sr.get("resistance" if long else "support") or []
        if side:
            tgt = side[0]["price"]
            out["structure_target"] = tgt
            r_to_tgt = abs(tgt - entry) / dist if dist else np.inf
            out["structure_target_r"] = round(float(r_to_tgt), 2)
            if r_to_tgt < p.tp_r[0]:
                kind = "Najbliższy opór" if long else "Najbliższe wsparcie"
                out["warnings"].append(f"{kind} ({tgt:.6g}) leży przed TP1 ({r_to_tgt:.2f}R) — cel może być trudniejszy")
    return out
