# TREND FOLLOWING — analityczny terminal (XTB)

Aplikacja webowa do wyszukiwania setupów **trend following** na 10 instrumentach XTB
(US100, US500, US30, US2000, GER40, UK100, JAP225, GOLD, SILVER, OIL.WTI).
Pokazuje Entry / SL / TP, score 0–100, zgodność interwałów, backtest z walk-forward
i wysyła alerty e-mail/SMS. **Nie składa zleceń** — tryb wyłącznie analityczny.

## Wdrożenie 24/7 (Railway + Telegram)

Instrukcja krok po kroku: **[DEPLOY.md](DEPLOY.md)**.

## Szybki start (lokalnie)

```bash
./run.sh            # Linux / macOS   (Windows: run.bat)
# → http://127.0.0.1:8000
```
Skrypt tworzy `.venv`, instaluje zależności i kopiuje `.env.example` → `.env`.
Wymagany Python 3.11+. Testy: `pip install -r requirements-dev.txt && cd backend && pytest`.

## Struktura projektu

```
aura-trend/
├─ backend/app/
│  ├─ main.py                  FastAPI + serwowanie frontendu, start skanera
│  ├─ config.py                ustawienia z .env
│  ├─ instruments.py           rejestr instrumentów XTB (tu dodajesz nowe)
│  ├─ database.py              Database: SQLite (świece, sygnały, alerty, ustawienia)
│  ├─ market_data/             Market Data
│  │   ├─ base.py              interfejs dostawcy, interwały, resampling
│  │   ├─ yahoo.py             adapter Yahoo Finance (domyślny)
│  │   ├─ csv_provider.py      import CSV
│  │   ├─ xtb.py               adapter XTB — punkt podłączenia
│  │   └─ service.py           cache w bazie, 5M→15M, 1H→4H/1D, kursy walut
│  ├─ strategy/                Strategy Engine
│  │   ├─ params.py            wszystkie parametry strategii
│  │   ├─ indicators.py        SMA, RSI, ATR, ADX, MACD, ROC, swingi, S/R
│  │   ├─ scoring.py           score 0–100, bramki trendu, MTF, zdarzenia sygnału
│  │   ├─ levels.py            SL / TP
│  │   └─ engine.py            analiza interwału, podsumowanie MTF, dane wykresu
│  ├─ backtest/                Backtesting Engine (engine, metrics, walkforward)
│  ├─ notifications/           Notification System (channels: SMTP/Twilio/SMSAPI, dispatcher: filtry + cooldown)
│  ├─ services/                analiza/skaner/historia sygnałów + pętla w tle
│  └─ api/routes.py            REST API /api/*
├─ backend/tests/              32 testy (m.in. brak look-ahead w score i backteście)
├─ frontend/                   Frontend: HTML + ES modules, bez kroku budowania
│  └─ assets/{css,js,vendor}   czarny motyw (CSS), widoki, TradingView Lightweight Charts
└─ data/                       baza SQLite + data/import/ na pliki CSV
```

## Jak działa strategia

Interwały: **4H** trend główny → **1H** potwierdzenie → **15M** setup (score, SL/TP) → **5M** timing.
15M buduje się z 5M, a 4H/1D z 1H. Do wyliczeń brane są tylko **zamknięte** świece.

**Warunki konieczne (bramki)** — bez nich nie ma sygnału, nawet przy wysokim score:
LONG: close > SMA200, SMA50 > SMA200, SMA20 > SMA50, ADX ≥ 20, +DI > −DI (SHORT lustrzanie).

**Sygnał** pojawia się, gdy bramki są spełnione i score przekracza próg (domyślnie 75).
Histereza: kolejny sygnał w tym samym kierunku dopiero, gdy poprzedni setup wygaśnie
(score < próg − 10 albo znikną bramki). W skanerze/alertach dodatkowo 4H i 1H muszą mieć trend
zgodny z kierunkiem (można wyłączyć).

RSI nie jest używany jako „wyprzedanie/wykupienie" — tylko jako potwierdzenie strefy trendu
(LONG 50–70, SHORT 30–50).

## Jak liczony jest score (0–100)

