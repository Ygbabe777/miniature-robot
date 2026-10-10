"""Overfitting-aware statistics (Bailey & Lopez de Prado)."""
from __future__ import annotations

import numpy as np
from scipy import stats as st

EULER_GAMMA = 0.5772156649015329


def sharpe(returns) -> float:
    """Per-period (NOT annualised) Sharpe ratio."""
    r = np.asarray(returns, dtype=float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def probabilistic_sharpe(returns, benchmark_sr: float = 0.0) -> float:
    """P(true SR > benchmark_sr), adjusting for sample length, skew and kurtosis."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 3:
        return 0.0
    sr = sharpe(r)
    skew = st.skew(r)
    kurt = st.kurtosis(r, fisher=False)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    return float(st.norm.cdf((sr - benchmark_sr) * np.sqrt(n - 1) / np.sqrt(denom)))


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Expected best SR among n_trials unskilled trials (the 'luck' benchmark)."""
    if n_trials <= 1:
        return 0.0
    g = EULER_GAMMA
    return float(np.sqrt(var_sr) * ((1 - g) * st.norm.ppf(1 - 1 / n_trials)
                                    + g * st.norm.ppf(1 - 1 / (n_trials * np.e))))


def deflated_sharpe(returns, n_trials: int, var_sr_trials: float) -> float:
    """PSR against the best-of-N luck benchmark. n_trials = EVERY variant ever tried."""
    return probabilistic_sharpe(returns, expected_max_sharpe(n_trials, var_sr_trials))
