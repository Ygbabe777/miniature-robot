"""Knowledge Agent (spec section 5) — builds the knowledge graph from a
paper's extracted facts: every signal/feature mentioned becomes (or
reuses) a `Concept` node, related to the paper via a `discovers` edge by
default. Cross-paper relations (`confirms`/`contradicts`/`improves`/
`applies_to`) require comparing against prior papers on the same concept
and are left as `TODO` hooks an LLM-backed reasoning step would fill in —
recording them automatically from keyword overlap alone would risk
fabricating agreement/disagreement that isn't really there.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from memory.research_memory import evidence_summary, record_relation
from strategies.schemas.research import PaperUnderstanding

from .base import Agent


class KnowledgeAgent(Agent):
    name = "knowledge_agent"

    def record_paper_concepts(self, session: Session, paper_id: str, understanding: PaperUnderstanding) -> list[str]:
        concept_names = list({*understanding.facts.signals, *understanding.facts.features})
        for name in concept_names:
            record_relation(session, paper_id=paper_id, concept_name=name, concept_kind="signal", relation="discovers")

        if concept_names:
            self.log_event(
                session, "concepts_recorded", {"paper_id": paper_id, "concepts": concept_names},
                decision_log=f"Recorded {len(concept_names)} concept(s) from {paper_id}: {concept_names}",
            )
        return concept_names

    def evidence_for(self, session: Session, concept_name: str) -> dict:
        return evidence_summary(session, concept_name)
