"""Computes the full `BacktestMetrics` record (spec section 10) from an
equity curve and trade list. No metric here is a proxy for "is this
strategy good" — that judgment is deliberately left to the validation /
robustness layer. This module only measures.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting.execution import Trade
from strategies.schemas.results import BacktestMetrics


def _drawdown_series(equity: pd.Series) -> pd.Series:
    running_max = equity.cummax()
    return (equity - running_max) / running_max


def _max_drawdown_duration_days(equity: pd.Series) -> int:
    dd = _drawdown_series(equity)
    underwater = dd < 0
    if not underwater.any():
        return 0
    # longest run of consecutive underwater bars, converted to days via index span
    groups = (~underwater).cumsum()
    longest_days = 0
    for _, grp in dd[underwater].groupby(groups[underwater]):
        span = grp.index.max() - grp.index.min()
        longest_days = max(longest_days, span.days)
    return longest_days


def compute_metrics(
    equity: pd.Series,
    trades: list[Trade],
    bars_per_year: float,
    exposed_bars: int,
    total_bars: int,
) -> BacktestMetrics:
    if len(equity) < 2 or equity.iloc[0] == 0:
        raise ValueError("Equity curve must have >= 2 points and a nonzero starting value.")

    bar_returns = equity.pct_change().dropna()
    total_return_pct = (equity.iloc[-1] / equity.iloc[0] - 1) * 100

    n_years = len(equity) / bars_per_year if bars_per_year > 0 else np.nan
    if equity.iloc[-1] <= 0:
        cagr = -1.0  # total loss of capital
    elif n_years and n_years > 0:
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1
    else:
        cagr = 0.0

    ann_factor = np.sqrt(bars_per_year) if bars_per_year > 0 else 1.0
    ret_std = bar_returns.std(ddof=1)
    sharpe = (bar_returns.mean() / ret_std * ann_factor) if ret_std and ret_std > 0 else 0.0

    downside = bar_returns[bar_returns < 0]
    downside_std = downside.std(ddof=1)
    sortino = (bar_returns.mean() / downside_std * ann_factor) if downside_std and downside_std > 0 else 0.0

    dd = _drawdown_series(equity)
    max_dd_pct = abs(dd.min()) * 100
    calmar = (cagr * 100 / max_dd_pct) if max_dd_pct > 0 else 0.0
    max_dd_duration_days = _max_drawdown_duration_days(equity)

    gross_pnls = np.array([t.gross_pnl for t in trades])
    net_pnls = np.array([t.net_pnl for t in trades])
    total_costs = float(sum(t.total_costs for t in trades))
    total_slippage = float(sum(t.entry.slippage + t.exit.slippage for t in trades))

    wins = net_pnls[net_pnls > 0]
    losses = net_pnls[net_pnls <= 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    expectancy = float(net_pnls.mean()) if len(net_pnls) else 0.0

    turnover = float(sum(abs(t.quantity) for t in trades) * 2)

    monthly = (equity.resample("ME").last().pct_change().dropna() * 100) if len(equity) > 1 else pd.Series(dtype=float)
    yearly = (equity.resample("YE").last().pct_change().dropna() * 100) if len(equity) > 1 else pd.Series(dtype=float)

    return BacktestMetrics(
        cagr=float(cagr),
        total_return_pct=float(total_return_pct),
        sharpe=float(sharpe),
        sortino=float(sortino),
        calmar=float(calmar),
        max_drawdown_pct=float(max_dd_pct),
        max_drawdown_duration_days=int(max_dd_duration_days),
        profit_factor=float(profit_factor) if np.isfinite(profit_factor) else 999.0,
        expectancy=float(expectancy),
        win_rate=float(win_rate),
        avg_win=float(avg_win),
        avg_loss=float(avg_loss),
        trade_count=len(trades),
        avg_holding_period_bars=float(np.mean([_holding(t) for t in trades])) if trades else 0.0,
        exposure_pct=float(exposed_bars / total_bars * 100) if total_bars else 0.0,
        turnover=turnover,
        total_transaction_costs=total_costs,
        total_slippage=total_slippage,
        gross_pnl=float(gross_pnls.sum()) if len(gross_pnls) else 0.0,
        net_pnl=float(net_pnls.sum()) if len(net_pnls) else 0.0,
        monthly_returns={str(k.date()): float(v) for k, v in monthly.items()},
        yearly_returns={str(k.year): float(v) for k, v in yearly.items()},
    )


def _holding(trade: Trade) -> float:
    delta = trade.exit.timestamp - trade.entry.timestamp
    return delta.total_seconds() / 60.0
