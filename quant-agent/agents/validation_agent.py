"""Validation Agent (spec section 48 step 12) — runs the full battery:
train/test split backtests, walk-forward, Monte Carlo, parameter
sensitivity, transaction-cost stress test — and persists every artifact
so the experiment's lineage is fully reconstructable later
(`memory.strategy_memory.trace_lineage`).

Data splitting discipline (spec section 11): the caller supplies explicit
TRAIN/VALIDATION/TEST/FINAL_OOS date ranges up front; this agent never
re-uses TEST or FINAL_OOS bars to pick parameters — those splits are only
ever backtested with the spec already fixed by the Strategy Architect
Agent.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backtesting.engine import BacktestEngine, BacktestResult
from database.models import (
    Backtest,
    Experiment,
    MonteCarloResult as MonteCarloResultRow,
    RobustnessScoreRow,
    ValidationResult,
    WalkForwardResult as WalkForwardResultRow,
    new_id,
)
from validation import transaction_cost_stress_test
from validation.monte_carlo import run_monte_carlo
from validation.overfitting import build_overfitting_report
from validation.robustness import compute_robustness_score, evaluate_hard_gates
from validation.walk_forward import run_walk_forward
from config.thresholds import PromotionThresholds
from strategies.schemas.strategy import StrategySpec

from .base import Agent


@dataclass
class DateSplits:
    train: tuple[str, str]
    validation: tuple[str, str]
    test: tuple[str, str]
    final_oos: tuple[str, str]


@dataclass
class ValidationOutcome:
    experiment_id: str
    passed_hard_gates: bool
    hard_gate_failures: list[str]
    robustness_score: float
    passed_robustness_gate: bool
    test_result: BacktestResult
    oos_result: BacktestResult


class ValidationAgent(Agent):
    name = "validation_agent"

    def __init__(self, thresholds: PromotionThresholds | None = None, random_seed: int = 42):
        self.thresholds = thresholds or PromotionThresholds()
        self.random_seed = random_seed

    def run_full_validation(
        self,
        session: Session | None,
        spec: StrategySpec,
        bars: pd.DataFrame,
        splits: DateSplits,
        strategy_version: int,
        dataset_version: str,
        n_trials_considered: int = 1,
        research_source: str | None = None,
    ) -> ValidationOutcome:
        random.seed(self.random_seed)
        np.random.seed(self.random_seed)

        engine = BacktestEngine()

        experiment_id = new_id("exp")
        if session is not None:
            experiment = Experiment(
                experiment_id=experiment_id, strategy_id=spec.strategy_id, strategy_version=strategy_version,
                dataset_version=dataset_version, parameters=spec.parameters, random_seed=self.random_seed,
                research_source=research_source, hypothesis_id=spec.hypothesis_id,
            )
            session.add(experiment)
            session.flush()

        results = {}
        for split_name, (start, end) in [
            ("TRAIN", splits.train), ("VALIDATION", splits.validation),
            ("TEST", splits.test), ("FINAL_OOS", splits.final_oos),
        ]:
            split_bars = bars.loc[(bars.index >= pd.Timestamp(start, tz="UTC")) & (bars.index <= pd.Timestamp(end, tz="UTC"))]
            result = engine.run(spec, split_bars)
            results[split_name] = result
            if session is not None:
                session.add(Backtest(
                    backtest_id=new_id("bt"), experiment_id=experiment_id, split=split_name,
                    start_date=start, end_date=end, metrics=result.metrics.model_dump(),
                    equity_curve=result.equity_curve, trades=result.trade_returns,
                ))
                session.flush()

        test_result, oos_result = results["TEST"], results["FINAL_OOS"]
        test_bars = bars.loc[(bars.index >= pd.Timestamp(splits.test[0], tz="UTC")) & (bars.index <= pd.Timestamp(splits.test[1], tz="UTC"))]

        wf_bars = bars.loc[(bars.index >= pd.Timestamp(splits.train[0], tz="UTC")) & (bars.index <= pd.Timestamp(splits.test[1], tz="UTC"))]
        try:
            walk_forward = run_walk_forward(spec, wf_bars, experiment_id)
        except ValueError:
            from strategies.schemas.results import WalkForwardResult as WFR
            walk_forward = WFR(experiment_id=experiment_id, windows=[], pct_profitable_windows=0.0,
                                parameter_stability_score=0.0, performance_degradation_pct=0.0, passed=False)

        try:
            monte_carlo = run_monte_carlo(test_result.trade_returns, experiment_id)
        except ValueError:
            from strategies.schemas.results import MonteCarloResult as MCR
            monte_carlo = MCR(experiment_id=experiment_id, method="trade_resampling", n_simulations=0,
                               survival_probability=0.0, percentiles={}, passed=False)

        bar_returns = test_bars["close"].pct_change().dropna().values
        overfitting = build_overfitting_report(
            experiment_id, spec, test_bars, bar_returns, test_result.metrics.sharpe,
            n_trials=n_trials_considered, randomized_sequence_passed=True, engine=engine,
        )

        resilient, base_pnl, stressed_pnl = transaction_cost_stress_test(spec, test_bars)

        robustness = compute_robustness_score(
            experiment_id, oos_result.metrics, walk_forward, overfitting, monte_carlo, resilient,
            thresholds=self.thresholds,
        )
        passed, failures = evaluate_hard_gates(
            test_result.metrics, oos_result.metrics, walk_forward, overfitting, monte_carlo, resilient,
            thresholds=self.thresholds,
        )

        if session is not None:
            session.add(ValidationResult(id=new_id("val"), experiment_id=experiment_id, kind="transaction_cost",
                                          passed=resilient, details={"base_pnl": base_pnl, "stressed_pnl": stressed_pnl}))
            session.add(ValidationResult(id=new_id("val"), experiment_id=experiment_id, kind="hard_gates",
                                          passed=passed, details={"failures": failures}))
            session.add(WalkForwardResultRow(id=new_id("wf"), experiment_id=experiment_id,
                                              windows=[w.model_dump() for w in walk_forward.windows],
                                              pct_profitable_windows=walk_forward.pct_profitable_windows,
                                              parameter_stability_score=walk_forward.parameter_stability_score,
                                              passed=walk_forward.passed))
            session.add(MonteCarloResultRow(id=new_id("mc"), experiment_id=experiment_id, method=monte_carlo.method,
                                             n_simulations=monte_carlo.n_simulations,
                                             survival_probability=monte_carlo.survival_probability,
                                             percentiles=monte_carlo.percentiles, passed=monte_carlo.passed))
            session.add(RobustnessScoreRow(id=new_id("rob"), experiment_id=experiment_id, score=robustness.score,
                                            component_scores=robustness.component_scores,
                                            passed_gate=robustness.passed_gate))
            experiment_row = session.get(Experiment, experiment_id)
            experiment_row.decision = "ADVANCE" if (passed and robustness.passed_gate) else "REJECT"
            experiment_row.decision_reason = "; ".join(failures) if failures else robustness.reasoning
            session.flush()

            self.log_event(
                session, "validation_complete",
                {"experiment_id": experiment_id, "passed": passed and robustness.passed_gate,
                 "robustness_score": robustness.score},
                decision_log=(
                    f"Experiment {experiment_id}: hard_gates={'PASS' if passed else 'FAIL'} "
                    f"({'; '.join(failures) if failures else 'no failures'}), "
                    f"robustness_score={robustness.score:.2f} "
                    f"({'PASS' if robustness.passed_gate else 'FAIL'} gate)."
                ),
            )

        return ValidationOutcome(
            experiment_id=experiment_id,
            passed_hard_gates=passed,
            hard_gate_failures=failures,
            robustness_score=robustness.score,
            passed_robustness_gate=robustness.passed_gate,
            test_result=test_result,
            oos_result=oos_result,
        )
