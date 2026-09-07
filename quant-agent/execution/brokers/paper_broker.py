"""Deterministic simulated-fill paper broker (spec section 17/30).

Fills market orders immediately at the last known price (plus a
configurable slippage/spread assumption identical to the backtester's cost
model, so paper-trading results are comparable to backtest expectations).
Limit/stop orders are held and checked against subsequent price updates.

This is the ONLY broker implementation in this build that can actually
place an "order" — see `live_stubs.py` for why real brokers are not
implemented.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from backtesting.costs import CostModel
from execution.brokers.base import BrokerInterface
from execution.orders import OrderRequest, OrderResult, OrderSide, OrderStatus, OrderType


@dataclass
class _PendingOrder:
    request: OrderRequest
    order_id: str


@dataclass
class PaperBroker(BrokerInterface):
    cost_model: CostModel
    name: str = "paper"
    cash: float = 100_000.0
    _last_prices: dict[str, float] = field(default_factory=dict)
    _positions: dict[str, float] = field(default_factory=dict)
    _pending: dict[str, _PendingOrder] = field(default_factory=dict)
    _connected: bool = True

    def is_connected(self) -> bool:
        return self._connected

    def set_last_price(self, market: str, price: float) -> None:
        self._last_prices[market] = price
        self._check_pending(market, price)

    def _check_pending(self, market: str, price: float) -> None:
        for order_id, pending in list(self._pending.items()):
            req = pending.request
            if req.market != market:
                continue
            triggered = False
            if req.order_type == OrderType.LIMIT and req.limit_price is not None:
                triggered = (req.side == OrderSide.BUY and price <= req.limit_price) or (
                    req.side == OrderSide.SELL and price >= req.limit_price
                )
            elif req.order_type == OrderType.STOP and req.stop_price is not None:
                triggered = (req.side == OrderSide.BUY and price >= req.stop_price) or (
                    req.side == OrderSide.SELL and price <= req.stop_price
                )
            if triggered:
                self._execute(order_id, req, price)
                del self._pending[order_id]

    def _execute(self, order_id: str, req: OrderRequest, price: float) -> OrderResult:
        signed_qty = req.quantity if req.side == OrderSide.BUY else -req.quantity
        cost = self.cost_model.total_cost(req.quantity)
        slip = self.cost_model.slippage_ticks * self.cost_model.tick_size
        fill_price = price + (slip if req.side == OrderSide.BUY else -slip)
        self.cash -= cost
        self._positions[req.market] = self._positions.get(req.market, 0.0) + signed_qty
        return OrderResult(order_id=order_id, status=OrderStatus.FILLED, filled_quantity=req.quantity,
                            avg_fill_price=fill_price)

    def submit_order(self, order: OrderRequest) -> OrderResult:
        order_id = f"paper_{uuid.uuid4().hex[:12]}"
        if order.order_type == OrderType.MARKET:
            price = self._last_prices.get(order.market)
            if price is None:
                return OrderResult(order_id=order_id, status=OrderStatus.REJECTED,
                                    rejection_reason=f"No market data for {order.market}")
            return self._execute(order_id, order, price)

        self._pending[order_id] = _PendingOrder(request=order, order_id=order_id)
        return OrderResult(order_id=order_id, status=OrderStatus.SUBMITTED)

    def cancel_order(self, order_id: str) -> bool:
        return self._pending.pop(order_id, None) is not None

    def get_positions(self) -> dict[str, float]:
        return dict(self._positions)

    def get_account_status(self) -> dict:
        return {"cash": self.cash, "positions": dict(self._positions), "connected": self._connected}

    def get_last_price(self, market: str) -> float | None:
        return self._last_prices.get(market)

    def disconnect(self) -> None:
        """Test hook for the safety-check suite (spec section 43)."""
        self._connected = False
