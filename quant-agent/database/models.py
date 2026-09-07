"""
Relational schema (spec section 34).

Every table carries `created_at`/`updated_at`. IDs are UUID strings so that
lineage references (`paper_id`, `hypothesis_id`, `strategy_id`,
`experiment_id`, ...) are stable across environments and can be generated
client-side before a row is committed (needed so an agent can stamp an
`experiment_id` into a backtest's logs before the DB row exists).

The full lineage chain is enforced structurally, not just by convention:

    Paper -> Hypothesis -> Strategy -> StrategyVersion -> Experiment
           -> Backtest -> ValidationResult -> PaperTrade -> LiveTrade -> Deployment

Given any `live_trades` row you can walk foreign keys back to the exact
paper that inspired the strategy — see `memory/research_memory.py::trace_lineage`.

JSON columns use SQLAlchemy's generic `JSON` type (renders as JSONB on
Postgres, TEXT-backed JSON on SQLite) so the schema also works against
SQLite for tests without a running Postgres instance.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------------------
# Research
# --------------------------------------------------------------------------


class Paper(Base, TimestampMixin):
    __tablename__ = "papers"

    paper_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("paper"))
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    publication_date: Mapped[str | None] = mapped_column(String, nullable=True)
    doi: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String, index=True)  # arxiv, ssrn, nber, ...
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    score_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    topics: Mapped[list] = mapped_column(JSON, default=list)  # query family that found it

    understanding: Mapped["PaperUnderstanding"] = relationship(back_populates="paper", uselist=False)
    hypotheses: Mapped[list["Hypothesis"]] = relationship(back_populates="paper")


class PaperUnderstanding(Base, TimestampMixin):
    """Structured extraction of a paper (spec section 4).

    `facts` holds only content directly attributable to the paper's text;
    `interpretations` holds AI-generated inferences. The two are NEVER
    merged into a single free-form blob so downstream agents (and human
    reviewers) can tell which is which.
    """

    __tablename__ = "paper_understanding"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pu"))
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id"), unique=True)

    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    interpretations: Mapped[dict] = mapped_column(JSON, default=dict)

    extraction_method: Mapped[str] = mapped_column(String)  # "llm:<model>" or "rule_based_fallback"
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    paper: Mapped[Paper] = relationship(back_populates="understanding")


class PaperChunk(Base, TimestampMixin):
    __tablename__ = "paper_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("chunk"))
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class PaperEmbedding(Base, TimestampMixin):
    """Vector representation of a chunk.

    `vector` is stored as JSON (list[float]) here so this works without a
    pgvector extension. TODO: REQUIRES API — swap to a native `Vector`
    column (pgvector) or a Qdrant-backed table when EMBEDDING_BACKEND != none.
    """

    __tablename__ = "paper_embeddings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("emb"))
    chunk_id: Mapped[str] = mapped_column(ForeignKey("paper_chunks.id"), index=True)
    backend: Mapped[str] = mapped_column(String)
    vector: Mapped[list] = mapped_column(JSON)


class Concept(Base, TimestampMixin):
    """Knowledge-graph node: a market phenomenon / indicator / signal / etc."""

    __tablename__ = "concepts"

    concept_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("concept"))
    name: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)  # phenomenon, indicator, signal, feature, asset, regime, ...
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConceptRelation(Base, TimestampMixin):
    """Edge in the knowledge graph, e.g. Paper B `confirms` Concept X."""

    __tablename__ = "concept_relations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rel"))
    source_paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id"), index=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.concept_id"), index=True)
    relation: Mapped[str] = mapped_column(String)  # discovers, confirms, contradicts, improves, applies_to
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------
# Hypotheses & Strategies
# --------------------------------------------------------------------------


class Hypothesis(Base, TimestampMixin):
    __tablename__ = "hypotheses"

    hypothesis_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("hyp"))
    paper_id: Mapped[str | None] = mapped_column(ForeignKey("papers.paper_id"), nullable=True, index=True)
    parent_hypothesis_ids: Mapped[list] = mapped_column(JSON, default=list)  # for cross-pollination (sec 26)

    statement: Mapped[str] = mapped_column(Text)
    economic_rationale: Mapped[str] = mapped_column(Text)
    measurable_variables: Mapped[list] = mapped_column(JSON, default=list)
    expected_direction: Mapped[str] = mapped_column(String)
    expected_horizon: Mapped[str] = mapped_column(String)
    falsification_criteria: Mapped[str] = mapped_column(Text)
    required_dataset: Mapped[str] = mapped_column(Text)
    statistical_test: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String, default="PROPOSED")  # PROPOSED, SUPPORTED, FALSIFIED
    test_result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    paper: Mapped[Paper | None] = relationship(back_populates="hypotheses")
    strategies: Mapped[list["Strategy"]] = relationship(back_populates="hypothesis")


class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"

    strategy_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("strat"))
    hypothesis_id: Mapped[str | None] = mapped_column(ForeignKey("hypotheses.hypothesis_id"), nullable=True, index=True)
    parent_strategy_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    mutation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    name: Mapped[str] = mapped_column(String)
    market: Mapped[str] = mapped_column(String)
    timeframe: Mapped[str] = mapped_column(String)

    status: Mapped[str] = mapped_column(String, default="DISCOVERED", index=True)
    # DISCOVERED, RESEARCHED, HYPOTHESIS, PROTOTYPE, BACKTESTING, VALIDATION,
    # ROBUSTNESS, OOS, PAPER_TRADING, LIVE_CANDIDATE, HUMAN_APPROVAL, LIVE,
    # MONITORING, REVIEW, ACTIVE, PAUSED, RETIRED, REJECTED, KILLED

    hypothesis: Mapped[Hypothesis | None] = relationship(back_populates="strategies")
    versions: Mapped[list["StrategyVersion"]] = relationship(back_populates="strategy")


class StrategyVersion(Base, TimestampMixin):
    """Immutable, versioned Strategy Specification + generated code.

    Production strategies are never overwritten — a change creates
    `strategy_v{n+1}` (spec section 18).
    """

    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("sver"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)

    spec: Mapped[dict] = mapped_column(JSON)  # full StrategySpec.model_dump()
    generated_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String, nullable=True)

    strategy: Mapped[Strategy] = relationship(back_populates="versions")


# --------------------------------------------------------------------------
# Experiments / Backtests / Validation
# --------------------------------------------------------------------------


class Experiment(Base, TimestampMixin):
    __tablename__ = "experiments"

    experiment_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("exp"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    strategy_version: Mapped[int] = mapped_column(Integer)
    dataset_version: Mapped[str] = mapped_column(String)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    random_seed: Mapped[int] = mapped_column(Integer)
    code_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    research_source: Mapped[str | None] = mapped_column(String, nullable=True)  # paper_id
    hypothesis_id: Mapped[str | None] = mapped_column(String, nullable=True)

    decision: Mapped[str | None] = mapped_column(String, nullable=True)  # ADVANCE, REJECT
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Backtest(Base, TimestampMixin):
    __tablename__ = "backtests"

    backtest_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("bt"))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.experiment_id"), index=True)
    split: Mapped[str] = mapped_column(String)  # TRAIN, VALIDATION, TEST, FINAL_OOS
    start_date: Mapped[str] = mapped_column(String)
    end_date: Mapped[str] = mapped_column(String)

    metrics: Mapped[dict] = mapped_column(JSON)  # full BacktestMetrics.model_dump()
    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    trades: Mapped[list] = mapped_column(JSON, default=list)


class ValidationResult(Base, TimestampMixin):
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("val"))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.experiment_id"), index=True)
    kind: Mapped[str] = mapped_column(String)  # statistical, transaction_cost, bias_check
    passed: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class WalkForwardResult(Base, TimestampMixin):
    __tablename__ = "walk_forward_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("wf"))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.experiment_id"), index=True)
    windows: Mapped[list] = mapped_column(JSON)  # list of per-window results
    pct_profitable_windows: Mapped[float] = mapped_column(Float)
    parameter_stability_score: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)


class MonteCarloResult(Base, TimestampMixin):
    __tablename__ = "monte_carlo_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mc"))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.experiment_id"), index=True)
    method: Mapped[str] = mapped_column(String)  # trade_resampling, return_bootstrap, randomized_entries
    n_simulations: Mapped[int] = mapped_column(Integer)
    survival_probability: Mapped[float] = mapped_column(Float)
    percentiles: Mapped[dict] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(Boolean)


class RobustnessScoreRow(Base, TimestampMixin):
    __tablename__ = "robustness_scores"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rob"))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.experiment_id"), unique=True)
    score: Mapped[float] = mapped_column(Float)
    component_scores: Mapped[dict] = mapped_column(JSON)
    passed_gate: Mapped[bool] = mapped_column(Boolean)


# --------------------------------------------------------------------------
# Paper trading / Live / Deployment
# --------------------------------------------------------------------------


class PaperTrade(Base, TimestampMixin):
    __tablename__ = "paper_trades"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pt"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    side: Mapped[str] = mapped_column(String)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)


class LiveTrade(Base, TimestampMixin):
    __tablename__ = "live_trades"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("lt"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    side: Mapped[str] = mapped_column(String)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)


class Position(Base, TimestampMixin):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pos"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    market: Mapped[str] = mapped_column(String)
    quantity: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String)  # paper, live


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ord"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    mode: Mapped[str] = mapped_column(String)  # paper, live
    side: Mapped[str] = mapped_column(String)
    order_type: Mapped[str] = mapped_column(String)  # market, limit, stop
    quantity: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    broker: Mapped[str] = mapped_column(String)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class RiskEvent(Base, TimestampMixin):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("risk"))
    strategy_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String)  # INFO, WARNING, BREACH, KILL_SWITCH
    rule: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)


class Deployment(Base, TimestampMixin):
    __tablename__ = "deployments"

    deployment_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("dep"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    strategy_version: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String)  # PAPER, LIVE_CANDIDATE, LIVE
    deployment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    data_version: Mapped[str | None] = mapped_column(String, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    execution_config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="PENDING_APPROVAL")


class StrategyPerformance(Base, TimestampMixin):
    __tablename__ = "strategy_performance"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("perf"))
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.strategy_id"), index=True)
    period_start: Mapped[str] = mapped_column(String)
    period_end: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)  # paper, live
    expected: Mapped[dict] = mapped_column(JSON)
    actual: Mapped[dict] = mapped_column(JSON)
    deviation_pct: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------
# Memory / Failures
# --------------------------------------------------------------------------


class ResearchFailure(Base, TimestampMixin):
    __tablename__ = "research_failures"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rf"))
    paper_id: Mapped[str | None] = mapped_column(String, nullable=True)
    hypothesis_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyFailure(Base, TimestampMixin):
    __tablename__ = "strategy_failures"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("sf"))
    strategy_id: Mapped[str] = mapped_column(String, index=True)
    hypothesis_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    failure_mode: Mapped[str] = mapped_column(String)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    parameter_sensitivity: Mapped[dict] = mapped_column(JSON, default=dict)
    market_conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    research_source: Mapped[str | None] = mapped_column(String, nullable=True)


class MarketRegime(Base, TimestampMixin):
    __tablename__ = "market_regimes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("regime"))
    market: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    regime: Mapped[str] = mapped_column(String)  # trending, mean_reverting, high_vol, low_vol, ...
    confidence: Mapped[float] = mapped_column(Float)
    features: Mapped[dict] = mapped_column(JSON, default=dict)


class SystemEvent(Base, TimestampMixin):
    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("evt"))
    agent: Mapped[str] = mapped_column(String, index=True)
    event_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_log: Mapped[str | None] = mapped_column(Text, nullable=True)  # human-readable explainable-AI trail
