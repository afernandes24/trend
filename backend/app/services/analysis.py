"""Warstwa usług: analiza instrumentów, scanner, zapis sygnałów, wyniki, backfill.

Łączy Market Data + Strategy Engine + Backtesting Engine + Notification System.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..backtest.engine import run_backtest
from ..database import Database
from ..instruments import INSTRUMENTS, get_instrument
from ..market_data.base import HIGHER_TFS, UI_TIMEFRAMES, tf_minutes
from ..market_data.service import MarketDataService
from ..notifications.dispatcher import Notifier
from ..strategy.engine import (SETUP_TF, Prepared, analyze_tf, chart_payload, final_signal,
                               last_closed_index, mtf_summary, prepare)
from ..strategy.levels import compute_levels
from ..strategy.params import StrategyParams
from ..strategy.scoring import reasons_at

log = logging.getLogger(__name__)

EXPIRE_BARS = {"5M": 288, "15M": 192, "1H": 120, "4H": 60}
MIN_HIST_TRADES = 20


class AnalysisService:
    def __init__(self, db: Database, market: MarketDataService, notifier: Notifier):
        self.db = db
        self.market = market
        self.notifier = notifier
        self.latest: dict[str, dict] = {}
        self.last_scan: str | None = None
        self.scan_running = False
        self._prep_cache: dict[tuple, Prepared] = {}
        self._hist_cache: dict[tuple, dict] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------ params
    def get_params(self) -> StrategyParams:
        return StrategyParams.from_dict(self.db.get_setting("strategy", {}))

    def save_params(self, data: dict) -> StrategyParams:
        p = StrategyParams.from_dict({**self.get_params().to_dict(), **(data or {})})
        self.db.set_setting("strategy", p.to_dict())
        with self._lock:
            self._prep_cache.clear()
            self._hist_cache.clear()
        return p

    # ------------------------------------------------------------ core
    def htf_frames(self, symbol: str, tf: str) -> dict[str, pd.DataFrame]:
        return {h: self.market.get_candles(symbol, h) for h in HIGHER_TFS.get(tf, [])}

    def prepared(self, symbol: str, tf: str, p: StrategyParams) -> tuple[pd.DataFrame, Prepared | None]:
        df = self.market.get_candles(symbol, tf)
        if df.empty:
            return df, None
        key = (symbol, tf, len(df), int(df.index[-1].timestamp()), float(df["close"].iloc[-1]), p.key())
        with self._lock:
            hit = self._prep_cache.get(key)
        if hit is None:
            hit = prepare(df, tf, p, self.htf_frames(symbol, tf))
            with self._lock:
                if len(self._prep_cache) > 120:
                    self._prep_cache.clear()
                self._prep_cache[key] = hit
        return df, hit

    def analyze_instrument(self, symbol: str, p: StrategyParams | None = None, with_history: bool = True) -> dict:
        p = p or self.get_params()
        inst = get_instrument(symbol)
        per_tf = {}
        for tf in UI_TIMEFRAMES:
            df, pr = self.prepared(symbol, tf, p)
            if pr is None:
                per_tf[tf] = {"symbol": symbol, "tf": tf, "ok": False, "error": "Brak danych"}
                continue
            per_tf[tf] = analyze_tf(symbol, tf, df, p, prepared=pr)
        mtf = mtf_summary(per_tf, SETUP_TF)
        setup = per_tf[SETUP_TF]
        signal = final_signal(setup, mtf, p)
        out = {
            "symbol": symbol, "name": inst.name, "category": inst.category, "digits": inst.digits,
            "currency": inst.currency, "proxy_note": inst.proxy_note,
            "setup_tf": SETUP_TF, "signal": signal, "timeframes": per_tf, "mtf": mtf,
            "price": None, "change_pct": None, "updated": datetime.now(timezone.utc).isoformat(),
            "min_score": p.min_score,
        }
        five = self.market.get_candles(symbol, "5M")
        src = five if not five.empty else self.market.get_candles(symbol, "1H")
        if not src.empty:
            out["price"] = float(src["close"].iloc[-1])
            out["price_time"] = src.index[-1].isoformat()
            out["change_pct"] = self._change_pct(symbol, out["price"])
        trend4h = per_tf.get("4H", {})
        out["trend"] = trend4h.get("trend") if trend4h.get("ok") else None
        if setup.get("ok"):
            out.update(score=setup["score"], direction=setup["direction"], band=setup["band"],
                       rsi=setup["indicators"]["rsi"], adx=setup["indicators"]["adx"])
        else:
            fetch_err = next((i.get("error") for i in (self.market.status.get(symbol) or {}).values()
                              if i.get("error")), None)
            out.update(score=None, direction=None, band="BRAK DANYCH", rsi=None, adx=None,
                       error=fetch_err or setup.get("error"))
        if with_history and setup.get("ok"):
            out["historical"] = self.historical_stats(symbol, SETUP_TF, p)
        return out

    def _change_pct(self, symbol: str, price: float) -> float | None:
        d = self.market.get_candles(symbol, "1D")
        if len(d) < 2:
            return None
        today = pd.Timestamp.now(tz="UTC").floor("D")
        prev = d[d.index < today]
        if prev.empty:
            return None
        ref = float(prev["close"].iloc[-1]) if d.index[-1] >= today else float(d["close"].iloc[-2])
        return round((price / ref - 1) * 100, 2) if ref else None

    def historical_stats(self, symbol: str, tf: str, p: StrategyParams) -> dict:
        """Statystyki z backtestu dla TEJ SAMEJ konfiguracji, instrumentu i interwału."""
        df, pr = self.prepared(symbol, tf, p)
        if pr is None:
            return {"trades": 0, "note": "Brak danych"}
        key = (symbol, tf, len(df), int(df.index[-1].timestamp()), p.key())
        with self._lock:
            if key in self._hist_cache:
                return self._hist_cache[key]
        try:
            bt = run_backtest(df, tf, p, get_instrument(symbol), prepared=pr)
            m = bt["all"]
            res = {"trades": m["trades"], "win_rate": m["win_rate"], "expectancy_r": m["expectancy_r"],
                   "profit_factor": m["profit_factor"], "from": bt["from"], "to": bt["to"],
                   "rr_target": p.rr_target, "exit_mode": p.exit_mode, "min_score": p.min_score,
                   "small_sample": m["trades"] < MIN_HIST_TRADES}
        except ValueError as exc:
            res = {"trades": 0, "note": str(exc)}
        with self._lock:
            self._hist_cache[key] = res
        return res

    def chart(self, symbol: str, tf: str, bars: int = 400) -> dict:
        p = self.get_params()
        df, pr = self.prepared(symbol, tf, p)
        if pr is None:
            return {"symbol": symbol, "tf": tf, "ok": False, "error": "Brak danych dla instrumentu"}
        payload = chart_payload(df, tf, pr, p, bars)
        payload["analysis"] = analyze_tf(symbol, tf, df, p, prepared=pr)
        payload.update(symbol=symbol, tf=tf, ok=True, digits=get_instrument(symbol).digits)
        return payload

    # ------------------------------------------------------------ scanner
    def scan_all(self, dispatch_alerts: bool = True) -> list[dict]:
        p = self.get_params()
        results = []
        for inst in INSTRUMENTS:
            try:
                a = self.analyze_instrument(inst.symbol, p)
                if dispatch_alerts:
                    self._record_signals(inst.symbol, a, p)
            except Exception as exc:  # noqa: BLE001
                log.exception("Analiza %s nieudana", inst.symbol)
                a = {"symbol": inst.symbol, "name": inst.name, "signal": "WAIT", "score": None,
                     "error": str(exc), "timeframes": {}, "mtf": None}
            results.append(a)
            with self._lock:
                self.latest[inst.symbol] = a
        self.last_scan = datetime.now(timezone.utc).isoformat()
        return results

    def overview(self) -> list[dict]:
        with self._lock:
            have = dict(self.latest)
        if len(have) < len(INSTRUMENTS):
            self.scan_all(dispatch_alerts=False)
            with self._lock:
                have = dict(self.latest)
        return [have[i.symbol] for i in INSTRUMENTS if i.symbol in have]

    def _record_signals(self, symbol: str, a: dict, p: StrategyParams) -> None:
        """Zapisuje NOWE setupy (zdarzenie na ostatnich zamkniętych świecach) i wysyła alerty."""
        tf = SETUP_TF
        df, pr = self.prepared(symbol, tf, p)
        if pr is None or len(df) < 3:
            return
        i_last = last_closed_index(df, tf)
        mtf_ok = bool(a.get("mtf") and a["mtf"]["higher_tf_aligned"])
        for i in range(max(0, i_last - 2), i_last + 1):
            ev = int(pr.events.iloc[i])
            if ev == 0:
                continue
            direction = "LONG" if ev > 0 else "SHORT"
            # sygnał zapisujemy zawsze; o wysłaniu alertu (m.in. zgodność MTF) decyduje Notifier
            sig = self._build_signal(symbol, tf, pr, i, direction, p, a, source="LIVE")
            sig_id = self.db.insert_signal(sig)
            if sig_id is None:
                continue        # już zapisany
            sig["id"] = sig_id
            bar_close = pr.ind.index[i].timestamp() + tf_minutes(tf) * 60
            if time.time() - bar_close <= 2 * tf_minutes(tf) * 60:
                sig["historical"] = a.get("historical")
                sig["tp_r"] = p.tp_r
                self.notifier.dispatch(sig, mtf_ok)

    def _build_signal(self, symbol, tf, pr: Prepared, i, direction, p, a=None, source="LIVE") -> dict:
        side = "long" if direction == "LONG" else "short"
        lv = compute_levels(pr.ind, i, direction, p)
        return {
            "symbol": symbol, "tf": tf, "direction": direction,
            "bar_ts": int(pr.ind.index[i].timestamp()),
            "entry": lv["entry"], "sl": lv["sl"], "tp1": lv["tp1"], "tp2": lv["tp2"], "tp3": lv["tp3"],
            "score": int(pr.scores[f"{side}_score"].iloc[i]),
            "reasons": reasons_at(pr.scores, i, side),
            "mtf": a["mtf"]["rows"] if a and a.get("mtf") else None,
            "source": source,
        }

    # ------------------------------------------------------------ outcomes
    def update_outcomes(self) -> int:
        p = self.get_params()
        n = 0
        for sig in self.db.open_signals():
            df = self.market.get_candles(sig["symbol"], sig["tf"])
            res = evaluate_outcome(df, sig, p.tp_r, EXPIRE_BARS.get(sig["tf"], 192))
            if res:
                self.db.update_signal(sig["id"], **res)
                n += 1
        return n

    def backfill(self, days: int = 30, symbols: list[str] | None = None) -> dict:
        """Generuje historię sygnałów z danych historycznych (oznaczone jako BACKFILL)."""
        p = self.get_params()
        tf = SETUP_TF
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        self.db.delete_signals("BACKFILL")
        counts = {}
        for inst in INSTRUMENTS:
            if symbols and inst.symbol not in symbols:
                continue
            df, pr = self.prepared(inst.symbol, tf, p)
            if pr is None:
                counts[inst.symbol] = 0
                continue
            i_last = last_closed_index(df, tf)
            idx = np.where(pr.events.values[: i_last + 1] != 0)[0]
            c = 0
            for i in idx:
                if pr.ind.index[i] < cutoff:
                    continue
                direction = "LONG" if pr.events.iloc[i] > 0 else "SHORT"
                sig = self._build_signal(inst.symbol, tf, pr, int(i), direction, p, source="BACKFILL")
                res = evaluate_outcome(df, sig, p.tp_r, EXPIRE_BARS[tf]) or {}
                sig.update(res)
                if self.db.insert_signal(sig):
                    c += 1
            counts[inst.symbol] = c
        return counts


def evaluate_outcome(df: pd.DataFrame, sig: dict, tp_r: list[float], expire_bars: int) -> dict | None:
    """Wynik sygnału: najwyższy TP osiągnięty przed SL (w R). Konserwatywnie: SL przed TP w tej samej świecy.

    Zwraca dict z polami do aktualizacji albo None, gdy sygnał nadal otwarty.
    """
    if df.empty:
        return None
    after = df[df.index > pd.Timestamp(sig["bar_ts"], unit="s", tz="UTC")]
    if after.empty:
        return None
    long = sig["direction"] == "LONG"
    entry, sl = sig["entry"], sig["sl"]
    risk = abs(entry - sl)
    tps = [sig["tp1"], sig["tp2"], sig["tp3"]]
    best = 0
    for n, (ts, row) in enumerate(after.iterrows(), start=1):
        if (row["low"] <= sl) if long else (row["high"] >= sl):
            if best:
                return {"status": "CLOSED", "result": f"TP{best}", "result_r": tp_r[best - 1],
                        "closed_at": int(ts.timestamp())}
            return {"status": "CLOSED", "result": "SL", "result_r": -1.0, "closed_at": int(ts.timestamp())}
        for k in range(best, 3):
            if (row["high"] >= tps[k]) if long else (row["low"] <= tps[k]):
                best = k + 1
        if best == 3:
            return {"status": "CLOSED", "result": "TP3", "result_r": tp_r[2], "closed_at": int(ts.timestamp())}
        if n >= expire_bars:
            if best:
                return {"status": "CLOSED", "result": f"TP{best}", "result_r": tp_r[best - 1],
                        "closed_at": int(ts.timestamp())}
            mtm = ((row["close"] - entry) if long else (entry - row["close"])) / risk if risk else 0.0
            return {"status": "EXPIRED", "result": "EXPIRED", "result_r": round(float(mtm), 2),
                    "closed_at": int(ts.timestamp())}
    return None