| Składowa | Pkt | Co jest punktowane |
|---|---|---|
| Trend | 25 | cena vs SMA200 (8), nachylenie SMA200 (7), trend na 2 wyższych TF (10) |
| SMA | 15 | SMA50 vs SMA200 (6), SMA20 vs SMA50 (5), cena vs SMA20 (4) |
| RSI | 10 | RSI w strefie trendu (7) lub blisko (3), RSI w kierunku trendu (3) |
| ADX | 10 | ADX ≥ 25 (7) / ≥ 20 (4), ADX rośnie (3); 0 pkt, gdy DI przeciwne |
| MACD | 10 | MACD vs sygnał (4), MACD vs 0 (3), histogram rośnie (3) |
| Price action | 15 | pullback do SMA20 (5), świeca potwierdzająca (4), wybicie poprzedniej świecy (3), struktura HL/LH (3), −4 gdy opór/wsparcie < 1 ATR |
| Momentum | 10 | ROC(10) w kierunku (4), przyspieszenie (3), wolumen > średnia (3; bez wolumenu: zakres świecy > ATR) |
| ATR/zmienność | 5 | ATR w przedziale 0.7–1.8× mediany ze 100 świec (5), 0.5–2.5× (2) |

Progi: 0–49 BRAK SETUPU, 50–64 SŁABY, 65–74 OBSERWUJ, 75–84 DOBRY, 85–100 BARDZO MOCNY.
**Score nie jest prawdopodobieństwem.** „Historical win rate" pokazywany przy sygnale pochodzi
z backtestu tego samego instrumentu, interwału i konfiguracji — z liczbą transakcji i okresem.

## Jak wyznaczany jest SL/TP (`strategy/levels.py`)

- **SL LONG:** poniżej niższego z [ostatni potwierdzony swing low z 30 świec, minimum 5 ostatnich świec] − 0.3×ATR.
  **SHORT** lustrzanie (swing high + bufor).
- Odległość SL ograniczona do **0.8–3.0 × ATR** — więc SL dopasowuje się do zmienności każdego instrumentu.
- **TP1/TP2/TP3 = 1.5R / 2R / 3R** (zmieniasz w Ustawieniach / Backteście).
- Najbliższy opór/wsparcie ze swingów jest pokazywany; jeśli leży przed TP1, pojawia się ostrzeżenie.

## Jak działa backtest (`backtest/`)

- Sygnał na zamknięciu świecy → wejście po **open następnej świecy** (LONG po ask = open + spread).
- SL i TP w tej samej świecy → liczony **SL** (konserwatywnie); luka przez SL → wyjście po open.
- Jedna pozycja naraz. Tryb `single` (całość na RR) albo `scaled` (1/3 na TP1/TP2/TP3, SL na BE po TP1).
- Metryki: liczba transakcji, win rate, średni zysk/strata, profit factor, max drawdown, expectancy,
  serie zysków/strat, wynik całkowity, LONG, SHORT, equity curve.
- **TRAINING / TESTING:** podział okresu (domyślnie 70/30) i porównanie metryk.
- **Walk-forward:** dane dzielone na N+1 segmentów; na oknie treningowym wybierany jest min. score × RR
  wg expectancy, a wynik liczony wyłącznie na kolejnym, niewidzianym oknie (out-of-sample).
- Testy automatyczne sprawdzają, że score i transakcje nie zależą od przyszłych danych.

## Gdzie podłączyć dane rynkowe

- Domyślnie `MARKET_DATA_PROVIDER=yahoo`: futures (NQ, ES, YM, RTY, GC, SI, CL) i indeksy kasowe
  (DAX, FTSE, Nikkei). To **proxy** — ceny mogą różnić się od CFD w XTB (baza, godziny sesji).
  Limity Yahoo: 5M ≈ 60 dni, 1H ≈ 730 dni.
- `csv`: pliki `data/import/<SYMBOL>_<TF>.csv` albo import w Ustawieniach (dłuższa historia do backtestu).
- `xtb`: `backend/app/market_data/xtb.py` — zaimplementuj `fetch_candles()`. Publiczne xAPI XTB zostało
  wyłączone w 2025 r., więc adapter jest punktem podłączenia pod dowolne źródło z cenami XTB.
- Nowy instrument: dopisz `Instrument(...)` w `instruments.py` (symbol XTB, wartość punktu, spread, symbole źródła).

