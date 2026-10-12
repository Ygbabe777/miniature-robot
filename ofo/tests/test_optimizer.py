import numpy as np
import pytest

from ofo.backtest import MNQ
from ofo.optimizer import run_study, surface_metrics
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.specs import SpecError, from_dict
from ofo.strategies import TEMPLATES, orb, vwap_dev
from test_research import synth


def _grid_rows(f):
    return [{"a": a, "b": b, "tf": "15min", "sharpe": f(a, b)} for a in range(10) for b in range(10)]


def test_plateau_is_robust_and_isolated_spike_is_not():
    plateau = surface_metrics(_grid_rows(lambda a, b: 0.1 - 0.001 * abs(a - 5) - 0.001 * abs(b - 5)), ["a", "b"])
    spike = surface_metrics(_grid_rows(lambda a, b: 0.3 if (a, b) == (4, 6) else -0.02), ["a", "b"])
    assert plateau["robust"]
    assert not spike["robust"] and spike["best_sharpe"] > plateau["best_sharpe"]  # higher peak, still rejected


def test_noise_surface_is_not_robust():
    rng = np.random.default_rng(0)
    bad = 0
    for _ in range(20):
        rows = _grid_rows(lambda a, b: rng.normal(-0.01, 0.05))
        bad += not surface_metrics(rows, ["a", "b"])["robust"]
    assert bad >= 19


def test_spec_validation():
    ok = {"template": "orb", "ranges": {"or_minutes": [15, 30]}, "rationale": "range expansion after the open"}
    assert from_dict(ok).n_candidates == 8
    for bad in ({**ok, "template": "os.system"}, {**ok, "ranges": {"or_minutes": [1]}},
                {**ok, "ranges": {"or_minutes": [15], "x": [1]}}, {**ok, "rationale": ""},
                {**ok, "timeframes": ["1s"]}, {"template": "random_control", "ranges": {"seed": [1]}, "rationale": "x"}):
        with pytest.raises(SpecError):
            from_dict(bad)


def test_new_templates_are_causal():
    bars = synth(n_sessions=30, seed=2)
    for fn, kw in ((orb, {"or_minutes": 30}), (vwap_dev, {"window": 30, "z": 1.5})):
        full = fn(bars, **kw)
        cut = len(bars) // 2 + 137
        import pandas as pd
        pd.testing.assert_series_equal(full.iloc[:cut], fn(bars.iloc[:cut], **kw), check_names=False)


def test_study_on_random_walk_is_rejected_without_touching_locked(tmp_path):
    bars = synth(n_sessions=260, seed=4)
    spec = from_dict({"template": "ema_cross", "ranges": {"fast": [5, 9], "slow": [30, 60]},
                      "timeframes": ["15min", "30min"], "rationale": "test"})
    reg = TrialRegistry(tmp_path / "r.json")
    rep = run_study(bars, spec, MNQ, PRESETS["50k"], reg, train_days=60, test_days=10)
    assert rep["locked"]["status"] != "APPROVED"
    assert reg.n_trials >= rep["n_candidates"]
