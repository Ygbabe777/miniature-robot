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
from .strategies import flatten_overnight

EPOCH = pd.Timestamp("1970-01-01")


def _seconds(ts: pd.Timestamp) -> int:
    return int((ts - EPOCH) // pd.Timedelta(seconds=1))


def extract_trades(bars: pd.DataFrame, fn, params: dict, tf: str, contract: Contract,
                   contracts: int = 2, slippage_ticks: float = 1.0, exit: dict | None = None) -> list[dict]:
    b = bars if tf == "1min" else resample(bars, tf)
    pos = flatten_overnight(fn(b, **params))
    xpx = np.full(len(b), np.nan)
    if exit:
        pos, xs, _ = apply_exits(b, pos, exit["stop_atr"], exit.get("rr"))
        xpx = xs.to_numpy()
    pos = pos.to_numpy()
    close = b["close"].to_numpy()
    idx = b.index
    sess = (b["session"].astype(str).str[:10].to_numpy() if "session" in b.columns
            else idx.normalize().strftime("%Y-%m-%d").to_numpy())
    side_cost = (contract.commission_per_side + slippage_ticks * contract.tick_value) * contracts
    pv = contract.point_value * contracts
    trades, prev, entry = [], 0.0, None

    def close_trade(i: int) -> None:
        side, ei = entry
        px = xpx[i] if np.isfinite(xpx[i]) else close[i]
        gross = side * (px - close[ei]) * pv
        trades.append({"entry_t": _seconds(idx[ei]), "exit_t": _seconds(idx[i]), "side": int(side),
                       "entry_px": float(close[ei]), "exit_px": float(px),
                       "pnl": float(gross - 2 * side_cost), "session": str(sess[i]),
                       "entry_session": str(sess[ei])})

    for i in range(len(b)):
        p = pos[i]
        if p != prev:
            if prev != 0:
                close_trade(i)
            entry = (p, i) if p != 0 else None
            prev = p
    if prev != 0:                        # still open on the very last bar: close it there
        close_trade(len(b) - 1)
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": None, "pnl": 0.0}
    pnl = np.array([t["pnl"] for t in trades])
    return {"n": len(trades), "win_rate": float((pnl > 0).mean()), "pnl": float(pnl.sum()),
            "long": int(sum(t["side"] > 0 for t in trades)), "short": int(sum(t["side"] < 0 for t in trades))}
