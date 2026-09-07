"""Research Discovery Agent (spec section 3/40/48 step 1-2).

Cycles through the configured query families, pulls candidates from every
enabled `DiscoverySource`, and delegates scoring/persistence/extraction to
`research.ingestion.ingest_query`. Enforces `MAX_RESEARCH_PAPERS_PER_DAY`
and `MAX_LLM_CALLS_PER_DAY` indirectly via the ingestion pipeline's budget
check.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import Paper
from research.discovery import DEFAULT_QUERY_FAMILIES, ENABLED_SOURCES, DiscoverySource
from research.ingestion import ingest_query

from .base import Agent


class ResearchDiscoveryAgent(Agent):
    name = "research_discovery_agent"

    def __init__(self, sources: list[DiscoverySource] | None = None, query_families: list[str] | None = None):
        self.sources = sources if sources is not None else ENABLED_SOURCES
        self.query_families = query_families if query_families is not None else DEFAULT_QUERY_FAMILIES

    def run_cycle(self, session: Session, max_queries: int | None = None) -> list[Paper]:
        queries = self.query_families[:max_queries] if max_queries else self.query_families
        all_ingested: list[Paper] = []
        for query in queries:
            ingested = ingest_query(session, self.sources, query)
            all_ingested.extend(ingested)
            self.log_event(
                session, "papers_ingested",
                {"query": query, "count": len(ingested), "paper_ids": [p.paper_id for p in ingested]},
                decision_log=f"Query {query!r} -> {len(ingested)} new papers ingested.",
            )
        return all_ingested
