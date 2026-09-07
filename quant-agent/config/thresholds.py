"""
Promotion thresholds and the Strategy Robustness Score.

These numbers gate every state transition in the strategy promotion
pipeline (spec section 38). They are intentionally NOT buried inside the
validation agent — a reviewer should be able to open this one file and see
exactly what "robust enough" means today, and change it without touching
code.

None of these thresholds are tuned to make any particular strategy pass.
They encode conventional, conservative quant-research practice (minimum
trade count for statistical power, walk-forward consistency, Monte Carlo
survival, transaction-cost resilience, etc.) per spec section 15.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field


class RobustnessWeights(BaseModel):
    """Weights for the unified Strategy Robustness Score (spec section 15).

    Must sum to 1.0 (validated). Configurable per-deployment.
    """

    oos_performance: float = 0.20
    walk_forward_consistency: float = 0.15
    parameter_stability: float = 0.15
    drawdown_quality: float = 0.10
    monte_carlo_robustness: float = 0.10
    statistical_significance: float = 0.10
    transaction_cost_resilience: float = 0.10
    regime_diversification: float = 0.05
    cross_market_validation: float = 0.05

    def total(self) -> float:
        return sum(self.model_dump().values())


class PaperScoringWeights(BaseModel):
    """Weights for PAPER_SCORE (spec section 3)."""

    source_quality: float = 0.20
    citation_quality: float = 0.15
    methodological_rigor: float = 0.15
    reproducibility: float = 0.15
    statistical_significance: float = 0.10
    market_relevance: float = 0.10
    recency: float = 0.10
    data_quality: float = 0.05

    def total(self) -> float:
        return sum(self.model_dump().values())


class PromotionThresholds(BaseModel):
    """Minimum bar for a strategy to advance a stage in the pipeline.

    Spec section 15 example thresholds, made explicit and configurable
    rather than implied by code.
    """

    # Backtest -> Validation
    min_sharpe: float = 1.2
    min_profit_factor: float = 1.25
    min_trade_count: int = 300
    max_drawdown_pct: float = 25.0

    # Validation -> Robustness / OOS
    require_oos_profitable: bool = True
    min_walk_forward_profitable_windows_pct: float = 60.0
    require_parameter_stability: bool = True
    min_monte_carlo_survival_pct: float = 90.0
    require_transaction_cost_resilience: bool = True

    # Robustness score gate
    min_robustness_score: float = 0.60

    # Anti-overfitting (spec section 13)
    max_probability_of_backtest_overfitting: float = 0.30
    min_deflated_sharpe_ratio: float = 0.0  # DSR must be > 0 at minimum

    # Paper trading gate (spec section 17)
    paper_trading_min_days: int = 30
    paper_vs_expected_tolerance_pct: float = 40.0

    weights: RobustnessWeights = Field(default_factory=RobustnessWeights)
    paper_scoring_weights: PaperScoringWeights = Field(default_factory=PaperScoringWeights)


@lru_cache
def get_thresholds() -> PromotionThresholds:
    return PromotionThresholds()
