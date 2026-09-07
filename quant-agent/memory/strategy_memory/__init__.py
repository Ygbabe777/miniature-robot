"""Full lineage tracing (spec section 2 / 34): given any strategy (or a live
trade), walk the foreign keys back to the exact research paper that
inspired it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    Backtest,
    Deployment,
    Experiment,
    Hypothesis,
    Paper,
    Strategy,
    ValidationResult,
)


def trace_lineage(session: Session, strategy_id: str) -> dict:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError(f"Unknown strategy_id {strategy_id}")

    hypothesis = session.get(Hypothesis, strategy.hypothesis_id) if strategy.hypothesis_id else None
    paper = session.get(Paper, hypothesis.paper_id) if (hypothesis and hypothesis.paper_id) else None

    experiments = session.execute(
        select(Experiment).where(Experiment.strategy_id == strategy_id).order_by(Experiment.created_at)
    ).scalars().all()

    experiment_chain = []
    for exp in experiments:
        backtests = session.execute(
            select(Backtest).where(Backtest.experiment_id == exp.experiment_id)
        ).scalars().all()
        validations = session.execute(
            select(ValidationResult).where(ValidationResult.experiment_id == exp.experiment_id)
        ).scalars().all()
        experiment_chain.append(
            {
                "experiment_id": exp.experiment_id,
                "decision": exp.decision,
                "decision_reason": exp.decision_reason,
                "backtests": [{"backtest_id": b.backtest_id, "split": b.split, "metrics": b.metrics} for b in backtests],
                "validations": [{"kind": v.kind, "passed": v.passed} for v in validations],
            }
        )

    deployments = session.execute(
        select(Deployment).where(Deployment.strategy_id == strategy_id).order_by(Deployment.created_at)
    ).scalars().all()

    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy.name,
        "strategy_status": strategy.status,
        "paper": {"paper_id": paper.paper_id, "title": paper.title, "url": paper.url} if paper else None,
        "hypothesis": {"hypothesis_id": hypothesis.hypothesis_id, "statement": hypothesis.statement} if hypothesis else None,
        "experiments": experiment_chain,
        "deployments": [
            {"deployment_id": d.deployment_id, "stage": d.stage, "status": d.status} for d in deployments
        ],
    }
