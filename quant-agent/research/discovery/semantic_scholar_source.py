"""Semantic Scholar discovery source — real implementation.

The Graph API's `/paper/search` endpoint works without a key at a low,
shared rate limit; set `SEMANTIC_SCHOLAR_API_KEY` for a higher limit. This
source is primarily used to backfill citation counts for papers found via
other sources (e.g. arXiv), since arXiv itself does not track citations.
"""
from __future__ import annotations

import httpx

from config import get_settings
from strategies.schemas.research import PaperMetadata

from .base import DiscoverySource, SourceUnavailableError

_FIELDS = "title,authors,year,externalIds,abstract,url,citationCount,publicationDate"


class SemanticScholarSource(DiscoverySource):
    name = "semantic_scholar"
    requires_credentials = False  # optional key for higher rate limits
    base_url = "https://api.semanticscholar.org/graph/v1/paper/search"

    def search(self, query: str, max_results: int = 20) -> list[PaperMetadata]:
        settings = get_settings()
        headers = {"x-api-key": settings.semantic_scholar_api_key} if settings.semantic_scholar_api_key else {}
        params = {"query": query, "limit": max_results, "fields": _FIELDS}
        try:
            response = httpx.get(self.base_url, params=params, headers=headers, timeout=20.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"Semantic Scholar API request failed: {exc}") from exc

        data = response.json().get("data", [])
        papers = []
        for item in data:
            external_ids = item.get("externalIds") or {}
            papers.append(
                PaperMetadata(
                    paper_id=f"s2_{item['paperId']}",
                    title=item.get("title") or "",
                    authors=[a.get("name", "") for a in item.get("authors") or []],
                    publication_date=item.get("publicationDate"),
                    doi=external_ids.get("DOI"),
                    url=item.get("url") or f"https://www.semanticscholar.org/paper/{item['paperId']}",
                    abstract=item.get("abstract"),
                    source="semantic_scholar",
                    citation_count=item.get("citationCount"),
                    topics=[query],
                )
            )
        return papers