## Dane na żywo: Capital.com (zalecane)

`MARKET_DATA_PROVIDER=capital` + `CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_API_PASSWORD`, `CAPITAL_ENV=demo|live`.
Klucz: capital.com → Ustawienia → API integrations → Generate new key (wymaga 2FA). Ceny CFD (BID) na żywo,
skan 3 s po pełnej minucie → alert kilka sekund po zamknięciu świecy 15M. Instrumenty dopasowują się same
(podgląd: tryb zaawansowany → Ustawienia → Dane rynkowe); ręcznie: `CAPITAL_EPICS=US2000=RTY;JAP225=J225`.
Przy zmianie źródła danych aplikacja czyści stare świece i korektę cen oraz zamyka otwarte sygnały (inna skala cen).

## Korekta cen do XTB

Yahoo podaje ceny futures / indeksów, które różnią się od CFD w XTB (np. złoto: futures grudniowe ≈ 30–45 $ drożej niż spot).
Rynek → **Korekta cen do XTB** → wpisz aktualną cenę z xStation. Aplikacja przesuwa o różnicę wszystkie ceny
(wykres, Entry/SL/TP w alertach, otwarte sygnały i pozycje). Wskaźniki i score się nie zmieniają.
Odświeżaj raz w tygodniu (różnica powoli maleje, przy zmianie kontraktu futures skacze). GER40/UK100 kalibruj w godzinach sesji.

## Moje pozycje (licznik zysków i strat)

- Gdy wejdziesz w pozycję z alertu: **„Wszedłem w pozycję”** (kafelek na Rynku, wykres, Sygnały albo link w wiadomości Telegram).
- Wpisujesz cenę wejścia z XTB i wolumen — SL/TP uzupełniają się z odległości z alertu, wolumen z ustawień ryzyka.
- Otwarte pozycje: szacowany wynik na bieżąco (zmiana ceny od zapisu, więc różnica Yahoo/XTB się znosi).
- Zamknięcie: cena zamknięcia albo dokładny wynik w PLN z XTB. Licznik: łącznie / miesiąc / tydzień / dziś, % na plus.
- Tryb prosty (domyślny) pokazuje tylko najważniejsze; szczegóły: przełącznik **Tryb zaawansowany**.

## Alerty — co zawierają i kiedy są pomijane

- Entry, SL, TP1–TP3 z odległościami w punktach („SL = cena − 130 pkt”) i **wolumen w lotach** dla Twojego kapitału i ryzyka.
- Pomijane, gdy: cena odjechała od Entry o więcej niż X R (spóźniony alert), trwają ciche godziny,
  jest ±N min od ważnej publikacji makro (kalendarz ForexFactory, waluty instrumentu + USD), cooldown.
- Po alercie: wiadomość z wynikiem (TP/SL) i **podsumowanie tygodnia** w piątek po 23:00.
- Wszystko ustawiasz w zakładce **Alerty**.

## Gdzie skonfigurować Telegram / e-mail / SMS

Zmienne środowiskowe — na Railway w **Variables**, lokalnie w pliku `.env` (wzór: `.env.example`):
- Telegram (zalecany, najszybszy): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (chat id: Alerty → „Znajdź chat ID").
- E-mail: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`
  (Gmail: `smtp.gmail.com:587` + hasło aplikacji).
- SMS: `SMS_PROVIDER=twilio` lub `smsapi` + klucze i `ALERT_SMS_TO`.
- Logowanie do aplikacji: `APP_USERNAME`, `APP_PASSWORD` (wymagane na serwerze).
- Skaner: `SCAN_INTERVAL_SECONDS` (domyślnie 60), `H1_REFRESH_SECONDS` (domyślnie 300).
- Filtry (Telegram/Email/SMS, 75+/80+/85+, LONG/SHORT, cooldown, wymóg zgodności 4H/1H) — w aplikacji: **Alerty**.
  Tam też przycisk „Wyślij test".

## Zastrzeżenia

Narzędzie analityczne, nie porada inwestycyjna. Wyniki historyczne nie gwarantują przyszłych.
Wartości punktu i spreadów w `instruments.py` to domyślne założenia — zweryfikuj je w specyfikacji xStation.
