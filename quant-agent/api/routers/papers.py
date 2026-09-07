from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from database.models import Hypothesis, Paper, PaperUnderstanding

router = APIRouter(prefix="/papers", tags=["research"])


@router.get("")
def list_papers(session: Session = Depends(get_db), limit: int = 50, include_rejected: bool = True):
    stmt = select(Paper).order_by(Paper.created_at.desc()).limit(limit)
    if not include_rejected:
        stmt = stmt.where(Paper.rejected == False)  # noqa: E712
    papers = session.execute(stmt).scalars().all()
    return [
        {
            "paper_id": p.paper_id, "title": p.title, "source": p.source, "score": p.score,
            "rejected": p.rejected, "rejection_reason": p.rejection_reason,
            "publication_date": p.publication_date, "citation_count": p.citation_count, "url": p.url,
        }
        for p in papers
    ]


@router.get("/{paper_id}")
def get_paper(paper_id: str, session: Session = Depends(get_db)):
    paper = session.get(Paper, paper_id)
    if paper is None:
        return {"error": "not found"}
    understanding = session.execute(
        select(PaperUnderstanding).where(PaperUnderstanding.paper_id == paper_id)
    ).scalar_one_or_none()
    hypotheses = session.execute(select(Hypothesis).where(Hypothesis.paper_id == paper_id)).scalars().all()
    return {
        "paper_id": paper.paper_id, "title": paper.title, "authors": paper.authors, "source": paper.source,
        "score": paper.score, "score_breakdown": paper.score_breakdown, "score_reasoning": paper.score_reasoning,
        "rejected": paper.rejected, "rejection_reason": paper.rejection_reason, "abstract": paper.abstract,
        "understanding": (
            {"facts": understanding.facts, "interpretations": understanding.interpretations,
             "extraction_method": understanding.extraction_method,
             "extraction_confidence": understanding.extraction_confidence}
            if understanding else None
        ),
        "hypotheses": [
            {"hypothesis_id": h.hypothesis_id, "statement": h.statement, "status": h.status} for h in hypotheses
        ],
    }
