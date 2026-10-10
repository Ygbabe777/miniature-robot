"""Prop-firm account simulator and pass-probability Monte Carlo.

The rule presets are PLACEHOLDERS: firms change their rules often. Verify every
number against your firm's current rulebook before trusting a probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np


class Trailing(Enum):
    STATIC = "static"        # floor never moves
    EOD = "eod"              # floor trails the highest end-of-day balance
    # Intraday trailing needs intrabar data; not modelled at daily granularity.


@dataclass(frozen=True)
class PropRules:
    name: str
    start_balance: float
    profit_target: float
    max_drawdown: float                  # distance between peak balance and the floor
    trailing: Trailing = Trailing.EOD
    lock_at_start: bool = True           # floor stops trailing once it reaches start_balance
    daily_loss_limit: float | None = None
    max_days: int | None = None          # None = unlimited
    min_days: int = 1
    consistency_pct: float | None = None  # best day must be < pct of total profit


# PLACEHOLDER values (Topstep-like). VERIFY with your firm.
PRESETS = {
    "50k": PropRules("50k", 50_000, 3_000, 2_000, daily_loss_limit=None, min_days=2),
    "25k": PropRules("25k", 25_000, 1_500, 1_500, daily_loss_limit=None, min_days=2),
}


@dataclass(frozen=True)
class Outcome:
    status: str   # "passed" | "busted" | "timeout"
    days: int
    final_balance: float


def simulate_account(daily_pnl, rules: PropRules) -> Outcome:
    """Run one attempt over a sequence of daily P&L values."""
    balance = rules.start_balance
    peak = balance
    floor = balance - rules.max_drawdown
    best_day = 0.0
    n = 0
    for n, pnl in enumerate(daily_pnl, start=1):
        if rules.daily_loss_limit is not None and pnl <= -rules.daily_loss_limit:
            return Outcome("busted", n, balance + pnl)
        balance += pnl
        best_day = max(best_day, pnl)
        if balance <= floor:
            return Outcome("busted", n, balance)
        if rules.trailing is Trailing.EOD and balance > peak:
            peak = balance
            floor = peak - rules.max_drawdown
            if rules.lock_at_start:
                floor = min(floor, rules.start_balance)
        profit = balance - rules.start_balance
        if profit >= rules.profit_target and n >= rules.min_days:
            if rules.consistency_pct is None or best_day < rules.consistency_pct * profit:
                return Outcome("passed", n, balance)
        if rules.max_days is not None and n >= rules.max_days:
            return Outcome("timeout", n, balance)
    return Outcome("timeout", n, balance)


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def pass_probability(daily_pnl, rules: PropRules, n_sims: int = 5_000,
                     horizon: int = 120, block: int = 5, seed: int = 0) -> dict:
    """Block-bootstrap Monte Carlo of the evaluation.

    Blocks preserve short-term autocorrelation (losing streaks). The result is an
    ESTIMATE: with few historical days the confidence interval is wide and the
    interval, not the point value, is what should be reported.
    """
    x = np.asarray(daily_pnl, dtype=float)
    if len(x) < max(block, 20):
        raise ValueError("need at least 20 days of P&L to estimate anything")
    rng = np.random.default_rng(seed)
    counts = {"passed": 0, "busted": 0, "timeout": 0}
    days_to_pass = []
    n_blocks = -(-horizon // block)
    for _ in range(n_sims):
        starts = rng.integers(0, len(x) - block + 1, size=n_blocks)
        path = np.concatenate([x[s:s + block] for s in starts])[:horizon]
        out = simulate_account(path, rules)
        counts[out.status] += 1
        if out.status == "passed":
            days_to_pass.append(out.days)
    lo, hi = wilson_interval(counts["passed"], n_sims)
    return {
        "p_pass": counts["passed"] / n_sims,
        "p_pass_ci95": (lo, hi),
        "p_bust": counts["busted"] / n_sims,
        "p_timeout": counts["timeout"] / n_sims,
        "median_days_to_pass": float(np.median(days_to_pass)) if days_to_pass else None,
        "n_sims": n_sims,
        "n_history_days": len(x),
        "note": "sampling CI only: ignores that the future may differ from history",
    }
