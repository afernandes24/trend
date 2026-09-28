"""Konfiguracja aplikacji — wszystkie ustawienia środowiskowe w jednym miejscu.

Wartości czytane są z pliku `.env` w katalogu głównym projektu (patrz `.env.example`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on", "tak"}


def _list(name: str) -> list[str]:
    v = os.getenv(name, "")
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class Settings:
    # --- Aplikacja ---
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8000"))
    database_path: Path = Path(os.getenv("DATABASE_PATH", str(ROOT_DIR / "data" / "aura_trend.db")))
    frontend_dir: Path = ROOT_DIR / "frontend"
    # Logowanie (HTTP Basic). Puste = bez hasła (tylko do użytku lokalnego!)
    auth_username: str = os.getenv("APP_USERNAME", "")
    auth_password: str = os.getenv("APP_PASSWORD", "")
    import_dir: Path = ROOT_DIR / "data" / "import"

    # --- Market data ---
    # yahoo  = darmowe dane (proxy dla CFD XTB, patrz README)
    # csv    = tylko pliki z data/import
    # xtb    = adapter do podłączenia (szkielet)
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "yahoo")
    # co ile sekund sprawdzać świeże świece 5M (alert pojawia się tuż po zamknięciu świecy 15M)
    scan_interval_seconds: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
    # co ile sekund odświeżać świece 1H (trend 1H/4H zmienia się wolno)
    h1_refresh_seconds: int = int(os.getenv("H1_REFRESH_SECONDS", "300"))
    scanner_enabled: bool = _bool("SCANNER_ENABLED", True)
    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "20"))

    # --- E-mail (SMTP) ---
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_use_tls: bool = _bool("SMTP_USE_TLS", True)
    smtp_use_ssl: bool = _bool("SMTP_USE_SSL", False)
    # Publiczny adres aplikacji (do linków w alertach). Na Railway ustawiany automatycznie.
    app_url: str = os.getenv("APP_URL") or (f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN')}"
                                             if os.getenv("RAILWAY_PUBLIC_DOMAIN") else "")
    email_from: str = os.getenv("ALERT_EMAIL_FROM", "")
    email_to: list[str] = field(default_factory=lambda: _list("ALERT_EMAIL_TO"))

    # --- Telegram (najszybszy kanał: bot → telefon w 1–2 s) ---
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: list[str] = field(default_factory=lambda: _list("TELEGRAM_CHAT_ID"))

    # --- SMS ---
    # none | twilio | smsapi
    sms_provider: str = os.getenv("SMS_PROVIDER", "none")
    sms_to: list[str] = field(default_factory=lambda: _list("ALERT_SMS_TO"))
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from: str = os.getenv("TWILIO_FROM", "")
    smsapi_token: str = os.getenv("SMSAPI_TOKEN", "")
    smsapi_sender: str = os.getenv("SMSAPI_SENDER", "")

    # --- XTB (adapter do przyszłego podłączenia) ---
    xtb_user: str = os.getenv("XTB_USER", "")
    xtb_password: str = os.getenv("XTB_PASSWORD", "")
    xtb_mode: str = os.getenv("XTB_MODE", "demo")


settings = Settings()
