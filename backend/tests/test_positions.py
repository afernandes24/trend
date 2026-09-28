import pytest

from app.config import Settings
from app.market_data.service import MarketDataService
from app.notifications.dispatcher import Notifier
from app.services.positions import PositionError, PositionService

from .conftest import FixtureProvider

FX = {"rates": {"USDPLN": 4.0, "EURPLN": 4.3, "GBPPLN": 5.0, "JPYPLN": 0.027}, "live": []}


@pytest.fixture
def svc(db):
    m = MarketDataService(db, FixtureProvider())
    m.refresh("GOLD")
    m.refresh("US100")
    m.fx_rates = lambda: FX
    return PositionService(db, m, Notifier(db, Settings(), fx=m.fx_rates))


def test_open_close_long_pnl(svc):
    p = svc.open({"symbol": "GOLD", "direction": "LONG", "entry": 4212.0, "sl": 4206.0, "tp": 4224.0, "lots": 0.05})
    # ryzyko: 6 pkt * 0.05 lota * 100 USD * 4.0 = 120 PLN; cel 12 pkt = 240 PLN
    assert p["risk_pln"] == 120.0 and p["target_pln"] == 240.0 and p["rr"] == 2.0
    assert p["est_pnl_pln"] == 0.0                 # zapis w tej samej chwili -> brak zmiany
    c = svc.close(p["id"], exit_price=4220.0)
    assert c["status"] == "CLOSED" and c["pnl_pln"] == 160.0 and c["r_multiple"] == pytest.approx(1.33, abs=0.01)


def test_short_and_manual_pnl(svc):
    p = svc.open({"symbol": "US100", "direction": "SHORT", "entry": 24850, "sl": 24900, "tp": 24750, "lots": 0.1})
    assert p["risk_pln"] == 50 * 0.1 * 20 * 4.0
    c = svc.close(p["id"], exit_price=24800)
    assert c["pnl_pln"] == 50 * 0.1 * 20 * 4.0      # SHORT zarabia na spadku
    c2 = svc.close(p["id"], pnl_pln=-12.5)         # wynik wpisany z XTB ma pierwszeństwo
    assert c2["pnl_pln"] == -12.5 and c2["pnl_manual"] == 1


def test_validation(svc):
    with pytest.raises(PositionError):
        svc.open({"symbol": "GOLD", "direction": "LONG", "entry": 100, "sl": 101, "lots": 0.1})
    with pytest.raises(PositionError):
        svc.open({"symbol": "GOLD", "direction": "SHORT", "entry": 100, "tp": 101, "lots": 0.1})
    with pytest.raises(PositionError):
        svc.open({"symbol": "XXX", "direction": "LONG", "entry": 100, "lots": 0.1})
    with pytest.raises(PositionError):
        svc.close(999, exit_price=1)


def test_estimated_pnl_cancels_price_offset(svc, db):
    p = svc.open({"symbol": "GOLD", "direction": "LONG", "entry": 4212.0, "sl": 4206.0, "tp": 4224.0, "lots": 0.05})
    ref = p["ref_price"]
    db.update_position(p["id"], ref_price=ref - 3.0)   # rynek od zapisu wzrósł o 3 pkt
    e = svc.enrich(db.get_position(p["id"]))
    assert e["est_price"] == pytest.approx(4215.0)
    assert e["est_pnl_pln"] == pytest.approx(3 * 0.05 * 100 * 4.0)
    db.update_position(p["id"], ref_price=ref + 7.0)   # spadek o 7 pkt -> poniżej SL
    assert svc.enrich(db.get_position(p["id"]))["probably_hit"] == "SL"


def test_stats_and_prefill(svc, db):
    a = svc.open({"symbol": "GOLD", "direction": "LONG", "entry": 4212.0, "sl": 4206.0, "tp": 4224.0, "lots": 0.05})
    b = svc.open({"symbol": "GOLD", "direction": "LONG", "entry": 4212.0, "sl": 4206.0, "tp": 4224.0, "lots": 0.05})
    svc.close(a["id"], exit_price=4224.0)   # +240
    svc.close(b["id"], exit_price=4206.0)   # -120
    ov = svc.overview()
    st = ov["stats"]
    assert st["trades"] == 2 and st["win_rate"] == 50.0 and st["total_pln"] == 120.0
    assert st["today_pln"] == 120.0 and st["week_pln"] == 120.0 and st["month_pln"] == 120.0
    sig_id = db.insert_signal({"symbol": "GOLD", "tf": "15M", "direction": "SHORT", "bar_ts": 1_700_000_000,
                               "entry": 4250.0, "sl": 4256.0, "tp1": 4241.0, "tp2": 4238.0, "tp3": 4232.0,
                               "score": 86, "reasons": [], "mtf": None})
    pf = svc.prefill(sig_id)
    assert pf["direction"] == "SHORT" and pf["sl_distance"] == 6.0 and pf["tp_distances"] == [9.0, 12.0, 18.0]
    assert pf["suggested_lots"] and pf["current_price"]
