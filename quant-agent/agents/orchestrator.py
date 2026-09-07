"""Orchestrator (spec section 2/48) — wires every agent into the full loop:

    RESEARCH -> EXTRACT KNOWLEDGE -> GENERATE HYPOTHESES -> DESIGN STRATEGIES
    -> GENERATE CODE -> BACKTEST -> STATISTICAL VALIDATION -> ROBUSTNESS
    -> WALK-FORWARD -> OUT-OF-SAMPLE -> PAPER TRADING -> LIVE DEPLOYMENT
    -> MONITORING -> PERFORMANCE ANALYSIS -> LEARNING -> new hypotheses

Each step below is annotated with which spec-section-48 step it implements.
This class does not itself decide *whether* to run (that's a scheduler's
job — none is wired up here; `TODO: REQUIRES a scheduler / Celery worker
for unattended operation`, spec section 32/40). It exposes one method per
pipeline stage so a scheduler, a test, or `scripts/example_workflow.py` can
drive it deterministically.

The state-machine gates in `strategies.schemas.strategy.is_transition_allowed`
and the human-approval gate in `agents.live_execution_agent` are NOT
bypassable from here — the orchestrator calls the registry's `transition()`
just like anything else would, so it cannot accidentally skip a stage.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import Session

from data.providers.base import DataProvider
from database.models import Hypothesis as HypothesisRow
from strategies.registry import StrategyRegistry
from strategies.schemas.hypothesis import Hypothesis, HypothesisStatus
from strategies.schemas.research import PaperMetadata, PaperUnderstanding
from strategies.schemas.strategy import StrategySpec, StrategyStatus

from .base import Agent
from .hypothesis_agent import HypothesisAgent
from .knowledge_agent import KnowledgeAgent
from .paper_understanding_agent import PaperUnderstandingAgent
from .paper_validation_agent import PaperValidationAgent
from .research_agent import ResearchDiscoveryAgent
from .strategy_agent import StrategyArchitectAgent
from .validation_agent import DateSplits, ValidationAgent, ValidationOutcome


@dataclass
class PipelineRunResult:
    paper_id: str
    hypotheses_generated: int
    hypotheses_supported: int
    strategies_evaluated: int
    strategies_advanced: list[str]


class Orchestrator(Agent):
    name = "orchestrator"

    def __init__(self, data_provider: DataProvider):
        self.data_provider = data_provider
        self.research_agent = ResearchDiscoveryAgent()
        self.paper_validation_agent = PaperValidationAgent()
        self.paper_understanding_agent = PaperUnderstandingAgent()
        self.knowledge_agent = KnowledgeAgent()
        self.hypothesis_agent = HypothesisAgent()
        self.strategy_agent = StrategyArchitectAgent()
        self.validation_agent = ValidationAgent()

    # --- step 1-2: research discovery -----------------------------------
    def discover_research(self, session: Session, max_queries: int | None = None):
        return self.research_agent.run_cycle(session, max_queries=max_queries)

    # --- steps 3-7: validate paper, extract knowledge, generate hypotheses,
    #     eliminate weak ones with a real statistical test ----------------
    def process_paper(
        self, session: Session, paper_row, market: str, timeframe: str, signal_bars: pd.DataFrame,
        signal_col: str = "signal",
    ) -> list[Hypothesis]:
        qc_answers = self.paper_validation_agent.run(session, paper_row)
        if paper_row.rejected:
            self.log_event(session, "paper_skipped", {"paper_id": paper_row.paper_id, "reason": paper_row.rejection_reason})
            return []

        paper_meta = PaperMetadata(
            paper_id=paper_row.paper_id, title=paper_row.title, authors=paper_row.authors,
            publication_date=paper_row.publication_date, doi=paper_row.doi, url=paper_row.url,
            abstract=paper_row.abstract, source=paper_row.source, citation_count=paper_row.citation_count,
            topics=paper_row.topics,
        )
        understanding = self.paper_understanding_agent.understand(session, paper_meta)
        self.knowledge_agent.record_paper_concepts(session, paper_row.paper_id, understanding)

        hypotheses = self.hypothesis_agent.generate_hypotheses(paper_row.paper_id, understanding)
        for h in hypotheses:
            session.add(HypothesisRow(
                hypothesis_id=h.hypothesis_id, paper_id=h.paper_id, statement=h.statement,
                economic_rationale=h.economic_rationale, measurable_variables=h.measurable_variables,
                expected_direction=h.expected_direction.value, expected_horizon=h.expected_horizon,
                falsification_criteria=h.falsification_criteria, required_dataset=h.required_dataset,
                statistical_test=h.statistical_test, status=h.status.value,
            ))
        session.flush()

        # Statistical Research Agent step: actively attempt to falsify each hypothesis.
        for h in hypotheses:
            try:
                test_result = self.hypothesis_agent.test_hypothesis(h, signal_bars, signal_col=signal_col)
                h.status = test_result.conclusion
                h.test_result_summary = test_result.notes
            except ValueError as exc:
                h.status = HypothesisStatus.INCONCLUSIVE
                h.test_result_summary = str(exc)

            row = session.get(HypothesisRow, h.hypothesis_id)
            row.status = h.status.value
            row.test_result_summary = h.test_result_summary
        session.flush()

        self.log_event(
            session, "hypotheses_tested",
            {"paper_id": paper_row.paper_id,
             "results": [{"id": h.hypothesis_id, "status": h.status.value} for h in hypotheses]},
            decision_log=(
                f"Paper {paper_row.paper_id}: generated {len(hypotheses)} hypotheses, "
                f"{sum(1 for h in hypotheses if h.status == HypothesisStatus.SUPPORTED)} SUPPORTED after testing."
            ),
        )
        return hypotheses

    # --- steps 8-14: strategy generation through robustness/OOS ----------
    def develop_and_validate_strategies(
        self, session: Session, registry: StrategyRegistry, hypothesis: Hypothesis, market: str, timeframe: str,
        bars: pd.DataFrame, splits: DateSplits, signal_col: str, threshold: float, dataset_version: str,
    ) -> list[ValidationOutcome]:
        if hypothesis.status != HypothesisStatus.SUPPORTED:
            return []

        variants = self.strategy_agent.generate_variants(hypothesis, market, timeframe, signal_col, threshold)
        outcomes = []
        for spec in variants:
            spec.status = StrategyStatus.PROTOTYPE
            registry.register(spec)
            registry.transition(spec.strategy_id, StrategyStatus.BACKTESTING, "Beginning backtest phase.")

            outcome = self.validation_agent.run_full_validation(
                session, spec, bars, splits, strategy_version=spec.version, dataset_version=dataset_version,
                n_trials_considered=len(variants), research_source=hypothesis.paper_id,
            )
            outcomes.append(outcome)

            if outcome.passed_hard_gates and outcome.passed_robustness_gate:
                registry.transition(spec.strategy_id, StrategyStatus.VALIDATION, "Passed hard gates.")
                registry.transition(spec.strategy_id, StrategyStatus.ROBUSTNESS, "Passed robustness score gate.")
                registry.transition(spec.strategy_id, StrategyStatus.OOS, "Advancing to OOS-confirmed / paper-trading-eligible.")
            else:
                registry.transition(
                    spec.strategy_id, StrategyStatus.REJECTED,
                    f"Failed gates: {'; '.join(outcome.hard_gate_failures) or 'robustness score below threshold'}",
                )
        return outcomes
