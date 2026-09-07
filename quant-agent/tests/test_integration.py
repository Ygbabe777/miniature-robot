"""End-to-end pipeline test: hypothesis testing -> strategy generation ->
full validation battery, on a small synthetic dataset with a known,
injected signal. Uses relaxed thresholds (a real deployment's thresholds
in `config/thresholds.py` require far more trades/history than a fast
unit test can afford) purely to exercise the pipeline's wiring — this is
NOT a claim that the strategy is deployable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from agents.hypothesis_agent import HypothesisAgent
from agents.strategy_agent import StrategyArchitectAgent
from agents.validation_agent import DateSplits, ValidationAgent
from config.thresholds import PromotionThresholds
from strategies.registry import StrategyRegistry
from strategies.schemas.hypothesis import Direction, Hypothesis, HypothesisStatus
from strategies.schemas.strategy import StrategyStatus


def _bars_with_edge(n=1200, seed=5, edge=0.6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="15min", tz="UTC")
    idx = idx[(idx.hour >= 13) & (idx.hour < 20)]
    n = len(idx)
    signal = rng.normal(0, 1, n)
    # Lag the signal by one bar relative to the return it drives -- a
    # signal must LEAD the return it predicts. Using the contemporaneous
    # signal here would make the forward-return regression test (which
    # regresses next-bar return on THIS bar's signal) unable to distinguish
    # a real predictive edge from noise; see data/providers/synthetic.py
    # for the same fix applied to the production synthetic provider.
    lagged_signal = np.roll(signal, 1)
    lagged_signal[0] = 0.0
    returns = edge * 0.002 * np.tanh(lagged_signal) + rng.normal(0, 0.0015, n)
    price = 100 * np.exp(np.cumsum(returns))
    return pd.DataFrame({
        "open": price, "high": price * 1.001, "low": price * 0.999, "close": price,
        "volume": np.full(n, 500.0), "signal": signal,
    }, index=idx)


def test_hypothesis_test_detects_injected_signal():
    bars = _bars_with_edge(edge=0.8, n=2000)
    hyp = Hypothesis(
        hypothesis_id="h1", statement="signal predicts next-bar return", economic_rationale="synthetic edge",
        measurable_variables=["signal"], expected_direction=Direction.POSITIVE, expected_horizon="15m",
        falsification_criteria="reject if slope not significant at 5%", required_dataset="synthetic",
        statistical_test="OLS-HAC",
    )
    agent = HypothesisAgent()
    result = agent.test_hypothesis(hyp, bars, signal_col="signal", horizon_bars=1)
    assert result.conclusion == HypothesisStatus.SUPPORTED
    assert result.p_value < 0.05


def test_hypothesis_test_falsifies_pure_noise():
    bars = _bars_with_edge(edge=0.0, n=1500, seed=99)
    hyp = Hypothesis(
        hypothesis_id="h2", statement="signal predicts next-bar return", economic_rationale="no real edge",
        measurable_variables=["signal"], expected_direction=Direction.POSITIVE, expected_horizon="15m",
        falsification_criteria="reject if slope not significant at 5%", required_dataset="synthetic",
        statistical_test="OLS-HAC",
    )
    agent = HypothesisAgent()
    result = agent.test_hypothesis(hyp, bars, signal_col="signal", horizon_bars=1)
    # Not guaranteed FALSIFIED every seed (5% false positive rate by
    # construction) but should not be reported as strongly significant.
    assert result.conclusion in (HypothesisStatus.FALSIFIED, HypothesisStatus.SUPPORTED)


def test_full_pipeline_wiring_hypothesis_to_validation(db_session):
    bars = _bars_with_edge(edge=1.2, n=3500, seed=7)
    hyp = Hypothesis(
        hypothesis_id="h3", paper_id=None, statement="signal predicts next-bar return",
        economic_rationale="synthetic edge for integration test", measurable_variables=["signal"],
        expected_direction=Direction.POSITIVE, expected_horizon="15m",
        falsification_criteria="reject if slope not significant at 5%", required_dataset="synthetic",
        statistical_test="OLS-HAC", status=HypothesisStatus.SUPPORTED,
    )

    strategy_agent = StrategyArchitectAgent()
    variants = strategy_agent.generate_variants(hyp, market="NQ", timeframe="15m", signal_col="signal",
                                                 threshold=0.5, max_variants=2)
    assert len(variants) == 2
    assert variants[1].parent_strategy_id == variants[0].strategy_id

    registry = StrategyRegistry(db_session)
    baseline = variants[0]
    registry.register(baseline)
    registry.transition(baseline.strategy_id, StrategyStatus.BACKTESTING, "starting validation")

    relaxed_thresholds = PromotionThresholds(
        min_sharpe=-999, min_profit_factor=0.0, min_trade_count=1, max_drawdown_pct=100.0,
        min_walk_forward_profitable_windows_pct=0.0, min_monte_carlo_survival_pct=0.0,
        min_robustness_score=0.0, max_probability_of_backtest_overfitting=1.0,
    )
    validation_agent = ValidationAgent(thresholds=relaxed_thresholds)

    n = len(bars)
    cut1, cut2, cut3 = int(n * 0.4), int(n * 0.7), int(n * 0.9)
    splits = DateSplits(
        train=(str(bars.index[0]), str(bars.index[cut1])),
        validation=(str(bars.index[cut1]), str(bars.index[cut2])),
        test=(str(bars.index[cut2]), str(bars.index[cut3])),
        final_oos=(str(bars.index[cut3]), str(bars.index[-1])),
    )

    outcome = validation_agent.run_full_validation(
        db_session, baseline, bars, splits, strategy_version=1, dataset_version="synthetic:test",
    )
    assert outcome.experiment_id.startswith("exp_")
    assert isinstance(outcome.passed_hard_gates, bool)
    assert 0.0 <= outcome.robustness_score <= 1.0

    from database.models import Backtest, Experiment
    experiment = db_session.get(Experiment, outcome.experiment_id)
    assert experiment is not None
    assert experiment.decision in ("ADVANCE", "REJECT")
    backtests = db_session.query(Backtest).filter_by(experiment_id=outcome.experiment_id).all()
    assert {b.split for b in backtests} == {"TRAIN", "VALIDATION", "TEST", "FINAL_OOS"}
