import dataclasses

import numpy as np
import pandas as pd
import pytest

from ofo.backtest import MNQ
from ofo.ledger import run_ledger
from ofo.prop import PRESETS, PropRules, Trailing, simulate_account
from ofo.prop_intraday import BarArrays, arrays_from_ledger, describe, historical_attempts, intraday_report, simulate_intraday

R = PropRules("t", 50_000, 3_000, 2_000, trailing=Trailing.EOD, lock_at_start=True)


def arr(sessions):
    """sessions: list of (pnl, worst, best) lists per session."""
    pnl, worst, best, bounds, i = [], [], [], [], 0
    for p, w, b in sessions:
        pnl += p; worst += w; best += b
        bounds.append((i, i + len(p))); i += len(p)
    return BarArrays(np.array(pnl, float), np.array(worst, float), np.array(best, float), bounds,
                     [pd.Timestamp("2025-01-01") + pd.Timedelta(days=k) for k in range(len(bounds))])


def test_a_drawdown_inside_the_day_that_recovers_by_the_close_still_busts_the_account():
    # position open: -$2,500 at the worst moment, closes the day +$100. The daily model only sees +$100.
    a = arr([([0, -300, 400], [0, -2500, -100], [0, 0, 600])])
    for mode in ("static", "eod", "intraday"):
        r = simulate_intraday(a, R, mode)
        assert r["status"] == "busted" and r["breach"]["while_position_open"] and r["breach"]["bar"] == 1
        assert not r["breach"]["closed_below_floor"] and r["model"] == "intraday"
    assert simulate_account([100.0], R).status == "timeout" and simulate_account([100.0], R).model == "daily_simplified"


def test_intraday_trailing_floor_rises_with_unrealised_profit_but_eod_and_static_do_not():
    # day 1: runs +$1,800 unrealised, closes +$200.  day 2: gives back $400 intraday from the 50,200 close.
    a = arr([([0, 200], [0, 0], [0, 1800]), ([0, -100], [0, -400], [0, 0])])
    intr = simulate_intraday(a, R, "intraday")
    assert intr["status"] == "busted" and intr["breach"]["session"].endswith("01-02") and intr["breach"]["floor"] == 49_800
    assert simulate_intraday(a, R, "eod")["status"] == "timeout"        # EOD floor: 50,200 - 2,000 = 48,200
    assert simulate_intraday(a, R, "static")["status"] == "timeout"     # static floor: 48,000


def test_lock_at_start_stops_the_trailing_floor_at_the_start_balance():
    a = arr([([0, 3000], [0, 0], [0, 3500]), ([0, -100], [0, -2400], [0, 0])])      # equity peaked at 53,500 but the account passes day 1?
    r = dataclasses.replace(R, profit_target=10_000)                                  # keep it running
    out = simulate_intraday(a, r, "intraday")
    assert out["status"] == "busted" and out["breach"]["floor"] == 50_000                # floor locked at 50,000, not 51,500
    free = simulate_intraday(a, dataclasses.replace(r, lock_at_start=False), "intraday")
    assert free["breach"]["floor"] == 51_500


def test_daily_loss_limit_is_checked_against_the_intraday_low():
    a = arr([([0, 50], [0, -1200], [0, 0])])
    r = dataclasses.replace(R, daily_loss_limit=1_000)
    out = simulate_intraday(a, r, "eod")
    assert out["status"] == "busted" and out["breach"]["kind"] == "daily_loss_limit" and out["breach"]["while_position_open"]
    assert simulate_intraday(a, R, "eod")["status"] == "timeout"


def test_target_is_evaluated_at_the_end_of_the_session_and_min_days_apply():
    a = arr([([0, 3100], [0, 0], [0, 3100])] * 3)
    r = dataclasses.replace(R, min_days=2)
    out = simulate_intraday(a, r, "eod")
    assert out["status"] == "passed" and out["sessions"] == 2                            # first day reaches it but min_days=2
    assert simulate_intraday(a, dataclasses.replace(R, min_days=1), "eod")["sessions"] == 1


def test_daily_simulator_refuses_intraday_trailing_rules():
    with pytest.raises(ValueError):
        simulate_account([100.0, -50.0], dataclasses.replace(R, trailing=Trailing.INTRADAY))


def test_degenerate_one_bar_per_session_matches_the_daily_simulator_exactly():
    """With a single bar per session and no excursion beyond the close, both simulators describe the same account."""
    rng = np.random.default_rng(5)
    for trial in range(60):
        d = rng.normal(80, 700, 40)
        a = arr([([x], [min(0.0, x)], [max(0.0, x)]) for x in d])
        for mode in ("static", "eod"):
            rules = dataclasses.replace(R, trailing=Trailing(mode), min_days=2)
            i = simulate_intraday(a, rules, mode)
            dly = simulate_account(d, rules)
            assert (i["status"], i["sessions"]) == (dly.status, dly.days), (trial, mode, i, dly)


def test_historical_attempts_report_what_only_the_intraday_model_sees():
    sess = []
    for k in range(40):                      # every day: wanders -$2,300 intraday, closes +$50
        sess.append(([0, 50], [0, -2300], [0, 50]))
    a = arr(sess)
    rep = historical_attempts(a, R, "eod", horizon=10)
    assert rep["p_bust"] == 1.0 and rep["daily_model_p_bust"] == 0.0 and rep["p_bust_only_intraday"] == 1.0
    assert rep["p_bust_with_position_open"] == 1.0 and rep["windows_independent"] is False
    full = intraday_report(a, R, 10)
    assert set(full) == {"static", "eod", "intraday"} and full["intraday"]["model"] == "intraday"
    assert "INTRADAY" in describe(full, "intraday", "en") and "INTRADAY" in describe(full, "intraday", "it")
    half = intraday_report(a, R, 10, scale=0.5)                                      # half the size: -$1,150 worst: survives
    assert half["eod"]["p_bust"] == 0.0


def test_ledger_tracks_the_intrabar_path_of_an_open_position():
    idx = pd.date_range("2025-01-02 09:30", periods=5, freq="5min")
    bars = pd.DataFrame({"open": [100, 100, 100, 100, 100], "high": [101, 101, 104, 101, 101], "low": [99, 99, 96, 99, 99],
                         "close": [100, 100, 101, 100, 100], "volume": 1}, index=idx)
    led = run_ledger(bars, [0, 1, 1, 0, 0], MNQ, 1, 0.0)
    pv = 2.0
    assert led.worst.iloc[2] == (96 - 100) * pv and led.best.iloc[2] == (104 - 100) * pv       # bar 2 swung -4 / +4 points
    assert led.worst.iloc[1] == 0 == led.best.iloc[1]                                          # the entry bar holds nothing yet
    stopped = run_ledger(bars, [0, 1, 1, 0, 0], MNQ, 1, 0.0, {"stop_atr": 0.5, "rr": None}, atr_n=2)
    t = [x for x in stopped.trades if x["reason"] == "stop"]
    assert len(t) == 1 and t[0]["exit_px"] == 99.0                                              # ATR 2 x 0.5 = 1 point below 100
    assert abs(stopped.worst.iloc[2] - (99 - 100) * pv) < 1e-9                                  # the path ends at the stop, not at the low of 96
    a = arrays_from_ledger(bars, led)
    assert len(a.bounds) == 1 and a.pnl.shape == (5,)
