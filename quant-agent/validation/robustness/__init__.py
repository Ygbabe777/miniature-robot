"""Unified Strategy Robustness Score + promotion gate (spec section 15).

`compute_robustness_score` turns every validation artifact produced
upstream (OOS backtest, walk-forward, Monte Carlo, overfitting report,
transaction-cost stress test, regime/cross-market checks) into a single
configurable weighted score. `evaluate_hard_gates` applies the minimum
non-negotiable thresholds from spec section 15 — a strategy cannot be
promoted on score alone if it fails, e.g., the minimum trade count or OOS
profitability requirement.
"""
from __future__ import annotations

import numpy as np

from config.thresholds import PromotionThresholds
from strategies.schemas.results import (
    BacktestMetrics,
    MonteCarloResult,
    OverfittingReport,
    RobustnessScoreResult,
    WalkForwardResult,
)


def compute_robustness_score(
    experiment_id: str,
    oos_metrics: BacktestMetrics,
    walk_forward: WalkForwardResult,
    overfitting: OverfittingReport,
    monte_carlo: MonteCarloResult,
    transaction_cost_resilient: bool,
    regime_diversification_score: float = 0.5,
    cross_market_validation_score: float = 0.5,
    thresholds: PromotionThresholds | None = None,
) -> RobustnessScoreResult:
    thresholds = thresholds or PromotionThresholds()
    w = thresholds.weights

    oos_performance = float(np.clip(oos_metrics.sharpe / 2.0, 0.0, 1.0))
    walk_forward_consistency = float(np.clip(walk_forward.pct_profitable_windows / 100.0, 0.0, 1.0))
    parameter_stability = float(np.clip(overfitting.parameter_sensitivity_score, 0.0, 1.0))
    drawdown_quality = float(np.clip(1.0 - oos_metrics.max_drawdown_pct / max(thresholds.max_drawdown_pct, 1e-6), 0.0, 1.0))
    monte_carlo_robustness = float(np.clip(monte_carlo.survival_probability, 0.0, 1.0))
    statistical_significance = (
        float(np.clip(overfitting.deflated_sharpe_ratio, 0.0, 1.0))
        if overfitting.deflated_sharpe_ratio is not None
        else 0.5
    )
    transaction_cost_resilience = 1.0 if transaction_cost_resilient else 0.0

    components = {
        "oos_performance": oos_performance,
        "walk_forward_consistency": walk_forward_consistency,
        "parameter_stability": parameter_stability,
        "drawdown_quality": drawdown_quality,
        "monte_carlo_robustness": monte_carlo_robustness,
        "statistical_significance": statistical_significance,
        "transaction_cost_resilience": transaction_cost_resilience,
        "regime_diversification": float(np.clip(regime_diversification_score, 0.0, 1.0)),
        "cross_market_validation": float(np.clip(cross_market_validation_score, 0.0, 1.0)),
    }
    weights = w.model_dump()
    score = sum(components[k] * weights[k] for k in components)
    passed_gate = score >= thresholds.min_robustness_score

    reasoning = "; ".join(f"{k}={v:.2f}(w={weights[k]:.2f})" for k, v in components.items())
    return RobustnessScoreResult(
        experiment_id=experiment_id,
        component_scores=components,
        score=float(score),
        passed_gate=passed_gate,
        reasoning=reasoning,
    )


def evaluate_hard_gates(
    backtest_metrics: BacktestMetrics,
    oos_metrics: BacktestMetrics,
    walk_forward: WalkForwardResult,
    overfitting: OverfittingReport,
    monte_carlo: MonteCarloResult,
    transaction_cost_resilient: bool,
    thresholds: PromotionThresholds | None = None,
) -> tuple[bool, list[str]]:
    """Non-negotiable minimums (spec section 15). Returns (passed, reasons_for_failure)."""
    thresholds = thresholds or PromotionThresholds()
    failures: list[str] = []

    if backtest_metrics.sharpe < thresholds.min_sharpe:
        failures.append(f"Sharpe {backtest_metrics.sharpe:.2f} < min {thresholds.min_sharpe}")
    if backtest_metrics.profit_factor < thresholds.min_profit_factor:
        failures.append(f"Profit factor {backtest_metrics.profit_factor:.2f} < min {thresholds.min_profit_factor}")
    if backtest_metrics.trade_count < thresholds.min_trade_count:
        failures.append(f"Trade count {backtest_metrics.trade_count} < min {thresholds.min_trade_count}")
    if backtest_metrics.max_drawdown_pct > thresholds.max_drawdown_pct:
        failures.append(f"Max drawdown {backtest_metrics.max_drawdown_pct:.1f}% > max {thresholds.max_drawdown_pct}%")
    if thresholds.require_oos_profitable and oos_metrics.net_pnl <= 0:
        failures.append("Out-of-sample net PnL is not positive")
    if walk_forward.pct_profitable_windows < thresholds.min_walk_forward_profitable_windows_pct:
        failures.append(
            f"Walk-forward profitable windows {walk_forward.pct_profitable_windows:.0f}% < "
            f"min {thresholds.min_walk_forward_profitable_windows_pct}%"
        )
    if thresholds.require_parameter_stability and not overfitting.perturbation_test_passed:
        failures.append("Parameter stability / perturbation test failed")
    if monte_carlo.survival_probability * 100 < thresholds.min_monte_carlo_survival_pct:
        failures.append(
            f"Monte Carlo survival {monte_carlo.survival_probability * 100:.0f}% < "
            f"min {thresholds.min_monte_carlo_survival_pct}%"
        )
    if thresholds.require_transaction_cost_resilience and not transaction_cost_resilient:
        failures.append("Not resilient to conservative transaction-cost stress test")
    if overfitting.verdict == "OVERFIT / UNROBUST":
        failures.append("Overfitting battery verdict: OVERFIT / UNROBUST")
    if (
        overfitting.probability_of_backtest_overfitting is not None
        and overfitting.probability_of_backtest_overfitting > thresholds.max_probability_of_backtest_overfitting
    ):
        failures.append(
            f"PBO {overfitting.probability_of_backtest_overfitting:.2f} > "
            f"max {thresholds.max_probability_of_backtest_overfitting}"
        )

    return (len(failures) == 0, failures)
