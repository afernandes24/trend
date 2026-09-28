"""Notification System — filtry, cooldown, formatowanie, wysyłka alertów i raportów."""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from ..config import Settings
from ..database import Database
from ..instruments import get_instrument
from .calendar import EconomicCalendar
from .channels import ChannelError, EmailChannel, SmsChannel, TelegramChannel

log = logging.getLogger(__name__)

APP_NAME = "TREND FOLLOWING"
LOCAL_TZ = ZoneInfo("Europe/Warsaw")

DEFAULT_ALERT_SETTINGS = {
    "telegram": True,
    "email": True,
    "sms": False,
    "min_score": 75,              # 75 | 80 | 85
    "long": True,
    "short": True,
    "cooldown_minutes": 60,       # ten sam instrument + kierunek
    "require_mtf": True,          # tylko gdy 4H i 1H zgodne z kierunkiem
    "timeframes": ["15M"],
    # --- wolumen w alercie
    "capital": 10000.0,           # PLN
    "risk_pct": 1.0,              # % kapitału na transakcję
    # --- filtr spóźnionych alertów
    "stale_filter": True,
    "stale_r": 0.5,               # pomiń, jeśli cena odjechała od Entry o więcej niż 0.5R
    # --- ciche godziny (czas polski)
    "quiet_enabled": False,
    "quiet_from": "23:00",
    "quiet_to": "07:00",
    # --- ważne dane makro
    "news_filter": True,
    "news_window_minutes": 30,
    # --- raporty
    "result_notifications": True,
    "weekly_summary": True,
}


def fmt_price(v: float | None, digits: int) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}".replace(",", " ")


def fmt_pts(v: float, digits: int) -> str:
    return fmt_price(abs(v), digits)


def _hm(s: str) -> int:
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def in_quiet_hours(cfg: dict, now: datetime | None = None) -> bool:
    if not cfg.get("quiet_enabled"):
        return False
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    cur, a, b = now.hour * 60 + now.minute, _hm(cfg["quiet_from"]), _hm(cfg["quiet_to"])
    return (a <= cur < b) if a < b else (cur >= a or cur < b)


def position_size(sig: dict, cfg: dict, fx_rates: dict) -> dict | None:
    """Wolumen w lotach, tak by strata na SL = risk_pct% kapitału (PLN)."""
    inst = get_instrument(sig["symbol"])
    dist = abs(sig["entry"] - sig["sl"]) if sig.get("entry") is not None and sig.get("sl") is not None else 0
    rate = 1.0 if inst.currency == "PLN" else fx_rates.get(f"{inst.currency}PLN")
    if not dist or not rate:
        return None
    risk_amount = cfg["capital"] * cfg["risk_pct"] / 100
    loss_per_lot = dist * inst.value_per_point * rate
    raw = risk_amount / loss_per_lot
    lots = math.floor(raw / inst.lot_step + 1e-9) * inst.lot_step
    below_min = lots < inst.min_lot
    use = inst.min_lot if below_min else lots
    real = use * loss_per_lot
    return {"lots": round(use, 2), "risk_amount": risk_amount, "real_risk": real,
            "real_risk_pct": real / cfg["capital"] * 100, "below_min": below_min}


