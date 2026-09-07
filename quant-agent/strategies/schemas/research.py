"""Pydantic schemas for research papers and their structured understanding.

Spec section 3 (paper metadata + scoring) and section 4 (paper reading
engine). The critical design constraint from section 4 is enforced by
construction here: `PaperUnderstanding` has two top-level fields,
`facts` and `interpretations`, and nothing else — there is no field where
an agent could accidentally blend the two.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PaperMetadata(BaseModel):
    """Everything the discovery agent must record for a candidate paper."""

    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    doi: str | None = None
    url: str
    abstract: str | None = None
    source: str  # arxiv, ssrn, nber, journal:<name>, ...
    citation_count: int | None = None
    topics: list[str] = Field(default_factory=list)


class PaperScoreBreakdown(BaseModel):
    """PAPER_SCORE components (spec section 3). Weights live in config.thresholds."""

    source_quality: float = Field(ge=0, le=1)
    citation_quality: float = Field(ge=0, le=1)
    methodological_rigor: float = Field(ge=0, le=1)
    reproducibility: float = Field(ge=0, le=1)
    statistical_significance: float = Field(ge=0, le=1)
    market_relevance: float = Field(ge=0, le=1)
    recency: float = Field(ge=0, le=1)
    data_quality: float = Field(ge=0, le=1)


class PaperScore(BaseModel):
    paper_id: str
    breakdown: PaperScoreBreakdown
    total_score: float
    reasoning: str
    rejected: bool = False
    rejection_reason: str | None = None


class PaperFacts(BaseModel):
    """Content directly attributable to the paper's own text.

    Every field here must be traceable to a specific passage; the
    extraction agent is instructed never to fill these from prior
    knowledge about the topic.
    """

    research_question: str
    data: str
    assets: list[str] = Field(default_factory=list)
    timeframe: str
    features: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    entry_conditions: list[str] = Field(default_factory=list)
    exit_conditions: list[str] = Field(default_factory=list)
    position_sizing: str | None = None
    risk_management: str | None = None
    statistical_methods: list[str] = Field(default_factory=list)
    performance_metrics: dict[str, float | str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    stated_transaction_cost_assumptions: str | None = None


class PaperInterpretations(BaseModel):
    """AI-generated inferences layered on top of `PaperFacts`.

    These are hypotheses about *why* the paper's finding might hold or how
    it might generalize — never presented as fact.
    """

    hypothesis: str
    economic_intuition: str
    market_mechanism: str
    potential_biases: list[str] = Field(default_factory=list)
    survivorship_bias_risk: str
    look_ahead_bias_risk: str
    replication_difficulty: Literal["low", "medium", "high"]
    strategy_ideas: list[str] = Field(default_factory=list)


class PaperUnderstanding(BaseModel):
    paper_id: str
    facts: PaperFacts
    interpretations: PaperInterpretations
    extraction_method: str  # "llm:<model>" | "rule_based_fallback"
    extraction_confidence: float = Field(ge=0, le=1)
    extracted_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("extraction_confidence")
    @classmethod
    def _warn_low_confidence_llm(cls, v: float) -> float:
        return v
