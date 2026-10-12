import numpy as np
import pandas as pd
import pytest

from ofo.backtest import MNQ, run_backtest
from ofo.data import resample
from ofo.strategies import TEMPLATES, flatten_overnight
from ofo.trades import extract_trades, summarize
from test_research import synth


@pytest.mark.parametrize("name,params,tf", [
    ("ema_cross", {"fast": 5, "slow": 30}, "15min"),
    ("orb", {"or_minutes": 30}, "5min"),
    ("orb_retest", {"or_minutes": 30, "retest_bars": 6, "tol": 0.2}, "15min"),
    ("mean_reversion", {"window": 30, "z": 1.5}, "30min"),
])
def test_trade_pnls_add_up_to_the_backtest_pnl(name, params, tf):
    bars = synth(n_sessions=40, seed=12, drift_ar=0.03)
    fn = TEMPLATES[name].fn
    trades = extract_trades(bars, fn, params, tf, MNQ, contracts=2, slippage_ticks=1.0)
    b = resample(bars, tf)
    res = run_backtest(b, flatten_overnight(fn(b, **params)), MNQ, 2, 1.0)
    assert len(trades) > 3
    assert abs(sum(t["pnl"] for t in trades) - res["pnl"].sum()) < 1e-6


def test_trades_are_well_formed_and_flat_before_the_close():
    bars = synth(n_sessions=30, seed=2)
    trades = extract_trades(bars, TEMPLATES["breakout"].fn, {"lookback": 20}, "15min", MNQ)
    assert trades
    for t in trades:
        assert t["exit_t"] >= t["entry_t"] and t["side"] in (1, -1)
        assert 1.6e9 < t["entry_t"] < 1.9e9                      # epoch seconds
        hour = pd.Timestamp(t["exit_t"], unit="s")
        assert not (hour.hour * 60 + hour.minute > 16 * 60 + 45 and hour.hour < 18)   # never exits in the break after 16:45
    s = summarize(trades)
    assert s["n"] == len(trades) and s["long"] + s["short"] == s["n"] and 0 <= s["win_rate"] <= 1
    assert summarize([]) == {"n": 0, "win_rate": None, "pnl": 0.0}
