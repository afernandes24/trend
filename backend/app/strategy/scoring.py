"""System oceny setupu 0–100 (wektorowo dla całej historii).

Składowe (maks. punkty):
    Trend            25   cena vs SMA200, nachylenie SMA200, zgodność wyższych interwałów
    SMA              15   SMA50 vs SMA200, SMA20 vs SMA50, cena vs SMA20
    RSI              10   RSI w strefie zgodnej z trendem + kierunek RSI
    ADX              10   siła trendu + DI zgodne z kierunkiem + ADX rosnący
    MACD             10   MACD vs sygnał, MACD vs 0, histogram rośnie
    Price Action     15   pullback do SMA20, świeca potwierdzająca, wybicie, struktura HL/LH,
                          kara za bliski opór/wsparcie
    Momentum         10   ROC > 0, przyspieszenie, wolumen (lub rozszerzenie zakresu)
    ATR/zmienność     5   ATR względem mediany ze 100 świec w „zdrowym" zakresie

Warunki konieczne kierunku (bramki) — bez nich nie ma sygnału, niezależnie od score:
    LONG : close > SMA200, SMA50 > SMA200, SMA20 > SMA50, ADX ≥ adx_min, +DI > −DI
    SHORT: lustrzanie.
Score NIE jest prawdopodobieństwem zysku.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..market_data.base import HIGHER_TFS, resample, tf_minutes
from .indicators import sma
from .params import StrategyParams

MAX_POINTS = {"trend": 25, "sma": 15, "rsi": 10, "adx": 10, "macd": 10,
              "price_action": 15, "momentum": 10, "volatility": 5}
LABELS = {"trend": "Trend", "sma": "SMA alignment", "rsi": "RSI", "adx": "ADX", "macd": "MACD",
          "price_action": "Price action", "momentum": "Momentum", "volatility": "ATR / zmienność"}


def score_band(score: float | None) -> str:
    if score is None or np.isnan(score):
        return "BRAK DANYCH"
    if score >= 85:
        return "BARDZO MOCNY SETUP"
    if score >= 75:
        return "DOBRY SETUP"
    if score >= 65:
        return "OBSERWUJ"
    if score >= 50:
        return "SŁABY"
    return "BRAK SETUPU"


# ---------------------------------------------------------------------- MTF
def htf_state_frame(htf: pd.DataFrame, p: StrategyParams) -> pd.Series:
    """Stan trendu na interwale wyższym: +1 wzrost, -1 spadek, 0 brak."""
    c = htf["close"]
    f, m = sma(c, p.sma_fast), sma(c, p.sma_mid)
    state = pd.Series(0.0, index=htf.index)
    state[(c > m) & (f > m)] = 1.0
    state[(c < m) & (f < m)] = -1.0
    state[m.isna()] = np.nan
    return state


def align_htf(base: pd.DataFrame, base_tf: str, htf: pd.DataFrame, htf_tf: str,
              p: StrategyParams) -> pd.Series:
    """Przenosi stan trendu z wyższego TF na świece bazowe — tylko ZAMKNIĘTE świece HTF."""
    if htf.empty or base.empty:
        return pd.Series(np.nan, index=base.index)
    state = htf_state_frame(htf, p).dropna()
    if state.empty:
        return pd.Series(np.nan, index=base.index)
    done = pd.DataFrame({"t": state.index + pd.Timedelta(minutes=tf_minutes(htf_tf)), "s": state.values})
    closes = pd.DataFrame({"t": base.index + pd.Timedelta(minutes=tf_minutes(base_tf)), "i": np.arange(len(base))})
    done["t"] = done["t"].astype("datetime64[ns, UTC]")
    closes["t"] = closes["t"].astype("datetime64[ns, UTC]")
    merged = pd.merge_asof(closes.sort_values("t"), done.sort_values("t"), on="t", direction="backward")
    out = pd.Series(merged.sort_values("i")["s"].values, index=base.index)
    return out


def htf_context(base: pd.DataFrame, base_tf: str, p: StrategyParams,
                htf_frames: dict[str, pd.DataFrame] | None = None) -> dict[str, pd.Series]:
    ctx = {}
    for h in HIGHER_TFS.get(base_tf, []):
        frame = (htf_frames or {}).get(h)
        if frame is None or frame.empty:
            frame = resample(base[["open", "high", "low", "close", "volume"]], h)
        ctx[h] = align_htf(base, base_tf, frame, h, p)
    return ctx


# ---------------------------------------------------------------------- score
def _b(x) -> np.ndarray:
    return np.nan_to_num(np.asarray(x, dtype=float), nan=0.0) > 0


def compute_scores(ind: pd.DataFrame, p: StrategyParams, htf: dict[str, pd.Series]) -> pd.DataFrame:
    """Zwraca DataFrame: long_*/short_* składowe, long_score, short_score, bramki."""
    c, o, h, l = ind["close"], ind["open"], ind["high"], ind["low"]
    a = ind["atr"]
    rng = (h - l).replace(0, np.nan)
    res = pd.DataFrame(index=ind.index)
    has_vol = ind.attrs.get("has_volume", False)
    n_htf = max(1, len(htf))

    for side, sgn in (("long", 1), ("short", -1)):
        up = sgn > 0
        # ---------------- Trend (25)
        above_slow = (c > ind["sma_slow"]) if up else (c < ind["sma_slow"])
        slope_ok = (ind["sma_slow_slope"] > 0) if up else (ind["sma_slow_slope"] < 0)
        htf_pts = sum(((s == sgn).astype(float) * (10.0 / n_htf)) for s in htf.values()) if htf else 0.0
        trend = 8 * _b(above_slow) + 7 * _b(slope_ok) + np.nan_to_num(np.asarray(htf_pts, dtype=float))

        # ---------------- SMA (15)
        mid_slow = (ind["sma_mid"] > ind["sma_slow"]) if up else (ind["sma_mid"] < ind["sma_slow"])
        fast_mid = (ind["sma_fast"] > ind["sma_mid"]) if up else (ind["sma_fast"] < ind["sma_mid"])
        px_fast = (c > ind["sma_fast"]) if up else (c < ind["sma_fast"])
        sma_pts = 6 * _b(mid_slow) + 5 * _b(fast_mid) + 4 * _b(px_fast)

        # ---------------- RSI (10) — potwierdzenie trendu, nie „wyprzedanie"
        r = ind["rsi"]
        lo, hi = (p.rsi_long_min, p.rsi_long_max) if up else (p.rsi_short_min, p.rsi_short_max)
        in_zone = (r >= lo) & (r <= hi)
        near_zone = ((r >= lo - 5) & (r < lo)) | ((r > hi) & (r <= hi + 5))
        rsi_dir = (r > r.shift(3)) if up else (r < r.shift(3))
        rsi_pts = 7 * _b(in_zone) + 3 * _b(near_zone) + 3 * _b(rsi_dir & (in_zone | near_zone))

        # ---------------- ADX (10)
        di_ok = (ind["plus_di"] > ind["minus_di"]) if up else (ind["minus_di"] > ind["plus_di"])
        adx_v = ind["adx"]
        adx_lvl = np.where(adx_v >= p.adx_strong, 7, np.where(adx_v >= p.adx_min, 4, 0))
        adx_pts = (adx_lvl + 3 * _b(adx_v > adx_v.shift(3))) * _b(di_ok)

        # ---------------- MACD (10)
        m, ms, mh = ind["macd"], ind["macd_signal"], ind["macd_hist"]
        macd_pts = (4 * _b((m > ms) if up else (m < ms))
                    + 3 * _b((m > 0) if up else (m < 0))
                    + 3 * _b((mh > mh.shift(1)) if up else (mh < mh.shift(1))))

        # ---------------- Price action (15)
        if up:
            touched = (l <= ind["sma_fast"] + 0.25 * a).rolling(6, min_periods=1).max() > 0
            held = c > ind["sma_mid"]
            trigger = (c > o) & ((c - l) >= 0.6 * rng)
            breakout = c > h.shift(1)
            structure = ind["last_swing_low"] > ind["prev_swing_low"]
            opp = ind["last_swing_high"].where(ind["last_swing_high"] > c)
            blocked = (opp - c) < 1.0 * a
        else:
            touched = (h >= ind["sma_fast"] - 0.25 * a).rolling(6, min_periods=1).max() > 0
            held = c < ind["sma_mid"]
            trigger = (c < o) & ((h - c) >= 0.6 * rng)
            breakout = c < l.shift(1)
            structure = ind["last_swing_high"] < ind["prev_swing_high"]
            opp = ind["last_swing_low"].where(ind["last_swing_low"] < c)
            blocked = (c - opp) < 1.0 * a
        pa_pts = 5 * _b(touched & held) + 4 * _b(trigger) + 3 * _b(breakout) + 3 * _b(structure)
        pa_pts = np.clip(pa_pts - 4 * _b(blocked), 0, None)

        # ---------------- Momentum (10)
        roc = ind["roc"]
        mom_pts = 4 * _b((roc > 0) if up else (roc < 0)) + 3 * _b((roc > roc.shift(3)) if up else (roc < roc.shift(3)))
        if has_vol:
            mom_pts = mom_pts + 3 * _b(ind["volume"] > ind["vol_sma"])
        else:
            mom_pts = mom_pts + 3 * _b((h - l) > a)

        # ---------------- ATR / zmienność (5)
        ar = ind["atr_ratio"]
        vol_pts = np.where((ar >= 0.7) & (ar <= 1.8), 5, np.where(((ar >= 0.5) & (ar < 0.7)) | ((ar > 1.8) & (ar <= 2.5)), 2, 0))

        comps = {"trend": trend, "sma": sma_pts, "rsi": rsi_pts, "adx": adx_pts, "macd": macd_pts,
                 "price_action": pa_pts, "momentum": mom_pts, "volatility": vol_pts}
        total = np.zeros(len(ind))
        for k, v in comps.items():
            v = np.minimum(np.asarray(v, dtype=float), MAX_POINTS[k])
            res[f"{side}_{k}"] = v
            total += v
        ready = ind[["sma_slow", "adx", "macd_signal", "rsi", "atr"]].notna().all(axis=1).values
        res[f"{side}_score"] = np.where(ready, np.round(np.clip(total, 0, 100)), np.nan)
        res[f"{side}_gate"] = ready & _b(above_slow) & _b(mid_slow) & _b(fast_mid) & _b(adx_v >= p.adx_min) & _b(di_ok)
        res[f"{side}_blocked"] = _b(blocked)

    for hname, s in htf.items():
        res[f"htf_{hname}"] = s.values
    return res


REARM_GAP = 10  # histereza: po sygnale score musi spaść o 10 pkt poniżej progu (lub zniknąć trend)


def signal_events(scores: pd.DataFrame, min_score: float) -> pd.Series:
    """+1 / -1 na świecy, gdzie setup POJAWIA SIĘ, 0 w pozostałych.

    Z histerezą — kolejny sygnał w tym samym kierunku dopiero, gdy poprzedni setup
    „wygasł" (score < próg − 10 albo bramka trendu przestała być spełniona).
    Zapobiega to serii sygnałów, gdy score oscyluje wokół progu.
    """
    out = np.zeros(len(scores))
    for side, sgn in (("long", 1), ("short", -1)):
        gate = scores[f"{side}_gate"].astype(bool).values
        sc = np.nan_to_num(scores[f"{side}_score"].values, nan=0.0)
        armed = True
        for i in range(len(sc)):
            ok = gate[i] and sc[i] >= min_score
            if armed and ok:
                out[i] = sgn
                armed = False
            elif not armed and (not gate[i] or sc[i] < min_score - REARM_GAP):
                armed = True
    return pd.Series(out, index=scores.index)


def reasons_at(scores: pd.DataFrame, i: int, side: str) -> list[dict]:
    row = scores.iloc[i]
    out = []
    for k, mx in MAX_POINTS.items():
        pts = float(row[f"{side}_{k}"])
        status = "ok" if pts >= 0.6 * mx else ("partial" if pts > 0 else "fail")
        out.append({"key": k, "label": LABELS[k], "points": round(pts, 1), "max": mx, "status": status})
    return out
