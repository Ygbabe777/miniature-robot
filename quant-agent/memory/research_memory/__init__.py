"""Research memory / knowledge graph queries (spec sections 5 & 26).

Lets the Hypothesis Agent reason about accumulated evidence across papers,
e.g. "three independent papers provide evidence that this phenomenon may
persist under these conditions," and supports cross-pollination by finding
concepts studied by multiple, otherwise-unrelated papers.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Concept, ConceptRelation, Paper


def record_relation(session: Session, *, paper_id: str, concept_name: str, concept_kind: str,
                     relation: str, note: str | None = None) -> ConceptRelation:
    concept = session.execute(select(Concept).where(Concept.name == concept_name)).scalar_one_or_none()
    if concept is None:
        concept = Concept(name=concept_name, kind=concept_kind)
        session.add(concept)
        session.flush()
    row = ConceptRelation(source_paper_id=paper_id, concept_id=concept.concept_id, relation=relation, note=note)
    session.add(row)
    session.flush()
    return row


def evidence_summary(session: Session, concept_name: str) -> dict:
    """Aggregates relation counts for a concept, e.g.
    {"discovers": 1, "confirms": 2, "contradicts": 1}, plus the paper_ids
    behind each — this is the structure the Hypothesis Agent reads to
    decide whether a phenomenon has convergent, contested, or thin support.
    """
    concept = session.execute(select(Concept).where(Concept.name == concept_name)).scalar_one_or_none()
    if concept is None:
        return {"concept": concept_name, "relations": {}, "papers": {}}

    relations = session.execute(
        select(ConceptRelation).where(ConceptRelation.concept_id == concept.concept_id)
    ).scalars().all()

    by_relation: dict[str, list[str]] = defaultdict(list)
    for r in relations:
        by_relation[r.relation].append(r.source_paper_id)

    return {
        "concept": concept_name,
        "relations": {k: len(v) for k, v in by_relation.items()},
        "papers": dict(by_relation),
    }


def find_cross_pollination_candidates(session: Session, min_papers: int = 2) -> list[dict]:
    """Concepts referenced by >= `min_papers` distinct papers — candidates
    for combining findings across papers (spec section 26). The *reasoning*
    for why combining two concepts is theoretically sound is left to the
    Hypothesis Agent's LLM call (or rule-based fallback) — this function
    only surfaces the raw co-occurrence signal.
    """
    concepts = session.execute(select(Concept)).scalars().all()
    out = []
    for concept in concepts:
        relations = session.execute(
            select(ConceptRelation).where(ConceptRelation.concept_id == concept.concept_id)
        ).scalars().all()
        paper_ids = {r.source_paper_id for r in relations}
        if len(paper_ids) >= min_papers:
            out.append({"concept": concept.name, "kind": concept.kind, "paper_ids": sorted(paper_ids)})
    return out
