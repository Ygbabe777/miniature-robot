"""Modello eventi del Consiglio: unica fonte di verita' per la visualizzazione."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .schemas import MessageType


class EventType(str, Enum):
    PIPELINE_STARTED = "PIPELINE_STARTED"
    DATA_VALIDATED = "DATA_VALIDATED"
    FEATURES_COMPUTED = "FEATURES_COMPUTED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_THINKING = "AGENT_THINKING"
    AGENT_MESSAGE = "AGENT_MESSAGE"
    AGENT_CHALLENGE = "AGENT_CHALLENGE"
    AGENT_AGREEMENT = "AGENT_AGREEMENT"
    AGENT_FINISHED = "AGENT_FINISHED"
    AGENT_FAILED = "AGENT_FAILED"
    ROUND_STARTED = "ROUND_STARTED"
    SCENARIO_CREATED = "SCENARIO_CREATED"
    RISK_VETO = "RISK_VETO"
    RISK_VERDICT = "RISK_VERDICT"
    JUDGE_STARTED = "JUDGE_STARTED"
    JUDGE_FINISHED = "JUDGE_FINISHED"
    FINAL_DECISION = "FINAL_DECISION"
    REPORT_GENERATED = "REPORT_GENERATED"


class CouncilEvent(BaseModel):
    event_id: str
    timestamp: str
    run_id: str
    event_type: EventType
    agent: str | None = None
    target_agent: str | None = None
    message: str = ""
    message_type: MessageType | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    ui_action: str | None = None
    # "agent" = testo prodotto da un LLM; "system" = evento deterministico del motore
    source: str = "system"
    data: dict[str, Any] = Field(default_factory=dict)
