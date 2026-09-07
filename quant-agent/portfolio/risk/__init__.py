"""Portfolio-level risk aggregation — rolls up per-strategy risk into the
limits `risk.risk_manager.RiskManager` enforces (max_portfolio_drawdown_pct,
max_correlated_exposure).
"""
from __future__ import annotations

import pandas as pd

from portfolio.correlation import average_pairwise_correlation, return_correlation_matrix


def portfolio_equity_curve(strategy_equity_curves: dict[str, pd.Series], weights: dict[str, float] | None = None) -> pd.Series:
    weights = weights or {name: 1.0 / len(strategy_equity_curves) for name in strategy_equity_curves}
    df = pd.DataFrame(strategy_equity_curves).ffill().dropna()
    normalized = df / df.iloc[0]
    weighted = sum(normalized[name] * w for name, w in weights.items() if name in normalized)
    return weighted


def portfolio_drawdown_pct(equity: pd.Series) -> float:
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max
    return float(abs(dd.min()) * 100)


def correlated_exposure_fraction(strategy_returns: dict[str, pd.Series], threshold: float = 0.7) -> float:
    """Fraction of strategies whose average correlation to the rest of the
    book exceeds `threshold` — a proxy for `max_correlated_exposure`."""
    corr = return_correlation_matrix(strategy_returns)
    if corr.empty:
        return 0.0
    flagged = 0
    for name in corr.columns:
        others = corr[name].drop(index=name, errors="ignore")
        if not others.empty and others.abs().mean() > threshold:
            flagged += 1
    return flagged / len(corr.columns)
