"""Event-driven backtesting engine (spec section 10).

Explicitly NOT a vectorized backtest for final validation — every decision
is made bar-by-bar using only information available up to that bar, and
every fill happens on a LATER bar than the one that produced the signal
(next-bar-open for signal-driven entries/exits; same-bar high/low only for
stop-loss/take-profit, which is the one case where intrabar execution is
realistic and does not use future information).

Position sizing here is intentionally simple (fixed contract count via
`risk_model.max_position_size`, default 1) — the point of this engine is
correctness of the causal/execution model, not a sophisticated sizing
algorithm. Portfolio-level sizing is layered on top in `portfolio/`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

from backtesting.costs import cost_model_from_execution_model
from backtesting.execution import Fill, Trade
from backtesting.metrics import compute_metrics
from strategies.generator import compile_strategy
from strategies.schemas.results import BacktestMetrics
from strategies.schemas.strategy import StrategySpec

_TIMEFRAME_BARS_PER_YEAR = {
    "1m": 252 * 6.5 * 60,
    "5m": 252 * 6.5 * 12,
    "15m": 252 * 6.5 * 4,
    "1h": 252 * 6.5,
    "1d": 252,
}


class BacktestResult(BaseModel):
    metrics: BacktestMetrics
    equity_curve: list[float]
    equity_timestamps: list[str]
    trade_returns: list[float]
    trade_count: int


class BacktestEngine:
    def __init__(self, initial_capital: float = 100_000.0, cost_stress_multiplier: float = 1.0):
        self.initial_capital = initial_capital
        self.cost_stress_multiplier = cost_stress_multiplier

    def run(self, spec: StrategySpec, bars: pd.DataFrame) -> BacktestResult:
        if len(bars) < 3:
            raise ValueError("Need at least 3 bars to run a backtest (entry bar + fill bar + exit).")

        compiled = compile_strategy(spec, bars)
        df = compiled.features_df
        cost_model = cost_model_from_execution_model(spec.market, spec.execution_model, self.cost_stress_multiplier)
        quantity = spec.risk_model.max_position_size or 1.0
        side_param = int(spec.parameters.get("side", 1))

        capital = self.initial_capital
        position = 0
        entry_fill: Fill | None = None
        stop_price = tp_price = None
        entry_index: int | None = None

        equity_ts: list[pd.Timestamp] = [df.index[0]]
        equity_vals: list[float] = [capital]
        trades: list[Trade] = []
        exposed_bars = 0

        n = len(df)
        for i in range(n - 1):
            row = df.iloc[i]
            next_row = df.iloc[i + 1]

            if position != 0:
                exposed_bars += 1
                exit_side = -1 if position > 0 else 1
                hit_stop = stop_price is not None and (
                    (position > 0 and row["low"] <= stop_price) or (position < 0 and row["high"] >= stop_price)
                )
                hit_tp = tp_price is not None and (
                    (position > 0 and row["high"] >= tp_price) or (position < 0 and row["low"] <= tp_price)
                )
                time_stop = spec.risk_model.time_stop_bars
                time_stop_hit = time_stop is not None and entry_index is not None and (i - entry_index) >= time_stop
                signal_exit = compiled.exit_signal(i)

                fill = None
                if hit_stop or hit_tp:
                    trigger = stop_price if hit_stop else tp_price
                    fill = Fill.stop_or_limit_intrabar(row.name, trigger, exit_side, abs(position), cost_model)
                elif time_stop_hit or signal_exit:
                    fill = Fill.from_next_bar_open(next_row.name, next_row["open"], exit_side, abs(position), cost_model)

                if fill is not None:
                    trade = Trade(entry=entry_fill, exit=fill, quantity=abs(position), side=1 if position > 0 else -1)
                    trades.append(trade)
                    capital += trade.net_pnl
                    position = 0
                    entry_fill = None
                    stop_price = tp_price = None
                    entry_index = None

            if position == 0 and compiled.entry_signal(i):
                fill = Fill.from_next_bar_open(next_row.name, next_row["open"], side_param, quantity, cost_model)
                entry_fill = fill
                position = side_param * quantity
                entry_index = i + 1
                capital -= fill.commission + fill.slippage + fill.spread_cost
                stop_price = compiled.stop_loss_price(i, fill.price, side_param)
                tp_price = compiled.take_profit_price(i, fill.price, side_param)

            mark_price = row["close"]
            unrealized = position * (mark_price - (entry_fill.price if entry_fill else mark_price))
            equity_ts.append(row.name)
            equity_vals.append(capital + unrealized)

        # force-close any open position at the final bar for reporting purposes
        if position != 0 and entry_fill is not None:
            last_row = df.iloc[-1]
            exit_side = -1 if position > 0 else 1
            fill = Fill.stop_or_limit_intrabar(last_row.name, last_row["close"], exit_side, abs(position), cost_model)
            trade = Trade(entry=entry_fill, exit=fill, quantity=abs(position), side=1 if position > 0 else -1)
            trades.append(trade)
            capital += trade.net_pnl
            equity_vals[-1] = capital

        equity = pd.Series(equity_vals, index=pd.DatetimeIndex(equity_ts))
        equity = equity[~equity.index.duplicated(keep="last")]
        bars_per_year = _TIMEFRAME_BARS_PER_YEAR.get(spec.timeframe, 252)

        metrics = compute_metrics(equity, trades, bars_per_year, exposed_bars, n)
        return BacktestResult(
            metrics=metrics,
            equity_curve=[float(v) for v in equity.values],
            equity_timestamps=[str(ts) for ts in equity.index],
            trade_returns=[float(t.net_pnl) for t in trades],
            trade_count=len(trades),
        )
