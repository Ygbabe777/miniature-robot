import numpy as np
import pandas as pd

from ofo.backtest import MES, daily_pnl, run_backtest
from ofo.judge import judge
from ofo.prop import PRESETS, PropRules, Trailing, pass_probability, simulate_account
from ofo.stats import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe


def test_account_passes_and_busts():
    r = PRESETS["50k"]
    assert simulate_account([1500, 1600], r).status == "passed"
    assert simulate_account([-1000, -1100], r).status == "busted"


def test_trailing_floor_locks_at_start_balance():
    r = PropRules("t", 50_000, 99_999, 2_000, Trailing.EOD)
    # +3000 would trail the floor to 51000 unless locked at 50000
    out = simulate_account([3000, -2500, -400], r)
    assert out.status == "timeout"           # floor locked at 50000, balance 50100
    assert simulate_account([3000, -2500, -600], r).status == "busted"


def test_daily_loss_limit():
    r = PropRules("d", 50_000, 3_000, 2_000, daily_loss_limit=500)
    assert simulate_account([-600], r).status == "busted"


def test_mc_edge_beats_no_edge():
    rng = np.random.default_rng(1)
    good = rng.normal(120, 400, 250)
    bad = rng.normal(-60, 400, 250)
    p_good = pass_probability(good, PRESETS["50k"], n_sims=800)["p_pass"]
    p_bad = pass_probability(bad, PRESETS["50k"], n_sims=800)["p_pass"]
    assert p_good > p_bad + 0.2


def test_more_trials_raise_the_bar():
    assert expected_max_sharpe(1000, 0.01) > expected_max_sharpe(10, 0.01) > 0


def test_noise_strategy_fails_judge():
    """A zero-edge strategy picked from many trials must be rejected."""
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 300, 300)
    v = judge(noise, PRESETS["50k"], n_trials=500, var_sr_trials=0.01)
    assert not v.approved


def test_backtest_no_lookahead_and_costs():
    idx = pd.date_range("2025-01-02", periods=5, freq="D")
    bars = pd.DataFrame({"close": [100, 101, 102, 103, 104.0]}, index=idx)
    # long from bar 0: earns bars 1..4 (4 points), pays entry cost only
    res = run_backtest(bars, [1, 1, 1, 1, 1], MES, slippage_ticks=0)
    assert abs(res["pnl"].sum() - (4 * MES.point_value - MES.commission_per_side)) < 1e-9
    assert res["pnl"].iloc[0] < 0            # nothing earned on the entry bar
    assert len(daily_pnl(res)) == 5


def test_psr_prefers_stronger_signal():
    rng = np.random.default_rng(3)
    assert probabilistic_sharpe(rng.normal(0.2, 1, 500)) > probabilistic_sharpe(rng.normal(0.0, 1, 500))


def test_short_sample_is_insufficient_not_rejected():
    from ofo.judge import INSUFFICIENT
    v = judge(np.random.default_rng(0).normal(100, 300, 60), PRESETS["50k"], 10, 0.01)
    assert v.status == INSUFFICIENT and not v.approved


def test_ci_is_wide_for_short_history_and_narrow_for_long():
    from ofo.prop import pass_probability_ci
    rng = np.random.default_rng(11)
    short = pass_probability_ci(rng.normal(60, 400, 126), PRESETS["50k"], n_outer=60, n_inner=60)
    long_ = pass_probability_ci(rng.normal(60, 400, 1500), PRESETS["50k"], n_outer=60, n_inner=60)
    w = lambda r: r["p_pass_ci95"][1] - r["p_pass_ci95"][0]
    assert w(short) > 2 * w(long_)


def test_lucky_noise_cannot_be_approved_even_if_it_games_the_pass_rate():
    """Zero-edge days with a lucky positive mean: sizing up can make a 5-day pass rate look fine (that is just variance), so
    the pass-rate rule alone proves nothing. The deflated Sharpe, which counts every trial, must still reject them."""
    from ofo.judge import REJECTED
    rng = np.random.default_rng(5)
    for _ in range(5):
        x = rng.normal(0, 400, 126)
        x = x - x.mean() + 60          # force a lucky positive mean
        v = judge(x, PRESETS["50k"], n_trials=1747, var_sr_trials=0.01)
        assert v.status == REJECTED and not v.checks["dsr"]


def test_pass_rate_rule_uses_the_best_size_and_the_owners_threshold():
    from ofo.judge import Thresholds
    rng = np.random.default_rng(2)
    x = rng.normal(150, 250, 130)                                    # a real, steady edge
    ok = judge(x, PRESETS["50k"], n_trials=1, var_sr_trials=0.01, th=Thresholds(min_dsr=0.0))
    assert ok.checks["pass_rate"] and ok.mc["best_size"]["p_fast"] >= 0.30
    strict = judge(x, PRESETS["50k"], n_trials=1, var_sr_trials=0.01, th=Thresholds(min_dsr=0.0, min_pass_rate=0.99))
    assert not strict.checks["pass_rate"]                            # the threshold is the owner's to set
    losing = judge(rng.normal(-80, 300, 130), PRESETS["50k"], n_trials=1, var_sr_trials=0.01, th=Thresholds(min_dsr=0.0))
    assert not losing.checks["pass_rate"] and "pass_prob_ci_lower" not in losing.checks
