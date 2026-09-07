"""Monte Carlo robustness testing (spec section 13).

Two complementary simulations on the realized trade P&L series from a
backtest:

1. `trade_resampling` — bootstrap resample trades WITH replacement to
   build a distribution of possible equity-curve outcomes had the same
   edge produced a different specific sequence of wins/losses.
2. `randomized_sequence` — shuffle the SAME trades WITHOUT replacement
   (permutation test) to check whether the strategy's apparent edge
   depends on a lucky ordering of trades rather than the trades
   themselves.

`survival_probability` is P(simulated Sharpe-equivalent return > 0) across
simulations — a strategy whose backtest Sharpe is high but which only
"survives" in a small fraction of resampled histories is not robust.
"""
from __future__ import annotations

import numpy as np

from strategies.schemas.results import MonteCarloResult


def _simulate_paths(trade_returns: np.ndarray, n_simulations: int, replace: bool, rng: np.random.Generator) -> np.ndarray:
    n = len(trade_returns)
    idx = rng.integers(0, n, size=(n_simulations, n)) if replace else np.array(
        [rng.permutation(n) for _ in range(n_simulations)]
    )
    return trade_returns[idx].sum(axis=1)


def run_monte_carlo(
    trade_returns: list[float],
    experiment_id: str,
    method: str = "trade_resampling",
    n_simulations: int = 2000,
    seed: int = 7,
) -> MonteCarloResult:
    if len(trade_returns) < 30:
        raise ValueError(
            f"Only {len(trade_returns)} trades — Monte Carlo simulation needs >= 30 for a "
            "meaningful distribution (spec section 15: min_trade_count)."
        )
    arr = np.asarray(trade_returns, dtype=float)
    rng = np.random.default_rng(seed)
    replace = method == "trade_resampling"
    totals = _simulate_paths(arr, n_simulations, replace, rng)

    survival_probability = float(np.mean(totals > 0))
    percentiles = {
        "p5": float(np.percentile(totals, 5)),
        "p25": float(np.percentile(totals, 25)),
        "p50": float(np.percentile(totals, 50)),
        "p75": float(np.percentile(totals, 75)),
        "p95": float(np.percentile(totals, 95)),
    }
    passed = survival_probability >= 0.90

    return MonteCarloResult(
        experiment_id=experiment_id,
        method=method,
        n_simulations=n_simulations,
        survival_probability=survival_probability,
        percentiles=percentiles,
        passed=passed,
    )
