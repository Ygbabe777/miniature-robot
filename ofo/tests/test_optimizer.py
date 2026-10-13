import numpy as np
import pytest

from ofo.backtest import MNQ
from ofo.optimizer import run_study, surface_metrics
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.specs import SpecError, from_dict
from ofo.strategies import TEMPLATES, orb, orb_retest, vwap_dev
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
    assert from_dict(ok).n_candidates == 4 and from_dict(ok).timeframes == ["1min", "5min"]     # the default execution timeframes
    for bad in ({**ok, "template": "os.system"}, {**ok, "ranges": {"or_minutes": [1]}},
                {**ok, "ranges": {"or_minutes": [15], "x": [1]}}, {**ok, "rationale": ""},
                {**ok, "timeframes": ["1s"]}, {"template": "random_control", "ranges": {"seed": [1]}, "rationale": "x"}):
        with pytest.raises(SpecError):
            from_dict(bad)


def test_new_templates_are_causal():
    bars = synth(n_sessions=30, seed=2)
    for fn, kw in ((orb, {"or_minutes": 30}), (vwap_dev, {"window": 30, "z": 1.5}),
                   (orb_retest, {"or_minutes": 30, "retest_bars": 6, "tol": 0.2})):
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


def test_study_flags_profit_that_is_only_market_drift(tmp_path):
    """A long-biased strategy on a strongly rising market looks robust; once the drift is
    removed the surface must collapse and the locked block must not be touched."""
    import numpy as np
    from ofo.optimizer import detrend
    bars = synth(n_sessions=300, seed=8)
    bars = bars.copy()
    drift = 0.05 * np.arange(len(bars))
    for c in ("open", "high", "low", "close"):
        bars[c] = bars[c] + drift
    spec = from_dict({"template": "breakout", "ranges": {"lookback": [10, 20, 40, 80]},
                      "timeframes": ["15min", "30min"], "rationale": "test"})
    reg = TrialRegistry(tmp_path / "r.json")
    rep = run_study(bars, spec, MNQ, PRESETS["50k"], reg, train_days=60, test_days=10)
    assert rep["surface"]["robust"], rep["surface"]                  # raw: looks like an edge
    assert rep["surface_detrended"] is not None and not rep["surface_detrended"]["robust"]
    assert "fragile_without_market_drift" in rep["locked"]["reason"]
    assert reg.data["locked_looks"] == 0                              # locked block untouched
    d = detrend(bars)
    assert abs((d["close"].iloc[-1] - d["close"].iloc[0])) < 1e-6      # trend really removed


def test_orb_retest_enters_only_after_the_retest():
    """Hand-made day on 5-min bars: range 100..110 (09:30-10:00), breakout close 112, pullback
    touches 110.5 (within tol) and closes 111 -> entry on that bar, not on the breakout bar."""
    import pandas as pd
    idx = pd.date_range("2025-03-03 09:30", periods=12, freq="5min")
    close = [105, 106, 104, 108, 107, 106, 112, 113, 111, 112, 113, 114]
    high = [106, 107, 105, 110, 108, 107, 113, 114, 113, 113, 114, 115]
    low = [100, 104, 103, 107, 106, 105, 110, 111, 110.5, 111, 112, 113]
    bars = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
    pos = orb_retest(bars, or_minutes=30, retest_bars=4, tol=0.1)
    # range = bars 09:30..09:55 -> high 110 low 100; post starts 10:00. Breakout close 112 at 10:00 (i=6)
    assert pos.iloc[6] == 0                               # breakout bar: no entry yet
    assert pos.iloc[8] == 1 and (pos.iloc[8:] == 1).all()  # retest at i=8 (low 110.5, close 111 > 110)
    # no retest within the window -> never enters
    no_retest = orb_retest(bars, or_minutes=30, retest_bars=1, tol=0.0)
    assert (no_retest == 0).all()


def test_new_family_templates_are_causal_including_the_fifteen_minute_context():
    """The new whitelisted families use structural levels and a 15-minute context. Each bar may use only what was complete before it."""
    import pandas as pd
    from ofo.data import resample
    from ofo.strategies import TEMPLATES
    bars = synth(n_sessions=40, seed=3)
    cases = {"sweep_reclaim": {"sweep_atr": 0.2, "reclaim_bars": 3, "level_set": 2, "ctx": 1}, "overnight_gap": {"gap_atr": 0.3, "mode": 0, "exit_min": 120},
             "regime_adaptive": {"er_window": 10, "er_thresh": 0.3, "lookback": 40, "z": 1.5}, "trend_pullback": {"ema_fast": 9, "ema_slow": 50, "pullback_z": 1.0, "ctx": 1}}
    for tf in ("1min", "5min", "15min"):
        b = resample(bars, tf) if tf != "1min" else bars
        for name, kw in cases.items():
            full = TEMPLATES[name].fn(b, **kw)
            for frac in (0.35, 0.6, 0.85):
                cut = int(len(b) * frac) + 7
                pd.testing.assert_series_equal(full.iloc[:cut], TEMPLATES[name].fn(b.iloc[:cut], **kw), check_names=False, obj=f"{name}@{tf}")
            assert (full != 0).any(), name                                     # the family really trades on this data
