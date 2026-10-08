"""Schemi strict (Pydantic) per output degli agenti, scenari, rischio e giudizio."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AGENT_IDS = ("price_action", "options_flow", "strategist", "risk_manager", "judge")
ANALYST_IDS = ("price_action", "options_flow")
ADDRESSEES = AGENT_IDS + ("all", "human")


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Bias(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class MessageType(str, Enum):
    ANALYSIS = "ANALYSIS"
    QUESTION = "QUESTION"
    AGREEMENT = "AGREEMENT"
    DISAGREEMENT = "DISAGREEMENT"
    CHALLENGE = "CHALLENGE"
    WARNING = "WARNING"
    VETO = "VETO"
    DECISION = "DECISION"


class AgentStatus(str, Enum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class RiskVerdict(str, Enum):
    APPROVED = "APPROVED"
    APPROVED_WITH_CAUTION = "APPROVED_WITH_CAUTION"
    NO_TRADE = "NO_TRADE"
    VETO = "VETO"
    NOT_APPROVED = "NOT_APPROVED"  # solo di sistema (Risk Manager non disponibile)


# Severita' crescente: serve per "il piu' severo vince".
VERDICT_SEVERITY = {
    RiskVerdict.APPROVED: 0,
    RiskVerdict.APPROVED_WITH_CAUTION: 1,
    RiskVerdict.NO_TRADE: 2,
    RiskVerdict.VETO: 3,
    RiskVerdict.NOT_APPROVED: 4,
}


def worst_verdict(*verdicts: RiskVerdict) -> RiskVerdict:
    """Restituisce il verdetto piu' severo (il veto non si scavalca)."""
    return max(verdicts, key=lambda v: VERDICT_SEVERITY[v])


class FinalStatus(str, Enum):
    APPROVED_SETUP = "APPROVED_SETUP"
    APPROVED_WITH_CAUTION = "APPROVED_WITH_CAUTION"
    NO_TRADE = "NO_TRADE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    DATA_ERROR = "DATA_ERROR"


class DataQuality(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class _Strict(BaseModel):
    # Tipi rigorosi; campi extra dell'LLM ignorati (mai propagati).
    model_config = ConfigDict(extra="ignore", use_enum_values=False)


class Claim(_Strict):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class KeyLevel(_Strict):
    name: str = Field(min_length=1)
    value: float
    evidence_ids: list[str] = Field(default_factory=list)


class AgentMessage(_Strict):
    addressed_to: str | None = None
    message_type: MessageType = MessageType.ANALYSIS
    message: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("addressed_to")
    @classmethod
    def _known_addressee(cls, v: str | None) -> str | None:
        if v is not None and v not in ADDRESSEES:
            raise ValueError(f"destinatario sconosciuto: {v}")
        return v


class Scenario(_Strict):
    id: str = Field(min_length=1)
    direction: Direction
    setup_type: str
    entry_zone: list[float]
    invalidation: float
    target_1: float
    target_2: float | None = None
    expected_rr: float
    confirmation_required: list[str] = Field(default_factory=list)
    thesis: str
    failure_condition: str
    confidence: int = Field(ge=0, le=100)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("entry_zone")
    @classmethod
    def _zone(cls, v: list[float]) -> list[float]:
        if len(v) != 2:
            raise ValueError("entry_zone deve avere [min, max]")
        lo, hi = sorted(v)
        return [lo, hi]


class _AgentBase(_Strict):
    agent: str
    status: AgentStatus = AgentStatus.OK
    confidence: int = Field(ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    reasoning_summary: str = ""


class AnalystOutput(_AgentBase):
    """Output di Price Action e Options Flow."""

    bias: Bias = Bias.UNKNOWN
    facts: list[Claim] = Field(default_factory=list)
    interpretations: list[Claim] = Field(default_factory=list)
    key_levels: list[KeyLevel] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)


class StrategistOutput(_AgentBase):
    bias: Bias = Bias.UNKNOWN
    facts: list[Claim] = Field(default_factory=list)
    interpretations: list[Claim] = Field(default_factory=list)
    key_levels: list[KeyLevel] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    no_trade: bool = False
    no_trade_reason: str = ""

    @model_validator(mode="after")
    def _max_two(self) -> "StrategistOutput":
        if len(self.scenarios) > 2:
            raise ValueError("massimo 2 scenari")
        if not self.scenarios and not self.no_trade:
            raise ValueError("senza scenari no_trade deve essere true")
        return self


class RiskAssessment(_Strict):
    scenario_id: str
    verdict: RiskVerdict
    computed_rr: float | None = None
    reasons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RiskOutput(_AgentBase):
    assessments: list[RiskAssessment] = Field(default_factory=list)


class IssueType(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    FABRICATED_LEVEL = "fabricated_level"
    MISSING_DATA = "missing_data"
    CONTRADICTORY_CLAIM = "contradictory_claim"
    ARITHMETIC_ERROR = "arithmetic_error"


class Severity(str, Enum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class JudgeIssue(_Strict):
    type: IssueType
    severity: Severity = Severity.MAJOR
    description: str
    target_agent: str | None = None
    scenario_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class JudgeVerdict(str, Enum):
    VALID = "VALID"
    VALID_WITH_ISSUES = "VALID_WITH_ISSUES"
    REJECTED = "REJECTED"


class JudgeOutput(_AgentBase):
    score: int = Field(ge=0, le=100)
    verdict: JudgeVerdict = JudgeVerdict.VALID
    issues: list[JudgeIssue] = Field(default_factory=list)


OUTPUT_MODELS: dict[str, type[_AgentBase]] = {
    "price_action": AnalystOutput,
    "options_flow": AnalystOutput,
    "strategist": StrategistOutput,
    "risk_manager": RiskOutput,
    "judge": JudgeOutput,
}

HumanDecision = Literal["PENDING", "TAKE", "SKIP", "MODIFY", "WATCH", "NO_TRADE"]
HUMAN_DECISIONS = ("PENDING", "TAKE", "SKIP", "MODIFY", "WATCH", "NO_TRADE")


def confidence_label(value: int) -> str:
    """Scala di confidenza (NON e' probabilita' di profitto)."""
    if value <= 20:
        return "praticamente nulla"
    if value <= 40:
        return "debole"
    if value <= 60:
        return "moderata"
    if value <= 80:
        return "buona"
    return "elevata"
