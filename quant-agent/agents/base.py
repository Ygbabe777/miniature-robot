"""Shared agent base: every agent logs its decisions to `system_events` so
the dashboard's AI Decision Log (spec section 36) has a complete record,
and every agent receives its budget/config through `config.settings`
rather than hard-coding limits.
"""
from __future__ import annotations

import logging
from abc import ABC

from sqlalchemy.orm import Session

from database.models import SystemEvent

logger = logging.getLogger("quant_agent.agents")


class Agent(ABC):
    name: str = "agent"

    def log_event(self, session: Session, event_type: str, payload: dict, decision_log: str | None = None) -> None:
        event = SystemEvent(agent=self.name, event_type=event_type, payload=payload, decision_log=decision_log)
        session.add(event)
        session.flush()
        logger.info("[%s] %s: %s", self.name, event_type, decision_log or payload)
