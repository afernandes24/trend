import numpy as np
import pandas as pd
import pytest

from app.market_data.base import resample
from app.strategy.engine import analyze_tf, mtf_summary, prepare
from app.strategy.indicators import compute_indicators, rsi, sma
from app.strategy.levels import compute_levels
from app.strategy.params import StrategyParams
from app.strategy.scoring import MAX_POINTS, score_band

from .conftest import eurusd


@pytest.fixture(scope="module")
def data():
    return eurusd()


def test_indicator_basics(data):
    p = StrategyParams()
    ind = compute_indicators(data, p)
    assert np.allclose(ind["sma_fast"].iloc[-1], data["close"].iloc[-20:].mean())
    r = ind["rsi"].dropna()
    assert r.between(0, 100).all()
    assert (ind["atr"].dropna() > 0).all()
    assert ind["adx"].dropna().between(0, 100).all()


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert rsi(up, 14).dropna().iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert rsi(down, 14).dropna().iloc[-1] == pytest.approx(0.0)


def test_scores_within_bounds_and_components(data):
    pr = prepare(data, "1H", StrategyParams())
    s = pr.scores
    for side in ("long", "short"):
        sc = s[f"{side}_score"].dropna()
        assert sc.between(0, 100).all()
        for k, mx in MAX_POINTS.items():
            assert s[f"{side}_{k}"].max() <= mx
    assert sum(MAX_POINTS.values()) == 100


def test_gates_are_mutually_exclusive(data):
    pr = prepare(data, "1H", StrategyParams())
    assert not (pr.scores["long_gate"] & pr.scores["short_gate"]).any()


def test_no_lookahead_in_scores_and_events(data):
    """Wynik na świecy t nie może zależeć od danych po t."""
    p = StrategyParams(min_score=70)
    full = prepare(data, "1H", p)
    cut = 3500
    part = prepare(data.iloc[:cut], "1H", p)
    cols = ["long_score", "short_score", "long_gate", "short_gate", "htf_4H", "htf_1D"]
    a = full.scores[cols].iloc[:cut]
    b = part.scores[cols]
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    assert (full.events.iloc[:cut].values == part.events.values).all()
    for c in ("last_swing_low", "last_swing_high", "prev_swing_low"):
        pd.testing.assert_series_equal(full.ind[c].iloc[:cut], part.ind[c], check_dtype=False)


def test_levels_long_short(data):
    p = StrategyParams()
    pr = prepare(data, "1H", p)
    i = len(data) - 10
    lg = compute_levels(pr.ind, i, "LONG", p)
    sh = compute_levels(pr.ind, i, "SHORT", p)
    a = pr.ind["atr"].iloc[i]
    assert lg["sl"] < lg["entry"] < lg["tp1"] < lg["tp2"] < lg["tp3"]
    assert sh["sl"] > sh["entry"] > sh["tp1"] > sh["tp2"] > sh["tp3"]
    for lv in (lg, sh):
        assert p.sl_min_atr * a - 1e-12 <= lv["risk"] <= p.sl_max_atr * a + 1e-12
        assert abs(lv["tp1"] - lv["entry"]) == pytest.approx(1.5 * lv["risk"])
        assert abs(lv["tp3"] - lv["entry"]) == pytest.approx(3.0 * lv["risk"])


def test_analyze_and_mtf(data):
    p = StrategyParams()
    now = data.index[-1].timestamp() + 7200
    per = {}
    for tf in ("1H", "4H"):
        df = data if tf == "1H" else resample(data, "4H")
        per[tf] = analyze_tf("US100", tf, df, p, now=now)
        assert per[tf]["ok"], per[tf]
        assert 0 <= per[tf]["score"] <= 100
        assert per[tf]["band"] == score_band(per[tf]["score"])
    per["15M"] = {**per["1H"], "tf": "15M"}
    per["5M"] = {"ok": False}
    m = mtf_summary(per, "15M")
    assert len(m["rows"]) == 4 and m["rows"][0]["tf"] == "4H"
    assert m["all_aligned"] is False  # 5M brak danych


def test_forming_bar_is_ignored(data):
    p = StrategyParams()
    last = data.index[-1].timestamp()
    closed = analyze_tf("US100", "1H", data, p, now=last + 3601)
    forming = analyze_tf("US100", "1H", data, p, now=last + 60)
    assert closed["bar_time"] == data.index[-1].isoformat()
    assert forming["bar_time"] == data.index[-2].isoformat()


def test_params_validation():
    with pytest.raises(ValueError):
        StrategyParams.from_dict({"sma_fast": 60, "sma_mid": 50})
    with pytest.raises(ValueError):
        StrategyParams.from_dict({"tp_r": [1, 2]})
    p = StrategyParams.from_dict({"tp_r": [3, 1.5, 2]})
    assert p.tp_r == [1.5, 2.0, 3.0]
    assert sma(pd.Series([1.0, 2, 3]), 2).iloc[-1] == 2.5
