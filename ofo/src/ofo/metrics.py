"""Performance statistics and chart data for a daily P&L series (dollars per session)."""
from __future__ import annotations

import numpy as np

TRADING_DAYS = 252


def equity(pnl) -> np.ndarray:
    return np.cumsum(np.asarray(pnl, dtype=float))


def drawdown(pnl) -> np.ndarray:
    """Dollars below the running peak (<= 0). The starting balance counts as the first peak."""
    e = np.concatenate([[0.0], equity(pnl)])
    return (e - np.maximum.accumulate(e))[1:]


def longest_streak(flags) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def perf_metrics(pnl) -> dict:
    x = np.asarray(pnl, dtype=float)
    n = len(x)
    if n < 2:
        return {"days": n}
    mu, sd = x.mean(), x.std(ddof=1)
    downside = np.sqrt(np.mean(np.minimum(x, 0.0) ** 2))
    wins, losses = x[x > 0], x[x < 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    dd = drawdown(x)
    return {
        "days": n,
        "total_pnl": float(x.sum()),
        "mean_day": float(mu),
        "std_day": float(sd),
        "sharpe": float(mu / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0,
        "sortino": float(mu / downside * np.sqrt(TRADING_DAYS)) if downside > 0 else 0.0,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
        "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
        "win_rate": float((x > 0).mean()),
        "expectancy": float(mu),
        "max_drawdown": float(dd.min()),
        "calmar": float(mu * TRADING_DAYS / -dd.min()) if dd.min() < 0 else None,
        "best_day": float(x.max()),
        "worst_day": float(x.min()),
        "longest_losing_streak": longest_streak(x < 0),
    }


def rolling_sharpe(pnl, window: int = 40) -> list:
    x = np.asarray(pnl, dtype=float)
    out = [None] * len(x)
    for i in range(window, len(x) + 1):
        w = x[i - window:i]
        sd = w.std(ddof=1)
        out[i - 1] = float(w.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else None
    return out


def histogram(pnl, bins: int = 20) -> dict:
    counts, edges = np.histogram(np.asarray(pnl, dtype=float), bins=bins)
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def monte_carlo(pnl, horizon: int = 120, n_paths: int = 2000, block: int = 5, seed: int = 0,
                pcts=(5, 25, 50, 75, 95)) -> dict:
    """Block-bootstrap the daily P&L into `n_paths` possible futures of `horizon` days and
    summarise the cumulative P&L day by day (fan chart). Uncertainty about the true edge is
    included by drawing blocks from the whole history for every path."""
    x = np.asarray(pnl, dtype=float)
    if len(x) < max(block, 20):
        return {"error": "too few days for a Monte Carlo"}
    rng = np.random.default_rng(seed)
    n_blocks = -(-horizon // block)
    starts = rng.integers(0, len(x) - block + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)[:, :horizon]
    paths = np.cumsum(x[idx], axis=1)
    final = paths[:, -1]
    dd = np.min(paths - np.maximum.accumulate(np.maximum(paths, 0), axis=1), axis=1)
    return {
        "horizon": horizon, "n_paths": n_paths,
        "percentiles": {str(p): [float(v) for v in np.percentile(paths, p, axis=0)] for p in pcts},
        "final": {"mean": float(final.mean()), "p5": float(np.percentile(final, 5)),
                  "p50": float(np.percentile(final, 50)), "p95": float(np.percentile(final, 95))},
        "p_loss": float((final < 0).mean()),
        "median_max_drawdown": float(np.median(dd)),
        "p_drawdown_over_2000": float((dd <= -2000).mean()),
        "sample_paths": [[float(v) for v in paths[i]] for i in range(min(15, n_paths))],
    }


def chart_pack(dates, pnl, horizon: int = 120) -> dict:
    """Everything the UI plots for one P&L series."""
    x = np.asarray(pnl, dtype=float)
    return {"dates": [str(d)[:10] for d in dates], "pnl": [float(v) for v in x],
            "equity": [float(v) for v in equity(x)], "drawdown": [float(v) for v in drawdown(x)],
            "rolling_sharpe": rolling_sharpe(x), "histogram": histogram(x),
            "metrics": perf_metrics(x), "monte_carlo": monte_carlo(x, horizon)}
