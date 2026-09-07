"""System health checks surfaced on the dashboard's SYSTEM panel (spec section 35)."""
from __future__ import annotations

from dataclasses import dataclass

from execution.brokers.base import BrokerInterface


@dataclass
class HealthStatus:
    component: str
    healthy: bool
    detail: str


def check_broker(broker: BrokerInterface) -> HealthStatus:
    try:
        connected = broker.is_connected()
    except Exception as exc:  # noqa: BLE001
        return HealthStatus("broker", False, f"error checking connection: {exc}")
    return HealthStatus("broker", connected, "connected" if connected else "disconnected")


def check_database(session_factory) -> HealthStatus:
    try:
        session = session_factory()
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
        session.close()
        return HealthStatus("database", True, "reachable")
    except Exception as exc:  # noqa: BLE001
        return HealthStatus("database", False, f"unreachable: {exc}")
