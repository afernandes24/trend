"""Strategy Engine — łączy wskaźniki, score, poziomy SL/TP i analizę multi-timeframe.

Kierunek pracy (domyślnie):
    4H  → główny trend
    1H  → potwierdzenie trendu
    15M → setup (score, sygnał, SL/TP)
    5M  → timing wejścia (informacyjnie)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..market_data.base import HIGHER_TFS, UI_TIMEFRAMES, epoch_seconds, tf_minutes
from .indicators import compute_indicators, support_resistance
from .levels import compute_levels
from .params import StrategyParams
from .scoring import compute_scores, htf_context, reasons_at, score_band, signal_events

SETUP_TF = "15M"
MTF_ROLES = {"4H": "Trend główny", "1H": "Potwierdzenie", "15M": "Setup", "5M": "Wejście"}


@dataclass
class Prepared:
    ind: pd.DataFrame
    scores: pd.DataFrame
    events: pd.Series


def prepare(df: pd.DataFrame, tf: str, p: StrategyParams,
            htf_frames: dict[str, pd.DataFrame] | None = None) -> Prepared:
    ind = compute_indicators(df, p)
    ctx = htf_context(df, tf, p, htf_frames)
    scores = compute_scores(ind, p, ctx)
    return Prepared(ind, scores, signal_events(scores, p.min_score))


def last_closed_index(df: pd.DataFrame, tf: str, now: float | None = None) -> int:
    """Indeks ostatniej ZAMKNIĘTEJ świecy (ostatnia może się jeszcze formować)."""
    if df.empty:
        return -1
    now = now or time.time()
    last_start = df.index[-1].timestamp()
    forming = last_start + tf_minutes(tf) * 60 > now
    # świeca zbudowana z niższego interwału, któremu brakuje jeszcze świec składowych
    if df.attrs.get("last_complete") is False:
        forming = True
    return len(df) - 2 if forming else len(df) - 1


def trend_state(row: pd.Series) -> int:
    c, mid, slow = row["close"], row["sma_mid"], row["sma_slow"]
    if any(pd.isna(x) for x in (c, mid, slow)):
        return 0
    if c > slow and mid > slow:
        return 1
    if c < slow and mid < slow:
        return -1
    return 0


def _f(x, nd=None):
    if x is None or (isinstance(x, float) and not math.isfinite(x)) or pd.isna(x):
        return None
    x = float(x)
    return round(x, nd) if nd is not None else x


def analyze_tf(symbol: str, tf: str, df: pd.DataFrame, p: StrategyParams,
               htf_frames: dict[str, pd.DataFrame] | None = None, now: float | None = None,
               prepared: Prepared | None = None) -> dict:
    """Analiza jednego interwału na ostatniej zamkniętej świecy."""
    if df.empty or len(df) < p.sma_slow + 20:
        return {"symbol": symbol, "tf": tf, "ok": False,
                "error": f"Za mało danych ({len(df)} świec, potrzeba ≥ {p.sma_slow + 20})"}
    pr = prepared or prepare(df, tf, p, htf_frames)
    i = last_closed_index(df, tf, now)
    if i < 0:
        return {"symbol": symbol, "tf": tf, "ok": False, "error": "Brak zamkniętej świecy"}
    ind, sc = pr.ind, pr.scores
    row, srow = ind.iloc[i], sc.iloc[i]
    ls, ss = srow["long_score"], srow["short_score"]
    if pd.isna(ls) or pd.isna(ss):
        return {"symbol": symbol, "tf": tf, "ok": False, "error": "Wskaźniki jeszcze się nie ustabilizowały"}
    side = "long" if ls >= ss else "short"
    direction = "LONG" if side == "long" else "SHORT"
    score = float(srow[f"{side}_score"])
    gate = bool(srow[f"{side}_gate"])
    active = gate and score >= p.min_score
    sr = support_resistance(ind, i, n=p.swing_bars)
    levels = compute_levels(ind, i, direction, p, sr=sr)
    fresh_event = int(pr.events.iloc[i])
    htf_states = {h: _f(srow.get(f"htf_{h}")) for h in HIGHER_TFS.get(tf, [])}
    return {
        "symbol": symbol, "tf": tf, "ok": True,
        "bar_time": ind.index[i].isoformat(), "bar_ts": int(ind.index[i].timestamp()),
        "price": _f(ind["close"].iloc[-1]), "close": _f(row["close"]),
        "trend": trend_state(row),
        "direction": direction, "score": int(score), "band": score_band(score),
        "long_score": int(ls), "short_score": int(ss),
        "gate": gate, "active": active, "fresh": fresh_event != 0,
        "indicators": {
            "sma_fast": _f(row["sma_fast"]), "sma_mid": _f(row["sma_mid"]), "sma_slow": _f(row["sma_slow"]),
            "rsi": _f(row["rsi"], 1), "adx": _f(row["adx"], 1), "plus_di": _f(row["plus_di"], 1),
            "minus_di": _f(row["minus_di"], 1), "atr": _f(row["atr"]), "macd": _f(row["macd"]),
            "macd_signal": _f(row["macd_signal"]), "macd_hist": _f(row["macd_hist"]),
            "roc": _f(row["roc"], 3), "atr_ratio": _f(row["atr_ratio"], 2),
            "has_volume": bool(ind.attrs.get("has_volume")),
        },
        "components": reasons_at(sc, i, side),
        "htf": htf_states,
        "levels": levels,
        "sr": sr,
        "bars": len(df),
    }


def mtf_summary(per_tf: dict[str, dict], setup_tf: str = SETUP_TF) -> dict:
    """Czy interwały są zgodne z kierunkiem setupu."""
    setup = per_tf.get(setup_tf, {})
    direction = setup.get("direction") if setup.get("ok") else None
    want = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
    rows, agree_all = [], bool(want)
    for tf in reversed(UI_TIMEFRAMES):   # 4H, 1H, 15M, 5M
        a = per_tf.get(tf, {})
        if not a.get("ok"):
            rows.append({"tf": tf, "role": MTF_ROLES[tf], "trend": None, "score": None, "agree": None})
            if tf in ("4H", "1H"):
                agree_all = False
            continue
        if tf == "5M":
            # timing wejścia: 5M nie może być przeciwny (cena po właściwej stronie SMA20)
            px, f = a["close"], a["indicators"]["sma_fast"]
            ok = bool(want) and f is not None and ((px > f) if want > 0 else (px < f))
        elif tf == setup_tf:
            ok = a["gate"]
        else:
            ok = a["trend"] == want
        if tf in ("4H", "1H") and not ok:
            agree_all = False
        rows.append({"tf": tf, "role": MTF_ROLES[tf], "trend": a["trend"], "score": a["score"],
                     "direction": a["direction"], "agree": bool(ok)})
    all_ok = all(r["agree"] for r in rows) if want else False
    return {"direction": direction, "higher_tf_aligned": agree_all, "all_aligned": all_ok, "rows": rows}


def final_signal(setup: dict, mtf: dict, p: StrategyParams) -> str:
    if not setup.get("ok") or not setup.get("active"):
        return "WAIT"
    if p.require_mtf and not mtf["higher_tf_aligned"]:
        return "WAIT"
    return setup["direction"]


def chart_payload(df: pd.DataFrame, tf: str, pr: Prepared, p: StrategyParams, bars: int = 400) -> dict:
    ind, sc = pr.ind, pr.scores
    start = max(0, len(ind) - bars)
    part = ind.iloc[start:]
    t = epoch_seconds(part.index)

    def series(col):
        vals = part[col].values
        return [{"time": tt, "value": float(v)} for tt, v in zip(t, vals) if np.isfinite(v)]

    candles = [{"time": tt, "open": float(o), "high": float(h), "low": float(l), "close": float(c)}
               for tt, o, h, l, c in zip(t, part["open"], part["high"], part["low"], part["close"])]
    ev = pr.events.iloc[start:]
    markers = []
    for ts, v in ev[ev != 0].items():
        s = int(sc.loc[ts, "long_score" if v > 0 else "short_score"])
        markers.append({"time": int(ts.timestamp()), "direction": "LONG" if v > 0 else "SHORT", "score": s})
    return {
        "candles": candles,
        "volume": [{"time": tt, "value": float(v)} for tt, v in zip(t, part["volume"])]
        if ind.attrs.get("has_volume") else [],
        "sma_fast": series("sma_fast"), "sma_mid": series("sma_mid"), "sma_slow": series("sma_slow"),
        "rsi": series("rsi"), "adx": series("adx"),
        "macd": series("macd"), "macd_signal": series("macd_signal"), "macd_hist": series("macd_hist"),
        "markers": markers,
        "score_long": [{"time": tt, "value": float(v)} for tt, v in zip(t, sc["long_score"].iloc[start:]) if np.isfinite(v)],
        "score_short": [{"time": tt, "value": float(v)} for tt, v in zip(t, sc["short_score"].iloc[start:]) if np.isfinite(v)],
        "params": {"sma_fast": p.sma_fast, "sma_mid": p.sma_mid, "sma_slow": p.sma_slow},
    }
