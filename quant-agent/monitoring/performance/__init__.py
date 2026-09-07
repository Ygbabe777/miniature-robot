"""Expected-vs-actual performance tracking (spec section 17/22).

Compares what a strategy was expected to do (from its backtest/OOS metrics)
against what it actually did in paper or live trading, and flags deviation
beyond `paper_vs_expected_tolerance_pct` — the trigger for an automatic
halt (spec section 17) or a Post-Mortem Agent investigation (section 22).
"""
from __future__ import annotations

from dataclasses import dataclass

from strategies.schemas.results import BacktestMetrics


@dataclass
class PerformanceComparison:
    strategy_id: str
    metric: str
    expected: float
    actual: float

    @property
    def deviation_pct(self) -> float:
        if abs(self.expected) < 1e-9:
            return 0.0 if abs(self.actual) < 1e-9 else float("inf")
        return (self.actual - self.expected) / abs(self.expected) * 100


def compare_expected_vs_actual(
    strategy_id: str, expected: BacktestMetrics, actual: BacktestMetrics
) -> list[PerformanceComparison]:
    fields = ["sharpe", "net_pnl", "max_drawdown_pct", "win_rate", "trade_count"]
    return [
        PerformanceComparison(strategy_id, f, getattr(expected, f), getattr(actual, f))
        for f in fields
    ]


def should_halt(comparisons: list[PerformanceComparison], tolerance_pct: float) -> tuple[bool, list[str]]:
    reasons = []
    for c in comparisons:
        if c.metric in {"sharpe", "net_pnl", "win_rate"} and c.deviation_pct < -tolerance_pct:
            reasons.append(
                f"{c.metric} deviated {c.deviation_pct:.1f}% below expected "
                f"(expected={c.expected:.3f}, actual={c.actual:.3f})"
            )
        if c.metric == "max_drawdown_pct" and c.deviation_pct > tolerance_pct:
            reasons.append(
                f"max_drawdown_pct deviated {c.deviation_pct:.1f}% above expected "
                f"(expected={c.expected:.2f}, actual={c.actual:.2f})"
            )
    return len(reasons) > 0, reasons
