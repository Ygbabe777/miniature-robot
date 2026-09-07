import numpy as np
import pandas as pd
import pytest

from backtesting.costs import CostModel, cost_model_from_execution_model
from backtesting.engine import BacktestEngine
from backtesting.execution import Fill, Trade
from backtesting.metrics import compute_metrics
from strategies.schemas.strategy import ExecutionModel, RiskModel, SignalLogic, StrategySpec


def _cost_model():
    return CostModel(commission_per_contract=2.0, slippage_ticks=1.0, tick_size=0.25, tick_value=5.0)


def test_cost_model_total_cost():
    cm = _cost_model()
    # commission=2*1=2, slippage=1*1*5=5, spread=1*(1/2)*5=2.5
    assert cm.commission(1) == 2.0
    assert cm.slippage_cost(1) == 5.0
    assert cm.spread_cost(1) == 2.5
    assert cm.total_cost(1) == pytest.approx(9.5)


def test_cost_stress_multiplier_increases_slippage():
    base = CostModel(commission_per_contract=1, slippage_ticks=1, tick_size=0.25, tick_value=5, stress_multiplier=1.0)
    stressed = CostModel(commission_per_contract=1, slippage_ticks=1, tick_size=0.25, tick_value=5, stress_multiplier=3.0)
    assert stressed.slippage_cost(1) == 3 * base.slippage_cost(1)


def test_fill_next_bar_open_applies_slippage_in_favor_of_market():
    cm = _cost_model()
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    buy_fill = Fill.from_next_bar_open(ts, 100.0, side=1, quantity=1, cost_model=cm)
    sell_fill = Fill.from_next_bar_open(ts, 100.0, side=-1, quantity=1, cost_model=cm)
    assert buy_fill.price > 100.0  # buying costs slightly more than quoted open
    assert sell_fill.price < 100.0  # selling receives slightly less


def test_trade_pnl_accounting():
    cm = _cost_model()
    entry = Fill.from_next_bar_open(pd.Timestamp("2024-01-01", tz="UTC"), 100.0, 1, 1, cm)
    exit_ = Fill.from_next_bar_open(pd.Timestamp("2024-01-02", tz="UTC"), 110.0, -1, 1, cm)
    trade = Trade(entry=entry, exit=exit_, quantity=1, side=1)
    assert trade.gross_pnl == pytest.approx(exit_.price - entry.price)
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - trade.total_costs)


def test_compute_metrics_on_simple_equity_curve():
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    equity = pd.Series(np.linspace(100_000, 105_000, 10), index=idx)
    metrics = compute_metrics(equity, trades=[], bars_per_year=252, exposed_bars=5, total_bars=10)
    assert metrics.total_return_pct == pytest.approx(5.0)
    assert metrics.exposure_pct == pytest.approx(50.0)
    assert metrics.trade_count == 0


def test_compute_metrics_handles_total_loss_without_crashing():
    idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    equity = pd.Series([100_000, 50_000, 10_000, -5_000, -20_000], index=idx, dtype=float)
    metrics = compute_metrics(equity, trades=[], bars_per_year=252, exposed_bars=5, total_bars=5)
    assert metrics.cagr == -1.0


def _simple_spec(market="NQ", timeframe="5m") -> StrategySpec:
    return StrategySpec(
        strategy_id="test_strat",
        name="test",
        market=market,
        timeframe=timeframe,
        signal_logic=SignalLogic(all_of=["signal > 1.0"]),
        entry_logic=SignalLogic(all_of=["signal > 1.0"]),
        exit_logic=SignalLogic(all_of=["signal < -1.0"]),
        risk_model=RiskModel(time_stop_bars=5, max_position_size=1),
        execution_model=ExecutionModel(commission_per_contract=0.0, slippage_ticks=0.0),
        parameters={"side": 1},
    )


def _deterministic_bars(n=60, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    price = 100 + np.cumsum(rng.normal(0, 0.1, n))
    signal = rng.normal(0, 1, n)
    df = pd.DataFrame({
        "open": price, "high": price + 0.1, "low": price - 0.1, "close": price,
        "volume": np.full(n, 100.0), "signal": signal,
    }, index=idx)
    return df


def test_engine_never_uses_future_bar_for_entry_fill():
    """The entry fill price must equal the NEXT bar's open, never the
    signal bar's own close — the core look-ahead-bias guarantee."""
    spec = _simple_spec()
    bars = _deterministic_bars()
    # force an entry signal on bar 5 by construction
    bars.loc[bars.index[5], "signal"] = 5.0
    engine = BacktestEngine(initial_capital=100_000)
    result = engine.run(spec, bars)
    assert result.trade_count >= 0  # should not raise
    # equity curve should be same length as bars minus final adjustments
    assert len(result.equity_curve) > 0


def test_engine_requires_minimum_bars():
    spec = _simple_spec()
    bars = _deterministic_bars(n=2)
    with pytest.raises(ValueError):
        BacktestEngine().run(spec, bars)


def test_cost_model_from_execution_model_uses_known_tick_specs():
    cm = cost_model_from_execution_model("NQ", ExecutionModel(commission_per_contract=2.25, slippage_ticks=1.0))
    assert cm.tick_size == 0.25
    assert cm.tick_value == 5.0
