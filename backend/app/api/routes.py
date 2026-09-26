"""REST API (FastAPI). Wszystkie endpointy pod /api."""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..backtest.engine import run_backtest
from ..backtest.walkforward import run_walkforward
from ..instruments import INSTRUMENTS, get_instrument
from ..market_data.base import UI_TIMEFRAMES
from ..strategy.params import StrategyParams


def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    return o


class CleanJSON(Response):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json.dumps(_clean(content), ensure_ascii=False, default=str).encode("utf-8")


router = APIRouter(prefix="/api", default_response_class=CleanJSON)


def _svc(request: Request):
    return request.app.state


def _check_tf(tf: str) -> str:
    tf = tf.upper()
    if tf not in UI_TIMEFRAMES:
        raise HTTPException(400, f"Interwał: {', '.join(UI_TIMEFRAMES)}")
    return tf


def _check_symbol(symbol: str) -> str:
    try:
        return get_instrument(symbol).symbol
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- system
@router.get("/health")
def health(request: Request):
    s = _svc(request)
    return {"ok": True, "mode": "ANALIZA (brak wykonywania zleceń)", "provider": s.market.provider.name,
            "scheduler": s.scheduler.status(), "last_scan": s.analysis.last_scan}


@router.get("/instruments")
def instruments():
    return [i.to_dict() for i in INSTRUMENTS]


@router.get("/timeframes")
def timeframes():
    return UI_TIMEFRAMES


# ---------------------------------------------------------------- market / analysis
@router.get("/overview")
def overview(request: Request):
    s = _svc(request)
    items = s.analysis.overview()
    return {"items": [_tile(a) for a in items], "last_scan": s.analysis.last_scan,
            "scheduler": s.scheduler.status()}


def _tile(a: dict) -> dict:
    keys = ("symbol", "name", "category", "digits", "price", "price_time", "change_pct", "trend", "rsi", "adx",
            "score", "band", "direction", "signal", "error", "setup_tf", "min_score")
    t = {k: a.get(k) for k in keys}
    mtf = a.get("mtf") or {}
    t["mtf"] = [{"tf": r["tf"], "trend": r.get("trend"), "agree": r.get("agree")} for r in mtf.get("rows", [])]
    t["higher_tf_aligned"] = mtf.get("higher_tf_aligned")
    t["all_aligned"] = mtf.get("all_aligned")
    return t


@router.get("/scanner")
def scanner(request: Request, refresh: bool = False):
    s = _svc(request)
    items = s.analysis.scan_all(dispatch_alerts=False) if refresh else s.analysis.overview()
    rows = [_tile(a) for a in items]
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return {"items": rows, "last_scan": s.analysis.last_scan,
            "note": "Sortowanie wyłącznie wg bieżącego score. Score nie jest prawdopodobieństwem zysku."}


@router.get("/analysis/{symbol}")
def analysis(request: Request, symbol: str):
    return _svc(request).analysis.analyze_instrument(_check_symbol(symbol))


@router.get("/chart/{symbol}")
def chart(request: Request, symbol: str, tf: str = "15M", bars: int = Query(400, ge=100, le=3000)):
    return _svc(request).analysis.chart(_check_symbol(symbol), _check_tf(tf), bars)


# ---------------------------------------------------------------- strategy settings
@router.get("/settings/strategy")
def get_strategy(request: Request):
    return {"params": _svc(request).analysis.get_params().to_dict(), "defaults": StrategyParams().to_dict()}


@router.put("/settings/strategy")
def put_strategy(request: Request, body: dict):
    try:
        p = _svc(request).analysis.save_params(body)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"params": p.to_dict()}


# ---------------------------------------------------------------- backtest
class BacktestIn(BaseModel):
    symbol: str
    tf: str = "15M"
    date_from: str | None = None
    date_to: str | None = None
    params: dict = Field(default_factory=dict)
    split: float | None = 0.7
    capital: float = 10_000
    risk_pct: float = 1.0
    walkforward: bool = False
    wf_folds: int = 4
    wf_mode: str = "rolling"
    wf_min_trades: int = 5
    include_trades: bool = True


