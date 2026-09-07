"""Aggregates positions across strategies for portfolio-level risk checks."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class PositionManager:
    _by_strategy: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(dict))

    def update(self, strategy_id: str, market: str, signed_quantity_delta: float) -> None:
        current = self._by_strategy[strategy_id].get(market, 0.0)
        self._by_strategy[strategy_id][market] = current + signed_quantity_delta

    def position(self, strategy_id: str, market: str) -> float:
        return self._by_strategy.get(strategy_id, {}).get(market, 0.0)

    def total_exposure_by_market(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for positions in self._by_strategy.values():
            for market, qty in positions.items():
                totals[market] += qty
        return dict(totals)

    def open_position_count(self) -> int:
        return sum(1 for positions in self._by_strategy.values() for qty in positions.values() if qty != 0)

    def strategy_positions(self, strategy_id: str) -> dict[str, float]:
        return dict(self._by_strategy.get(strategy_id, {}))
