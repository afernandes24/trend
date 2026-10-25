from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.notifications.dispatcher import DEFAULT_ALERT_SETTINGS, Notifier, in_quiet_hours, position_size


class Cal:
    def __init__(self, ev=None):
        self.ev = ev

    def blocking_event(self, symbol, when=None, window_minutes=30):
        return self.ev

    def status(self):
        return {"ok": True}


class Fake:
    name = "telegram"

    def __init__(self):
        self.sent = []

    def configured(self):
        return True

    def send(self, subject, text, html=None):
        self.sent.append(text)


FX = {"rates": {"USDPLN": 4.0, "EURPLN": 4.3, "GBPPLN": 5.0, "JPYPLN": 0.027}}
SIG = {"id": None, "symbol": "GOLD", "direction": "LONG", "tf": "15M", "score": 88, "entry": 3600.0,
       "sl": 3595.0, "tp1": 3607.5, "tp2": 3610.0, "tp3": 3615.0, "reasons": [], "tp_r": [1.5, 2, 3]}


@pytest.fixture
def notifier(db):
    n = Notifier(db, Settings(), fx=lambda: FX, calendar=Cal())
    n.telegram = Fake()
    n.email = type("E", (), {"name": "email", "configured": lambda self: False})()
    return n


def test_position_size():
    cfg = {**DEFAULT_ALERT_SETTINGS, "capital": 10000, "risk_pct": 1.0}
    ps = position_size(SIG, cfg, FX["rates"])          # GOLD: 5 pkt * 100 USD * 4 = 2000 PLN / lot
    assert ps["lots"] == 0.05 and not ps["below_min"]
    assert ps["real_risk"] == pytest.approx(100.0)
    big = {**SIG, "symbol": "US100", "entry": 24850.0, "sl": 24720.0}   # 130 * 20 * 4 = 10 400 PLN / lot
    ps2 = position_size(big, cfg, FX["rates"])
    assert ps2["below_min"] and ps2["lots"] == 0.01


def test_alert_text_is_short(notifier):
    _, text = notifier.format_signal({**SIG, "symbol": "US100", "entry": 24850.0, "sl": 24720.0,
                                      "tp1": 25045.0, "tp2": 25110.0, "tp3": 25240.0, "score": 88})
    lines = text.split("\n")
    assert lines[0] == "🟢🟢🟢 <b>LONG · US100</b> 🇺🇸"
    assert "SL <code>24720.00</code>  (−130.00)" in text and "TP2 <code>25110.00</code>  (+260.00)" in text
    assert "0,01 lota" in text and "za duże" in text
    assert "<code>0.01 24850.00</code>" in text          # podpowiedź odpowiedzi na Telegramie
    assert len(lines) <= 11 and "Powody" not in text and "Historical" not in text
    _, short = notifier.format_signal({**SIG, "direction": "SHORT", "sl": 3605.0, "tp2": 3590.0,
                                       "tp1": 3592.5, "tp3": 3585.0})
    assert short.startswith("🔴🔴🔴 <b>SHORT · GOLD</b>")
    assert "SL <code>3605.00</code>  (+5.00)" in short and "TP2 <code>3590.00</code>  (−10.00)" in short
    assert "0,05 lota" in short and "za duże" not in short


def test_stale_filter(notifier):
    ok = notifier.dispatch({**SIG, "current_price": 3601.0}, True)       # +0.2R
    assert ok[0]["status"] == "sent"
    late = notifier.dispatch({**SIG, "symbol": "SILVER", "entry": 42.0, "sl": 41.9, "tp1": 42.15, "tp2": 42.2,
                              "tp3": 42.3, "current_price": 42.1}, True)   # +1R
    assert late[0]["status"] == "skipped" and "odjechała" in late[0]["reason"]
    against = notifier.dispatch({**SIG, "symbol": "OIL.WTI", "entry": 64.0, "sl": 63.8, "tp1": 64.3,
                                 "tp2": 64.4, "tp3": 64.6, "current_price": 63.85}, True)
    assert against[0]["status"] == "skipped" and "w stronę SL" in against[0]["reason"]


def test_quiet_hours():
    cfg = {**DEFAULT_ALERT_SETTINGS, "quiet_enabled": True, "quiet_from": "23:00", "quiet_to": "07:00"}
    # 2026-01-15 22:30 UTC = 23:30 w Polsce (CET)
    assert in_quiet_hours(cfg, datetime(2026, 1, 15, 22, 30, tzinfo=timezone.utc))
    assert not in_quiet_hours(cfg, datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc))
    assert not in_quiet_hours({**cfg, "quiet_enabled": False}, datetime(2026, 1, 15, 22, 30, tzinfo=timezone.utc))
    day = {**cfg, "quiet_from": "12:00", "quiet_to": "14:00"}
    assert in_quiet_hours(day, datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc))   # 13:00 CEST


