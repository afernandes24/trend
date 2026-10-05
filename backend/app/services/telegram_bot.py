"""Bot Telegram — zapisywanie i zamykanie pozycji prosto z czatu.

Jak to działa:
  • odpowiedz na alert:            0.01 4251.7      → zapisuje pozycję (lot + cena wejścia z XTB)
                                    tak / wszedłem   → pozycja z ceną i lotem z alertu
  • odpowiedz na potwierdzenie
    pozycji albo na wynik alertu:  zamknij 4230 · tp · sl · wynik -45 zł · usuń
  • bez odpowiadania:               GOLD 0.01 4251.7 · zamknij GOLD 4230 · pozycje · statystyki · pomoc

Bot przyjmuje polecenia WYŁĄCZNIE z czatów wpisanych w TELEGRAM_CHAT_ID.
Wiadomości są odbierane przez long polling (getUpdates) — nie trzeba niczego ustawiać na Railway.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata

from ..database import Database
from ..instruments import INSTRUMENTS, get_instrument
from ..notifications.channels import ChannelError
from ..notifications.dispatcher import Notifier, position_size
from .positions import PositionError, PositionService
from .stats import alert_stats, trade_stats

log = logging.getLogger(__name__)

NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?")
ALIASES = {"DE40": "GER40", "DAX": "GER40", "JP225": "JAP225", "NIKKEI": "JAP225", "OIL": "OIL.WTI",
           "WTI": "OIL.WTI", "ROPA": "OIL.WTI", "ZLOTO": "GOLD", "SREBRO": "SILVER", "NASDAQ": "US100",
           "NAS100": "US100", "SP500": "US500", "DOW": "US30", "RUSSELL": "US2000", "FTSE": "UK100"}
SYMBOLS = {i.symbol.upper(): i.symbol for i in INSTRUMENTS}
YES = {"tak", "wszedlem", "weszlem", "jestem", "ok", "+", "in", "wchodze"}
CLOSE = {"zamknij", "zamknalem", "wyszedlem", "close", "exit", "zamkniete"}
HELP = """🤖 <b>Polecenia</b>

<b>Wejście w pozycję</b> — odpowiedz na alert:
<code>0.01 4251.7</code>  (lot i cena z XTB)
<code>tak</code>  (lot i cena z alertu)
albo bez odpowiadania: <code>GOLD 0.01 4251.7</code>

<b>Zamknięcie</b> — odpowiedz na potwierdzenie pozycji lub wynik alertu:
<code>zamknij 4230</code> · <code>tp</code> · <code>sl</code> · <code>wynik -45 zł</code> · <code>usuń</code>
albo: <code>zamknij GOLD 4230</code>

