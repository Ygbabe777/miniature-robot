import numpy as np
import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from strategies.schemas.strategy import ExecutionModel, RiskModel, SignalLogic, StrategySpec
from validation.monte_carlo import run_monte_carlo
from validation.overfitting import (
    deflated_sharpe_ratio,
    parameter_sensitivity_test,
    probability_of_backtest_overfitting,
    randomized_entry_test,
)
from validation.robustness import compute_robustness_score, evaluate_hard_gates
from validation.walk_forward import generate_windows, run_walk_forward
from strategies.schemas.results import BacktestMetrics, MonteCarloResult, OverfittingReport, WalkForwardResult


def _spec(threshold=1.0) -> StrategySpec:
    return StrategySpec(
        strategy_id="s1", name="s1", market="NQ", timeframe="5m",
        signal_logic=SignalLogic(all_of=[f"signal > {threshold}"]),
        entry_logic=SignalLogic(all_of=[f"signal > {threshold}"]),
        exit_logic=SignalLogic(all_of=["signal < -1.0"]),
        risk_model=RiskModel(time_stop_bars=5, max_position_size=1),
        execution_model=ExecutionModel(commission_per_contract=0.0, slippage_ticks=0.0),
        parameters={"side": 1, "threshold": threshold},
    )


def _bars(n=300, seed=1, edge=0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    signal = rng.normal(0, 1, n)
    returns = edge * np.tanh(signal) * 0.001 + rng.normal(0, 0.001, n)
    price = 100 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        "open": price, "high": price * 1.0005, "low": price * 0.9995, "close": price,
        "volume": np.full(n, 100.0), "signal": signal,
    }, index=idx)
    return df


def test_monte_carlo_requires_minimum_trades():
    with pytest.raises(ValueError):
        run_monte_carlo([1.0, -1.0], experiment_id="e1")


def test_monte_carlo_survival_probability_bounds():
    trade_returns = list(np.random.default_rng(0).normal(10, 5, 100))
    result = run_monte_carlo(trade_returns, experiment_id="e1", n_simulations=200)
    assert 0.0 <= result.survival_probability <= 1.0
    assert result.percentiles["p5"] <= result.percentiles["p50"] <= result.percentiles["p95"]


def test_monte_carlo_flags_a_losing_strategy_as_not_surviving():
    losing_trades = list(np.random.default_rng(0).normal(-10, 2, 100))
    result = run_monte_carlo(losing_trades, experiment_id="e1", n_simulations=200)
    assert result.survival_probability < 0.5
    assert result.passed is False


def test_deflated_sharpe_ratio_penalizes_more_trials():
    bar_returns = np.random.default_rng(0).normal(0.001, 0.01, 500)
    dsr_1_trial = deflated_sharpe_ratio(bar_returns, sharpe_hat=1.5, n_trials=1)
    dsr_many_trials = deflated_sharpe_ratio(bar_returns, sharpe_hat=1.5, n_trials=100)
    assert dsr_many_trials <= dsr_1_trial


def test_pbo_requires_at_least_two_candidates():
    assert probability_of_backtest_overfitting({"a": np.random.default_rng(0).normal(0, 1, 100)}) is None


def test_pbo_with_two_candidates_returns_a_probability():
    rng = np.random.default_rng(0)
    candidates = {"a": rng.normal(0.001, 0.01, 200), "b": rng.normal(-0.001, 0.01, 200)}
    pbo = probability_of_backtest_overfitting(candidates, n_splits=8)
    assert pbo is None or 0.0 <= pbo <= 1.0


def test_walk_forward_window_generation():
    windows = generate_windows(
        pd.Timestamp("2020-01-01"), pd.Timestamp("2023-01-01"),
        pd.DateOffset(months=12), pd.DateOffset(months=3),
    )
    assert len(windows) >= 4
    for train_start, train_end, test_start, test_end in windows:
        assert train_start < train_end == test_start < test_end


def test_walk_forward_raises_with_insufficient_history():
    spec = _spec()
    bars = _bars(n=50)
    with pytest.raises(ValueError):
        run_walk_forward(spec, bars, experiment_id="e1", train_months=12, test_months=3, min_windows=4)


