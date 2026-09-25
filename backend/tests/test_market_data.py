import httpx
import pandas as pd

from app.market_data.base import resample
from app.market_data.csv_provider import parse_csv
from app.market_data.yahoo import YahooProvider


def _payload(ts, closes):
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{
        "open": closes, "high": [c + 1 if c else None for c in closes], "low": [c - 1 if c else None for c in closes],
        "close": closes, "volume": [10] * len(closes)}]}}], "error": None}}


def test_yahoo_parsing_and_fallback():
    calls = []

    def handler(req: httpx.Request):
        sym = req.url.path.rsplit("/", 1)[-1]
        calls.append(sym)
        if sym == "NQ=F":
            return httpx.Response(404, json={"chart": {"result": None, "error": {"code": "Not Found"}}})
        return httpx.Response(200, json=_payload([1700000000, 1700000300, 1700000600], [100.0, None, 102.0]))

    yp = YahooProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    df = yp.fetch_candles("US100", "5M")
    assert calls[:2] == ["NQ=F", "^NDX"]           # fallback na drugi symbol
    assert len(df) == 2                            # świeca z None usunięta
    assert str(df.index.tz) == "UTC"
    assert (df["high"] >= df["close"]).all()
    assert yp.source_symbol("US100") == "^NDX"


def test_resample_4h_alignment():
    idx = pd.date_range("2024-01-01 00:00", periods=8, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": range(8), "high": range(1, 9), "low": range(8), "close": range(8), "volume": 1.0},
                      index=idx).astype(float)
    r = resample(df, "4H")
    assert list(r.index.hour) == [0, 4]
    assert r["open"].tolist() == [0, 4] and r["close"].tolist() == [3, 7] and r["high"].tolist() == [4, 8]


def test_csv_epoch_and_tz():
    df = parse_csv("time,open,high,low,close\n1700000000000,1,2,0.5,1.5\n")
    assert df.index[0] == pd.Timestamp(1700000000, unit="s", tz="UTC")
    w = parse_csv("date;open;high;low;close\n2024-07-01 10:00;1;2;0,5;1,5\n", tz="Europe/Warsaw")
    assert w.index[0].hour == 8                   # CEST -> UTC


def test_drop_incomplete_last():
    from app.market_data.yahoo import drop_incomplete_last
    idx = pd.to_datetime([1_700_000_000, 1_700_000_300], unit="s", utc=True)
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)
    # rynek aktywny, ostatnia transakcja w środku ostatniej świecy -> usuwamy ją
    assert len(drop_incomplete_last(df, "5m", 1_700_000_400, now=1_700_000_500)) == 1
    # świeca domknięta
    assert len(drop_incomplete_last(df, "5m", 1_700_000_600, now=1_700_000_700)) == 2
    # rynek zamknięty dawno (weekend) -> zostawiamy
    assert len(drop_incomplete_last(df, "5m", 1_700_000_400, now=1_700_100_000)) == 2


def test_resampled_incomplete_candle_not_used(db):
    from app.market_data.service import MarketDataService
    from app.strategy.engine import last_closed_index

    class P:
        name = "p"
        def fetch_candles(self, symbol, tf, full_history=True):
            return pd.DataFrame()
        def fetch_fx(self, pair):
            return None
    m = MarketDataService(db, P())
    idx = pd.date_range("2024-01-01 00:00", periods=5, freq="5min", tz="UTC")   # 00:00..00:20
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}, index=idx)
    db.upsert_candles("US100", "5M", df)
    m15 = m.get_candles("US100", "15M")
    assert list(m15.index.minute) == [0, 15]
    assert m15.attrs["last_complete"] is False            # 00:15 ma tylko 2 z 3 świec 5M
    far_future = pd.Timestamp("2030-01-01", tz="UTC").timestamp()
    assert last_closed_index(m15, "15M", now=far_future) == 0
