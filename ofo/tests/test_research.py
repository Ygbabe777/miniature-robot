import numpy as np
import pandas as pd

from ofo.backtest import MNQ
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.research import build_candidates, run_cycle
from ofo.strategies import GRIDS


def synth(n_sessions=520, drift_ar=0.0, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2023-01-02", periods=n_sessions)
    idx, sess = [], []
    for d in days:
        t = pd.date_range(d - pd.Timedelta(hours=6), d + pd.Timedelta(hours=17) - pd.Timedelta(minutes=1), freq="1min")
        idx += list(t); sess += [d] * len(t)
    n = len(idx)
    e = rng.normal(0, 1.0, n)
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = drift_ar * r[i - 1] + e[i]
    close = 15000 + np.cumsum(r)
    df = pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                       "close": close, "volume": 100.0, "session": sess}, index=pd.DatetimeIndex(idx))
    return df


def small(cands):
    return [c for c in cands if c["tf"] in ("15min", "30min")][::3] + \
           [c for c in cands if c["family"] == "random_control"]


def test_random_walk_gets_no_approvals(tmp_path):
    bars = synth(n_sessions=260, drift_ar=0.0, seed=1)
    reg = TrialRegistry(tmp_path / "r.json")
    rep = run_cycle(bars, MNQ, PRESETS["50k"], reg, candidates=small(build_candidates()),
                    train_days=60, test_days=10)
    for fam, e in rep["families"].items():
        assert e["locked"]["status"] != "APPROVED", fam
    assert reg.n_trials >= rep["n_candidates"]


def test_registry_grows_and_persists(tmp_path):
    r = TrialRegistry(tmp_path / "r.json")
    r.add_trials(10, "a"); r.add_locked_look("x", "REJECTED")
    assert TrialRegistry(tmp_path / "r.json").n_trials == 11


def test_strategies_are_causal():
    bars = synth(n_sessions=30, seed=3)
    for fam, (fn, grid) in GRIDS.items():
        full = fn(bars, **grid[0])
        cut = len(bars) // 2
        part = fn(bars.iloc[:cut], **grid[0])
        pd.testing.assert_series_equal(full.iloc[:cut], part, check_names=False)
