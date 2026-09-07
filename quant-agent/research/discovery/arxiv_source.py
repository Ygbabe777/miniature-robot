"""arXiv discovery source — real implementation, no API key required.

Uses the public arXiv API (`export.arxiv.org/api/query`), which returns an
Atom feed. Restricted to `q-fin.*` categories by default since that's
where quantitative-finance preprints live, but any free-text query works.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from strategies.schemas.research import PaperMetadata

from .base import DiscoverySource, SourceUnavailableError

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class ArxivSource(DiscoverySource):
    name = "arxiv"
    requires_credentials = False
    base_url = "https://export.arxiv.org/api/query"

    def __init__(self, categories: tuple[str, ...] = ("q-fin.TR", "q-fin.ST", "q-fin.PM", "q-fin.MF", "q-fin.CP")):
        self.categories = categories

    def search(self, query: str, max_results: int = 20) -> list[PaperMetadata]:
        cat_filter = " OR ".join(f"cat:{c}" for c in self.categories)
        search_query = f"({cat_filter}) AND all:{query}" if self.categories else f"all:{query}"
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        try:
            response = httpx.get(self.base_url, params=params, timeout=20.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"arXiv API request failed: {exc}") from exc

        return self._parse_feed(response.text, query)

    def _parse_feed(self, xml_text: str, query_topic: str) -> list[PaperMetadata]:
        root = ET.fromstring(xml_text)
        papers: list[PaperMetadata] = []
        for entry in root.findall(f"{_ATOM_NS}entry"):
            arxiv_id = (entry.findtext(f"{_ATOM_NS}id") or "").rsplit("/", 1)[-1]
            title = " ".join((entry.findtext(f"{_ATOM_NS}title") or "").split())
            abstract = " ".join((entry.findtext(f"{_ATOM_NS}summary") or "").split())
            published = entry.findtext(f"{_ATOM_NS}published")
            authors = [
                a.findtext(f"{_ATOM_NS}name") for a in entry.findall(f"{_ATOM_NS}author")
                if a.findtext(f"{_ATOM_NS}name")
            ]
            doi = entry.findtext(f"{_ARXIV_NS}doi")
            url = entry.findtext(f"{_ATOM_NS}id") or ""

            papers.append(
                PaperMetadata(
                    paper_id=f"arxiv_{arxiv_id.replace('.', '_').replace('/', '_')}",
                    title=title,
                    authors=authors,
                    publication_date=_to_date(published),
                    doi=doi,
                    url=url,
                    abstract=abstract,
                    source="arxiv",
                    citation_count=None,  # TODO: REQUIRES API — arXiv itself has no citation counts;
                    # cross-reference with Semantic Scholar (research/discovery/semantic_scholar_source.py)
                    topics=[query_topic],
                )
            )
        return papers


def _to_date(published: str | None) -> str | None:
    if not published:
        return None
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return published
