import time

import pytest
from fastapi.testclient import TestClient

from app.main import app, build_services
from app.market_data.service import MarketDataService
from app.notifications.channels import ChannelError
from app.services.analysis import evaluate_outcome

from .conftest import FixtureProvider


class NoEvents:
    """Kalendarz makro bez sieci (testy)."""
    def blocking_event(self, *a, **k):
        return None

    def status(self):
        return {"ok": True, "error": None, "events_this_week": 0, "upcoming": []}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from app.database import Database
    db = Database(tmp_path_factory.mktemp("api") / "api.db")
    market = MarketDataService(db, FixtureProvider())
    build_services(app, db, market)
    app.state.notifier.calendar = NoEvents()
    app.state.autostart = False
    market.refresh_all()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health").json()
    assert r["ok"] and "ANALIZA" in r["mode"]


def test_instruments_only_xtb(client):
    syms = [i["symbol"] for i in client.get("/api/instruments").json()]
    assert syms == ["US100", "US500", "US30", "US2000", "GER40", "UK100", "JAP225", "GOLD", "SILVER", "OIL.WTI"]


def test_overview_and_scanner(client):
    ov = client.get("/api/overview").json()["items"]
    assert len(ov) == 10
    for t in ov:
        assert t["signal"] in ("LONG", "SHORT", "WAIT")
        assert t["price"] is not None and t["score"] is not None, t
        assert len(t["mtf"]) == 4
    sc = client.get("/api/scanner").json()["items"]
    scores = [x["score"] for x in sc]
    assert scores == sorted(scores, reverse=True)


def test_analysis_and_chart(client):
    a = client.get("/api/analysis/US100").json()
    assert set(a["timeframes"]) == {"5M", "15M", "1H", "4H"}
    assert a["historical"]["trades"] >= 0
    lv = a["timeframes"]["15M"]["levels"]
    assert lv["entry"] and lv["sl"] and lv["tp3"]
    for tf in ("5M", "15M", "1H", "4H"):
        c = client.get(f"/api/chart/OIL.WTI?tf={tf}&bars=300").json()
        assert c["ok"], c
        assert len(c["candles"]) == 300
        assert c["sma_slow"] and c["rsi"] and c["macd"]
    assert client.get("/api/chart/XXX?tf=15M").status_code == 404
    assert client.get("/api/chart/US100?tf=2H").status_code == 400


