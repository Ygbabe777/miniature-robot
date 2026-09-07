"""End-to-end demo of the full research -> ... -> paper-trading loop
(spec section 48), using synthetic data so it runs with ZERO external API
keys or a running Postgres instance (falls back to an in-memory SQLite DB
if DATABASE_URL is unset — see `config/settings.py`).

This script demonstrates the WIRING of the pipeline, not a profitable
strategy: the synthetic data has a deliberately small, known, decaying
autocorrelation injected into it (see `data/providers/synthetic.py`), and
`config/thresholds.py`'s real promotion thresholds are used un-relaxed —
so it is entirely expected, and correct, for every generated strategy
variant to be REJECTED. A system that always finds a way to approve a
strategy is the failure mode this whole project exists to avoid.

Run: `python -m scripts.example_workflow` from the `quant-agent/` directory.
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("DATABASE_URL", "sqlite:///example_workflow.db")

from database import init_db  # noqa: E402
from database.session import get_sessionmaker  # noqa: E402
from database.models import Paper, PaperUnderstanding as PaperUnderstandingRow, Hypothesis as HypothesisRow  # noqa: E402

from agents.hypothesis_agent import HypothesisAgent  # noqa: E402
from agents.knowledge_agent import KnowledgeAgent  # noqa: E402
from agents.paper_trading_agent import PaperTradingAgent  # noqa: E402
from agents.strategy_agent import StrategyArchitectAgent  # noqa: E402
from agents.validation_agent import DateSplits, ValidationAgent  # noqa: E402
from backtesting.costs import CostModel  # noqa: E402
from config.thresholds import get_thresholds  # noqa: E402
from data.providers.synthetic import SyntheticDataProvider  # noqa: E402
from execution.brokers.paper_broker import PaperBroker  # noqa: E402
from research.scoring import score_paper  # noqa: E402
from strategies.registry import StrategyRegistry  # noqa: E402
from strategies.schemas.hypothesis import HypothesisStatus  # noqa: E402
from strategies.schemas.research import PaperMetadata  # noqa: E402
from strategies.schemas.strategy import StrategyStatus  # noqa: E402


def _print_header(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> None:
    t0 = time.time()
    init_db()
    session = get_sessionmaker()()

    _print_header("STEP 1-2: Research Discovery (simulated — no network calls in this demo)")
    # A real run would use agents.research_agent.ResearchDiscoveryAgent against
    # arXiv/Crossref/Semantic Scholar. To keep this script runnable with zero
    # API keys and no network dependency, we construct one representative
    # paper record directly, exactly as ingest_query() would have.
    paper_meta = PaperMetadata(
        paper_id="demo_paper_ofi_001",
        title="Order Flow Imbalance and Short-Horizon Return Predictability in Index Futures",
        authors=["A. Researcher", "B. Researcher"],
        publication_date="2023-02-01",
        doi=None,
        url="https://arxiv.org/abs/0000.00000",
        abstract=(
            "We study whether order flow imbalance predicts short-horizon returns in equity index "
            "futures using high-frequency data. Using an empirical regression framework with "
            "out-of-sample validation and realistic transaction cost assumptions, we find a "
            "statistically significant but economically modest relationship that is strongest "
            "during high-volatility regimes and decays over longer horizons."
        ),
        source="arxiv",
        citation_count=12,
        topics=["order flow imbalance"],
    )
    score = score_paper(paper_meta)
    paper_row = Paper(
        paper_id=paper_meta.paper_id, title=paper_meta.title, authors=paper_meta.authors,
        publication_date=paper_meta.publication_date, doi=paper_meta.doi, url=paper_meta.url,
        abstract=paper_meta.abstract, source=paper_meta.source, citation_count=paper_meta.citation_count,
        score=score.total_score, score_breakdown=score.breakdown.model_dump(),
        score_reasoning=score.reasoning, rejected=score.rejected, rejection_reason=score.rejection_reason,
        topics=paper_meta.topics,
    )
    session.add(paper_row)
    session.flush()
    print(f"Ingested paper {paper_row.paper_id!r}, score={score.total_score:.2f}, rejected={score.rejected}")
    print(f"Score reasoning: {score.reasoning}")
    if score.rejected:
        print(f"Rejection reason: {score.rejection_reason}")
        session.commit()
        return

    _print_header("STEP 3-5: Paper Understanding + Knowledge Graph")
    from research.extraction import extract

    understanding = extract(paper_meta)
    session.add(PaperUnderstandingRow(
        paper_id=paper_meta.paper_id, facts=understanding.facts.model_dump(),
        interpretations=understanding.interpretations.model_dump(),
        extraction_method=understanding.extraction_method,
        extraction_confidence=understanding.extraction_confidence,
    ))
    session.flush()
    print(f"Extraction method: {understanding.extraction_method} (confidence={understanding.extraction_confidence:.2f})")
    print(f"Facts.research_question: {understanding.facts.research_question}")
    print(f"Interpretations.hypothesis: {understanding.interpretations.hypothesis}")

    KnowledgeAgent().record_paper_concepts(session, paper_row.paper_id, understanding)

    _print_header("STEP 6-7: Hypothesis Generation + Statistical Testing (actively trying to falsify)")
    # Synthetic market data with a small, known, decaying injected edge —
    # this plays the role of the paper's "order flow imbalance" signal.
    market, timeframe = "NQ", "5m"
    provider = SyntheticDataProvider(seed=11, autocorr=0.35)
    bars = provider.get_bars(market, timeframe, "2023-01-01", "2023-09-30")
    print(f"Synthetic dataset: {provider.data_version(market, timeframe)}, {len(bars)} bars")

    hyp_agent = HypothesisAgent()
    hypotheses = hyp_agent.generate_hypotheses(paper_row.paper_id, understanding, n=3)
    for h in hypotheses:
        session.add(HypothesisRow(
            hypothesis_id=h.hypothesis_id, paper_id=h.paper_id, statement=h.statement,
            economic_rationale=h.economic_rationale, measurable_variables=h.measurable_variables,
            expected_direction=h.expected_direction.value, expected_horizon=h.expected_horizon,
            falsification_criteria=h.falsification_criteria, required_dataset=h.required_dataset,
            statistical_test=h.statistical_test, status=h.status.value,
        ))
    session.flush()

    supported = []
    for h in hypotheses:
        try:
            test_result = hyp_agent.test_hypothesis(h, bars, signal_col="signal", horizon_bars=1)
            h.status = test_result.conclusion
            row = session.get(HypothesisRow, h.hypothesis_id)
            row.status = h.status.value
            row.test_result_summary = test_result.notes
            print(f"  [{h.hypothesis_id}] {h.statement}\n      -> {h.status.value} ({test_result.notes})")
            if h.status == HypothesisStatus.SUPPORTED:
                supported.append(h)
        except ValueError as exc:
            print(f"  [{h.hypothesis_id}] {h.statement}\n      -> INCONCLUSIVE ({exc})")
    session.flush()

    if not supported:
        print("\nNo hypothesis survived statistical testing — correctly stopping here. "
              "(This can happen; it is not a bug.) Nothing to build a strategy on.")
        session.commit()
        return

    _print_header("STEP 8-9: Strategy Generation (variants from the first SUPPORTED hypothesis)")
    hypothesis = supported[0]
    strategy_agent = StrategyArchitectAgent()
    variants = strategy_agent.generate_variants(
        hypothesis, market=market, timeframe=timeframe, signal_col="signal", threshold=1.0, max_variants=2,
    )
    print(f"Generated {len(variants)} strategy variant(s) from hypothesis {hypothesis.hypothesis_id}:")
    for v in variants:
        print(f"  - {v.name} (mutation_reason={v.mutation_reason or 'baseline'})")

    registry = StrategyRegistry(session)
    for v in variants:
        registry.register(v)
        registry.transition(v.strategy_id, StrategyStatus.BACKTESTING, "Beginning backtest phase.")
    session.flush()

    _print_header("STEP 10-13: Backtest -> Statistical Validation -> Robustness -> OOS")
    n = len(bars)
    cut_train, cut_val, cut_test = int(n * 0.5), int(n * 0.7), int(n * 0.9)
    splits = DateSplits(
        train=(str(bars.index[0]), str(bars.index[cut_train])),
        validation=(str(bars.index[cut_train]), str(bars.index[cut_val])),
        test=(str(bars.index[cut_val]), str(bars.index[cut_test])),
        final_oos=(str(bars.index[cut_test]), str(bars.index[-1])),
    )
    print(f"Splits -> TRAIN {splits.train}\n          VALIDATION {splits.validation}\n"
          f"          TEST {splits.test}\n          FINAL_OOS {splits.final_oos}")

    validation_agent = ValidationAgent()
    thresholds = get_thresholds()
    advanced = []
    for v in variants:
        print(f"\nValidating {v.name} ...")
        t_v = time.time()
        outcome = validation_agent.run_full_validation(
            session, v, bars, splits, strategy_version=v.version, dataset_version=provider.data_version(market, timeframe),
            n_trials_considered=len(variants), research_source=paper_row.paper_id,
        )
        print(f"  hard_gates_passed={outcome.passed_hard_gates} "
              f"robustness_score={outcome.robustness_score:.2f} (gate={thresholds.min_robustness_score}) "
              f"[{time.time() - t_v:.1f}s]")
        if outcome.hard_gate_failures:
            print(f"  failures: {'; '.join(outcome.hard_gate_failures)}")

        if outcome.passed_hard_gates and outcome.passed_robustness_gate:
            registry.transition(v.strategy_id, StrategyStatus.VALIDATION, "Passed hard gates.")
            registry.transition(v.strategy_id, StrategyStatus.ROBUSTNESS, "Passed robustness score gate.")
            registry.transition(v.strategy_id, StrategyStatus.OOS, "OOS-confirmed, eligible for paper trading.")
            advanced.append((v, outcome))
        else:
            registry.transition(
                v.strategy_id, StrategyStatus.REJECTED,
                f"Failed gates: {'; '.join(outcome.hard_gate_failures) or 'robustness score below threshold'}",
            )
    session.flush()

    _print_header("Strategy Leaderboard")
    for row in registry.leaderboard():
        print(f"  {row.name:45s} status={row.status:12s} "
              f"sharpe(test)={row.latest_sharpe if row.latest_sharpe is not None else float('nan'):.2f} "
              f"sharpe(oos)={row.latest_oos_sharpe if row.latest_oos_sharpe is not None else float('nan'):.2f} "
              f"robustness={row.robustness_score if row.robustness_score is not None else float('nan'):.2f}")

    if not advanced:
        print("\nNo strategy variant passed the promotion gate — correctly stopping before paper trading. "
              "This is the expected outcome for a small, deliberately modest synthetic edge; a real "
              "deployment run continues by proposing follow-up hypotheses (see agents/learning_agent.py) "
              "instead of relaxing the thresholds to force a pass.")
        session.commit()
        print(f"\nTotal runtime: {time.time() - t0:.1f}s")
        return

    _print_header("STEP 14-16: Paper Trading (best-advanced strategy)")
    best_spec, best_outcome = max(advanced, key=lambda pair: pair[1].robustness_score)
    paper_bars = bars.loc[bars.index >= bars.index[cut_test]]
    cost_model = CostModel(commission_per_contract=best_spec.execution_model.commission_per_contract,
                            slippage_ticks=best_spec.execution_model.slippage_ticks, tick_size=0.25, tick_value=5.0)
    broker = PaperBroker(cost_model=cost_model, cash=100_000.0)
    paper_agent = PaperTradingAgent()
    actual_metrics, halt, reasons = paper_agent.run_session(
        session, best_spec, paper_bars, broker, expected_metrics=best_outcome.oos_result.metrics,
        tolerance_pct=thresholds.paper_vs_expected_tolerance_pct,
    )
    print(f"Paper trading actual Sharpe={actual_metrics.sharpe:.2f}, net_pnl={actual_metrics.net_pnl:.2f}")
    print(f"Auto-halt triggered: {halt}" + (f" ({'; '.join(reasons)})" if halt else ""))

    session.commit()
    print(f"\nTotal runtime: {time.time() - t0:.1f}s")
    print("\nFull decision lineage for this run is queryable via "
          "memory.strategy_memory.trace_lineage(session, strategy_id) or GET /strategies/{id}/lineage.")


if __name__ == "__main__":
    main()