class Notifier:
    def __init__(self, db: Database, settings: Settings, fx: Callable[[], dict] | None = None,
                 calendar: EconomicCalendar | None = None):
        self.db = db
        self.email = EmailChannel(settings)
        self.sms = SmsChannel(settings)
        self.telegram = TelegramChannel(settings)
        self.fx = fx or (lambda: {"rates": {}})
        self.app_url = (settings.app_url or "").rstrip("/")
        self.calendar = calendar or EconomicCalendar()

    # ------------------------------------------------------------ settings
    def get_settings(self) -> dict:
        return {**DEFAULT_ALERT_SETTINGS, **(self.db.get_setting("alerts", {}) or {})}

    def save_settings(self, data: dict) -> dict:
        cur = self.get_settings()
        for k in DEFAULT_ALERT_SETTINGS:
            if k in data and data[k] is not None:
                cur[k] = data[k]
        cur["min_score"] = int(max(0, min(100, int(cur["min_score"]))))
        cur["cooldown_minutes"] = int(max(0, int(cur["cooldown_minutes"])))
        cur["capital"] = float(max(0.0, float(cur["capital"])))
        cur["risk_pct"] = float(min(100.0, max(0.01, float(cur["risk_pct"]))))
        cur["stale_r"] = float(max(0.05, float(cur["stale_r"])))
        cur["news_window_minutes"] = int(max(0, min(240, int(cur["news_window_minutes"]))))
        for k in ("quiet_from", "quiet_to"):
            _hm(cur[k])          # walidacja HH:MM (ValueError -> 400)
        self.db.set_setting("alerts", cur)
        return cur

    def channel_status(self) -> dict:
        return {
            "telegram": {"configured": self.telegram.configured(), "missing": self.telegram.missing()},
            "email": {"configured": self.email.configured(), "missing": self.email.missing()},
            "sms": {"configured": self.sms.configured(), "missing": self.sms.missing(), "provider": self.sms.provider},
        }

    # ------------------------------------------------------------ format
    def format_signal(self, sig: dict, cfg: dict | None = None) -> tuple[str, str]:
        cfg = cfg or self.get_settings()
        inst = get_instrument(sig["symbol"])
        d = inst.digits
        long = sig["direction"] == "LONG"
        emoji = "🟢" if long else "🔴"
        tp_r = sig.get("tp_r") or [1.5, 2.0, 3.0]
        reasons = [r["label"] for r in (sig.get("reasons") or []) if r.get("status") == "ok"]
        e = sig.get("entry")

        def lvl(name, v, sign):
            if v is None or e is None:
                return f"{name}: {fmt_price(v, d)}"
            return f"{name}: {fmt_price(v, d)}   ({sign}{fmt_pts(v - e, d)} pkt)"

        up, down = ("+", "−") if long else ("−", "+")
        lines = [
            "🚨 NOWY SETUP",
            "",
            f"{emoji} {sig['symbol']} — {sig['direction']}  ({sig['tf']})",
            f"Score: {sig['score']}/100",
            "",
            f"Entry: {fmt_price(e, d)}",
            lvl("SL", sig.get("sl"), down),
            lvl("TP1", sig.get("tp1"), up),
            lvl("TP2", sig.get("tp2"), up),
            lvl("TP3", sig.get("tp3"), up),
            "RR: " + " / ".join(f"1:{r:g}" for r in tp_r),
        ]
        if e is not None and sig.get("sl") is not None:
            sl_pts = fmt_pts(sig["sl"] - e, d)
            tp2_pts = fmt_pts(sig["tp2"] - e, d)
            lines += ["", "📐 W XTB licz od SWOJEJ ceny wejścia:",
                      f"SL = cena {down} {sl_pts} pkt  |  TP2 = cena {up} {tp2_pts} pkt"]
            ps = position_size(sig, cfg, (self.fx() or {}).get("rates", {}))
            if ps:
                lots = f"{ps['lots']:.2f}".replace(".", ",")
                risk_pln = f"{ps['real_risk']:,.0f}".replace(",", " ")
                cap_pln = f"{cfg['capital']:,.0f}".replace(",", " ")
                pct = f"{ps['real_risk_pct']:.2f}".replace(".", ",")
                lines += [f"💰 Wolumen: {lots} lota → ryzyko ok. {risk_pln} PLN ({pct}% z {cap_pln} PLN)"]
                if ps["below_min"]:
                    lines += ["⚠️ Nawet minimalny lot przekracza Twoje ryzyko — rozważ pominięcie."]
        if self.app_url and sig.get("id"):
            lines += ["", f"➕ Wszedłeś w pozycję? Zapisz ją: {self.app_url}/#/positions?signal={sig['id']}"]
        lines += ["", "Powody: " + (" + ".join(reasons) if reasons else "—")]
        hist = sig.get("historical")
        if hist and hist.get("trades"):
            lines += [f"Historical win rate: {hist['win_rate']}% (based on {hist['trades']} trades, "
                      f"{hist['from'][:10]} – {hist['to'][:10]}, backtest)"]
        lines += ["", "Ceny z Yahoo — w XTB mogą się różnić. Score ≠ prawdopodobieństwo zysku."]
        subject = f"🚨 {sig['symbol']} {sig['direction']} — score {sig['score']}/100 ({sig['tf']})"
        return subject, "\n".join(lines)

    @staticmethod
    def format_sms(sig: dict) -> str:
        d = get_instrument(sig["symbol"]).digits
        return (f"SETUP {sig['symbol']} {sig['direction']} {sig['tf']} score {sig['score']} | "
                f"E {fmt_price(sig['entry'], d)} SL {fmt_price(sig['sl'], d)} "
                f"TP1 {fmt_price(sig['tp1'], d)} TP2 {fmt_price(sig['tp2'], d)}")

    # ------------------------------------------------------------ filters
    def should_alert(self, sig: dict, cfg: dict, mtf_aligned: bool) -> tuple[bool, str]:
        if sig["score"] < cfg["min_score"]:
            return False, f"score {sig['score']} < {cfg['min_score']}"
        if sig["direction"] == "LONG" and not cfg["long"]:
            return False, "LONG wyłączone"
        if sig["direction"] == "SHORT" and not cfg["short"]:
            return False, "SHORT wyłączone"
        if sig["tf"] not in cfg["timeframes"]:
            return False, f"interwał {sig['tf']} poza listą alertów"
        if cfg["require_mtf"] and not mtf_aligned:
            return False, "wyższe interwały niezgodne"
        cur = sig.get("current_price")
        if cfg["stale_filter"] and cur is not None and sig.get("entry") is not None and sig.get("sl") is not None:
            risk = abs(sig["entry"] - sig["sl"])
            if risk > 0:
                moved = (cur - sig["entry"]) / risk * (1 if sig["direction"] == "LONG" else -1)
                if abs(moved) > cfg["stale_r"]:
                    kier = "w stronę TP" if moved > 0 else "w stronę SL"
                    return False, f"cena odjechała od Entry o {abs(moved):.2f}R {kier} (spóźniony alert)"
        if in_quiet_hours(cfg):
            return False, f"ciche godziny ({cfg['quiet_from']}–{cfg['quiet_to']})"
        if cfg["news_filter"] and cfg["news_window_minutes"] > 0:
            ev = self.calendar.blocking_event(sig["symbol"], window_minutes=cfg["news_window_minutes"])
            if ev:
                t = ev["time"].astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")
                return False, f"ważne dane makro: {ev['currency']} {ev['title']} ({t})"
        last = self.db.last_alert_ts(sig["symbol"], sig["direction"])
        if last and time.time() - last < cfg["cooldown_minutes"] * 60:
            return False, f"cooldown ({cfg['cooldown_minutes']} min)"
        return True, ""

    # ------------------------------------------------------------ dispatch
    def dispatch(self, sig: dict, mtf_aligned: bool) -> list[dict]:
        cfg = self.get_settings()
        ok, why = self.should_alert(sig, cfg, mtf_aligned)
        results = []
        if not ok:
            self.db.log_alert(signal_id=sig.get("id"), symbol=sig["symbol"], direction=sig["direction"],
                              channel="-", status="skipped", message=None, error=why)
            return [{"channel": "-", "status": "skipped", "reason": why}]
        subject, text = self.format_signal(sig, cfg)
        # zawsze zapisujemy alert w aplikacji (powiadomienie w przeglądarce)
        self.db.log_alert(signal_id=sig.get("id"), symbol=sig["symbol"], direction=sig["direction"],
                          channel="app", status="sent", message=text, error=None)
        results.append({"channel": "app", "status": "sent"})
        channels = ((cfg["telegram"], self.telegram, text), (cfg["email"], self.email, text),
                    (cfg["sms"], self.sms, self.format_sms(sig)))
        for enabled, ch, body in channels:
            if not enabled or not ch.configured():
                continue            # kanał wyłączony albo bez konfiguracji (status widać w UI)
            try:
                ch.send(subject, body)
                status, err = "sent", None
            except ChannelError as exc:
                status, err = "failed", str(exc)
                log.warning("Alert %s nieudany: %s", ch.name, exc)
            self.db.log_alert(signal_id=sig.get("id"), symbol=sig["symbol"], direction=sig["direction"],
                              channel=ch.name, status=status, message=body, error=err)
            results.append({"channel": ch.name, "status": status, "error": err})
        return results

    def send_info(self, subject: str, text: str, symbol: str | None = None, direction: str | None = None,
                  signal_id: int | None = None) -> list[dict]:
        """Wiadomości informacyjne (wyniki, raporty) — Telegram i e-mail, bez SMS i bez cooldownu."""
        cfg = self.get_settings()
        out = []
        for enabled, ch in ((cfg["telegram"], self.telegram), (cfg["email"], self.email)):
            if not enabled or not ch.configured():
                continue
            try:
                ch.send(subject, text)
                status, err = "info", None
            except ChannelError as exc:
                status, err = "failed", str(exc)
            self.db.log_alert(signal_id=signal_id, symbol=symbol, direction=direction, channel=ch.name,
                              status=status, message=text, error=err)
            out.append({"channel": ch.name, "status": status, "error": err})
        return out

    # ------------------------------------------------------------ results & reports
    def notify_result(self, sig: dict, res: dict) -> list[dict]:
        """Wiadomość, gdy alertowany sygnał dojdzie do TP / SL / wygaśnie."""
        cfg = self.get_settings()
        if not cfg["result_notifications"] or not sig.get("id") or not self.db.alert_sent_for(sig["id"]):
            return []
        r = res.get("result_r") or 0.0
        result = res.get("result")
        icon = "✅" if r > 0 else "❌" if r < 0 else "➖"
        label = {"SL": "Stop Loss", "EXPIRED": "wygasł (brak rozstrzygnięcia)"}.get(result, f"{result} osiągnięty")
        opened = datetime.fromtimestamp(sig["bar_ts"], tz=timezone.utc).astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")
        text = (f"{icon} {sig['symbol']} {sig['direction']} ({sig['tf']}) → {label}\n"
                f"Wynik: {r:+.2f}R\n"
                f"Sygnał z {opened}, score {sig['score']}.\n\n"
                "Wynik liczony od cen z alertu (Yahoo) — w XTB mógł być nieco inny.")
        return self.send_info(f"{icon} {sig['symbol']} {sig['direction']} → {result} ({r:+.2f}R)", text,
                              sig["symbol"], sig["direction"], sig["id"])

    def weekly_summary(self, now: datetime | None = None, force: bool = False) -> dict | None:
        """Podsumowanie tygodnia: w piątek po 23:00 (czas PL) albo w weekend, raz na tydzień."""
        cfg = self.get_settings()
        if not cfg["weekly_summary"] and not force:
            return None
        now_l = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
        due = (now_l.weekday() == 4 and now_l.hour >= 23) or now_l.weekday() >= 5
        week = f"{now_l.isocalendar().year}-W{now_l.isocalendar().week:02d}"
        if not force and (not due or self.db.get_setting("weekly_summary_sent") == week):
            return None
        since = int((now_l - timedelta(days=7)).timestamp())
        sigs = [s for s in self.db.list_signals(source="LIVE", date_from=since, limit=5000)
                if self.db.alert_sent_for(s["id"])]
        if not sigs and not force and not any((p["closed_at"] or 0) >= since
                                              for p in self.db.list_positions("CLOSED")):
            self.db.set_setting("weekly_summary_sent", week)
            return None
        closed = [s for s in sigs if s["status"] != "OPEN" and s["result_r"] is not None]
        wins = [s for s in closed if s["result_r"] > 0]
        total = sum(s["result_r"] for s in closed)
        by_sym: dict[str, float] = {}
        for s in closed:
            by_sym[s["symbol"]] = by_sym.get(s["symbol"], 0.0) + s["result_r"]
        best = sorted(by_sym.items(), key=lambda x: -x[1])
        lines = [f"📊 PODSUMOWANIE TYGODNIA ({week})", "",
                 f"Alerty: {len(sigs)}  (zamknięte: {len(closed)}, otwarte: {len(sigs) - len(closed)})"]
        if closed:
            lines += [f"Win rate: {len(wins) / len(closed) * 100:.0f}%",
                      f"Wynik: {total:+.2f}R  (przy 1% ryzyka ≈ {total:+.1f}% konta)"]
            lines += ["", "Instrumenty:"] + [f"• {sym}: {r:+.2f}R" for sym, r in best]
        pos = [p for p in self.db.list_positions("CLOSED") if (p["closed_at"] or 0) >= since]
        if pos:
            pnl = sum(p["pnl_pln"] or 0 for p in pos)
            won = sum(1 for p in pos if (p["pnl_pln"] or 0) > 0)
            lines += ["", f"💼 Twoje pozycje: {len(pos)} zamkniętych, {won} na plusie",
                      f"Wynik: {pnl:+,.2f} PLN".replace(",", " ")]
        lines += ["", "Wyniki alertów liczone od cen z alertów (Yahoo), bez spreadu."]
        text = "\n".join(lines)
        sent = self.send_info(f"📊 {APP_NAME} — podsumowanie tygodnia {week}", text)
        self.db.set_setting("weekly_summary_sent", week)
        return {"week": week, "alerts": len(sigs), "closed": len(closed), "total_r": round(total, 2), "sent": sent,
                "text": text}

    # ------------------------------------------------------------ test
    def send_test(self, channel: str) -> dict:
        ch = {"email": self.email, "sms": self.sms, "telegram": self.telegram}.get(channel)
        if ch is None:
            raise ValueError("channel: telegram | email | sms")
        sample = {"symbol": "US100", "direction": "LONG", "tf": "15M", "score": 0, "entry": None, "sl": None,
                  "tp1": None, "tp2": None, "tp3": None, "reasons": []}
        text = (f"To jest wiadomość testowa {APP_NAME}. Konfiguracja kanału działa.\n\n"
                "Przykładowy format alertu:\n\n" + self.format_signal(sample)[1])
        short = {"sms": f"{APP_NAME}: test SMS OK",
                 "telegram": f"✅ {APP_NAME}: Telegram działa. Tu będą przychodzić alerty o nowych setupach."}
        try:
            ch.send(f"{APP_NAME} — test powiadomień", short.get(channel, text))
            return {"ok": True}
        except ChannelError as exc:
            return {"ok": False, "error": str(exc)}
