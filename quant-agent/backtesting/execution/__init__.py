"""Order/fill simulation for the event-driven backtester.

Look-ahead-bias guard: a signal computed from bar `i` is only ever filled
using bar `i+1`'s open price (or worse, under slippage) — never bar `i`'s
own close. This is the single most common source of fake backtest alpha
and is enforced structurally by `Fill.from_next_bar_open` being the only
fill constructor the engine uses for signal-driven entries/exits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backtesting.costs import CostModel


@dataclass
class Fill:
    timestamp: datetime
    side: int  # +1 buy, -1 sell
    quantity: float
    price: float
    commission: float
    slippage: float
    spread_cost: float

    @property
    def total_cost(self) -> float:
        return self.commission + self.slippage + self.spread_cost

    @classmethod
    def from_next_bar_open(cls, next_bar_timestamp: datetime, next_bar_open: float,
                            side: int, quantity: float, cost_model: CostModel) -> "Fill":
        slip_ticks = cost_model.slippage_ticks * cost_model.stress_multiplier
        fill_price = next_bar_open + side * slip_ticks * cost_model.tick_size
        return cls(
            timestamp=next_bar_timestamp,
            side=side,
            quantity=quantity,
            price=fill_price,
            commission=cost_model.commission(quantity),
            slippage=cost_model.slippage_cost(quantity),
            spread_cost=cost_model.spread_cost(quantity),
        )

    @classmethod
    def stop_or_limit_intrabar(cls, timestamp: datetime, trigger_price: float, side: int,
                                quantity: float, cost_model: CostModel) -> "Fill":
        """Used only for stop-loss/take-profit exits, which realistically
        can fill within the bar that breaches the level (still not before
        it — the engine only calls this for bar `i` after confirming bar
        `i`'s high/low breached the level, so no future information is
        used)."""
        slip_ticks = cost_model.slippage_ticks * cost_model.stress_multiplier
        fill_price = trigger_price + side * slip_ticks * cost_model.tick_size
        return cls(
            timestamp=timestamp,
            side=side,
            quantity=quantity,
            price=fill_price,
            commission=cost_model.commission(quantity),
            slippage=cost_model.slippage_cost(quantity),
            spread_cost=cost_model.spread_cost(quantity),
        )


@dataclass
class Trade:
    entry: Fill
    exit: Fill
    quantity: float
    side: int

    @property
    def gross_pnl(self) -> float:
        return self.side * (self.exit.price - self.entry.price) * self.quantity

    @property
    def total_costs(self) -> float:
        return self.entry.total_cost + self.exit.total_cost

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.total_costs

    @property
    def holding_bars(self) -> int | None:
        return None  # filled in by the engine, which knows bar indices
