import numpy as np
import pandas as pd
import pytest

from ofo.backtest import MES, MGC, MNQ, run_backtest
from ofo.data import resample
from ofo.exits import apply_exits
from ofo.ledger import AccountingError, reconcile, run_ledger, run_ledger_next_open
from ofo.strategies import TEMPLATES, flatten_overnight
from test_research import synth


def random_world(seed: int, n: int = 400):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-02 09:30", periods=n, freq="5min")
    gap = rng.normal(0, 3, n) * (rng.random(n) < 0.05)
    close = 1000 + np.cumsum(rng.normal(0, 2, n) + gap)
    op = np.concatenate([[1000], close[:-1]]) + gap
    hi = np.maximum(op, close) + np.abs(rng.normal(0, 1.5, n))
    lo = np.minimum(op, close) - np.abs(rng.normal(0, 1.5, n))
    bars = pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1}, index=idx)
    pos, cur = np.zeros(n), 0
    for t in range(n):
        if rng.random() < 0.08:
            cur = rng.choice([-1, 0, 1])
        pos[t] = cur
    pos[-3:] = 0
    return bars, pos


def test_vectorised_engine_and_ledger_agree_bar_by_bar_on_random_worlds():
    """Two independent implementations of the same accounting rules: timing, costs, intrabar exits, gaps, reversals."""
    rng = np.random.default_rng(1)
    for seed in range(60):
        bars, pos = random_world(seed)
        ex = None if seed % 4 == 0 else {"stop_atr": float(rng.choice([0.5, 1, 1.5, 3])),
                                         "rr": None if seed % 5 == 0 else float(rng.choice([0.5, 1, 2, 3]))}
        r = reconcile(bars, pos, [MNQ, MES, MGC][seed % 3], contracts=int(rng.integers(1, 5)),
                      slippage_ticks=float(rng.choice([0, 1, 2])), exit=ex)
        assert r["ok"], (seed, ex, r)


def test_real_strategies_with_exits_reconcile_and_trade_pnl_sums_to_the_engine():
    bars = synth(n_sessions=40, seed=5)
    for name, params in (("breakout", {"lookback": 20}), ("ema_cross", {"fast": 8, "slow": 30})):
        b = resample(bars, "15min")
        pos = flatten_overnight(TEMPLATES[name].fn(b, **params))
        for ex in (None, {"stop_atr": 1.5, "rr": 2.0}, {"stop_atr": 2.0, "rr": 0.5}, {"stop_atr": 1.0, "rr": None}):
            assert reconcile(b, pos, MNQ, 2, 1.0, ex)["ok"]
            led = run_ledger(b, pos, MNQ, 2, 1.0, ex)
            assert abs(sum(t["pnl"] for t in led.trades) - led.pnl.sum()) < 1e-6 or led.open_trade is not None


def test_ledger_records_every_fill_with_price_slippage_and_commission():
    idx = pd.date_range("2025-01-02 09:30", periods=6, freq="5min")
    bars = pd.DataFrame({"open": [100] * 6, "high": [101] * 6, "low": [99] * 6, "close": [100, 100, 102, 104, 104, 104],
                         "volume": 1}, index=idx)
    led = run_ledger(bars, [0, 1, 1, 0, 0, 0], MNQ, contracts=2, slippage_ticks=1.0)
    assert [f["reason"] for f in led.fills] == ["entry", "signal_exit"]
    buy, sell = led.fills
    assert buy["side"] == 1 and buy["price"] == 100.25 and sell["side"] == -1 and sell["price"] == 103.75   # slippage hurts both ways
    assert buy["qty"] == 2 and abs(buy["commission"] - 1.24) < 1e-9 and abs(buy["slippage"] - 1.0) < 1e-9
    gross = (104 - 100) * 2.0 * 2
    assert abs(led.pnl.sum() - (gross - 2 * (1.24 + 1.0))) < 1e-9 and len(led.trades) == 1
    assert abs(led.trades[0]["pnl"] - led.pnl.sum()) < 1e-9
    assert abs((sell["price"] - buy["price"]) * 2.0 * 2 - (led.pnl.sum() + 2 * 1.24)) < 1e-9        # fill prices + commission = P&L


def test_no_lookahead_truncating_the_future_never_changes_the_past():
    bars, pos = random_world(7, 500)
    ex = {"stop_atr": 1.5, "rr": 2.0}
    full = run_ledger(bars, pos, MNQ, 2, 1.0, ex).pnl
    for cut in (150, 260, 400):
        part = run_ledger(bars.iloc[:cut], pos[:cut], MNQ, 2, 1.0, ex).pnl
        assert np.allclose(part.iloc[:-1].to_numpy(), full.iloc[:cut - 1].to_numpy())         # last bar may differ (open trade)
    vec_full = run_backtest(bars, *apply_exits(bars, pd.Series(pos, index=bars.index), 1.5, 2.0)[:1], MNQ, 2, 1.0,
                            exit_px=apply_exits(bars, pd.Series(pos, index=bars.index), 1.5, 2.0)[1])["pnl"]
    p2, x2, _ = apply_exits(bars.iloc[:300], pd.Series(pos[:300], index=bars.index[:300]), 1.5, 2.0)
    vec_part = run_backtest(bars.iloc[:300], p2, MNQ, 2, 1.0, exit_px=x2)["pnl"]
    assert np.allclose(vec_part.iloc[:-2].to_numpy(), vec_full.iloc[:298].to_numpy())


def test_next_open_execution_removes_profit_that_lived_in_the_gap():
    idx = pd.date_range("2025-01-02 09:30", periods=8, freq="5min")
    cl = [100, 100, 100, 103, 104, 105, 105, 105]
    bars = pd.DataFrame({"open": [100, 100, 100, 103, 103, 103, 103, 103], "high": [c + 1 for c in cl],
                         "low": [c - 1 for c in cl], "close": cl, "volume": 1}, index=idx)
    pos = [0, 0, 1, 1, 1, 0, 0, 0]
    same = run_ledger(bars, pos, MNQ, 1, 0.0)
    nxt = run_ledger_next_open(bars, pos, MNQ, 1, 0.0)
    assert abs(same.pnl.sum() - 8.76) < 1e-9                       # +5 points at the close fills
    assert abs(nxt.pnl.sum() - (-1.24)) < 1e-9                      # the 3-point gap was missed; costs remain
    assert nxt.trades[0]["entry_px"] == 103.0


def test_next_open_stop_and_target_work_from_the_entry_bar():
    idx = pd.date_range("2025-01-02 09:30", periods=30, freq="5min")
    rng = np.random.default_rng(3)
    cl = 100 + np.cumsum(rng.normal(0, 1, 30))
    bars = pd.DataFrame({"open": np.concatenate([[100], cl[:-1]]), "high": cl + 1.5, "low": cl - 1.5, "close": cl, "volume": 1}, index=idx)
    pos = np.zeros(30); pos[16:24] = 1
    led = run_ledger_next_open(bars, pos, MNQ, 1, 1.0, {"stop_atr": 1.0, "rr": 1.0})
    assert len(led.trades) >= 1 and all(t["reason"] in ("stop", "target", "signal") for t in led.trades)
    assert abs(sum(t["pnl"] for t in led.trades) - led.pnl.sum()) < 1e-6


def test_accounting_error_is_an_exception_type():
    assert issubclass(AccountingError, RuntimeError)