<b>Podgląd</b>: <code>pozycje</code> · <code>statystyki</code>"""


def norm(text: str) -> str:
    t = (text or "").replace("ł", "l").replace("Ł", "L")
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()


def fnum(x: str) -> float:
    return float(x.replace(",", "."))


def pl(v: float, d: int = 2) -> str:
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


class TelegramBot:
    def __init__(self, db: Database, notifier: Notifier, positions: PositionService,
                 tp_r: callable = lambda: [1.5, 2.0, 3.0]):
        self.db = db
        self.notifier = notifier
        self.positions = positions
        self.tp_r = tp_r
        self.seen_chats: dict = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None

    @property
    def tg(self):
        return self.notifier.telegram

    def allowed(self) -> set[str]:
        return {str(c) for c in (self.tg.s.telegram_chat_id or [])}

    # ------------------------------------------------------------ polling
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.tg.s.telegram_bot_token or self.running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telegram-bot", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        offset = self.db.get_setting("tg_offset")
        while not self._stop.is_set():
            try:
                updates = self.tg.get_updates(offset, timeout=25)
                self.last_error = None
            except ChannelError as exc:
                self.last_error = str(exc)
                # „Conflict" = przez chwilę działają dwie kopie aplikacji (np. w trakcie wdrożenia)
                self._stop.wait(15 if "Conflict" in str(exc) else 5)
                continue
            except Exception as exc:  # noqa: BLE001
                log.exception("Telegram bot")
                self.last_error = str(exc)
                self._stop.wait(5)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    self.handle_update(upd)
                except Exception:  # noqa: BLE001
                    log.exception("Błąd obsługi wiadomości Telegram")
            if updates:
                self.db.set_setting("tg_offset", offset)

    # ------------------------------------------------------------ handling
    def reply(self, chat, text: str, reply_to: int | None = None, signal_id: int | None = None,
              position_id: int | None = None) -> None:
        try:
            ids = self.tg.send("", text, reply_to=reply_to, chats=[str(chat)])
        except ChannelError as exc:
            log.warning("Telegram odpowiedź nieudana: %s", exc)
            return
        if signal_id or position_id:
            for m in ids or []:
                self.db.add_tg_link(m["chat_id"], m["message_id"], signal_id, position_id)

    def handle_update(self, upd: dict) -> str | None:
        msg = upd.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if chat is None or not text:
            return None
        ch = msg.get("chat") or {}
        self.seen_chats[chat] = {"chat_id": chat, "type": ch.get("type"),
                                 "name": ch.get("title") or ch.get("first_name") or ch.get("username") or ""}
        if msg.get("date") and time.time() - msg["date"] > 900:
            return None                                   # stare wiadomości (np. po restarcie) pomijamy
        allowed = self.allowed()
        if str(chat) not in allowed:
            if text.startswith("/start") or not allowed:
                self.reply(chat, f"Twój chat ID: <code>{chat}</code>\nWpisz go w Railway → Variables → "
                                 f"TELEGRAM_CHAT_ID, żeby dostawać alerty i móc wpisywać pozycje.")
            return None
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        link = self.db.get_tg_link(str(chat), reply_to) if reply_to else None
        try:
            out, sig_id, pos_id = self.process(text, link)
        except PositionError as exc:
            out, sig_id, pos_id = f"❗ {exc}", None, None
        if out:
            self.reply(chat, out, reply_to=msg.get("message_id"), signal_id=sig_id, position_id=pos_id)
        return out

    # ------------------------------------------------------------ commands
    def process(self, text: str, link: dict | None) -> tuple[str, int | None, int | None]:
        t = norm(text)
        words = re.findall(r"[a-z0-9.+]+", t)
        first = words[0].lstrip("/") if words else ""
        if first in ("start", "pomoc", "help", "?"):
            return HELP, None, None
        if first in ("pozycje", "poz", "otwarte"):
            return self.cmd_positions(), None, None
        if first in ("statystyki", "stat", "staty", "wyniki"):
            return self.cmd_stats(), None, None
        symbol = self._symbol(words)
        if first in ("usun", "skasuj", "delete"):
            pos = self._target_position(link, symbol)
            self.positions.delete(pos["id"])
            return f"🗑 Usunięto pozycję {pos['symbol']} {pos['direction']} z dziennika.", None, None
        # odpowiedź z liczbą na potwierdzenie pozycji / wynik alertu = zamknięcie
        if first in CLOSE or first in ("tp", "sl", "wynik") or (link and link.get("position_id") and NUM.search(t)):
            return self.cmd_close(t, words, link, symbol)
        if link or symbol or first in YES or NUM.search(t):
            return self.cmd_open(t, words, link, symbol)
        return "Nie rozumiem 🤔 Napisz <code>pomoc</code>, żeby zobaczyć polecenia.", None, None

    def _symbol(self, words: list[str]) -> str | None:
        for w in words:
            u = w.upper()
            if u in SYMBOLS:
                return SYMBOLS[u]
            if u in ALIASES:
                return ALIASES[u]
        return None

    # ---------------- open
    def _alert_for(self, symbol: str, direction: str | None) -> dict | None:
        since = int(time.time()) - 24 * 3600
        for r in self.db.sent_alerts("telegram", since_ts=since, limit=200):
            if r["symbol"] == symbol and (direction is None or r["direction"] == direction):
                return r
        return None

    def cmd_open(self, t: str, words: list[str], link: dict | None, symbol: str | None):
        direction = "LONG" if any(w in ("long", "buy", "kupno", "kup") for w in words) else \
            "SHORT" if any(w in ("short", "sell", "sprzedaz", "sprzedaj") for w in words) else None
        sig = None
        if link and link.get("signal_id"):
            sig = self.db.get_signal(link["signal_id"])
        elif link and link.get("position_id"):
            raise PositionError("To potwierdzenie pozycji — żeby ją zamknąć napisz np. „zamknij 4230”, „tp” albo „sl”.")
        elif symbol:
            sig = self._alert_for(symbol, direction)
        if sig is None and not (symbol and direction):
            raise PositionError("Odpowiedz na wiadomość z alertem albo podaj instrument i kierunek, "
                                "np. „GOLD SHORT 0.01 4251.7”.")
        symbol = sig["symbol"] if sig else symbol
        direction = sig["direction"] if sig else direction
        inst = get_instrument(symbol)
        d = inst.digits

        explicit = {}
        for key in ("sl", "tp"):
            m = re.search(rf"\b{key}\s*[:=]?\s*({NUM.pattern})", t)
            if m:
                explicit[key] = fnum(m.group(1))
                t = t[:m.start()] + " " + t[m.end():]
        ref = sig["entry"] if sig else self.positions._last_price(symbol)
        nums = [fnum(x) for x in NUM.findall(t)]
        price = lots = None
        for n in nums:
            if ref and abs(n - ref) / ref < 0.2 and price is None:
                price = n
            elif 0 < n <= 100 and lots is None:
                lots = n
            elif price is None:
                price = n
        if price is None:
            if not sig:
                raise PositionError("Podaj cenę wejścia, np. „GOLD SHORT 0.01 4251.7”.")
            price = sig["entry"]
        if lots is None:
            ps = position_size(sig, self.notifier.get_settings(),
                               (self.notifier.fx() or {}).get("rates", {})) if sig else None
            lots = ps["lots"] if ps else inst.min_lot
        sl = explicit.get("sl")
        tp = explicit.get("tp")
        if sig:
            shift = price - sig["entry"]                       # te same odległości co w alercie
            sl = sl if sl is not None else round(sig["sl"] + shift, d)
            tp = tp if tp is not None else round(sig["tp2"] + shift, d)
        body = {"symbol": symbol, "direction": direction, "entry": price, "lots": lots, "sl": sl, "tp": tp,
                "signal_id": sig["id"] if sig else None}
        existing = [p for p in self.db.positions_for_signal(sig["id"]) if p["status"] == "OPEN"] if sig else []
        if existing:
            pos = self.positions.update(existing[0]["id"], body)
            head = "🔵 <b>Poprawiono pozycję</b>"
        else:
            pos = self.positions.open(body)
            head = "🔵 <b>Zapisano pozycję</b>"
        lines = [f"{head} {pos['symbol']} {pos['direction']}",
                 f"{pl(pos['lots'])} lota @ <code>{pos['entry']:.{d}f}</code>"]
        if pos["sl"] is not None:
            lines.append(f"🛑 SL <code>{pos['sl']:.{d}f}</code> · ryzyko {pl(pos['risk_pln'] or 0, 0)} PLN")
        if pos["tp"] is not None:
            lines.append(f"🎯 TP <code>{pos['tp']:.{d}f}</code> · cel +{pl(pos['target_pln'] or 0, 0)} PLN")
        lines.append("↩️ Zamkniesz odpowiedzią: <code>zamknij CENA</code> · <code>tp</code> · <code>sl</code>")
        return "\n".join(lines), pos.get("signal_id"), pos["id"]

    # ---------------- close
    def _target_position(self, link: dict | None, symbol: str | None) -> dict:
        open_ = self.db.list_positions("OPEN")
        if link and link.get("position_id"):
            p = self.db.get_position(link["position_id"])
            if p and p["status"] == "OPEN":
                return p
            if p:
                raise PositionError(f"Pozycja {p['symbol']} {p['direction']} jest już zamknięta.")
        if link and link.get("signal_id"):
            mine = [p for p in self.db.positions_for_signal(link["signal_id"]) if p["status"] == "OPEN"]
            if mine:
                return mine[0]
        if symbol:
            mine = [p for p in open_ if p["symbol"] == symbol]
            if mine:
                return mine[0]
            raise PositionError(f"Nie masz otwartej pozycji na {symbol}.")
        if len(open_) == 1:
            return open_[0]
        if not open_:
            raise PositionError("Nie masz otwartych pozycji.")
        raise PositionError("Masz kilka otwartych pozycji — odpowiedz na potwierdzenie pozycji "
                            "albo podaj instrument, np. „zamknij GOLD 4230”.")

    def cmd_close(self, t: str, words: list[str], link: dict | None, symbol: str | None):
        pos = self._target_position(link, symbol)
        d = get_instrument(pos["symbol"]).digits
        nums = [fnum(x) for x in NUM.findall(t)]
        pnl = price = None
        if "tp" in words:
            if pos["tp"] is None:
                raise PositionError("Ta pozycja nie ma zapisanego TP — podaj cenę, np. „zamknij 4230”.")
            price = pos["tp"]
        elif "sl" in words:
            if pos["sl"] is None:
                raise PositionError("Ta pozycja nie ma zapisanego SL — podaj cenę, np. „zamknij 4230”.")
            price = pos["sl"]
        elif nums and ("wynik" in words or re.search(r"\d\s*(zl|pln)\b", t)):
            pnl = nums[-1]
        elif nums:
            near = [n for n in nums if abs(n - pos["entry"]) / pos["entry"] < 0.2]
            if not near:
                raise PositionError("Podaj cenę zamknięcia z XTB (albo wynik: „wynik -45 zł”).")
            price = near[0]
        else:
            raise PositionError("Podaj cenę zamknięcia, np. „zamknij 4230”, albo napisz „tp” / „sl”.")
        res = self.positions.close(pos["id"], exit_price=price, pnl_pln=pnl)
        v = res["pnl_pln"] or 0
        icon = "✅" if v > 0 else "⛔️" if v < 0 else "⚪️"
        r = f" ({res['r_multiple']:+.2f}R)".replace(".", ",") if res.get("r_multiple") is not None else ""
        st = self.positions.overview()["stats"]
        lines = [f"{icon} <b>Zamknięto {res['symbol']} {res['direction']}: {'+' if v > 0 else ''}{pl(v)} PLN</b>{r}"]
        if price is not None:
            lines.append(f"<code>{res['entry']:.{d}f}</code> → <code>{price:.{d}f}</code>")
        lines.append(f"Dziś: {'+' if st['today_pln'] > 0 else ''}{pl(st['today_pln'])} PLN · "
                     f"miesiąc: {'+' if st['month_pln'] > 0 else ''}{pl(st['month_pln'])} PLN")
        return "\n".join(lines), res.get("signal_id"), res["id"]

    # ---------------- info
    def cmd_positions(self) -> str:
        ov = self.positions.overview()
        if not ov["open"]:
            return "Brak otwartych pozycji."
        lines = ["📂 <b>Otwarte pozycje</b>"]
        for p in ov["open"]:
            d = p["digits"]
            est = p.get("est_pnl_pln")
            e = "" if est is None else f" → teraz ≈ {'+' if est > 0 else ''}{pl(est, 0)} PLN"
            lines.append(f"{'🟢' if p['direction'] == 'LONG' else '🔴'} {p['symbol']} {pl(p['lots'])} lota "
                         f"@ <code>{p['entry']:.{d}f}</code>{e}")
        lines.append(f"\nRazem ≈ {'+' if ov['stats']['open_est_pln'] > 0 else ''}{pl(ov['stats']['open_est_pln'], 0)} PLN")
        return "\n".join(lines)

    def cmd_stats(self) -> str:
        cfg = self.notifier.get_settings()
        a = alert_stats(self.db, self.tp_r(), cfg["risk_pct"], days=30)
        closed = [self.positions.enrich(p) for p in self.db.list_positions("CLOSED")]
        m = trade_stats(closed, cfg["capital"], days=30)
        lines = ["📈 <b>Statystyki — 30 dni</b>", "",
                 f"<b>Alerty</b>: {a['count']} (zamknięte {a['closed']}, wszedłeś w {a['taken']})"]
        if a["closed"]:
            lines.append(f"Trafność {pl(a['style_win_rate'], 0)}% · wynik {a['style_total_r']:+.1f}R "
                         f"≈ {a['style_pct']:+.1f}% konta".replace(".", ","))
        lines += ["", f"<b>Twoje transakcje</b>: {m['trades']}"]
        if m["trades"]:
            lines.append(f"Na plus {pl(m['win_rate'], 0)}% · wynik {'+' if m['total_pln'] > 0 else ''}"
                         f"{pl(m['total_pln'])} PLN ({m['pct_capital']:+.1f}%)".replace(".", ","))
        return "\n".join(lines)
