"""Moje pozycje — dziennik transakcji otwartych na podstawie alertów + licznik zysków i strat.

Ceny wpisujesz z XTB, więc wynik liczony jest na prawdziwych cenach Twojej pozycji.
Dla OTWARTYCH pozycji wynik jest szacowany: bierzemy zmianę ceny w źródle danych
od momentu zapisu pozycji (ref_price) i dodajemy ją do Twojej ceny wejścia —
dzięki temu różnica między Yahoo a XTB (np. futures vs spot) się znosi.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..database import Database
from ..instruments import get_instrument
from ..market_data.service import MarketDataService
from ..notifications.dispatcher import Notifier, position_size

LOCAL_TZ = ZoneInfo("Europe/Warsaw")


class PositionError(ValueError):
    pass


class PositionService:
    def __init__(self, db: Database, market: MarketDataService, notifier: Notifier):
        self.db = db
        self.market = market
        self.notifier = notifier

    # ------------------------------------------------------------ helpers
    def _fx(self, currency: str) -> float:
        if currency == "PLN":
            return 1.0
        rates = (self.market.fx_rates() or {}).get("rates", {})
        return float(rates.get(f"{currency}PLN") or 1.0)

    def _last_price(self, symbol: str) -> float | None:
        for tf in ("5M", "1H"):
            df = self.market.get_candles(symbol, tf)
            if not df.empty:
                return float(df["close"].iloc[-1])
        return None

    def pln(self, symbol: str, points: float, lots: float) -> float:
        inst = get_instrument(symbol)
        return points * lots * inst.value_per_point * self._fx(inst.currency)

    @staticmethod
    def _validate(d: dict) -> dict:
        try:
            inst = get_instrument(str(d.get("symbol", "")))
        except KeyError as exc:
            raise PositionError(str(exc)) from exc
        direction = str(d.get("direction", "")).upper()
        if direction not in ("LONG", "SHORT"):
            raise PositionError("Kierunek: LONG albo SHORT")
        try:
            entry = float(d["entry"])
            lots = float(d["lots"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PositionError("Podaj cenę wejścia i wolumen") from exc
        if entry <= 0 or lots <= 0:
            raise PositionError("Cena wejścia i wolumen muszą być > 0")
        sl = float(d["sl"]) if d.get("sl") not in (None, "") else None
        tp = float(d["tp"]) if d.get("tp") not in (None, "") else None
        sgn = 1 if direction == "LONG" else -1
        if sl is not None and (sl - entry) * sgn >= 0:
            raise PositionError(f"Dla {direction} Stop Loss musi być {'poniżej' if sgn > 0 else 'powyżej'} ceny wejścia")
        if tp is not None and (tp - entry) * sgn <= 0:
            raise PositionError(f"Dla {direction} Take Profit musi być {'powyżej' if sgn > 0 else 'poniżej'} ceny wejścia")
        return {"symbol": inst.symbol, "direction": direction, "entry": entry, "lots": lots, "sl": sl, "tp": tp,
                "note": (str(d.get("note") or "").strip() or None),
                "signal_id": int(d["signal_id"]) if d.get("signal_id") not in (None, "") else None}

    # ------------------------------------------------------------ prefill from alert
    def prefill(self, signal_id: int) -> dict:
        sig = self.db.get_signal(signal_id)
        if not sig:
            raise PositionError("Nie znaleziono sygnału")
        cfg = self.notifier.get_settings()
        risk = abs(sig["entry"] - sig["sl"])
        ps = position_size(sig, cfg, (self.market.fx_rates() or {}).get("rates", {}))
        return {
            "signal_id": sig["id"], "symbol": sig["symbol"], "direction": sig["direction"],
            "score": sig["score"], "bar_ts": sig["bar_ts"],
            "signal_entry": sig["entry"],
            "sl_distance": risk,
            "tp_distances": [abs(sig[k] - sig["entry"]) for k in ("tp1", "tp2", "tp3")],
            "suggested_lots": ps["lots"] if ps else None,
            "current_price": self._last_price(sig["symbol"]),
            "digits": get_instrument(sig["symbol"]).digits,
        }

    # ------------------------------------------------------------ CRUD
    def open(self, d: dict) -> dict:
        v = self._validate(d)
        v["ref_price"] = self._last_price(v["symbol"])
        pos_id = self.db.insert_position(v)
        return self.enrich(self.db.get_position(pos_id))

    def update(self, pos_id: int, d: dict) -> dict:
        cur = self.db.get_position(pos_id)
        if not cur:
            raise PositionError("Nie ma takiej pozycji")
        merged = {**cur, **{k: v for k, v in d.items() if k in ("entry", "sl", "tp", "lots", "note")}}
        v = self._validate(merged)
        fields = {k: v[k] for k in ("entry", "sl", "tp", "lots", "note")}
        if cur["status"] == "CLOSED" and not cur["pnl_manual"] and cur["exit_price"] is not None:
            sgn = 1 if cur["direction"] == "LONG" else -1
            fields["pnl_pln"] = round(self.pln(cur["symbol"], (cur["exit_price"] - v["entry"]) * sgn, v["lots"]), 2)
        self.db.update_position(pos_id, **fields)
        return self.enrich(self.db.get_position(pos_id))

    def close(self, pos_id: int, exit_price: float | None = None, pnl_pln: float | None = None) -> dict:
        cur = self.db.get_position(pos_id)
        if not cur:
            raise PositionError("Nie ma takiej pozycji")
        if exit_price in (None, "") and pnl_pln in (None, ""):
            raise PositionError("Podaj cenę zamknięcia albo wynik w PLN z XTB")
        sgn = 1 if cur["direction"] == "LONG" else -1
        fields = {"status": "CLOSED", "closed_at": int(time.time())}
        if exit_price not in (None, ""):
            fields["exit_price"] = float(exit_price)
        if pnl_pln not in (None, ""):
            fields["pnl_pln"], fields["pnl_manual"] = round(float(pnl_pln), 2), 1
        else:
            fields["pnl_pln"] = round(self.pln(cur["symbol"], (float(exit_price) - cur["entry"]) * sgn, cur["lots"]), 2)
            fields["pnl_manual"] = 0
        self.db.update_position(pos_id, **fields)
        return self.enrich(self.db.get_position(pos_id))

    def reopen(self, pos_id: int) -> dict:
        if not self.db.get_position(pos_id):
            raise PositionError("Nie ma takiej pozycji")
        self.db.update_position(pos_id, status="OPEN", exit_price=None, closed_at=None, pnl_pln=None, pnl_manual=0)
        return self.enrich(self.db.get_position(pos_id))

    def apply_offset_change(self, deltas: dict[str, float]) -> None:
        """Cena odniesienia otwartych pozycji musi przesunąć się razem z korektą (inaczej fałszywy zysk/strata)."""
        for p in self.db.list_positions("OPEN"):
            d = deltas.get(p["symbol"])
            if d and p["ref_price"] is not None:
                self.db.update_position(p["id"], ref_price=p["ref_price"] + d)

    def delete(self, pos_id: int) -> bool:
        return self.db.delete_position(pos_id)

    # ------------------------------------------------------------ calculations
    def enrich(self, p: dict) -> dict:
        inst = get_instrument(p["symbol"])
        sgn = 1 if p["direction"] == "LONG" else -1
        out = {**p, "digits": inst.digits, "currency": inst.currency}
        out["risk_pln"] = round(self.pln(p["symbol"], abs(p["entry"] - p["sl"]), p["lots"]), 2) if p["sl"] else None
        out["target_pln"] = round(self.pln(p["symbol"], abs(p["tp"] - p["entry"]), p["lots"]), 2) if p["tp"] else None
        out["rr"] = round(abs(p["tp"] - p["entry"]) / abs(p["entry"] - p["sl"]), 2) if p["sl"] and p["tp"] else None
        if p["status"] == "OPEN":
            now = self._last_price(p["symbol"])
            if now is not None and p["ref_price"] is None:
                # np. po zmianie źródła danych — ustawiamy punkt odniesienia od teraz
                self.db.update_position(p["id"], ref_price=now)
                p = {**p, "ref_price": now}
            if now is not None and p["ref_price"]:
                est = p["entry"] + (now - p["ref_price"])
                out["est_price"] = est
                out["est_pnl_pln"] = round(self.pln(p["symbol"], (est - p["entry"]) * sgn, p["lots"]), 2)
                hit = None
                if p["sl"] is not None and (est - p["sl"]) * sgn <= 0:
                    hit = "SL"
                elif p["tp"] is not None and (est - p["tp"]) * sgn >= 0:
                    hit = "TP"
                out["probably_hit"] = hit
            else:
                out["est_price"] = out["est_pnl_pln"] = out["probably_hit"] = None
        else:
            out["r_multiple"] = round(p["pnl_pln"] / out["risk_pln"], 2) if out["risk_pln"] and p["pnl_pln"] is not None else None
        return out

    def overview(self) -> dict:
        open_ = [self.enrich(p) for p in self.db.list_positions("OPEN")]
        closed = [self.enrich(p) for p in self.db.list_positions("CLOSED")]
        return {"open": open_, "closed": closed, "stats": self.stats(closed, open_)}

    def stats(self, closed: list[dict], open_: list[dict] | None = None, now: datetime | None = None) -> dict:
        now_l = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
        day0 = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
        week0 = day0 - timedelta(days=day0.weekday())
        month0 = day0.replace(day=1)

        def total_since(t0: datetime) -> float:
            ts = t0.timestamp()
            return round(sum(p["pnl_pln"] or 0 for p in closed if (p["closed_at"] or 0) >= ts), 2)

        vals = [p["pnl_pln"] or 0 for p in closed]
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v < 0]
        return {
            "trades": len(closed),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "total_pln": round(sum(vals), 2),
            "today_pln": total_since(day0), "week_pln": total_since(week0), "month_pln": total_since(month0),
            "avg_win_pln": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss_pln": round(sum(losses) / len(losses), 2) if losses else None,
            "best_pln": max(vals) if vals else None, "worst_pln": min(vals) if vals else None,
            "open_count": len(open_ or []),
            "open_est_pln": round(sum(p.get("est_pnl_pln") or 0 for p in (open_ or [])), 2),
            "open_risk_pln": round(sum(p.get("risk_pln") or 0 for p in (open_ or [])), 2),
        }

