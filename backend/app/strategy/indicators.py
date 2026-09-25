"""Wskaźniki techniczne (wektorowo, pandas/numpy).

Wszystkie wskaźniki są PRZYCZYNOWE: wartość dla świecy t używa tylko danych do t
włącznie. Swingi są potwierdzane dopiero po `n` kolejnych świecach (brak
zaglądania w przyszłość w backteście).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .params import StrategyParams


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(dn != 0, 100.0)            # same wzrosty -> 100
    return out.where(up.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                     axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr.replace(0, np.nan)
    minus_di = 100 * wilder(minus_dm, n) / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx, n), plus_di, minus_di


def macd(close: pd.Series, fast=12, slow=26, signal=9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def swings(df: pd.DataFrame, n: int = 3) -> tuple[pd.Series, pd.Series]:
    """Zwraca (swing_high, swing_low) — wartość na świecy swingu, NaN gdzie indziej.

    Uwaga: swing na świecy i jest znany dopiero na świecy i+n.
    """
    h, l = df["high"], df["low"]
    left_h = h.shift(1).rolling(n, min_periods=n).max()
    right_h = h[::-1].shift(1).rolling(n, min_periods=n).max()[::-1]
    left_l = l.shift(1).rolling(n, min_periods=n).min()
    right_l = l[::-1].shift(1).rolling(n, min_periods=n).min()[::-1]
    sh = h.where((h > left_h) & (h >= right_h))
    sl = l.where((l < left_l) & (l <= right_l))
    return sh, sl


def compute_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Dodaje wszystkie wskaźniki strategii do kopii DataFrame."""
    out = df.copy()
    c = out["close"]
    out["sma_fast"] = sma(c, p.sma_fast)
    out["sma_mid"] = sma(c, p.sma_mid)
    out["sma_slow"] = sma(c, p.sma_slow)
    out["sma_slow_slope"] = out["sma_slow"] - out["sma_slow"].shift(10)
    out["rsi"] = rsi(c, p.rsi_period)
    out["atr"] = atr(out, p.atr_period)
    out["atr_ratio"] = out["atr"] / out["atr"].rolling(100, min_periods=30).median()
    out["adx"], out["plus_di"], out["minus_di"] = adx(out, p.adx_period)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c, p.macd_fast, p.macd_slow, p.macd_signal)
    out["roc"] = c.pct_change(p.momentum_period) * 100
    has_volume = bool((out["volume"] > 0).mean() > 0.8)
    out.attrs["has_volume"] = has_volume
    out["vol_sma"] = sma(out["volume"], 20) if has_volume else np.nan

    n = p.swing_bars
    sh, sl = swings(out, n)
    out["swing_high"], out["swing_low"] = sh, sl
    pos = pd.Series(np.arange(len(out)), index=out.index, dtype=float)
    # potwierdzone swingi (widoczne od świecy i+n)
    conf_sh = sh.shift(n)
    conf_sl = sl.shift(n)
    out["last_swing_high"] = conf_sh.ffill()
    out["last_swing_low"] = conf_sl.ffill()
    out["last_swing_high_pos"] = pos.shift(n).where(conf_sh.notna()).ffill()
    out["last_swing_low_pos"] = pos.shift(n).where(conf_sl.notna()).ffill()
    prev_sh = conf_sh.dropna().shift(1)
    prev_sl = conf_sl.dropna().shift(1)
    out["prev_swing_high"] = prev_sh.reindex(out.index).ffill()
    out["prev_swing_low"] = prev_sl.reindex(out.index).ffill()
    return out


def support_resistance(df: pd.DataFrame, at: int, lookback: int = 150, n: int = 3,
                       max_levels: int = 4) -> dict:
    """Poziomy S/R z potwierdzonych swingów (do świecy `at` włącznie), zgrupowane wg ATR."""
    lo = max(0, at - lookback)
    window = df.iloc[lo: at + 1]
    confirmed_limit = at - n
    if confirmed_limit <= lo:
        return {"support": [], "resistance": []}
    w = window.iloc[: confirmed_limit - lo + 1]
    price = float(df["close"].iloc[at])
    a = float(df["atr"].iloc[at]) if "atr" in df and not np.isnan(df["atr"].iloc[at]) else price * 0.002
    pts = pd.concat([w["swing_high"].dropna(), w["swing_low"].dropna()]).sort_values()
    clusters: list[list[float]] = []
    for v in pts.values:
        if clusters and abs(v - np.mean(clusters[-1])) <= 0.5 * a:
            clusters[-1].append(float(v))
        else:
            clusters.append([float(v)])
    levels = [(float(np.mean(cl)), len(cl)) for cl in clusters]
    res = sorted([lv for lv in levels if lv[0] > price], key=lambda x: x[0])[:max_levels]
    sup = sorted([lv for lv in levels if lv[0] < price], key=lambda x: -x[0])[:max_levels]
    return {
        "support": [{"price": v, "touches": t} for v, t in sup],
        "resistance": [{"price": v, "touches": t} for v, t in res],
    }
