"""One trade a week is a valid frequency (the owner runs several strategies per account): it is not treated as sporadic, and the sample floors fit ~2 years of data."""
import numpy as np

from ofo import judges as J
from ofo.judge import Thresholds, judge
from ofo.prop import PropRules


def series(days, seed=1):
    return list(np.random.default_rng(seed).normal(20, 150, days))


def test_one_trade_a_week_is_enough_frequency_for_the_legacy_judge_sample_rule():
    th = Thresholds()
    assert th.min_trades_per_day == 0.2 and th.min_trades == 15
    v = judge(series(100), PropRules("t", 50000, 3000, 2000), n_trials=10, var_sr_trials=0.001, th=th, n_trades=20)             # 0.2 trades/day, fewer than 120 days
    assert v.checks["enough_days"] is True and v.mc["sample"]["days_waived"] is True and v.mc["sample"]["sporadic"] is False


def test_less_than_a_trade_a_week_is_still_sporadic_and_a_tiny_sample_is_still_refused():
    th = Thresholds()
    v = judge(series(100), PropRules("t", 50000, 3000, 2000), n_trials=10, var_sr_trials=0.001, th=th, n_trades=8)              # 0.08/day and < 15 trades
    assert v.checks["enough_days"] is False and v.status == "INSUFFICIENT_EVIDENCE"
    assert v.mc["sample"]["sporadic"] is True


def test_the_statistical_bar_did_not_move_only_the_sample_floors_fit_weekly_trading():
    assert J.DEFAULTS["confidence"] == 0.95 and J.DEFAULTS["min_oos_days"] == 80
    assert J.DEFAULTS["min_oos_trades"] == 15 and J.DEFAULTS["min_backtest_trades"] == 100
