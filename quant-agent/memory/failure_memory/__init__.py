"""Failure database (spec section 24/25) — the AI must remember failed
research so it does not repeatedly rediscover the same dead end.

Similarity matching here is a normalized-text containment/overlap check,
NOT semantic embedding similarity — that requires `EMBEDDING_BACKEND` to be
configured (`TODO: REQUIRES API` for a true nearest-neighbor lookup over
`paper_embeddings`). This is a conservative MVP: it will catch near-exact
repeats of a hypothesis statement and obviously-identical conditions, but
will miss paraphrased repeats.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import ResearchFailure, StrategyFailure


def _normalize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _overlap_ratio(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def record_research_failure(session: Session, *, paper_id: str | None, hypothesis_id: str | None,
                             reason: str, conditions: dict) -> ResearchFailure:
    row = ResearchFailure(paper_id=paper_id, hypothesis_id=hypothesis_id, reason=reason, conditions=conditions)
    session.add(row)
    session.flush()
    return row


def record_strategy_failure(session: Session, *, strategy_id: str, hypothesis_id: str | None, reason: str,
                             failure_mode: str, metrics: dict, parameter_sensitivity: dict,
                             market_conditions: dict, research_source: str | None) -> StrategyFailure:
    row = StrategyFailure(
        strategy_id=strategy_id, hypothesis_id=hypothesis_id, reason=reason, failure_mode=failure_mode,
        metrics=metrics, parameter_sensitivity=parameter_sensitivity, market_conditions=market_conditions,
        research_source=research_source,
    )
    session.add(row)
    session.flush()
    return row


def find_similar_past_failure(
    session: Session, hypothesis_statement: str, conditions: dict, similarity_threshold: float = 0.6
) -> ResearchFailure | None:
    """Returns the most similar previously-recorded research failure whose
    recorded conditions overlap this hypothesis's conditions above
    `similarity_threshold`, or None. Used by the Hypothesis Agent to avoid
    proposing "do not repeat this exact hypothesis under identical
    conditions" (spec section 24).
    """
    target_tokens = _normalize(hypothesis_statement)
    condition_keys = set(conditions.keys())
    candidates = session.execute(select(ResearchFailure)).scalars().all()
    best, best_score = None, 0.0
    for candidate in candidates:
        candidate_tokens = _normalize(candidate.reason)
        score = _overlap_ratio(target_tokens, candidate_tokens)
        shared_condition_keys = condition_keys & set((candidate.conditions or {}).keys())
        if shared_condition_keys:
            matching = sum(
                1 for k in shared_condition_keys if candidate.conditions.get(k) == conditions.get(k)
            )
            score = max(score, matching / max(len(condition_keys), 1))
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= similarity_threshold else None
