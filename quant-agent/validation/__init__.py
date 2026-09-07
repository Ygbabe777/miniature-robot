"""Validation framework entry points.

`transaction_cost_stress_test` implements spec section 14 — the
gross-vs-net-vs-conservative-net distinction. A strategy must remain
profitable even under a pessimistic (default 3x) multiplier on slippage/
spread costs; if it only works at the optimistic cost assumption, it is
not economically significant, only statistically so.
"""
from __future__ import annotations

from backtesting.engine import BacktestEngine
from strategies.schemas.strategy import StrategySpec

import pandas as pd


def transaction_cost_stress_test(
    spec: StrategySpec, bars: pd.DataFrame, stress_multiplier: float = 3.0
) -> tuple[bool, float, float]:
    """Returns (resilient, base_net_pnl, stressed_net_pnl)."""
    base_engine = BacktestEngine(cost_stress_multiplier=1.0)
    stressed_engine = BacktestEngine(cost_stress_multiplier=stress_multiplier)
    base = base_engine.run(spec, bars)
    stressed = stressed_engine.run(spec, bars)
    resilient = stressed.metrics.net_pnl > 0
    return resilient, base.metrics.net_pnl, stressed.metrics.net_pnl
