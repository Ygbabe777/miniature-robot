"""Paper ingestion pipeline: discover -> dedupe -> score -> persist -> extract.

Ties together `research/discovery`, `research/scoring`, and
`research/extraction` into the single call the Research Discovery Agent
makes each cycle, and enforces the daily paper budget
(`MAX_RESEARCH_PAPERS_PER_DAY`, spec section 40).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database.models import Paper, PaperUnderstanding as PaperUnderstandingRow
from research.discovery import DiscoverySource, SourceUnavailableError
from research.extraction import extract
from research.scoring import score_paper
from strategies.schemas.research import PaperMetadata

logger = logging.getLogger("quant_agent.ingestion")


def _already_known(session: Session, paper: PaperMetadata) -> bool:
    if paper.doi:
        existing = session.execute(select(Paper).where(Paper.doi == paper.doi)).scalar_one_or_none()
        if existing is not None:
            return True
    existing_by_id = session.get(Paper, paper.paper_id)
    return existing_by_id is not None


def ingest_query(
    session: Session, sources: list[DiscoverySource], query: str, max_results_per_source: int = 10
) -> list[Paper]:
    settings = get_settings()
    ingested: list[Paper] = []
    already_today = session.execute(
        select(Paper).where(Paper.created_at >= __import__("datetime").datetime.utcnow().date().isoformat())
    ).scalars().all()
    budget_remaining = max(0, settings.max_research_papers_per_day - len(already_today))

    for source in sources:
        if budget_remaining <= 0:
            logger.info("Daily research paper budget exhausted; stopping ingestion for this cycle.")
            break
        try:
            candidates = source.search(query, max_results=max_results_per_source)
        except SourceUnavailableError as exc:
            logger.warning("Source %s unavailable for query %r: %s", source.name, query, exc)
            continue

        for candidate in candidates:
            if budget_remaining <= 0:
                break
            if _already_known(session, candidate):
                continue

            score = score_paper(candidate)
            row = Paper(
                paper_id=candidate.paper_id,
                title=candidate.title,
                authors=candidate.authors,
                publication_date=candidate.publication_date,
                doi=candidate.doi,
                url=candidate.url,
                abstract=candidate.abstract,
                source=candidate.source,
                citation_count=candidate.citation_count,
                score=score.total_score,
                score_breakdown=score.breakdown.model_dump(),
                score_reasoning=score.reasoning,
                rejected=score.rejected,
                rejection_reason=score.rejection_reason,
                topics=candidate.topics,
            )
            session.add(row)
            session.flush()
            budget_remaining -= 1
            ingested.append(row)

            if not score.rejected:
                understanding = extract(candidate)
                session.add(
                    PaperUnderstandingRow(
                        paper_id=candidate.paper_id,
                        facts=understanding.facts.model_dump(),
                        interpretations=understanding.interpretations.model_dump(),
                        extraction_method=understanding.extraction_method,
                        extraction_confidence=understanding.extraction_confidence,
                    )
                )
                session.flush()

    return ingested