@router.post("/backtest")
def backtest(request: Request, body: BacktestIn):
    s = _svc(request)
    symbol, tf = _check_symbol(body.symbol), _check_tf(body.tf)
    try:
        p = StrategyParams.from_dict({**s.analysis.get_params().to_dict(), **body.params})
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    df = s.market.get_candles(symbol, tf)
    inst = get_instrument(symbol)
    htf = s.analysis.htf_frames(symbol, tf)
    try:
        res = run_backtest(df, tf, p, inst, htf, body.date_from, body.date_to, body.split,
                           body.capital, body.risk_pct)
        if body.walkforward:
            res["walkforward"] = run_walkforward(df, tf, p, inst, htf, body.wf_folds, body.wf_mode,
                                                 body.wf_min_trades, date_from=body.date_from,
                                                 date_to=body.date_to, capital=body.capital,
                                                 risk_pct=body.risk_pct)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    res["digits"] = inst.digits
    res["data_range"] = {"from": df.index[0].isoformat(), "to": df.index[-1].isoformat(), "bars": len(df)}
    if not body.include_trades:
        res.pop("trades", None)
    return res


# ---------------------------------------------------------------- signals history
def _ts(d: str | None, end: bool = False) -> int | None:
    if not d:
        return None
    dt = datetime.fromisoformat(d)
    ts = int(dt.timestamp()) if dt.tzinfo else int(datetime(dt.year, dt.month, dt.day).timestamp())
    return ts + 86399 if end and len(d) <= 10 else ts


@router.get("/signals")
def signals(request: Request, symbol: str | None = None, direction: str | None = None,
            min_score: int | None = None, result: str | None = None, date_from: str | None = None,
            date_to: str | None = None, source: str | None = None, limit: int = Query(500, le=5000)):
    rows = _svc(request).db.list_signals(symbol or None, direction or None, min_score, result or None,
                                         _ts(date_from), _ts(date_to, end=True), source or None, limit)
    closed = [r for r in rows if r["result_r"] is not None and r["status"] != "OPEN"]
    wins = [r for r in closed if r["result_r"] > 0]
    summary = {"count": len(rows), "closed": len(closed),
               "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
               "total_r": round(sum(r["result_r"] for r in closed), 2) if closed else 0}
    return {"items": rows, "summary": summary}


class BackfillIn(BaseModel):
    days: int = 30


@router.post("/signals/backfill")
def backfill(request: Request, body: BackfillIn):
    return {"inserted": _svc(request).analysis.backfill(max(1, min(body.days, 730)))}


# ---------------------------------------------------------------- alerts
@router.get("/settings/alerts")
def get_alerts(request: Request):
    n = _svc(request).notifier
    return {"settings": n.get_settings(), "channels": n.channel_status(), "calendar": n.calendar.status()}


@router.put("/settings/alerts")
def put_alerts(request: Request, body: dict):
    n = _svc(request).notifier
    try:
        return {"settings": n.save_settings(body), "channels": n.channel_status()}
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


class TestAlertIn(BaseModel):
    channel: str


@router.post("/alerts/test")
def test_alert(request: Request, body: TestAlertIn):
    try:
        return _svc(request).notifier.send_test(body.channel)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/alerts/weekly-summary")
def weekly_summary_now(request: Request):
    """Wysyła podsumowanie ostatnich 7 dni od razu (np. do sprawdzenia)."""
    return _svc(request).notifier.weekly_summary(force=True)


@router.get("/alerts/telegram/chats")
def telegram_chats(request: Request):
    """Pomocnik: pokazuje chat id osób, które napisały do bota (do wpisania w TELEGRAM_CHAT_ID)."""
    from ..notifications.channels import ChannelError
    try:
        return {"chats": _svc(request).notifier.telegram.find_chats()}
    except ChannelError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/alerts/log")
def alerts_log(request: Request, since_id: int = 0, limit: int = Query(100, le=1000)):
    return {"items": _svc(request).db.list_alerts(limit, since_id)}


# ---------------------------------------------------------------- data
@router.get("/data/status")
def data_status(request: Request):
    return _svc(request).market.data_status()


@router.post("/data/refresh")
def data_refresh(request: Request):
    return _svc(request).scheduler.cycle()


@router.post("/data/import")
async def data_import(request: Request, symbol: str = Form(...), tf: str = Form(...),
                      tz: str = Form("UTC"), file: UploadFile = File(...)):
    symbol = _check_symbol(symbol)
    tf = tf.upper()
    if tf not in ("5M", "15M", "1H", "4H", "1D"):
        raise HTTPException(400, "Interwał pliku: 5M, 15M, 1H, 4H lub 1D")
    content = await file.read()
    try:
        n = _svc(request).market.import_csv(symbol, tf, content, tz)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Nie udało się wczytać CSV: {exc}") from exc
    return {"imported": n, "symbol": symbol, "tf": tf}


@router.get("/fx")
def fx(request: Request):
    return _svc(request).market.fx_rates()
