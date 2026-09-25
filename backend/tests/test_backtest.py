import pandas as pd
import pytest

from app.backtest.engine import run_backtest, simulate
from app.backtest.metrics import compute_metrics
from app.backtest.walkforward import run_walkforward
from app.instruments import Instrument
from app.strategy.engine import prepare
from app.strategy.params import StrategyParams

from .conftest import eurusd

INST = Instrument("TEST", "EURUSD fixture", "fx", "USD", 5, 100000, 0.00008)


@pytest.fixture(scope="module")
def data():
    return eurusd()


def test_metrics_known_values():
    tr = [{"r": 2.0, "pnl_points": 2, "direction": "LONG", "bars_held": 3, "entry_time": "a", "exit_time": "b"},
          {"r": -1.0, "pnl_points": -1, "direction": "LONG", "bars_held": 3, "entry_time": "a", "exit_time": "b"},
          {"r": -1.0, "pnl_points": -1, "direction": "SHORT", "bars_held": 3, "entry_time": "a", "exit_time": "b"},
          {"r": 2.0, "pnl_points": 2, "direction": "SHORT", "bars_held": 3, "entry_time": "a", "exit_time": "b"}]
    m = compute_metrics(tr)
    assert m["win_rate"] == 50.0
    assert m["profit_factor"] == 2.0
    assert m["expectancy_r"] == 0.5
    assert m["total_r"] == 2.0
    assert m["max_drawdown_r"] == 2.0
    assert m["max_loss_streak"] == 2 and m["max_win_streak"] == 1
    assert m["long"]["total_r"] == 1.0 and m["short"]["total_r"] == 1.0


@pytest.mark.parametrize("mode", ["single", "scaled"])
def test_backtest_invariants(data, mode):
    p = StrategyParams(min_score=70, exit_mode=mode)
    res = run_backtest(data, "1H", p, INST, split=0.7)
    trades = res["trades"]
    assert res["all"]["trades"] == len(trades) > 10
    # brak nakładających się pozycji, wejście po sygnale
    for a, b in zip(trades, trades[1:]):
        assert b["entry_ts"] > a["exit_ts"] or b["entry_ts"] >= a["exit_ts"]
    for t in trades:
        assert t["entry_time"] > t["signal_time"]
        if t["exit_reason"] == "SL":
            assert t["r"] <= -1 + 1e-9          # SL = -1R (lub gorzej przy luce)
        if mode == "single" and t["exit_reason"] == "TP":
            assert t["r"] >= p.rr_target - 1e-9
    # podział train/test pokrywa cały okres bez nakładania
    sp = res["split"]
    assert sp["train"]["to"] < sp["test"]["from"]


def test_backtest_no_lookahead(data):
    """Transakcje zamknięte przed końcem krótszej próbki muszą być identyczne."""
    p = StrategyParams(min_score=70)
    full = run_backtest(data, "1H", p, INST)["trades"]
    cut = data.index[3800]
    part = run_backtest(data.iloc[:3801], "1H", p, INST)["trades"]
    done_full = [t for t in full if pd.Timestamp(t["exit_time"]) < cut]
    done_part = [t for t in part if pd.Timestamp(t["exit_time"]) < cut]
    assert len(done_full) == len(done_part) > 0
    for a, b in zip(done_full, done_part):
        assert a["entry_time"] == b["entry_time"] and a["r"] == pytest.approx(b["r"])


def test_spread_costs_money(data):
    p = StrategyParams(min_score=70)
    with_cost = run_backtest(data, "1H", p, INST)["all"]["total_r"]
    p2 = StrategyParams(min_score=70, use_spread=False)
    no_cost = run_backtest(data, "1H", p2, INST)["all"]["total_r"]
    assert no_cost > with_cost


def test_walkforward(data):
    wf = run_walkforward(data, "1H", StrategyParams(), INST, folds=4)
    assert len(wf["folds"]) == 4
    for f in wf["folds"]:
        assert f["train_to"] < f["test_from"]
    assert wf["oos"]["trades"] == len(wf["trades"])


def test_too_little_data(data):
    with pytest.raises(ValueError):
        run_backtest(data.iloc[:150], "1H", StrategyParams(), INST)


def test_simulate_range_respected(data):
    p = StrategyParams(min_score=70)
    pr = prepare(data, "1H", p)
    tr = simulate(pr, p, INST.spread, 1000, 2000)
    for t in tr:
        assert data.index[1000] <= pd.Timestamp(t["entry_time"]) <= data.index[1999]
        assert pd.Timestamp(t["exit_time"]) <= data.index[1999]
