"""Paper Understanding Agent (spec section 4) — thin agent wrapper around
`research.extraction.extract`, responsible only for logging the decision
trail (which extraction method was used and at what confidence) so a
reviewer can see when a "fact" came from a low-confidence rule-based guess.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import PaperUnderstanding as PaperUnderstandingRow
from research.extraction import extract
from strategies.schemas.research import PaperMetadata, PaperUnderstanding

from .base import Agent


class PaperUnderstandingAgent(Agent):
    name = "paper_understanding_agent"

    def understand(self, session: Session, paper: PaperMetadata) -> PaperUnderstanding:
        understanding = extract(paper)
        existing = session.query(PaperUnderstandingRow).filter_by(paper_id=paper.paper_id).one_or_none()
        if existing is None:
            existing = PaperUnderstandingRow(paper_id=paper.paper_id)
            session.add(existing)
        existing.facts = understanding.facts.model_dump()
        existing.interpretations = understanding.interpretations.model_dump()
        existing.extraction_method = understanding.extraction_method
        existing.extraction_confidence = understanding.extraction_confidence
        session.flush()

        self.log_event(
            session, "paper_understood",
            {"paper_id": paper.paper_id, "method": understanding.extraction_method,
             "confidence": understanding.extraction_confidence},
            decision_log=(
                f"Extracted understanding for {paper.paper_id} via {understanding.extraction_method} "
                f"(confidence={understanding.extraction_confidence:.2f})."
            ),
        )
        return understanding
