"""Performance Monitoring Agent (spec section 17/22).

Watches live/paper `StrategyPerformance` rows and flags degradation for
the Post-Mortem Agent. Also owns the retirement flag checks from spec
section 39 (drawdown breach, Sharpe well below expected).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import StrategyPerformance

from .base import Agent


class MonitoringAgent(Agent):
    name = "monitoring_agent"

    def check_degradation(self, session: Session, strategy_id: str, max_drawdown_pct_limit: float,
                           min_sharpe_ratio_of_expected: float = 0.5) -> tuple[bool, list[str]]:
        rows = session.execute(
            select(StrategyPerformance)
            .where(StrategyPerformance.strategy_id == strategy_id)
            .order_by(StrategyPerformance.created_at.desc())
            .limit(1)
        ).scalars().all()
        if not rows:
            return False, []

        latest = rows[0]
        reasons = []
        actual_dd = latest.actual.get("max_drawdown_pct", 0.0)
        if actual_dd > max_drawdown_pct_limit:
            reasons.append(f"Live/paper drawdown {actual_dd:.1f}% exceeds limit {max_drawdown_pct_limit}%.")

        expected_sharpe = latest.expected.get("sharpe", 0.0)
        actual_sharpe = latest.actual.get("sharpe", 0.0)
        if expected_sharpe > 0 and actual_sharpe < expected_sharpe * min_sharpe_ratio_of_expected:
            reasons.append(
                f"Actual Sharpe {actual_sharpe:.2f} is well below expected {expected_sharpe:.2f} "
                f"(< {min_sharpe_ratio_of_expected * 100:.0f}% of expected)."
            )

        flagged = len(reasons) > 0
        if flagged:
            self.log_event(
                session, "degradation_flagged", {"strategy_id": strategy_id, "reasons": reasons},
                decision_log=f"Strategy {strategy_id} flagged for degradation: {'; '.join(reasons)}",
            )
        return flagged, reasons
