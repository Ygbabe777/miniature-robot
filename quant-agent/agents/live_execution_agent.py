"""Live Execution Agent (spec section 18/19/28/29).

This is the ONLY code path in the system permitted to submit a live order,
and it enforces, in order:

1. The operating-mode gate (`HUMAN_APPROVAL` by default — refuses unless a
   `Deployment` row has `approved_by`/`approved_at` set, or mode is
   `FULL_AUTO_LIVE` with `ALLOW_FULL_AUTO_LIVE=true`).
2. The full `RiskManager.pre_trade_check` (spec section 29) — any failed
   check blocks the order.
3. The broker call itself — which, for every broker currently registered
   in `execution/brokers/live_stubs.py`, raises `BrokerNotImplementedError`.
   That is intentional: there is no working live broker in this build, so
   this agent cannot actually place a real order no matter how it's
   configured. Wiring up a real broker is future work explicitly gated by
   spec section 30/47.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from config import OperatingMode, get_settings
from database.models import Deployment
from execution.brokers.base import BrokerInterface, BrokerNotImplementedError
from execution.orders import OrderRequest, OrderResult, OrderStatus
from risk.risk_manager import RiskManager

from .base import Agent


class ApprovalRequiredError(RuntimeError):
    pass


class LiveExecutionAgent(Agent):
    name = "live_execution_agent"

    def __init__(self, broker: BrokerInterface, risk_manager: RiskManager):
        self.broker = broker
        self.risk_manager = risk_manager

    def submit_live_order(self, session: Session, order: OrderRequest, deployment: Deployment) -> OrderResult:
        settings = get_settings()

        if settings.mode == OperatingMode.HUMAN_APPROVAL or settings.mode == OperatingMode.AUTO_PAPER:
            if deployment.approved_by is None or deployment.approved_at is None:
                raise ApprovalRequiredError(
                    f"Deployment {deployment.deployment_id} for strategy {order.strategy_id} has not been "
                    f"approved by a human (mode={settings.mode.value}). Call the approval API endpoint first."
                )
        elif settings.mode == OperatingMode.FULL_AUTO_LIVE and not settings.allow_full_auto_live:
            raise ApprovalRequiredError("FULL_AUTO_LIVE requires ALLOW_FULL_AUTO_LIVE=true.")
        elif settings.mode == OperatingMode.FULL_AUTO_RESEARCH:
            raise ApprovalRequiredError("Mode is FULL_AUTO_RESEARCH — live trading is not permitted in this mode.")

        check = self.risk_manager.pre_trade_check(order)
        if not check.allowed:
            self.log_event(
                session, "live_order_blocked", {"order": order.model_dump(), "reasons": check.reasons},
                decision_log=f"BLOCKED live order for {order.strategy_id}: {'; '.join(check.reasons)}",
            )
            return OrderResult(order_id="blocked", status=OrderStatus.REJECTED, rejection_reason="; ".join(check.reasons))

        try:
            result = self.broker.submit_order(order)
        except BrokerNotImplementedError as exc:
            self.log_event(
                session, "live_order_failed_no_broker", {"order": order.model_dump(), "reason": str(exc)},
                decision_log=f"Live order for {order.strategy_id} could not be submitted: {exc}",
            )
            raise

        self.log_event(
            session, "live_order_submitted", {"order": order.model_dump(), "result": result.model_dump()},
            decision_log=f"Live order for {order.strategy_id} submitted: {result.status.value}",
        )
        return result
