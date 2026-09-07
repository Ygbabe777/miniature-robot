"""Falsifiable hypothesis schema (spec section 6).

A hypothesis is deliberately NOT a strategy. It must be stated in a form
that a statistical test can reject. `StrategyArchitectAgent` only accepts
hypotheses whose `status` is `SUPPORTED`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class HypothesisStatus(str, Enum):
    PROPOSED = "PROPOSED"
    TESTING = "TESTING"
    SUPPORTED = "SUPPORTED"
    FALSIFIED = "FALSIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"


class Direction(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NONLINEAR = "nonlinear"
    REGIME_DEPENDENT = "regime_dependent"


class Hypothesis(BaseModel):
    hypothesis_id: str
    paper_id: str | None = None
    parent_hypothesis_ids: list[str] = Field(default_factory=list)

    statement: str = Field(description="A single falsifiable claim, e.g. "
                            "'Order-flow imbalance predicts the next 5-minute "
                            "return on NQ'.")
    economic_rationale: str
    measurable_variables: list[str]
    expected_direction: Direction
    expected_horizon: str  # e.g. "5m", "1d"
    falsification_criteria: str = Field(
        description="The exact statistical outcome that would disprove this hypothesis."
    )
    required_dataset: str
    statistical_test: str = Field(
        description="e.g. 'OLS with Newey-West SE', 'rank correlation + bootstrap CI'"
    )

    status: HypothesisStatus = HypothesisStatus.PROPOSED
    test_result_summary: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def _must_be_falsifiable(self) -> "Hypothesis":
        if not self.falsification_criteria.strip():
            raise ValueError("A hypothesis without falsification_criteria is not science — reject it.")
        return self


class HypothesisTestResult(BaseModel):
    """Output of the Statistical Research Agent trying to DISPROVE a hypothesis."""

    hypothesis_id: str
    test_name: str
    statistic: float
    p_value: float
    effect_size: float | None = None
    sample_size: int
    conclusion: HypothesisStatus
    notes: str
