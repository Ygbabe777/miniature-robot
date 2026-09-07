"""Paper Trading Agent (spec section 17).

Replays bars through the SAME causal loop the backtester uses (signal on
bar i -> fill on bar i+1's open) but routes every fill through a real
`BrokerInterface` (`PaperBroker`), so the deployment path is exercised
end-to-end before anything reaches a live broker. Records every fill as a
`PaperTrade` row and, at the end of the session, compares realized
performance against the OOS backtest's expectation
(`monitoring.performance`), auto-halting per spec section 17 if the
deviation exceeds `paper_vs_expected_tolerance_pct`.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from backtesting.metrics import compute_metrics
from backtesting.execution import Fill, Trade
from database.models import PaperTrade, StrategyPerformance, new_id
from execution.brokers.paper_broker import PaperBroker
from execution.orders import OrderRequest, OrderSide, OrderType
from monitoring.performance import compare_expected_vs_actual, should_halt
from strategies.generator import compile_strategy
from strategies.schemas.results import BacktestMetrics
from strategies.schemas.strategy import StrategySpec

from .base import Agent


class PaperTradingAgent(Agent):
    name = "paper_trading_agent"

    def run_session(
        self, session: Session, spec: StrategySpec, bars: pd.DataFrame, broker: PaperBroker,
        expected_metrics: BacktestMetrics, tolerance_pct: float = 40.0,
    ) -> tuple[BacktestMetrics, bool, list[str]]:
        compiled = compile_strategy(spec, bars)
        df = compiled.features_df
        quantity = spec.risk_model.max_position_size or 1.0
        side_param = int(spec.parameters.get("side", 1))

        position = 0.0
        entry_fill: Fill | None = None
        trades: list[Trade] = []
        equity_vals, equity_ts = [broker.cash], [df.index[0]]

        for i in range(len(df) - 1):
            row, next_row = df.iloc[i], df.iloc[i + 1]
            broker.set_last_price(spec.market, row["close"])

            if position != 0 and compiled.exit_signal(i):
                broker.set_last_price(spec.market, next_row["open"])
                side = OrderSide.SELL if position > 0 else OrderSide.BUY
                result = broker.submit_order(OrderRequest(
                    strategy_id=spec.strategy_id, mode="paper", market=spec.market, side=side,
                    order_type=OrderType.MARKET, quantity=abs(position),
                ))
                if result.avg_fill_price is not None:
                    exit_fill = Fill(next_row.name, -1 if position > 0 else 1, abs(position),
                                      result.avg_fill_price, 0.0, 0.0, 0.0)
                    trade = Trade(entry=entry_fill, exit=exit_fill, quantity=abs(position),
                                  side=1 if position > 0 else -1)
                    trades.append(trade)
                    session.add(PaperTrade(strategy_id=spec.strategy_id, timestamp=next_row.name,
                                            side=side.value, quantity=abs(position),
                                            price=result.avg_fill_price, pnl=trade.net_pnl))
                    position = 0.0
                    entry_fill = None

            if position == 0 and compiled.entry_signal(i):
                broker.set_last_price(spec.market, next_row["open"])
                side = OrderSide.BUY if side_param > 0 else OrderSide.SELL
                result = broker.submit_order(OrderRequest(
                    strategy_id=spec.strategy_id, mode="paper", market=spec.market, side=side,
                    order_type=OrderType.MARKET, quantity=quantity,
                ))
                if result.avg_fill_price is not None:
                    entry_fill = Fill(next_row.name, side_param, quantity, result.avg_fill_price, 0.0, 0.0, 0.0)
                    position = side_param * quantity
                    session.add(PaperTrade(strategy_id=spec.strategy_id, timestamp=next_row.name,
                                            side=side.value, quantity=quantity, price=result.avg_fill_price))

            unrealized = position * (row["close"] - (entry_fill.price if entry_fill else row["close"]))
            equity_ts.append(row.name)
            equity_vals.append(broker.cash + unrealized)

        session.flush()
        equity = pd.Series(equity_vals, index=pd.DatetimeIndex(equity_ts))
        equity = equity[~equity.index.duplicated(keep="last")]
        bars_per_year = 252 * 6.5 * (60 / _minutes(spec.timeframe))
        actual_metrics = compute_metrics(equity, trades, bars_per_year, exposed_bars=len(trades) * 2, total_bars=len(df))

        comparisons = compare_expected_vs_actual(spec.strategy_id, expected_metrics, actual_metrics)
        halt, reasons = should_halt(comparisons, tolerance_pct)

        session.add(StrategyPerformance(
            strategy_id=spec.strategy_id, period_start=str(df.index[0].date()), period_end=str(df.index[-1].date()),
            mode="paper", expected=expected_metrics.model_dump(), actual=actual_metrics.model_dump(),
            deviation_pct={c.metric: c.deviation_pct for c in comparisons},
        ))
        session.flush()

        self.log_event(
            session, "paper_trading_session_complete",
            {"strategy_id": spec.strategy_id, "halt": halt, "reasons": reasons},
            decision_log=(
                f"Paper trading session for {spec.strategy_id}: "
                f"{'HALT triggered — ' + '; '.join(reasons) if halt else 'within tolerance, continuing.'}"
            ),
        )
        return actual_metrics, halt, reasons


def _minutes(timeframe: str) -> int:
    return {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 60 * 24}.get(timeframe, 5)