def test_news_filter(notifier):
    notifier.calendar = Cal({"currency": "USD", "title": "Non-Farm Payrolls",
                             "time": datetime.now(timezone.utc) + timedelta(minutes=10)})
    r = notifier.dispatch(SIG, True)
    assert r[0]["status"] == "skipped" and "Non-Farm" in r[0]["reason"]
    notifier.save_settings({"news_filter": False})
    assert notifier.dispatch(SIG, True)[0]["status"] == "sent"


def test_result_notification_only_for_alerted(notifier, db):
    sig_id = db.insert_signal({**SIG, "bar_ts": 1_700_000_000, "reasons": [], "mtf": None})
    sig = {**SIG, "id": sig_id, "bar_ts": 1_700_000_000}
    assert notifier.notify_result(sig, {"result": "TP2", "result_r": 2.0}) == []   # nie było alertu
    notifier.dispatch(sig, True)
    out = notifier.notify_result(sig, {"result": "TP2", "result_r": 2.0})
    assert out and out[0]["status"] == "info"
    assert notifier.telegram.sent[-1].startswith("✅✅✅ <b>GOLD LONG → TP2 (+2.0R)</b>")
    # raport nie resetuje cooldownu i nie jest liczony jako alert
    assert db.last_alert_ts("GOLD", "LONG") is not None


def test_weekly_summary(notifier, db):
    now = datetime.now(timezone.utc)
    sig_id = db.insert_signal({**SIG, "bar_ts": int(now.timestamp()) - 3600, "reasons": [], "mtf": None,
                               "status": "CLOSED", "result": "TP2", "result_r": 2.0})
    notifier.dispatch({**SIG, "id": sig_id}, True)
    friday_late = datetime(2026, 9, 25, 21, 30, tzinfo=timezone.utc)   # 23:30 w Polsce
    wednesday = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    assert notifier.weekly_summary(now=wednesday) is None
    res = notifier.weekly_summary(now=friday_late)
    assert res and res["week"] == "2026-W39"
    assert notifier.weekly_summary(now=friday_late) is None               # tylko raz na tydzień
    forced = notifier.weekly_summary(force=True)
    assert "PODSUMOWANIE TYGODNIA" in forced["text"] and "+2.00R" in forced["text"]


def test_settings_validation(notifier):
    with pytest.raises(ValueError):
        notifier.save_settings({"quiet_from": "abc"})
    s = notifier.save_settings({"risk_pct": 50, "capital": 5000})
    assert s["risk_pct"] == 50 and s["capital"] == 5000


def test_no_risk_filter(notifier):
    # filtr ryzyka usunięty: alert idzie, w treści jest tylko ostrzeżenie o zbyt dużym ryzyku
    notifier.save_settings({"capital": 5000})
    big = {**SIG, "symbol": "US500", "entry": 6500.0, "sl": 6470.0, "tp1": 6545.0, "tp2": 6560.0, "tp3": 6590.0}
    assert notifier.dispatch(big, True)[0]["status"] == "sent"
    assert "za duże" in notifier.telegram.sent[-1]


def test_stats_periods_and_score_buckets(db):
    import time as _t
    from app.services.stats import alert_stats, period_start, score_bucket, signal_score_stats, trade_stats
    assert score_bucket(75) == 75 and score_bucket(79) == 75 and score_bucket(84) == 80 and score_bucket(100) == 95
    now = int(_t.time())
    rows = [("GOLD", 77, "SL", -1.0), ("GOLD", 78, "TP2", 2.0), ("US100", 86, "TP3", 3.0), ("US100", 88, "TP1", 1.5)]
    for k, (sym, sc, res, r) in enumerate(rows):
        sid = db.insert_signal({**SIG, "symbol": sym, "score": sc, "bar_ts": now - 3600 - k, "reasons": [],
                                "mtf": None, "status": "CLOSED", "result": res, "result_r": r})
        db.log_alert(signal_id=sid, symbol=sym, direction="LONG", channel="telegram", status="sent")
    a = alert_stats(db, [1.5, 2, 3], 1.0, since_ts=period_start("week"))
    b = {x["from"]: x for x in a["by_score"]}
    assert b[75]["n"] == 2 and b[75]["total"] == 1.0 and b[75]["win_rate"] == 50.0
    assert b[85]["n"] == 2 and b[85]["total"] == 2.0          # TP3 -> 2R (TP na TP2), TP1 -> 0R
    gold = next(x for x in a["by_symbol"] if x["symbol"] == "GOLD")
    assert gold["pct"] == 1.0                                 # +1R przy 1% ryzyka = +1% konta
    assert len(signal_score_stats(db, [1.5, 2, 3], 1.0)) == 2
    t = trade_stats([{"status": "CLOSED", "closed_at": now, "pnl_pln": 50.0, "symbol": "GOLD", "direction": "LONG",
                      "signal_id": 1, "r_multiple": 1.0}], 5000, since_ts=0, scores={1: 82})
    assert t["by_symbol"][0]["pct"] == 1.0 and t["by_score"][0]["from"] == 80
