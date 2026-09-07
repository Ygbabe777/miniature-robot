"""Transaction cost models — commissions, exchange fees, slippage, spread.

Kept separate from the engine so cost assumptions can be swept
independently for transaction-cost stress tests (spec section 13/14).
"""
from __future__ import annotations

from dataclasses import dataclass

from strategies.schemas.strategy import ExecutionModel

TICK_SIZE = {"NQ": 0.25, "MNQ": 0.25, "ES": 0.25, "MES": 0.25}
TICK_VALUE = {"NQ": 5.0, "MNQ": 0.5, "ES": 12.5, "MES": 1.25}


@dataclass
class CostModel:
    commission_per_contract: float
    slippage_ticks: float
    tick_size: float
    tick_value: float
    spread_ticks: float = 1.0
    stress_multiplier: float = 1.0
    """`stress_multiplier` > 1.0 implements the conservative "worse than
    observed" execution assumption used by transaction-cost stress tests."""

    def commission(self, contracts: float) -> float:
        return abs(contracts) * self.commission_per_contract

    def slippage_cost(self, contracts: float) -> float:
        ticks = self.slippage_ticks * self.stress_multiplier
        return abs(contracts) * ticks * self.tick_value

    def spread_cost(self, contracts: float) -> float:
        return abs(contracts) * (self.spread_ticks / 2) * self.tick_value

    def total_cost(self, contracts: float) -> float:
        return self.commission(contracts) + self.slippage_cost(contracts) + self.spread_cost(contracts)


def cost_model_from_execution_model(market: str, execution_model: ExecutionModel,
                                     stress_multiplier: float = 1.0) -> CostModel:
    tick_size = TICK_SIZE.get(market, 0.01)
    tick_value = TICK_VALUE.get(market, 1.0)
    return CostModel(
        commission_per_contract=execution_model.commission_per_contract,
        slippage_ticks=execution_model.slippage_ticks,
        tick_size=tick_size,
        tick_value=tick_value,
        spread_ticks=1.0 if execution_model.assume_bid_ask_spread else 0.0,
        stress_multiplier=stress_multiplier,
    )
