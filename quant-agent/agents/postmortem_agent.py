"""Post-Mortem Agent (spec section 22).

Given a flagged degradation, produces a diagnosis from a fixed set of
candidate explanations (regime change, execution degradation, alpha
decay, market structure change, volatility change, liquidity change, data
problem, overfitting, random variance). This is a heuristic triage, not a
certain diagnosis — it ranks candidate explanations by which observable
signals support them and always names its evidence, and defers to
"random variance" only when no other signal is present (never as a
default excuse to avoid investigating further).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from database.models import StrategyPerformance
from memory.failure_memory import record_strategy_failure

from .base import Agent

CANDIDATE_EXPLANATIONS = [
    "regime_change", "execution_degradation", "alpha_decay", "market_structure_change",
    "volatility_change", "liquidity_change", "data_problem", "overfitting", "random_variance",
]


@dataclass
class Diagnosis:
    explanation: str
    confidence: float
    evidence: str


class PostMortemAgent(Agent):
    name = "postmortem_agent"

    def diagnose(self, performance: StrategyPerformance) -> Diagnosis:
        expected, actual = performance.expected, performance.actual
        deviation = performance.deviation_pct or {}

        trade_count_dev = deviation.get("trade_count", 0.0)
        dd_dev = deviation.get("max_drawdown_pct", 0.0)
        sharpe_dev = deviation.get("sharpe", 0.0)
        winrate_dev = deviation.get("win_rate", 0.0)

        if trade_count_dev < -50:
            return Diagnosis(
                "liquidity_change", 0.6,
                f"Trade count deviated {trade_count_dev:.0f}% below expected — the strategy is firing "
                "far less often, consistent with a liquidity or signal-availability change.",
            )
        if dd_dev > 80 and sharpe_dev < -50:
            return Diagnosis(
                "regime_change", 0.6,
                f"Drawdown +{dd_dev:.0f}% and Sharpe {sharpe_dev:.0f}% below expected together suggest the "
                "market regime the strategy was validated on no longer holds.",
            )
        if sharpe_dev < -30 and winrate_dev > -10 and trade_count_dev > -20:
            return Diagnosis(
                "execution_degradation", 0.5,
                f"Sharpe down {sharpe_dev:.0f}% while win rate and trade count are roughly stable — "
                "consistent with fills being worse than assumed (slippage/latency), not a broken signal.",
            )
        if sharpe_dev < -20 and abs(trade_count_dev) < 20 and abs(winrate_dev) < 20:
            return Diagnosis(
                "alpha_decay", 0.45,
                f"Sharpe down {sharpe_dev:.0f}% with stable trade frequency and win rate — the signal may "
                "simply be getting arbitraged away over time.",
            )
        if abs(sharpe_dev) < 15 and abs(dd_dev) < 15:
            return Diagnosis(
                "random_variance", 0.4,
                "No metric deviated substantially from expectation — likely within normal sampling variance, "
                "not a structural problem. Continue monitoring rather than acting.",
            )
        return Diagnosis(
            "overfitting", 0.35,
            "No single clean explanation fits the deviation pattern; the original backtest edge may not "
            "have been real. Recommend re-running the anti-overfitting battery.",
        )

    def record_and_diagnose(self, session: Session, performance: StrategyPerformance, strategy_id: str,
                             hypothesis_id: str | None, research_source: str | None) -> Diagnosis:
        diagnosis = self.diagnose(performance)
        record_strategy_failure(
            session, strategy_id=strategy_id, hypothesis_id=hypothesis_id,
            reason=diagnosis.evidence, failure_mode=diagnosis.explanation,
            metrics=performance.actual, parameter_sensitivity={},
            market_conditions={}, research_source=research_source,
        )
        self.log_event(
            session, "postmortem_diagnosis",
            {"strategy_id": strategy_id, "explanation": diagnosis.explanation, "confidence": diagnosis.confidence},
            decision_log=f"Post-mortem for {strategy_id}: {diagnosis.explanation} (confidence={diagnosis.confidence:.2f}) — {diagnosis.evidence}",
        )
        return diagnosis