def test_backtest_endpoint(client):
    body = {"symbol": "GER40", "tf": "1H", "params": {"min_score": 70, "rr_target": 2},
            "split": 0.7, "walkforward": True, "wf_folds": 3}
    r = client.post("/api/backtest", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    for k in ("trades", "win_rate", "avg_win_r", "avg_loss_r", "profit_factor", "max_drawdown_r",
              "expectancy_r", "max_loss_streak", "max_win_streak", "total_r", "long", "short"):
        assert k in j["all"]
    assert "train" in j["split"] and "test" in j["split"]
    assert len(j["walkforward"]["folds"]) == 3
    bad = client.post("/api/backtest", json={**body, "params": {"sma_fast": 300}})
    assert bad.status_code == 400


def test_strategy_settings_roundtrip(client):
    r = client.put("/api/settings/strategy", json={"min_score": 80, "tp_r": [1.5, 2.5, 4]})
    assert r.status_code == 200
    assert client.get("/api/settings/strategy").json()["params"]["min_score"] == 80
    client.put("/api/settings/strategy", json={"min_score": 75, "tp_r": [1.5, 2, 3]})


def test_signals_backfill_and_filters(client):
    r = client.post("/api/signals/backfill", json={"days": 400}).json()
    total = sum(r["inserted"].values())
    assert total > 0
    allrows = client.get("/api/signals").json()
    assert allrows["summary"]["count"] == total
    longs = client.get("/api/signals?direction=LONG").json()["items"]
    assert all(x["direction"] == "LONG" for x in longs)
    hi = client.get("/api/signals?min_score=85").json()["items"]
    assert all(x["score"] >= 85 for x in hi)
    wins = client.get("/api/signals?result=WIN").json()["items"]
    assert all(x["result_r"] > 0 for x in wins)


def test_alert_settings_and_cooldown(client):
    s = client.put("/api/settings/alerts", json={"min_score": 80, "cooldown_minutes": 30, "email": True}).json()
    assert s["settings"]["min_score"] == 80
    assert s["channels"]["email"]["configured"] is False
    t = client.post("/api/alerts/test", json={"channel": "email"}).json()
    assert t["ok"] is False and "nieskonfigurowany" in t["error"]

    notifier = app.state.notifier
    sent = []

    class Fake:
        name = "email"
        def configured(self):
            return True
        def send(self, subject, text, html=None):
            sent.append(subject)
    notifier.email = Fake()
    sig = {"id": None, "symbol": "US100", "direction": "LONG", "tf": "15M", "score": 88, "entry": 100.0,
           "sl": 99.0, "tp1": 101.5, "tp2": 102.0, "tp3": 103.0, "reasons": []}
    first = notifier.dispatch(sig, mtf_aligned=True)
    assert any(x["status"] == "sent" and x["channel"] == "email" for x in first)
    second = notifier.dispatch(sig, mtf_aligned=True)
    assert second[0]["status"] == "skipped" and "cooldown" in second[0]["reason"]
    low = notifier.dispatch({**sig, "symbol": "GOLD", "score": 70}, mtf_aligned=True)
    assert low[0]["status"] == "skipped"
    nomtf = notifier.dispatch({**sig, "symbol": "GER40"}, mtf_aligned=False)
    assert nomtf[0]["status"] == "skipped"
    assert len(sent) == 1
    log = client.get("/api/alerts/log").json()["items"]
    assert any(x["channel"] == "app" for x in log)

    class Broken:
        name = "email"
        def configured(self):
            return True
        def send(self, *a, **k):
            raise ChannelError("SMTP down")
    notifier.email = Broken()
    notifier.save_settings({"risk_filter": False})
    res = notifier.dispatch({**sig, "symbol": "SILVER"}, mtf_aligned=True)
    assert any(x["status"] == "failed" for x in res)


def test_csv_import(client):
    csv = "Date;Open;High;Low;Close;Volume\n"
    rows = []
    for k in range(50):
        rows.append(f"2020-01-0{1 + k // 24} {k % 24:02d}:00;1,5;1,6;1,4;1,55;10")
    csv += "\n".join(rows)
    r = client.post("/api/data/import", data={"symbol": "UK100", "tf": "1H", "tz": "Europe/Warsaw"},
                    files={"file": ("uk.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 50
    bad = client.post("/api/data/import", data={"symbol": "UK100", "tf": "1H"},
                      files={"file": ("x.csv", b"a,b\n1,2", "text/csv")})
    assert bad.status_code == 400


def test_evaluate_outcome_conservative():
    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": [100, 100, 100, 100], "high": [100, 102.5, 101, 104],
                       "low": [100, 99.5, 98, 99], "close": [100, 101, 99, 103], "volume": 0}, index=idx)
    sig = {"direction": "LONG", "entry": 100, "sl": 99, "tp1": 101.5, "tp2": 102, "tp3": 103,
           "bar_ts": int(idx[0].timestamp())}
    r = evaluate_outcome(df, sig, [1.5, 2, 3], 100)
    assert r["result"] == "TP2" and r["result_r"] == 2          # TP2 osiągnięty, potem SL
    sig2 = {**sig, "sl": 99.6}
    r2 = evaluate_outcome(df, sig2, [1.5, 2, 3], 100)
    assert r2["result"] == "SL"                                  # SL i TP w tej samej świecy -> SL
    assert evaluate_outcome(df.iloc[:1], sig, [1.5, 2, 3], 100) is None


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "TREND FOLLOWING" in r.text and "AURA" not in r.text
    assert client.get("/assets/vendor/lightweight-charts.standalone.production.js").status_code == 200


def test_data_status_and_refresh(client):
    st = client.get("/api/data/status").json()
    assert st["provider"] == "fixture"
    assert all(x["5M"] and x["1H"] for x in st["instruments"])
    t = time.time()
    r = client.post("/api/data/refresh").json()
    assert r["ok"], r
    assert time.time() - t < 120


def test_basic_auth(client, monkeypatch):
    import base64
    from app.config import settings
    monkeypatch.setattr(settings, "auth_username", "ala")
    monkeypatch.setattr(settings, "auth_password", "tajne")
    assert client.get("/api/health").status_code == 200           # healthcheck bez hasła
    assert client.get("/api/overview").status_code == 401
    assert client.get("/").status_code == 401
    bad = base64.b64encode(b"ala:zle").decode()
    assert client.get("/api/instruments", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    ok = base64.b64encode(b"ala:tajne").decode()
    assert client.get("/api/instruments", headers={"Authorization": f"Basic {ok}"}).status_code == 200


def test_telegram_channel(monkeypatch):
    import httpx
    from app.config import Settings
    from app.notifications.channels import ChannelError, TelegramChannel
    s = Settings()
    s.telegram_bot_token, s.telegram_chat_id = "123:abc", ["42"]
    sent = []

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None):
            sent.append((url, json))
            if url.endswith("getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": [{"message": {"chat": {"id": 42, "type": "private", "first_name": "Ala"}}}]})
            if json.get("chat_id") == "bad":
                return httpx.Response(400, json={"ok": False, "description": "chat not found"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
    monkeypatch.setattr(httpx, "Client", FakeClient)
    ch = TelegramChannel(s)
    ids = ch.send("x", "🚨 <b>NOWY SETUP</b>")
    assert sent[0][1] == {"chat_id": "42", "text": "🚨 <b>NOWY SETUP</b>", "parse_mode": "HTML",
                          "disable_web_page_preview": True}
    assert ids == [{"chat_id": "42", "message_id": 7}]
    assert ch.find_chats()[0]["chat_id"] == 42
    s.telegram_chat_id = ["bad"]
    with pytest.raises(ChannelError, match="chat not found"):
        ch.send("x", "y")
    assert TelegramChannel(Settings()).configured() is False


def test_positions_api(client):
    r = client.post("/api/positions", json={"symbol": "GOLD", "direction": "LONG", "entry": 3600, "sl": 3590,
                                            "tp": 3620, "lots": 0.02})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert client.get("/api/positions").json()["stats"]["open_count"] >= 1
    bad = client.post("/api/positions", json={"symbol": "GOLD", "direction": "LONG", "entry": 3600, "sl": 3610,
                                              "lots": 0.02})
    assert bad.status_code == 400 and "Stop Loss" in bad.json()["detail"]
    c = client.post(f"/api/positions/{pid}/close", json={"exit_price": 3610})
    assert c.status_code == 200 and c.json()["pnl_pln"] > 0
    assert client.post(f"/api/positions/{pid}/reopen").json()["status"] == "OPEN"
    assert client.delete(f"/api/positions/{pid}").status_code == 200
    assert client.delete(f"/api/positions/{pid}").status_code == 404
    sigs = client.get("/api/signals?limit=1").json()["items"]
    if sigs:
        pf = client.get(f"/api/positions/prefill?signal={sigs[0]['id']}").json()
        assert pf["symbol"] == sigs[0]["symbol"]


def test_price_offsets_api(client):
    items = client.get("/api/prices/offsets").json()["items"]
    gold = next(i for i in items if i["symbol"] == "GOLD")
    assert gold["offset"] is None and gold["src_price"]
    r = client.put("/api/prices/offsets", json={"GOLD": gold["src_price"] - 39})
    assert r.status_code == 200 and r.json()["changed"]["GOLD"] == pytest.approx(-39)
    g2 = next(i for i in r.json()["items"] if i["symbol"] == "GOLD")
    assert g2["offset"] == pytest.approx(-39) and g2["adjusted"] == pytest.approx(gold["src_price"] - 39)
    ov = next(t for t in client.get("/api/overview").json()["items"] if t["symbol"] == "GOLD")
    assert ov["price"] == pytest.approx(gold["src_price"] - 39)
    assert client.put("/api/prices/offsets", json={"GOLD": None}).json()["changed"]["GOLD"] == pytest.approx(39)
    assert client.put("/api/prices/offsets", json={"XXX": 1}).status_code == 400
