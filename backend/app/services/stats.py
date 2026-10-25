"""Statystyki: alerty wysłane na Telegram (ogólne) i Twoje transakcje (Moje pozycje)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..database import Database

LOCAL_TZ = ZoneInfo("Europe/Warsaw")
PERIODS = {"week": "Ten tydzień", "month": "Ten miesiąc", "all": "Całość"}


def period_start(period: str, now: datetime | None = None) -> int | None:
    """Początek okresu kalendarzowego (czas polski): tydzień od poniedziałku, miesiąc od 1. dnia."""
    now_l = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    day0 = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return int((day0 - timedelta(days=day0.weekday())).timestamp())
    if period == "month":
        return int(day0.replace(day=1).timestamp())
    return None


def score_bucket(score: int | None) -> int | None:
    """Przedział co 5 punktów: 75 = 75–79, 80 = 80–84 … (100 wpada do 95)."""
    if score is None:
        return None
    return min(95, int(score) // 5 * 5)


def _buckets(rows: list[dict], val, risk_pct: float | None = None, capital: float | None = None) -> list[dict]:
    out: dict[int, dict] = {}
    for r in rows:
        b = score_bucket(r.get("score"))
        if b is None:
            continue
        v = val(r)
        g = out.setdefault(b, {"from": b, "to": 100 if b == 95 else b + 4, "n": 0, "wins": 0, "total": 0.0})
        g["n"] += 1
        g["wins"] += 1 if v > 0 else 0
        g["total"] += v
    res = []
    for b in sorted(out):
        g = out[b]
        g["total"] = round(g["total"], 2)
        g["win_rate"] = round(g["wins"] / g["n"] * 100, 1)
        g["avg"] = round(g["total"] / g["n"], 2)
        if risk_pct is not None:
            g["pct"] = round(g["total"] * risk_pct, 2)
        if capital:
            g["pct"] = round(g["total"] / capital * 100, 2)
        res.append(g)
    return res


def style_r(row: dict, tp_r: list[float]) -> float | None:
    """Wynik przy Twoim sposobie grania: TP ustawiony na TP2, po TP1 SL przesunięty na wejście.

    TP1 (a potem SL albo brak dalszego ruchu) = 0R, TP2/TP3 = R z TP2, SL = −1R, wygasły = wynik na zamknięciu.
    """
    res, r = row.get("result"), row.get("result_r")
    if r is None:
        return None
    if res == "SL":
        return -1.0
    if res == "TP1":
        return 0.0
    if res in ("TP2", "TP3"):
        return float(tp_r[1] if len(tp_r) > 1 else r)
    return float(r)


def _group(rows: list[dict], key: str, val, risk_pct: float | None = None, capital: float | None = None) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        v = val(r)
        g = out.setdefault(r[key], {key: r[key], "n": 0, "wins": 0, "total": 0.0})
        g["n"] += 1
        g["wins"] += 1 if v > 0 else 0
        g["total"] += v
    res = []
    for g in out.values():
        g["total"] = round(g["total"], 2)
        g["win_rate"] = round(g["wins"] / g["n"] * 100, 1) if g["n"] else None
        if risk_pct is not None:
            g["pct"] = round(g["total"] * risk_pct, 2)        # % konta przy danym ryzyku na transakcję
        if capital:
            g["pct"] = round(g["total"] / capital * 100, 2)   # % kapitału
        res.append(g)
    return sorted(res, key=lambda g: -g["total"])


def alert_stats(db: Database, tp_r: list[float], risk_pct: float, days: int | None = None,
                since_ts: int | None = None) -> dict:
    since = since_ts if since_ts is not None else (int(time.time()) - days * 86400 if days else None)
    rows = db.sent_alerts("telegram", since_ts=since, limit=5000)
    closed = [r for r in rows if r["status"] != "OPEN" and r["result_r"] is not None]
    for r in closed:
        r["style_r"] = style_r(r, tp_r)
    wins = [r for r in closed if r["result_r"] > 0]
    s_wins = [r for r in closed if r["style_r"] > 0]
    total = sum(r["result_r"] for r in closed)
    s_total = sum(r["style_r"] for r in closed)
    taken = sum(1 for r in rows if db.positions_for_signal(r["id"]))
    results = {}
    for r in closed:
        results[r["result"]] = results.get(r["result"], 0) + 1
    gp = sum(r["style_r"] for r in closed if r["style_r"] > 0)
    gl = -sum(r["style_r"] for r in closed if r["style_r"] < 0)
    return {
        "count": len(rows), "open": len(rows) - len(closed), "closed": len(closed), "taken": taken,
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "total_r": round(total, 2),
        "style_win_rate": round(len(s_wins) / len(closed) * 100, 1) if closed else None,
        "style_total_r": round(s_total, 2),
        "style_avg_r": round(s_total / len(closed), 2) if closed else None,
        "style_pct": round(s_total * risk_pct, 2),
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "results": results,
        "by_symbol": _group(closed, "symbol", lambda r: r["style_r"], risk_pct=risk_pct),
        "by_direction": _group(closed, "direction", lambda r: r["style_r"], risk_pct=risk_pct),
        "by_score": _buckets(closed, lambda r: r["style_r"], risk_pct=risk_pct),
    }


def signal_score_stats(db: Database, tp_r: list[float], risk_pct: float, since_ts: int | None = None) -> list[dict]:
    """Wszystkie sygnały skanera (także te, które nie poszły na Telegram) — skuteczność wg score."""
    rows = [r for r in db.list_signals(source="LIVE", date_from=since_ts, limit=20000)
            if r["status"] != "OPEN" and r["result_r"] is not None]
    for r in rows:
        r["style_r"] = style_r(r, tp_r)
    return _buckets(rows, lambda r: r["style_r"], risk_pct=risk_pct)


def trade_stats(positions: list[dict], capital: float, days: int | None = None, since_ts: int | None = None,
                scores: dict[int, int] | None = None) -> dict:
    since = since_ts if since_ts is not None else (int(time.time()) - days * 86400 if days else 0)
    closed = [p for p in positions if p["status"] == "CLOSED" and (p["closed_at"] or 0) >= since
              and p["pnl_pln"] is not None]
    vals = [p["pnl_pln"] for p in closed]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    rs = [p["r_multiple"] for p in closed if p.get("r_multiple") is not None]
    total = sum(vals)
    return {
        "trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "total_pln": round(total, 2),
        "pct_capital": round(total / capital * 100, 2) if capital else None,
        "avg_win_pln": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_pln": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
        "best_pln": max(vals) if vals else None, "worst_pln": min(vals) if vals else None,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "from_alerts": sum(1 for p in closed if p.get("signal_id")),
        "by_symbol": _group(closed, "symbol", lambda p: p["pnl_pln"], capital=capital),
        "by_direction": _group(closed, "direction", lambda p: p["pnl_pln"], capital=capital),
        "by_score": _buckets([{**p, "score": (scores or {}).get(p.get("signal_id"))} for p in closed],
                             lambda p: p["pnl_pln"], capital=capital),
    }


def summary_row(db: Database, positions: list[dict], tp_r: list[float], risk_pct: float, capital: float,
                period: str) -> dict:
    since = period_start(period)
    a = alert_stats(db, tp_r, risk_pct, since_ts=since)
    t = trade_stats(positions, capital, since_ts=since or 0)
    return {"period": period, "label": PERIODS[period],
            "alerts": a["count"], "alerts_closed": a["closed"], "alerts_win_rate": a["style_win_rate"],
            "alerts_r": a["style_total_r"], "alerts_pct": a["style_pct"],
            "trades": t["trades"], "trades_win_rate": t["win_rate"], "trades_pln": t["total_pln"],
            "trades_pct": t["pct_capital"]}
