"""REST API (FastAPI). Wszystkie endpointy pod /api."""
from __future__ import annotations

import json
import math
import time
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
    bot = getattr(s, "bot", None)
    return {"ok": True, "mode": "ANALIZA (brak wykonywania zleceń)", "provider": s.market.provider.name,
            "scheduler": s.scheduler.status(), "last_scan": s.analysis.last_scan,
            "telegram_bot": {"running": bool(bot and bot.running()), "error": bot.last_error if bot else None}}


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
    tiles = [_tile(a) for a in items]
    # „Wszedłem” tylko dla alertów faktycznie wysłanych na Telegram (ostatnie 12 h, sygnał nadal otwarty)
    recent: dict[str, dict] = {}
    for r in s.db.sent_alerts("telegram", since_ts=int(time.time()) - 12 * 3600, limit=100):
        if r["status"] == "OPEN" and r["symbol"] not in recent:
            recent[r["symbol"]] = {"signal_id": r["id"], "direction": r["direction"], "ts": r["alert_ts"],
                                   "taken": bool(s.db.positions_for_signal(r["id"]))}
    for t in tiles:
        t["alert"] = recent.get(t["symbol"])
    return {"items": tiles, "last_scan": s.analysis.last_scan, "scheduler": s.scheduler.status()}


def _tile(a: dict) -> dict:
    keys = ("symbol", "name", "category", "digits", "price", "price_time", "change_pct", "trend", "rsi", "adx",
            "score", "band", "direction", "signal", "error", "setup_tf", "min_score")
    t = {k: a.get(k) for k in keys}
    mtf = a.get("mtf") or {}
    t["mtf"] = [{"tf": r["tf"], "trend": r.get("trend"), "agree": r.get("agree")} for r in mtf.get("rows", [])]
    t["higher_tf_aligned"] = mtf.get("higher_tf_aligned")
    lv = ((a.get("timeframes") or {}).get(a.get("setup_tf") or "15M") or {}).get("levels") or {}
    if lv.get("entry") is not None:
        t["sl_d"] = abs(lv["entry"] - lv["sl"])
        t["tp_d"] = abs(lv["tp2"] - lv["entry"])
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


# ---------------------------------------------------------------- korekta cen do XTB
def _is_cash(m, symbol: str) -> bool:
    """Indeks kasowy (notowany tylko w sesji giełdowej) — korektę robimy wtedy w godzinach sesji."""
    src = getattr(m.provider, "source_symbol", lambda s: None)(symbol)
    return src.startswith("^") if src else symbol in ("GER40", "UK100")


@router.get("/prices/offsets")
def price_offsets(request: Request):
    m = _svc(request).market
    offs = m.offsets()
    rows = []
    for inst in INSTRUMENTS:
        raw, raw_time = m.raw_last(inst.symbol)
        o = offs.get(inst.symbol) or {}
        off = float(o.get("offset") or 0.0)
        rows.append({"symbol": inst.symbol, "name": inst.name, "digits": inst.digits,
                     "cash_index": _is_cash(m, inst.symbol),
                     "src_price": raw, "src_time": raw_time, "offset": off if o else None,
                     "adjusted": (raw + off) if raw is not None else None,
                     "calibrated_at": o.get("ts"), "xtb_price": o.get("xtb_price")})
    return {"items": rows}


@router.put("/prices/offsets")
def set_price_offsets(request: Request, body: dict):
    s = _svc(request)
    try:
        deltas = s.market.set_offsets({k: (None if v in (None, "") else float(v)) for k, v in body.items()})
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    s.analysis.apply_offset_change(deltas)
    s.positions.apply_offset_change(deltas)
    s.analysis.scan_all(dispatch_alerts=False)
    return {"changed": deltas, **price_offsets(request)}


# ---------------------------------------------------------------- positions (Moje pozycje)
def _pos(request: Request):
    return _svc(request).positions


def _pos_call(fn, *a, **k):
    from ..services.positions import PositionError
    try:
        return fn(*a, **k)
    except PositionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/positions")
def positions(request: Request):
    return _pos(request).overview()


@router.get("/positions/prefill")
def positions_prefill(request: Request, signal: int):
    return _pos_call(_pos(request).prefill, signal)


@router.post("/positions")
def positions_open(request: Request, body: dict):
    return _pos_call(_pos(request).open, body)


@router.put("/positions/{pos_id}")
def positions_update(request: Request, pos_id: int, body: dict):
    return _pos_call(_pos(request).update, pos_id, body)


class CloseIn(BaseModel):
    exit_price: float | None = None
    pnl_pln: float | None = None


@router.post("/positions/{pos_id}/close")
def positions_close(request: Request, pos_id: int, body: CloseIn):
    return _pos_call(_pos(request).close, pos_id, body.exit_price, body.pnl_pln)


@router.post("/positions/{pos_id}/reopen")
def positions_reopen(request: Request, pos_id: int):
    return _pos_call(_pos(request).reopen, pos_id)


@router.delete("/positions/{pos_id}")
def positions_delete(request: Request, pos_id: int):
    if not _pos(request).delete(pos_id):
        raise HTTPException(404, "Nie ma takiej pozycji")
    return {"ok": True}


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
    s = _svc(request)
    bot = getattr(s, "bot", None)
    try:
        return {"chats": s.notifier.telegram.find_chats(seen=bot.seen_chats if bot and bot.running() else None)}
    except ChannelError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/alerts/sent")
def alerts_sent(request: Request, days: int = Query(30, ge=0, le=3650), limit: int = Query(200, le=2000)):
    """Dziennik: tylko alerty wysłane na Telegram, z wynikiem sygnału i Twoją pozycją (jeśli weszłeś)."""
    s = _svc(request)
    since = int(time.time()) - days * 86400 if days else None
    rows = s.db.sent_alerts("telegram", since_ts=since, limit=limit)
    for r in rows:
        r["digits"] = get_instrument(r["symbol"]).digits
        mine = s.db.positions_for_signal(r["id"])
        r["position"] = ({k: mine[0][k] for k in ("id", "status", "entry", "lots", "pnl_pln")} if mine else None)
    return {"items": rows}


@router.get("/stats")
def stats(request: Request, days: int = Query(30, ge=0, le=3650)):
    from ..services.stats import alert_stats, trade_stats
    s = _svc(request)
    cfg = s.notifier.get_settings()
    d = days or None
    closed = [s.positions.enrich(p) for p in s.db.list_positions("CLOSED")]
    return {"days": days, "capital": cfg["capital"], "risk_pct": cfg["risk_pct"],
            "tp_r": list(s.analysis.get_params().tp_r),
            "alerts": alert_stats(s.db, list(s.analysis.get_params().tp_r), cfg["risk_pct"], d),
            "trades": trade_stats(closed, cfg["capital"], d)}


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
