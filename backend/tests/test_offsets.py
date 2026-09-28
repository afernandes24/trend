import pytest

from app.config import Settings
from app.market_data.service import MarketDataService
from app.notifications.dispatcher import Notifier
from app.services.positions import PositionService

from .conftest import FixtureProvider


@pytest.fixture
def market(db):
    m = MarketDataService(db, FixtureProvider())
    m.refresh("GOLD")
    m.fx_rates = lambda: {"rates": {"USDPLN": 4.0}, "live": []}
    return m


def test_offset_shifts_all_prices(market):
    raw, _ = market.raw_last("GOLD")
    before = market.get_candles("GOLD", "15M")
    deltas = market.set_offsets({"GOLD": raw - 39.0})       # XTB 39 $ taniej niż futures
    assert deltas["GOLD"] == pytest.approx(-39.0)
    after = market.get_candles("GOLD", "15M")
    assert (after["close"] - before["close"]).round(6).eq(-39.0).all()
    assert (after["high"] - before["high"]).round(6).eq(-39.0).all()
    assert market.get_candles("GOLD", "15M", raw=True)["close"].iloc[-1] == pytest.approx(raw)
    assert after.attrs == before.attrs
    # ponowna kalibracja zwraca tylko różnicę
    assert market.set_offsets({"GOLD": raw - 40.0})["GOLD"] == pytest.approx(-1.0)
    # usunięcie korekty
    assert market.set_offsets({"GOLD": None})["GOLD"] == pytest.approx(40.0)
    assert market.offset("GOLD") == 0.0


def test_offset_moves_open_signals_and_positions(db, market):
    from app.services.analysis import AnalysisService
    n = Notifier(db, Settings(), fx=market.fx_rates)
    an = AnalysisService(db, market, n)
    ps = PositionService(db, market, n)
    raw, _ = market.raw_last("GOLD")
    sig_id = db.insert_signal({"symbol": "GOLD", "tf": "15M", "direction": "SHORT", "bar_ts": 1_700_000_000,
                               "entry": raw, "sl": raw + 10, "tp1": raw - 15, "tp2": raw - 20, "tp3": raw - 30,
                               "score": 80, "reasons": [], "mtf": None})
    pos = ps.open({"symbol": "GOLD", "direction": "SHORT", "entry": raw - 39, "sl": raw - 29, "tp": raw - 59,
                   "lots": 0.01})
    assert pos["est_pnl_pln"] == 0.0
    deltas = market.set_offsets({"GOLD": raw - 39.0})
    an.apply_offset_change(deltas)
    ps.apply_offset_change(deltas)
    sig = db.get_signal(sig_id)
    assert sig["entry"] == pytest.approx(raw - 39) and sig["sl"] == pytest.approx(raw - 29)
    # szacowany wynik pozycji nie może „skoczyć" przez samą korektę
    assert ps.enrich(db.get_position(pos["id"]))["est_pnl_pln"] == pytest.approx(0.0)


def test_alert_uses_corrected_prices(db, market):
    raw, _ = market.raw_last("GOLD")
    market.set_offsets({"GOLD": raw - 39.0})
    assert market.get_candles("GOLD", "5M")["close"].iloc[-1] == pytest.approx(raw - 39.0)
