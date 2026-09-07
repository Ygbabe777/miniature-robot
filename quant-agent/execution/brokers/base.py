"""BrokerInterface abstraction (spec section 30).

The research engine and risk manager depend only on this interface, never
on a concrete broker SDK, so the same strategy code runs unchanged against
`PaperBroker` today and a real broker later.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from execution.orders import OrderRequest, OrderResult


class BrokerInterface(ABC):
    name: str

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_positions(self) -> dict[str, float]: ...

    @abstractmethod
    def get_account_status(self) -> dict: ...

    @abstractmethod
    def get_last_price(self, market: str) -> float | None: ...


class BrokerNotImplementedError(NotImplementedError):
    """Raised by every real (non-paper) broker in this build — see
    execution/brokers/live_stubs.py. Distinguished from a plain
    NotImplementedError so the risk manager can catch it specifically and
    refuse to trade rather than silently doing nothing.
    """
