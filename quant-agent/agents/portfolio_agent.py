"""Portfolio Construction Agent (spec section 21) — thin wrapper over
`portfolio.optimizer.select_diversified_portfolio`, responsible for
logging *why* a candidate was included or skipped (the correlation that
excluded it), not just the final list.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from portfolio.correlation import average_pairwise_correlation, return_correlation_matrix
from portfolio.optimizer import PortfolioCandidate, select_diversified_portfolio

from .base import Agent


class PortfolioAgent(Agent):
    name = "portfolio_agent"

    def build_portfolio(
        self, session: Session, candidates: list[PortfolioCandidate], max_correlation: float = 0.6,
        max_strategies: int | None = None,
    ) -> list[str]:
        selected = select_diversified_portfolio(candidates, max_correlation, max_strategies)
        returns_map = {c.strategy_id: c.returns for c in candidates}
        corr = return_correlation_matrix(returns_map)
        avg_corr = average_pairwise_correlation(corr.loc[selected, selected]) if len(selected) > 1 else 0.0

        self.log_event(
            session, "portfolio_constructed",
            {"selected": selected, "candidates_considered": [c.strategy_id for c in candidates],
             "avg_pairwise_correlation": avg_corr},
            decision_log=(
                f"Selected {len(selected)}/{len(candidates)} candidates for the portfolio "
                f"(avg pairwise correlation={avg_corr:.2f}, max allowed={max_correlation})."
            ),
        )
        return selected
