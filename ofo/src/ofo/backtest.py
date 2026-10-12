"""Fast vectorised futures backtester used for research screening.

NinjaTrader remains the final gate: results here are a filter, not a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Contract:
    symbol: str
    tick_size: float
    tick_value: float
    commission_per_side: float = 0.62   # placeholder, set to your broker's real fee

    @property
    def point_value(self) -> float:
        return self.tick_value / self.tick_size


MES = Contract("MES", 0.25, 1.25)
MNQ = Contract("MNQ", 0.25, 0.50)
ES = Contract("ES", 0.25, 12.50, 2.0)
NQ = Contract("NQ", 0.25, 5.00, 2.0)


def run_backtest(bars: pd.DataFrame, position, contract: Contract,
                 contracts: int = 1, slippage_ticks: float = 1.0, exit_px=None) -> pd.DataFrame:
    """bars: DatetimeIndex frame with 'close'. position: target in {-1,0,1} decided
    at the close of each bar and filled at that close; it earns from the NEXT bar
    (the shift below prevents lookahead). Slippage and commission are charged on
    every position change. exit_px (optional, see exits.py): prices at which a stop or target filled
    inside a bar; on those bars the P&L runs to that price instead of the close."""
    pos = pd.Series(np.asarray(position, dtype=float), index=bars.index).fillna(0.0)
    held = pos.shift(1).fillna(0.0) * contracts
    eff = bars["close"] if exit_px is None else bars["close"].where(exit_px.isna(), exit_px)
    gross = held * (eff - bars["close"].shift(1)).fillna(0.0) * contract.point_value
    traded = pos.diff().abs().fillna(pos.abs()) * contracts
    cost = traded * (contract.commission_per_side
                     + slippage_ticks * contract.tick_value)
    out = pd.DataFrame({"pnl": gross - cost, "position": held})
    if "session" in bars.columns:
        out["session"] = bars["session"]
    return out


def daily_pnl(result: pd.DataFrame) -> pd.Series:
    """Per trading session when a `session` column exists (futures sessions start the
    evening before and prop firms count the day by session), else per calendar day."""
    key = pd.to_datetime(result["session"]) if "session" in result.columns \
        else result.index.normalize()
    return result["pnl"].groupby(key).sum()
