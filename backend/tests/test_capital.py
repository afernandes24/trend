from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.market_data.capital import CapitalProvider, parse_prices
from app.market_data.base import MarketDataError


def bars(n, start, minutes, price=100.0):
    out = []
    for k in range(n):
        t = start + timedelta(minutes=minutes * k)
        p = price + k * 0.1
        out.append({"snapshotTime": t.strftime("%Y-%m-%dT%H:%M:%S"), "snapshotTimeUTC": t.strftime("%Y-%m-%dT%H:%M:%S"),
                    "openPrice": {"bid": p, "ask": p + 0.3}, "highPrice": {"bid": p + 1, "ask": p + 1.3},
                    "lowPrice": {"bid": p - 1, "ask": p - 0.7}, "closePrice": {"bid": p + 0.5, "ask": p + 0.8},
                    "lastTradedVolume": 42})
    return {"prices": out, "instrumentType": "INDICES"}


class Fake:
    def __init__(self):
        self.calls = []
        self.logins = 0
        self.expire_next = False
        self.missing_epics = {"US2000"}

    def __call__(self, req: httpx.Request):
        path = req.url.path
        self.calls.append((req.method, path, dict(req.url.params)))
        if path == "/api/v1/session":
            assert req.headers["X-CAP-API-KEY"] == "key"
            self.logins += 1
            return httpx.Response(200, json={}, headers={"CST": f"cst{self.logins}", "X-SECURITY-TOKEN": "sec"})
        if req.headers.get("CST") is None:
            return httpx.Response(401, json={"errorCode": "error.null.client.token"})
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"errorCode": "error.invalid.session.token"})
        if path == "/api/v1/markets":
            return httpx.Response(200, json={"markets": [
                {"epic": "RTY", "instrumentName": "US Russell 2000", "instrumentType": "INDICES"}]})
        if path.startswith("/api/v1/prices/"):
            epic = path.rsplit("/", 1)[-1]
            if epic in self.missing_epics:
                return httpx.Response(404, json={"errorCode": "error.not-found.epic"})
            params = dict(req.url.params)
            n = int(params.get("max", 10))
            minutes = 5 if params["resolution"] == "MINUTE_5" else 60
            if "to" in params:
                to = datetime.fromisoformat(params["to"])
                if to < datetime(2026, 6, 1):
                    return httpx.Response(400, json={"errorCode": "error.invalid.daterange"})
                start = to - timedelta(minutes=minutes * (n - 1))
            else:
                start = datetime(2026, 9, 28, 12, 0) - timedelta(minutes=minutes * (n - 1))
            return httpx.Response(200, json=bars(n, start, minutes))
        return httpx.Response(404)


def provider(fake):
    return CapitalProvider(client=httpx.Client(transport=httpx.MockTransport(fake)), env="demo",
                           api_key="key", identifier="me@x.pl", password="pw")


def test_parse_uses_bid_and_utc():
    df = parse_prices(bars(3, datetime(2026, 9, 28, 10, 0), 5))
    assert len(df) == 3 and str(df.index.tz) == "UTC"
    assert df["open"].iloc[0] == 100.0 and df["high"].iloc[0] == 101.0   # BID
    assert df["volume"].iloc[0] == 42


def test_login_resolution_and_incremental():
    f = Fake()
    p = provider(f)
    df = p.fetch_candles("US100", "5M", full_history=False)
    assert len(df) == 12 and f.logins == 1
    assert p.source_symbol("US100") == "US100"
    # US2000: pierwszy kandydat nie istnieje -> drugi (RTY)
    df2 = p.fetch_candles("US2000", "1H", full_history=False)
    assert p.source_symbol("US2000") == "RTY" and not df2.empty
    assert not any(c[1] == "/api/v1/markets" for c in f.calls)
    # żaden kandydat nie istnieje -> wyszukiwarka
    f.missing_epics = {"J225", "JP225", "JPN225"}
    p.fetch_candles("JAP225", "1H", full_history=False)
    assert any(c[1] == "/api/v1/markets" for c in f.calls) and p.source_symbol("JAP225") == "RTY"


def test_full_history_pages_and_session_renewal():
    f = Fake()
    p = provider(f)
    df = p.fetch_candles("GOLD", "1H", full_history=True)
    assert len(df) > 1000                       # kilka stron historii sklejonych bez duplikatów
    assert df.index.is_monotonic_increasing and not df.index.duplicated().any()
    f.expire_next = True                        # sesja wygasła -> ponowne logowanie
    p.fetch_candles("GOLD", "5M", full_history=False)
    assert f.logins == 2


def test_missing_credentials():
    p = CapitalProvider(client=httpx.Client(transport=httpx.MockTransport(Fake())), env="demo",
                        api_key="", identifier="", password="")
    with pytest.raises(MarketDataError, match="CAPITAL_API_KEY"):
        p.fetch_candles("GOLD", "5M")


def test_provider_switch_cleans_old_scale(db):
    from app.market_data.service import MarketDataService
    from .conftest import FixtureProvider
    m = MarketDataService(db, FixtureProvider())
    m.refresh("GOLD")
    raw, _ = m.raw_last("GOLD")
    m.set_offsets({"GOLD": raw - 39})
    sid = db.insert_signal({"symbol": "GOLD", "tf": "15M", "direction": "LONG", "bar_ts": 1, "entry": 1.0,
                            "sl": 0.5, "tp1": 2, "tp2": 3, "tp3": 4, "score": 80, "reasons": [], "mtf": None})
    pid = db.insert_position({"symbol": "GOLD", "direction": "LONG", "entry": 1.0, "lots": 0.01, "ref_price": 5.0})

    class Other(FixtureProvider):
        name = "capital"
    m2 = MarketDataService(db, Other())
    assert not db.has_candles()
    assert m2.offsets() == {}
    assert db.get_signal(sid)["status"] == "EXPIRED"
    assert db.get_position(pid)["ref_price"] is None
    # ponowne uruchomienie z tym samym źródłem niczego nie czyści
    m2.refresh("GOLD")
    MarketDataService(db, Other())
    assert db.has_candles()
