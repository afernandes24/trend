"""Kanały powiadomień: e-mail (SMTP) i SMS (Twilio / SMSAPI.pl).

Konfiguracja wyłącznie przez `.env` (patrz `.env.example`). Nowy kanał =
nowa klasa z metodą `send(subject, text)` i `configured()`.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from ..config import Settings

log = logging.getLogger(__name__)


class ChannelError(RuntimeError):
    pass


class EmailChannel:
    name = "email"

    def __init__(self, s: Settings):
        self.s = s

    def configured(self) -> bool:
        return bool(self.s.smtp_host and self.s.email_from and self.s.email_to)

    def missing(self) -> list[str]:
        req = {"SMTP_HOST": self.s.smtp_host, "ALERT_EMAIL_FROM": self.s.email_from,
               "ALERT_EMAIL_TO": self.s.email_to}
        return [k for k, v in req.items() if not v]

    def send(self, subject: str, text: str, html: str | None = None) -> None:
        if not self.configured():
            raise ChannelError(f"E-mail nieskonfigurowany (brak: {', '.join(self.missing())})")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.s.email_from
        msg["To"] = ", ".join(self.s.email_to)
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        ctx = ssl.create_default_context()
        try:
            if self.s.smtp_use_ssl:
                with smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, context=ctx, timeout=20) as srv:
                    self._login_send(srv, msg)
            else:
                with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20) as srv:
                    if self.s.smtp_use_tls:
                        srv.starttls(context=ctx)
                    self._login_send(srv, msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise ChannelError(f"SMTP: {exc}") from exc

    def _login_send(self, srv: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.s.smtp_user:
            srv.login(self.s.smtp_user, self.s.smtp_password)
        srv.send_message(msg)


class SmsChannel:
    """SMS przez wybranego dostawcę. SMS_PROVIDER = twilio | smsapi | none."""
    name = "sms"

    def __init__(self, s: Settings):
        self.s = s
        self.provider = (s.sms_provider or "none").lower()

    def configured(self) -> bool:
        return not self.missing()

    def missing(self) -> list[str]:
        if self.provider == "twilio":
            req = {"TWILIO_ACCOUNT_SID": self.s.twilio_account_sid, "TWILIO_AUTH_TOKEN": self.s.twilio_auth_token,
                   "TWILIO_FROM": self.s.twilio_from, "ALERT_SMS_TO": self.s.sms_to}
        elif self.provider == "smsapi":
            req = {"SMSAPI_TOKEN": self.s.smsapi_token, "ALERT_SMS_TO": self.s.sms_to}
        else:
            return ["SMS_PROVIDER (twilio | smsapi)"]
        return [k for k, v in req.items() if not v]

    def send(self, subject: str, text: str, html: str | None = None) -> None:
        if not self.configured():
            raise ChannelError(f"SMS nieskonfigurowany (brak: {', '.join(self.missing())})")
        body = text[:600]
        try:
            with httpx.Client(timeout=20) as cli:
                for to in self.s.sms_to:
                    if self.provider == "twilio":
                        r = cli.post(
                            f"https://api.twilio.com/2010-04-01/Accounts/{self.s.twilio_account_sid}/Messages.json",
                            data={"From": self.s.twilio_from, "To": to, "Body": body},
                            auth=(self.s.twilio_account_sid, self.s.twilio_auth_token))
                    else:  # smsapi.pl
                        data = {"to": to.lstrip("+"), "message": body, "format": "json", "encoding": "utf-8"}
                        if self.s.smsapi_sender:
                            data["from"] = self.s.smsapi_sender
                        r = cli.post("https://api.smsapi.pl/sms.do", data=data,
                                     headers={"Authorization": f"Bearer {self.s.smsapi_token}"})
                    if r.status_code >= 300 or '"error"' in r.text:
                        raise ChannelError(f"{self.provider} HTTP {r.status_code}: {r.text[:200]}")
        except httpx.HTTPError as exc:
            raise ChannelError(f"{self.provider}: {exc}") from exc


class TelegramChannel:
    """Bot Telegram — powiadomienie na telefonie w 1–2 sekundy, za darmo.

    Konfiguracja: TELEGRAM_BOT_TOKEN (od @BotFather) i TELEGRAM_CHAT_ID (Twój chat id,
    można kilka po przecinku). Chat id znajdziesz w aplikacji: Alerty → „Znajdź chat ID".
    """
    name = "telegram"
    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, s: Settings):
        self.s = s

    def configured(self) -> bool:
        return not self.missing()

    def missing(self) -> list[str]:
        req = {"TELEGRAM_BOT_TOKEN": self.s.telegram_bot_token, "TELEGRAM_CHAT_ID": self.s.telegram_chat_id}
        return [k for k, v in req.items() if not v]

    def _call(self, method: str, timeout: float = 15, **data) -> dict:
        try:
            with httpx.Client(timeout=timeout) as cli:
                r = cli.post(self.API.format(token=self.s.telegram_bot_token, method=method), json=data)
        except httpx.HTTPError as exc:
            raise ChannelError(f"Telegram: {exc}") from exc
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code >= 300 or not j.get("ok"):
            raise ChannelError(f"Telegram HTTP {r.status_code}: {j.get('description') or r.text[:200]}")
        return j

    def send(self, subject: str, text: str, html: str | None = None, reply_to: int | None = None,
             chats: list[str] | None = None) -> list[dict]:
        """`text` w formacie HTML Telegrama (<b>, <i>, <code>). Zwraca id wysłanych wiadomości."""
        if not self.configured() and not chats:
            raise ChannelError(f"Telegram nieskonfigurowany (brak: {', '.join(self.missing())})")
        out = []
        for chat in (chats or self.s.telegram_chat_id):
            data = {"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_to:
                data["reply_to_message_id"] = reply_to
                data["allow_sending_without_reply"] = True
            j = self._call("sendMessage", **data)
            mid = (j.get("result") or {}).get("message_id")
            if mid is not None:
                out.append({"chat_id": str(chat), "message_id": mid})
        return out

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        data = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            data["offset"] = offset
        return self._call("getUpdates", timeout=timeout + 10, **data).get("result", [])

    def find_chats(self, seen: dict | None = None) -> list[dict]:
        """Lista czatów, które napisały do bota (do odczytania chat id).

        Gdy działa bot odbierający wiadomości (`seen`), korzystamy z jego listy — dwa równoległe
        getUpdates kończą się błędem „Conflict" w Telegramie."""
        if not self.s.telegram_bot_token:
            raise ChannelError("Najpierw ustaw TELEGRAM_BOT_TOKEN")
        if seen is not None:
            return list(seen.values())
        res = self._call("getUpdates", limit=50).get("result", [])
        chats = {}
        for upd in res:
            msg = upd.get("message") or upd.get("channel_post") or {}
            ch = msg.get("chat") or {}
            if ch.get("id") is not None:
                name = ch.get("title") or " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) \
                    or ch.get("username") or ""
                chats[ch["id"]] = {"chat_id": ch["id"], "name": name, "type": ch.get("type")}
        return list(chats.values())
