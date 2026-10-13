"""Trade list of a strategy: entries and exits for the chart.

Same rules as backtest.run_backtest: the target position is decided at the CLOSE of a bar
and filled at that close, with commission + slippage on every unit traded. Summing the
trade P&Ls therefore reproduces the backtest P&L exactly (checked by a test)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import Contract
from .data import resample
from .exits import apply_exits
from .ledger import run_ledger
from .strategies import flatten_overnight

EPOCH = pd.Timestamp("1970-01-01")


def _seconds(ts: pd.Timestamp) -> int:
    return int((ts - EPOCH) // pd.Timedelta(seconds=1))


def extract_trades(bars: pd.DataFrame, fn, params: dict, tf: str, contract: Contract,
                   contracts: int = 2, slippage_ticks: float = 1.0, exit: dict | None = None) -> list[dict]:
    """Entries and exits of a strategy, taken from the execution ledger (ledger.py): the single record of what the
    simulated account did. A trade still open on the very last bar is closed there for display."""
    b = bars if tf == "1min" else resample(bars, tf)
    pos = flatten_overnight(fn(b, **params))
    led = run_ledger(b, pos, contract, contracts, slippage_ticks, exit)
    trades = list(led.trades)
    ot = led.open_trade
    if ot is not None:
        pv = contract.point_value * contracts
        side_cost = (contract.commission_per_side + slippage_ticks * contract.tick_value) * contracts
        last = len(b) - 1
        trades.append({"entry_t": ot["entry_t"], "exit_t": _seconds(b.index[last]), "side": ot["side"],
                       "entry_px": ot["entry_px"], "exit_px": ot["last_px"],
                       "pnl": float(ot["side"] * (ot["last_px"] - ot["entry_px"]) * pv - 2 * side_cost),
                       "session": (str(b["session"].iloc[last])[:10] if "session" in b.columns else b.index[last].strftime("%Y-%m-%d")),
                       "entry_session": ot["entry_session"], "reason": "open"})
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": None, "pnl": 0.0}
    pnl = np.array([t["pnl"] for t in trades])
    return {"n": len(trades), "win_rate": float((pnl > 0).mean()), "pnl": float(pnl.sum()),
            "long": int(sum(t["side"] > 0 for t in trades)), "short": int(sum(t["side"] < 0 for t in trades))}
