"""Live broker interfaces — intentionally NOT implemented.

Spec section 30 asks for `InteractiveBrokersBroker`, `TradovateBroker`, and
`NinjaTraderBroker`. Each requires a licensed API connection, real
credentials, and — critically — a live-market connectivity test this
codebase cannot perform safely without a human operator and a funded (or
sandbox) brokerage account. Rather than fake a broker that "works" against
mocked data, every method here raises `BrokerNotImplementedError` with a
`TODO: REQUIRES API` message, so nothing downstream can mistake a stub for
a working connection. `config.settings.Settings` already refuses
`FULL_AUTO_LIVE` unless a real broker is registered; wiring one of these up
for real is future work.
"""
from __future__ import annotations

from execution.brokers.base import BrokerInterface, BrokerNotImplementedError
from execution.orders import OrderRequest, OrderResult


class _UnimplementedLiveBroker(BrokerInterface):
    name = "unimplemented"
    requirement_note = "TODO: REQUIRES API"

    def is_connected(self) -> bool:
        return False

    def submit_order(self, order: OrderRequest) -> OrderResult:
        raise BrokerNotImplementedError(
            f"{self.name} is not implemented in this build ({self.requirement_note}). "
            "Refusing to submit a live order."
        )

    def cancel_order(self, order_id: str) -> bool:
        raise BrokerNotImplementedError(f"{self.name} is not implemented ({self.requirement_note}).")

    def get_positions(self) -> dict[str, float]:
        raise BrokerNotImplementedError(f"{self.name} is not implemented ({self.requirement_note}).")

    def get_account_status(self) -> dict:
        raise BrokerNotImplementedError(f"{self.name} is not implemented ({self.requirement_note}).")

    def get_last_price(self, market: str) -> float | None:
        raise BrokerNotImplementedError(f"{self.name} is not implemented ({self.requirement_note}).")


class InteractiveBrokersBroker(_UnimplementedLiveBroker):
    """TODO: REQUIRES API — needs `ib_insync`/TWS API, IBKR_HOST/PORT/CLIENT_ID,
    and a running TWS/IB Gateway session plus a connectivity + paper-account
    validation pass before this can be trusted with real orders."""

    name = "interactive_brokers"


class TradovateBroker(_UnimplementedLiveBroker):
    """TODO: REQUIRES API — needs Tradovate REST/WebSocket API credentials
    (TRADOVATE_API_KEY/SECRET) and OAuth flow implementation."""

    name = "tradovate"


class NinjaTraderBroker(_UnimplementedLiveBroker):
    """TODO: REQUIRES API — needs NinjaTrader's ATI/DLL or a running
    NinjaTrader instance reachable at NINJATRADER_HOST."""

    name = "ninjatrader"
