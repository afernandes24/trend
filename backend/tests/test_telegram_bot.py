import time

import pytest

from app.config import Settings
from app.market_data.base import empty_frame
from app.notifications.dispatcher import Notifier
from app.services.positions import PositionService
from app.services.telegram_bot import TelegramBot

FX = {"rates": {"USDPLN": 4.0, "EURPLN": 4.3, "GBPPLN": 5.0, "JPYPLN": 0.027}}


class Cal:
    def blocking_event(self, *a, **k):
        return None


class FakeTG:
    name = "telegram"

    def __init__(self, s):
        self.s = s
        self.sent = []
        self.n = 100

    def configured(self):
        return True

    def send(self, subject, text, html=None, reply_to=None, chats=None):
        self.n += 1
        self.sent.append({"text": text, "reply_to": reply_to, "chats": chats, "id": self.n})
        return [{"chat_id": c, "message_id": self.n} for c in (chats or self.s.telegram_chat_id)]


class Market:
    def get_candles(self, symbol, tf):
        return empty_frame()

    def fx_rates(self):
        return FX


@pytest.fixture
def env(db):
    s = Settings()
    s.telegram_bot_token, s.telegram_chat_id = "1:x", ["42"]
    n = Notifier(db, s, fx=lambda: FX, calendar=Cal())
    n.telegram = FakeTG(s)
    n.email = type("E", (), {"name": "email", "configured": lambda self: False})()
    n.save_settings({"capital": 40000, "risk_pct": 1.0})
    pos = PositionService(db, Market(), n)
    bot = TelegramBot(db, n, pos)
    return n, pos, bot, db


def msg(text, reply_to=None, chat=42):
    m = {"message_id": int(time.time() * 1000) % 10_000_000, "date": int(time.time()), "chat": {"id": chat},
         "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": 1, "message": m}


def alert(n, db):
    sig = {"symbol": "GOLD", "direction": "SHORT", "tf": "15M", "score": 80, "entry": 4251.7, "sl": 4279.71,
           "tp1": 4209.69, "tp2": 4195.68, "tp3": 4167.67, "bar_ts": int(time.time()) - 900, "reasons": [],
           "mtf": None}
    sig["id"] = db.insert_signal(sig)
    n.dispatch(sig, True)
    return sig, n.telegram.sent[-1]["id"]


def test_reply_opens_and_closes_position(env):
    n, pos, bot, db = env
    sig, alert_msg = alert(n, db)
    out = bot.handle_update(msg("0,01 4250.5", reply_to=alert_msg))
    assert "Zapisano pozycję" in out
    p = db.list_positions("OPEN")[0]
    assert p["signal_id"] == sig["id"] and p["lots"] == 0.01 and p["entry"] == 4250.5
    assert p["sl"] == pytest.approx(4278.51) and p["tp"] == pytest.approx(4194.48)   # te same odległości co w alercie
    confirm = n.telegram.sent[-1]["id"]
    out = bot.handle_update(msg("zamknij 4230", reply_to=confirm))
    assert "Zamknięto GOLD SHORT" in out
    closed = db.get_position(p["id"])
    assert closed["status"] == "CLOSED" and closed["pnl_pln"] == pytest.approx(20.5 * 0.01 * 100 * 4)


def test_yes_uses_alert_values_and_symbol_commands(env):
    n, pos, bot, db = env
    sig, alert_msg = alert(n, db)
    bot.handle_update(msg("wszedłem", reply_to=alert_msg))
    p = db.list_positions("OPEN")[0]
    assert p["entry"] == 4251.7 and p["lots"] == 0.03      # 1% z 40 000 PLN / (28 pkt * 100 USD * 4)
    bot.handle_update(msg("0.02 4252", reply_to=alert_msg))          # poprawka zamiast drugiej pozycji
    assert len(db.list_positions("OPEN")) == 1 and db.list_positions("OPEN")[0]["lots"] == 0.02
    assert "Otwarte pozycje" in bot.handle_update(msg("pozycje"))
    out = bot.handle_update(msg("zamknij złoto tp"))
    assert "Zamknięto" in out and db.list_positions("OPEN") == []
    assert "Statystyki" in bot.handle_update(msg("/statystyki"))


def test_manual_entry_pnl_and_security(env):
    n, pos, bot, db = env
    out = bot.handle_update(msg("US100 LONG 0.01 24850"))
    assert "Zapisano" in out
    out = bot.handle_update(msg("wynik -45 zł"))
    assert "-45" in out and db.list_positions("CLOSED")[0]["pnl_pln"] == -45
    # obcy czat nie może nic zapisać
    before = len(db.list_positions())
    bot.handle_update(msg("GOLD SHORT 0.01 4251", chat=999))
    assert len(db.list_positions()) == before
    assert "Odpowiedz na wiadomość z alertem" in bot.handle_update(msg("0.01 4251"))
