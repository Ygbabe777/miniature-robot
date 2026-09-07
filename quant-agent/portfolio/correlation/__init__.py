"""Strategy correlation analysis (spec section 21) — the input to portfolio
construction. Computes return, drawdown, and exposure correlation between
strategies so the portfolio optimizer can avoid deploying five strategies
that are all essentially the same trade.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def align_returns(strategy_returns: dict[str, pd.Series]) -> pd.DataFrame:
    df = pd.DataFrame(strategy_returns)
    return df.dropna(how="all")


def return_correlation_matrix(strategy_returns: dict[str, pd.Series]) -> pd.DataFrame:
    df = align_returns(strategy_returns)
    return df.corr(min_periods=10)


def drawdown_correlation_matrix(strategy_equity_curves: dict[str, pd.Series]) -> pd.DataFrame:
    drawdowns = {}
    for name, equity in strategy_equity_curves.items():
        running_max = equity.cummax()
        drawdowns[name] = (equity - running_max) / running_max
    return align_returns(drawdowns).corr(min_periods=10)


def exposure_overlap(strategy_active_masks: dict[str, pd.Series]) -> pd.DataFrame:
    """Fraction of bars where both strategies are simultaneously in a
    position (1 = fully overlapping exposure, 0 = never overlapping)."""
    names = list(strategy_active_masks)
    df = pd.DataFrame(strategy_active_masks).fillna(False).astype(bool)
    overlap = pd.DataFrame(index=names, columns=names, dtype=float)
    for a in names:
        for b in names:
            both = (df[a] & df[b]).sum()
            either = (df[a] | df[b]).sum()
            overlap.loc[a, b] = both / either if either > 0 else 0.0
    return overlap


def average_pairwise_correlation(corr: pd.DataFrame) -> float:
    n = len(corr)
    if n < 2:
        return 0.0
    mask = ~np.eye(n, dtype=bool)
    return float(np.nanmean(corr.values[mask]))
