"""Emergency kill switch (spec section 19).

Fires on: abnormal market conditions, execution anomaly, data anomaly,
drawdown breach, or unexpected strategy behavior. Action sequence is fixed
and always runs in this order: cancel open orders -> close positions (if
required) -> disable the strategy -> alert the user. Every trigger is
persisted as a `RiskEvent` row so the monitoring dashboard has a full,
queryable history of every automatic safety action taken.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from database.models import RiskEvent
from execution.brokers.base import BrokerInterface
from execution.orders import OrderRequest, OrderSide, OrderType
from execution.position_manager import PositionManager
from risk.risk_manager import RiskManager

logger = logging.getLogger("quant_agent.kill_switch")

AlertFn = Callable[[str, str], None]


def _default_alert(strategy_id: str, message: str) -> None:
    logger.critical("KILL SWITCH ALERT strategy=%s: %s", strategy_id, message)


@dataclass
class KillSwitch:
    broker: BrokerInterface
    position_manager: PositionManager
    risk_manager: RiskManager
    session: Session | None = None
    alert_fn: AlertFn = _default_alert

    def trigger(self, *, strategy_id: str, rule: str, message: str, close_positions: bool = True,
                pending_order_ids: list[str] | None = None) -> None:
        action_parts = []

        for order_id in pending_order_ids or []:
            try:
                self.broker.cancel_order(order_id)
                action_parts.append(f"cancelled order {order_id}")
            except Exception as exc:  # noqa: BLE001 - we log and continue; never let one failure block the rest
                action_parts.append(f"failed to cancel {order_id}: {exc}")

        if close_positions:
            positions = self.position_manager.strategy_positions(strategy_id)
            for market, qty in positions.items():
                if qty == 0:
                    continue
                side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                order = OrderRequest(
                    strategy_id=strategy_id, mode="live", market=market, side=side,
                    order_type=OrderType.MARKET, quantity=abs(qty),
                )
                try:
                    result = self.broker.submit_order(order)
                    action_parts.append(f"flatten {market}: {result.status}")
                except Exception as exc:  # noqa: BLE001
                    action_parts.append(f"failed to flatten {market}: {exc}")

        self.risk_manager.disable_strategy(strategy_id)
        action_parts.append(f"disabled strategy {strategy_id}")

        self.alert_fn(strategy_id, f"{rule}: {message}")

        if self.session is not None:
            event = RiskEvent(
                strategy_id=strategy_id, severity="KILL_SWITCH", rule=rule, message=message,
                action_taken="; ".join(action_parts),
            )
            self.session.add(event)
            self.session.flush()
