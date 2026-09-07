"""Crossref discovery source — real implementation, no key required.

Crossref indexes DOI metadata for most peer-reviewed journals (Journal of
Finance, JFE, RFS, Journal of Financial Markets, Mathematical Finance,
etc.), so it is the best free source for peer-reviewed (not just preprint)
coverage. Setting `CROSSREF_MAILTO` opts into Crossref's "polite pool" for
better reliability — not required.
"""
from __future__ import annotations

import httpx

from config import get_settings
from strategies.schemas.research import PaperMetadata

from .base import DiscoverySource, SourceUnavailableError


class CrossrefSource(DiscoverySource):
    name = "crossref"
    requires_credentials = False
    base_url = "https://api.crossref.org/works"

    def search(self, query: str, max_results: int = 20) -> list[PaperMetadata]:
        settings = get_settings()
        params = {"query.bibliographic": query, "rows": max_results, "sort": "relevance"}
        if settings.crossref_mailto:
            params["mailto"] = settings.crossref_mailto
        try:
            response = httpx.get(self.base_url, params=params, timeout=20.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"Crossref API request failed: {exc}") from exc

        items = response.json().get("message", {}).get("items", [])
        papers = []
        for item in items:
            title = " ".join(item.get("title") or [""])
            authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in item.get("author", [])
            ]
            doi = item.get("DOI")
            date_parts = (item.get("published") or item.get("created") or {}).get("date-parts", [[None]])[0]
            pub_date = "-".join(str(p) for p in date_parts if p is not None) or None

            papers.append(
                PaperMetadata(
                    paper_id=f"crossref_{(doi or title).replace('/', '_').replace(' ', '_')[:80]}",
                    title=title,
                    authors=authors,
                    publication_date=pub_date,
                    doi=doi,
                    url=item.get("URL", ""),
                    abstract=item.get("abstract"),
                    source=f"journal:{item.get('container-title', ['unknown'])[0] if item.get('container-title') else 'unknown'}",
                    citation_count=item.get("is-referenced-by-count"),
                    topics=[query],
                )
            )
        return papers