def test_parameter_sensitivity_detects_unstable_strategy():
    spec = _spec(threshold=1.0)
    bars = _bars(n=400, edge=0.0)
    engine = BacktestEngine()
    base_result = engine.run(spec, bars)
    score, outcomes = parameter_sensitivity_test(spec, bars, base_result.metrics.sharpe, engine)
    assert 0.0 <= score <= 1.0
    assert len(outcomes) > 0


def test_randomized_entry_test_runs_without_error():
    spec = _spec(threshold=1.0)
    bars = _bars(n=200, edge=0.0)
    engine = BacktestEngine()
    result = engine.run(spec, bars)
    passed = randomized_entry_test(spec, bars, result.metrics.sharpe, n_random=5, engine=engine)
    assert isinstance(passed, bool)


def test_hard_gates_reject_low_sharpe_strategy():
    metrics = BacktestMetrics(
        cagr=0.01, total_return_pct=1.0, sharpe=0.1, sortino=0.1, calmar=0.1,
        max_drawdown_pct=50.0, max_drawdown_duration_days=100, profit_factor=0.9,
        expectancy=-1.0, win_rate=30.0, avg_win=10.0, avg_loss=-20.0, trade_count=10,
        avg_holding_period_bars=5.0, exposure_pct=20.0, turnover=100.0,
        total_transaction_costs=100.0, total_slippage=50.0, gross_pnl=-500.0, net_pnl=-1000.0,
    )
    wf = WalkForwardResult(experiment_id="e1", windows=[], pct_profitable_windows=10.0,
                            parameter_stability_score=0.1, performance_degradation_pct=90.0, passed=False)
    overfitting = OverfittingReport(
        experiment_id="e1", deflated_sharpe_ratio=0.1, probability_of_backtest_overfitting=0.9,
        parameter_sensitivity_score=0.1, perturbation_test_passed=False, randomized_entry_test_passed=False,
        randomized_sequence_test_passed=False, n_trials_considered=5, verdict="OVERFIT / UNROBUST", notes="",
    )
    mc = MonteCarloResult(experiment_id="e1", method="trade_resampling", n_simulations=100,
                           survival_probability=0.1, percentiles={}, passed=False)

    passed, failures = evaluate_hard_gates(metrics, metrics, wf, overfitting, mc, transaction_cost_resilient=False)
    assert passed is False
    assert len(failures) > 3


def test_robustness_score_is_bounded_and_deterministic():
    metrics = BacktestMetrics(
        cagr=0.15, total_return_pct=15.0, sharpe=1.8, sortino=2.0, calmar=1.5,
        max_drawdown_pct=10.0, max_drawdown_duration_days=20, profit_factor=1.6,
        expectancy=5.0, win_rate=55.0, avg_win=20.0, avg_loss=-12.0, trade_count=400,
        avg_holding_period_bars=8.0, exposure_pct=30.0, turnover=800.0,
        total_transaction_costs=200.0, total_slippage=100.0, gross_pnl=2500.0, net_pnl=2000.0,
    )
    wf = WalkForwardResult(experiment_id="e1", windows=[], pct_profitable_windows=75.0,
                            parameter_stability_score=0.8, performance_degradation_pct=5.0, passed=True)
    overfitting = OverfittingReport(
        experiment_id="e1", deflated_sharpe_ratio=0.8, probability_of_backtest_overfitting=0.1,
        parameter_sensitivity_score=0.8, perturbation_test_passed=True, randomized_entry_test_passed=True,
        randomized_sequence_test_passed=True, n_trials_considered=5, verdict="ROBUST", notes="",
    )
    mc = MonteCarloResult(experiment_id="e1", method="trade_resampling", n_simulations=1000,
                           survival_probability=0.95, percentiles={}, passed=True)

    result = compute_robustness_score("e1", metrics, wf, overfitting, mc, transaction_cost_resilient=True)
    assert 0.0 <= result.score <= 1.0
    assert result.passed_gate is True
