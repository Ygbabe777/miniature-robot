import numpy as np
import pandas as pd

from ofo.backtest import MNQ, daily_pnl, run_backtest
from ofo.exits import apply_exits, atr, exit_grid
from ofo.strategies import TEMPLATES
from ofo.trades import extract_trades
from test_research import synth


def bars_from(rows, n_warm=20):
    """rows: (open, high, low, close) after a flat warm-up of range 2 (ATR = 2)."""
    warm = [(100, 101, 99, 100)] * n_warm
    idx = pd.date_range("2025-01-02 09:30", periods=n_warm + len(rows), freq="5min")
    df = pd.DataFrame(warm + rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1
    return df


def pos_long_at(n, i, until):
    p = np.zeros(n); p[i:until + 1] = 1; return pd.Series(p)


def test_stop_hit_fills_at_stop_and_stays_flat():
    b = bars_from([(100, 100.5, 99.5, 100), (100, 100.5, 96, 97), (97, 98, 96, 97.5), (97.5, 99, 97, 98)])
    i = 20                                              # entry at the close of the first real bar (100)
    pos = pos_long_at(len(b), i, i + 3)
    assert abs(atr(b)[i] - 2.0) < 0.2
    p2, xpx, info = apply_exits(b, pos, stop_atr=1.5, rr=None)
    stop = 100 - 1.5 * atr(b)[i]
    assert np.isclose(xpx.iloc[i + 1], stop) and info["kinds"] == ["stop"]
    assert (p2.iloc[i + 1:i + 4] == 0).all()           # no re-entry while the strategy still says long


def test_target_hit_and_tie_goes_to_the_stop_and_gap_fills_at_open():
    a = atr(bars_from([(100, 100.5, 99.5, 100)]))[20]
    up = bars_from([(100, 100.5, 99.5, 100), (100, 100 + 3 * a, 99.9, 100 + a)])
    _, x, info = apply_exits(up, pos_long_at(len(up), 20, 21), 1.0, 2.0)
    assert info["kinds"] == ["target"] and np.isclose(x.iloc[21], 100 + 2 * a)
    tie = bars_from([(100, 100.5, 99.5, 100), (100, 100 + 3 * a, 100 - 3 * a, 100)])
    _, x, info = apply_exits(tie, pos_long_at(len(tie), 20, 21), 1.0, 2.0)
    assert info["kinds"] == ["stop"]                   # both touched in one bar: assume the worst
    gap = bars_from([(100, 100.5, 99.5, 100), (90, 91, 89, 90)])
    _, x, _ = apply_exits(gap, pos_long_at(len(gap), 20, 21), 1.0, None)
    assert x.iloc[21] == 90                            # gapped through the stop: filled at the open


def test_short_side_is_symmetric():
    a = atr(bars_from([(100, 100.5, 99.5, 100)]))[20]
    b = bars_from([(100, 100.5, 99.5, 100), (100, 100 + 2 * a, 99.9, 100)])
    pos = pd.Series(np.concatenate([np.zeros(20), [-1, -1], np.zeros(0)]))
    _, x, info = apply_exits(b, pos, 1.0, None)
    assert info["kinds"] == ["stop"] and np.isclose(x.iloc[21], 100 + a)


def test_trade_list_still_sums_to_the_backtest_with_exits():
    bars = synth(n_sessions=40, seed=5)
    fn = TEMPLATES["breakout"].fn
    params = {"lookback": 20}
    from ofo.data import resample
    from ofo.strategies import flatten_overnight
    b = resample(bars, "15min")
    pos, xpx, _ = apply_exits(b, flatten_overnight(fn(b, **params)), 1.5, 2.0)
    bt = daily_pnl(run_backtest(b, pos, MNQ, 2, 1.0, exit_px=xpx)).sum()
    tr = extract_trades(bars, fn, params, "15min", MNQ, 2, 1.0, exit={"stop_atr": 1.5, "rr": 2.0})
    assert len(tr) > 5 and np.isclose(sum(t["pnl"] for t in tr), bt, rtol=1e-6, atol=0.01)


def test_grid_and_study_report_risk_reward_table(tmp_path):
    assert len(exit_grid()) == 28 and any(g["rr"] == 0.5 for g in exit_grid())      # includes targets closer than the stop
    from ofo.optimizer import run_study
    from ofo.prop import PRESETS
    from ofo.registry import TrialRegistry
    from ofo.specs import from_dict
    spec = from_dict({"name": "bo", "template": "breakout", "ranges": {"lookback": [10, 20]}, "timeframes": ["15min"],
                      "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"})
    from ofo.data import resample
    from ofo.optimizer import _exit_study, _session_key, _sweep
    from ofo.research import daily_series
    reg = TrialRegistry(tmp_path / "r.json")
    bars = synth(n_sessions=200, seed=3)
    eid = reg.begin_experiment("study:bo", spec.n_candidates)
    days = sorted(_session_key(bars).unique())
    cands, series, _ = _sweep(bars, spec, MNQ, 2, 1.0)
    tfs = {"15min": resample(bars, "15min")}
    ex = _exit_study(tfs, cands[0], series[0], MNQ, 2, 1.0, days, reg, "bo", eid, {"family": "E"})
    assert len(ex["grid"]) == 28 and {"stop_atr", "rr", "sharpe", "win_rate", "median_stop_points"} <= set(ex["grid"][0])
    assert reg.n_trials == spec.n_candidates + 28                  # every stop/target cell is a counted trial, in the validation stage
    assert reg.usage(group="stage")["validation"]["trials"] == 28
