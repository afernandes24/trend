# Wdrożenie na Railway (działa 24/7, alerty na Telegram)

## 1. Bot Telegram (5 min)
1. W Telegramie otwórz **@BotFather** → `/newbot` → podaj nazwę → skopiuj **token**.
2. Otwórz swojego nowego bota i wyślij mu `/start`.

## 2. Kod na GitHub
1. Utwórz **prywatne** repozytorium, np. `aura-trend`.
2. Wrzuć do niego zawartość tego folderu (bez `.env` i `data/*.db` — `.gitignore` to pilnuje).

## 3. Railway
1. **New Project → Deploy from GitHub repo** → wybierz `aura-trend`.
   Railway sam wykryje `Dockerfile` i `railway.json`.
2. Serwis → **Settings → Volumes → Add Volume**, mount path: `/data`
   (baza sygnałów i świec przetrwa restarty).
3. Serwis → **Variables** — dodaj:
   | Zmienna | Wartość |
   |---|---|
   | `APP_USERNAME` | np. `admin` |
   | `APP_PASSWORD` | mocne hasło |
   | `TELEGRAM_BOT_TOKEN` | token od BotFather |
   | `DATABASE_PATH` | `/data/aura_trend.db` |
4. Serwis → **Settings → Networking → Generate Domain** → dostajesz adres `https://….up.railway.app`.

## 4. Chat ID i test
1. Wejdź na adres aplikacji, zaloguj się (`APP_USERNAME` / `APP_PASSWORD`).
2. **Alerty → „Znajdź chat ID"** → skopiuj liczbę.
3. Railway → Variables → dodaj `TELEGRAM_CHAT_ID` = ta liczba (aplikacja zrestartuje się sama).
4. **Alerty → Telegram → „Wyślij test"** — wiadomość powinna przyjść na telefon.
5. Ustaw filtry (75+/80+/85+, LONG/SHORT, cooldown) i kliknij **Zapisz**.

## Jak szybko przychodzą alerty
- Skan co 60 s, 20 s po pełnej minucie; świece 1H co 5 min.
- Sygnał powstaje po zamknięciu świecy 15M (:00, :15, :30, :45) — dopiero wtedy, gdy wszystkie 3 świece 5M są w danych.
- Dane Yahoo są opóźnione (futures zwykle ~10 min, indeksy kasowe ~15 min) — to główne opóźnienie. Szybciej = płatne źródło danych w czasie rzeczywistym.

## Problemy
- Kafelki „Brak danych" / „Błąd pobierania danych" z kodem **429** → Yahoo ogranicza zapytania z serwera. Ustaw `SCAN_INTERVAL_SECONDS=120` i spróbuj ponownie.
- Nie przychodzi Telegram → Alerty: status kanału i przycisk „Wyślij test" pokażą dokładny błąd; dziennik alertów pokazuje, czy setup został pominięty (np. cooldown, score, brak zgodności 4H/1H).
- Nie ustawiaj więcej niż 1 repliki — każda wysyłałaby te same alerty.
