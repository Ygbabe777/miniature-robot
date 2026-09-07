"""Portfolio-level strategy selection (spec section 21).

A simple, transparent greedy diversification selector: rank robust
candidates by robustness score, then add them to the portfolio one at a
time, skipping any candidate whose average pairwise return-correlation
with already-selected strategies exceeds `max_correlation`. This is
deliberately not a mean-variance optimizer — with only a handful of
strategy candidates and short live histories, a full Markowitz
optimization would overfit its own inputs. The goal here is the qualitative
guarantee from the spec: prefer strategies that are individually robust
AND collectively diversified.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from portfolio.correlation import return_correlation_matrix


@dataclass
class PortfolioCandidate:
    strategy_id: str
    robustness_score: float
    returns: pd.Series


def select_diversified_portfolio(
    candidates: list[PortfolioCandidate], max_correlation: float = 0.6, max_strategies: int | None = None
) -> list[str]:
    ranked = sorted(candidates, key=lambda c: c.robustness_score, reverse=True)
    selected: list[PortfolioCandidate] = []

    for candidate in ranked:
        if max_strategies is not None and len(selected) >= max_strategies:
            break
        if not selected:
            selected.append(candidate)
            continue
        returns_map = {c.strategy_id: c.returns for c in selected + [candidate]}
        corr = return_correlation_matrix(returns_map)
        if candidate.strategy_id not in corr:
            selected.append(candidate)
            continue
        correlations_to_selected = corr.loc[candidate.strategy_id, [c.strategy_id for c in selected]]
        if correlations_to_selected.abs().max() <= max_correlation:
            selected.append(candidate)

    return [c.strategy_id for c in selected]
