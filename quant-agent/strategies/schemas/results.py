"""Backtest metrics, experiment lineage, validation, robustness, deployment schemas."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class BacktestMetrics(BaseModel):
    """Spec section 10 — everything every backtest must record."""

    cagr: float
    total_return_pct: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    profit_factor: float
    expectancy: float
    win_rate: float
    avg_win: float
    avg_loss: float
    trade_count: int
    avg_holding_period_bars: float
    exposure_pct: float
    turnover: float
    total_transaction_costs: float
    total_slippage: float
    gross_pnl: float
    net_pnl: float
    monthly_returns: dict[str, float] = Field(default_factory=dict)
    yearly_returns: dict[str, float] = Field(default_factory=dict)


class DataSplit(str, Enum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"
    FINAL_OOS = "FINAL_OOS"


class BacktestRecord(BaseModel):
    backtest_id: str
    experiment_id: str
    split: DataSplit
    start_date: str
    end_date: str
    metrics: BacktestMetrics
    equity_curve: list[float] = Field(default_factory=list)
    trade_returns: list[float] = Field(default_factory=list)


class ExperimentRecord(BaseModel):
    """Spec section 27 — full experimental discipline / reproducibility."""

    experiment_id: str
    strategy_id: str
    strategy_version: int
    dataset_version: str
    parameters: dict
    random_seed: int
    code_commit: str | None = None
    research_source: str | None = None  # paper_id
    hypothesis_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    decision: str | None = None
    decision_reason: str | None = None


class WalkForwardWindow(BaseModel):
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    test_sharpe: float
    test_return_pct: float
    profitable: bool
    parameters_used: dict


class WalkForwardResult(BaseModel):
    experiment_id: str
    windows: list[WalkForwardWindow]
    pct_profitable_windows: float
    parameter_stability_score: float = Field(ge=0, le=1)
    performance_degradation_pct: float
    passed: bool


class MonteCarloResult(BaseModel):
    experiment_id: str
    method: str  # trade_resampling | return_bootstrap | randomized_entries | randomized_sequence
    n_simulations: int
    survival_probability: float = Field(ge=0, le=1, description="P(simulated Sharpe > 0)")
    percentiles: dict[str, float]  # e.g. {"p5": ..., "p50": ..., "p95": ...}
    passed: bool


class OverfittingReport(BaseModel):
    """Spec section 13 — anti-overfitting battery."""

    experiment_id: str
    deflated_sharpe_ratio: float | None = None
    probability_of_backtest_overfitting: float | None = None
    parameter_sensitivity_score: float = Field(ge=0, le=1)
    perturbation_test_passed: bool
    randomized_entry_test_passed: bool
    randomized_sequence_test_passed: bool
    n_trials_considered: int = Field(description="For multiple-testing awareness / PBO")
    verdict: str  # "ROBUST" | "OVERFIT / UNROBUST" | "INCONCLUSIVE"
    notes: str


class RobustnessScoreResult(BaseModel):
    experiment_id: str
    component_scores: dict[str, float]
    score: float = Field(ge=0, le=1)
    passed_gate: bool
    reasoning: str


class DeploymentStage(str, Enum):
    PAPER = "PAPER"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"
    LIVE = "LIVE"


class DeploymentRecord(BaseModel):
    """Spec section 18 — controlled, reproducible deployment."""

    deployment_id: str
    strategy_id: str
    strategy_version: int
    stage: DeploymentStage
    deployment_score: float | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    git_commit: str | None = None
    data_version: str | None = None
    model_version: str | None = None
    execution_config: dict = Field(default_factory=dict)
    status: str = "PENDING_APPROVAL"
    deployment_timestamp: datetime = Field(default_factory=datetime.utcnow)


class DecisionLogEntry(BaseModel):
    """Spec section 36 — every major AI decision must be explainable, never
    reducible to 'looks profitable.'"""

    strategy_id: str
    research_source: str | None
    hypothesis_id: str | None
    evidence_summary: str
    backtest_sharpe: float | None
    oos_sharpe: float | None
    walk_forward_pct_profitable: float | None
    monte_carlo_survival_pct: float | None
    transaction_cost_resilience: bool | None
    parameter_stability: bool | None
    regime_dependence: str | None
    decision: str
    reason: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def render(self) -> str:
        lines = [
            f"STRATEGY: {self.strategy_id}",
            f"Research source: {self.research_source or 'n/a'}",
            f"Hypothesis: {self.hypothesis_id or 'n/a'}",
            f"Evidence: {self.evidence_summary}",
        ]
        if self.backtest_sharpe is not None:
            lines.append(f"Backtest Sharpe: {self.backtest_sharpe:.2f}")
        if self.oos_sharpe is not None:
            lines.append(f"OOS Sharpe: {self.oos_sharpe:.2f}")
        if self.walk_forward_pct_profitable is not None:
            lines.append(f"Walk-forward: {self.walk_forward_pct_profitable:.0f}% profitable windows")
        if self.monte_carlo_survival_pct is not None:
            lines.append(f"Monte Carlo survival: {self.monte_carlo_survival_pct:.0f}%")
        if self.transaction_cost_resilience is not None:
            lines.append(f"Transaction-cost resilience: {'PASS' if self.transaction_cost_resilience else 'FAIL'}")
        if self.parameter_stability is not None:
            lines.append(f"Parameter stability: {'PASS' if self.parameter_stability else 'FAIL'}")
        if self.regime_dependence is not None:
            lines.append(f"Regime dependence: {self.regime_dependence}")
        lines.append(f"Decision: {self.decision}")
        lines.append(f"Reason: {self.reason}")
        return "\n".join(lines)
