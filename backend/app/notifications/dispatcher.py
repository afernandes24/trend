"""Notification System — filtrowanie, cooldown, formatowanie i wysyłka alertów."""
from __future__ import annotations

import logging
import time

from ..config import Settings
from ..database import Database
from ..instruments import get_instrument
from .channels import ChannelError, EmailChannel, SmsChannel, TelegramChannel

log = logging.getLogger(__name__)

DEFAULT_ALERT_SETTINGS = {
    "telegram": True,
    "email": True,
    "sms": False,
    "min_score": 75,            # 75 | 80 | 85
    "long": True,
    "short": True,
    "cooldown_minutes": 60,     # ten sam instrument + kierunek
    "require_mtf": True,        # tylko gdy 4H i 1H zgodne z kierunkiem
    "timeframes": ["15M"],
}


def fmt_price(v: float | None, digits: int) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{digits}f}".replace(",", " ")
    return s


class Notifier:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.email = EmailChannel(settings)
        self.sms = SmsChannel(settings)
        self.telegram = TelegramChannel(settings)

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
        self.db.set_setting("alerts", cur)
        return cur

    def channel_status(self) -> dict:
        return {
            "telegram": {"configured": self.telegram.configured(), "missing": self.telegram.missing()},
            "email": {"configured": self.email.configured(), "missing": self.email.missing()},
            "sms": {"configured": self.sms.configured(), "missing": self.sms.missing(), "provider": self.sms.provider},
        }

    # ------------------------------------------------------------ format
    @staticmethod
    def format_signal(sig: dict) -> tuple[str, str]:
        inst = get_instrument(sig["symbol"])
        d = inst.digits
        emoji = "🟢" if sig["direction"] == "LONG" else "🔴"
        tp_r = sig.get("tp_r") or [1.5, 2.0, 3.0]
        reasons = [r["label"] for r in (sig.get("reasons") or []) if r.get("status") == "ok"]
        lines = [
            "🚨 NOWY SETUP",
            "",
            f"{emoji} {sig['symbol']} — {sig['direction']}  ({sig['tf']})",
            "",
            f"Score: {sig['score']}/100",
            "",
            f"Entry: {fmt_price(sig['entry'], d)}",
            f"SL: {fmt_price(sig['sl'], d)}",
            f"TP1: {fmt_price(sig['tp1'], d)}",
            f"TP2: {fmt_price(sig['tp2'], d)}",
            f"TP3: {fmt_price(sig['tp3'], d)}",
            "",
            "RR: " + " / ".join(f"1:{r:g}" for r in tp_r),
            "",
            "Powody:",
            " + ".join(reasons) if reasons else "—",
        ]
        hist = sig.get("historical")
        if hist and hist.get("trades"):
            lines += ["", f"Historical win rate: {hist['win_rate']}% (based on {hist['trades']} trades, "
                          f"{hist['from'][:10]} – {hist['to'][:10]}, backtest)"]
        lines += ["", "Score nie jest prawdopodobieństwem zysku. Aplikacja analityczna — nie składa zleceń."]
        subject = f"🚨 {sig['symbol']} {sig['direction']} — score {sig['score']}/100 ({sig['tf']})"
        return subject, "\n".join(lines)

    @staticmethod
    def format_sms(sig: dict) -> str:
        d = get_instrument(sig["symbol"]).digits
        return (f"SETUP {sig['symbol']} {sig['direction']} {sig['tf']} score {sig['score']} | "
                f"E {fmt_price(sig['entry'], d)} SL {fmt_price(sig['sl'], d)} "
                f"TP1 {fmt_price(sig['tp1'], d)} TP2 {fmt_price(sig['tp2'], d)}")

    # ------------------------------------------------------------ dispatch
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
        last = self.db.last_alert_ts(sig["symbol"], sig["direction"])
        if last and time.time() - last < cfg["cooldown_minutes"] * 60:
            return False, f"cooldown ({cfg['cooldown_minutes']} min)"
        return True, ""

    def dispatch(self, sig: dict, mtf_aligned: bool) -> list[dict]:
        cfg = self.get_settings()
        ok, why = self.should_alert(sig, cfg, mtf_aligned)
        results = []
        if not ok:
            self.db.log_alert(signal_id=sig.get("id"), symbol=sig["symbol"], direction=sig["direction"],
                              channel="-", status="skipped", message=None, error=why)
            return [{"channel": "-", "status": "skipped", "reason": why}]
        subject, text = self.format_signal(sig)
        # zawsze zapisujemy alert w aplikacji (powiadomienie w przeglądarce)
        self.db.log_alert(signal_id=sig.get("id"), symbol=sig["symbol"], direction=sig["direction"],
                          channel="app", status="sent", message=text, error=None)
        results.append({"channel": "app", "status": "sent"})
        channels = ((cfg["telegram"], self.telegram, text), (cfg["email"], self.email, text),
                    (cfg["sms"], self.sms, self.format_sms(sig)))
        for enabled, ch, body in channels:
            if not enabled or not ch.configured():
                continue            # kanał wyłączony albo bez konfiguracji w .env (status widać w UI)
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

    def send_test(self, channel: str) -> dict:
        ch = {"email": self.email, "sms": self.sms, "telegram": self.telegram}.get(channel)
        if ch is None:
            raise ValueError("channel: telegram | email | sms")
        sample = {"symbol": "US100", "direction": "LONG", "tf": "15M", "score": 0, "entry": None, "sl": None,
                  "tp1": None, "tp2": None, "tp3": None, "reasons": []}
        text = ("To jest wiadomość testowa AURA TREND. Konfiguracja kanału działa.\n\n"
                "Przykładowy format alertu:\n\n" + self.format_signal(sample)[1])
        try:
            short = {"sms": "AURA TREND: test SMS OK", "telegram": "✅ AURA TREND: Telegram działa. Tu będą przychodzić alerty o nowych setupach."}
            ch.send("AURA TREND — test powiadomień", short.get(channel, text))
            return {"ok": True}
        except ChannelError as exc:
            return {"ok": False, "error": str(exc)}
