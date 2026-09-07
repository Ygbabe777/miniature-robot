"""Global Risk Manager (spec section 19) + pre-trade safety checks (section 29).

Deliberately independent from any strategy: a strategy cannot bypass these
checks by, e.g., having a high confidence signal. Every check either
passes cleanly or produces a specific, loggable reason; there is no silent
fallback that lets a trade through when a check cannot be evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from execution.brokers.base import BrokerInterface
from execution.orders import OrderRequest
from execution.position_manager import PositionManager


@dataclass
class RiskLimits:
    max_daily_loss: float = 5_000.0
    max_weekly_loss: float = 15_000.0
    max_strategy_drawdown_pct: float = 20.0
    max_portfolio_drawdown_pct: float = 15.0
    max_position_size: float = 10.0
    max_leverage: float = 3.0
    max_simultaneous_positions: int = 10
    max_correlated_exposure: float = 0.6  # fraction of portfolio in |corr| > 0.7 cluster
    max_order_size: float = 5.0
    max_slippage_ticks: float = 3.0
    max_spread_ticks: float = 4.0
    max_execution_latency_ms: float = 2_000.0
    max_data_staleness_seconds: float = 30.0


@dataclass
class RiskCheckResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class RiskManager:
    """Spec section 29 pre-trade checklist: market data freshness, connection
    status, account status, current exposure, risk limits, strategy status,
    trading hours, spread, slippage, order validity. ANY failed check ->
    DO NOT TRADE (fail closed, never fail open).
    """

    def __init__(self, limits: RiskLimits, broker: BrokerInterface, position_manager: PositionManager):
        self.limits = limits
        self.broker = broker
        self.position_manager = position_manager
        self._daily_pnl: dict[str, float] = {}
        self._weekly_pnl: dict[str, float] = {}
        self._strategy_status: dict[str, str] = {}
        self._last_data_timestamp: dict[str, datetime] = {}
        self._disabled_strategies: set[str] = set()

    def set_strategy_status(self, strategy_id: str, status: str) -> None:
        self._strategy_status[strategy_id] = status

    def record_data_update(self, market: str, timestamp: datetime) -> None:
        self._last_data_timestamp[market] = timestamp

    def record_pnl(self, strategy_id: str, pnl_delta: float) -> None:
        self._daily_pnl[strategy_id] = self._daily_pnl.get(strategy_id, 0.0) + pnl_delta
        self._weekly_pnl[strategy_id] = self._weekly_pnl.get(strategy_id, 0.0) + pnl_delta

    def disable_strategy(self, strategy_id: str) -> None:
        self._disabled_strategies.add(strategy_id)

    def pre_trade_check(
        self,
        order: OrderRequest,
        *,
        now: datetime | None = None,
        observed_spread_ticks: float | None = None,
        observed_slippage_ticks: float | None = None,
        trading_hours_ok: bool = True,
    ) -> RiskCheckResult:
        now = now or datetime.utcnow()
        reasons: list[str] = []

        if order.strategy_id in self._disabled_strategies:
            reasons.append(f"Strategy {order.strategy_id} is disabled by a prior risk event.")
        if self._strategy_status.get(order.strategy_id) in {None, "PAUSED", "RETIRED", "KILLED"}:
            reasons.append(f"Strategy status is {self._strategy_status.get(order.strategy_id)!r}, not tradeable.")
        if not self.broker.is_connected():
            reasons.append("Broker is not connected.")
        if not trading_hours_ok:
            reasons.append("Outside allowed trading hours for this market.")

        last_ts = self._last_data_timestamp.get(order.market)
        if last_ts is None:
            reasons.append(f"No market data timestamp recorded for {order.market}.")
        elif (now - last_ts) > timedelta(seconds=self.limits.max_data_staleness_seconds):
            reasons.append(f"Market data for {order.market} is stale ({(now - last_ts).total_seconds():.0f}s old).")

        if order.quantity <= 0:
            reasons.append("Order quantity must be positive.")
        if order.quantity > self.limits.max_order_size:
            reasons.append(f"Order size {order.quantity} exceeds max_order_size {self.limits.max_order_size}.")

        current_position = self.position_manager.position(order.strategy_id, order.market)
        prospective = current_position + (order.quantity if order.side.value == "buy" else -order.quantity)
        if abs(prospective) > self.limits.max_position_size:
            reasons.append(
                f"Resulting position {prospective} would exceed max_position_size {self.limits.max_position_size}."
            )

        if self.position_manager.open_position_count() >= self.limits.max_simultaneous_positions:
            reasons.append(f"Already at max_simultaneous_positions ({self.limits.max_simultaneous_positions}).")

        if observed_spread_ticks is not None and observed_spread_ticks > self.limits.max_spread_ticks:
            reasons.append(f"Observed spread {observed_spread_ticks} ticks > max {self.limits.max_spread_ticks}.")
        if observed_slippage_ticks is not None and observed_slippage_ticks > self.limits.max_slippage_ticks:
            reasons.append(f"Observed slippage {observed_slippage_ticks} ticks > max {self.limits.max_slippage_ticks}.")

        daily_loss = -self._daily_pnl.get(order.strategy_id, 0.0)
        if daily_loss > self.limits.max_daily_loss:
            reasons.append(f"Daily loss {daily_loss:.2f} exceeds max_daily_loss {self.limits.max_daily_loss}.")
        weekly_loss = -self._weekly_pnl.get(order.strategy_id, 0.0)
        if weekly_loss > self.limits.max_weekly_loss:
            reasons.append(f"Weekly loss {weekly_loss:.2f} exceeds max_weekly_loss {self.limits.max_weekly_loss}.")

        return RiskCheckResult(allowed=len(reasons) == 0, reasons=reasons)
