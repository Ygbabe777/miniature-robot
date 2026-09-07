"""Sources with no legitimate free/programmatic API available to this
build. Each raises `SourceUnavailableError` with a clear `TODO: REQUIRES
API` reason instead of scraping a site in a way that would violate its
terms of service or silently return nothing.
"""
from __future__ import annotations

from strategies.schemas.research import PaperMetadata

from .base import DiscoverySource, SourceUnavailableError


class SSRNSource(DiscoverySource):
    """TODO: REQUIRES API — SSRN has no public search/metadata API. A real
    integration needs either a licensed data partnership or a manual
    export/import workflow; scraping ssrn.com directly would violate its
    terms of service and is not implemented here."""

    name = "ssrn"
    requires_credentials = True

    def search(self, query: str, max_results: int = 20) -> list[PaperMetadata]:
        raise SourceUnavailableError(
            "SSRN source not implemented: TODO: REQUIRES API (no public SSRN API; "
            "requires a licensed data partnership)."
        )


class NBERSource(DiscoverySource):
    """TODO: REQUIRES API — NBER does not publish a general-purpose public
    search API for working papers; consider Crossref (many NBER papers are
    later published and indexed there) as a partial substitute."""

    name = "nber"
    requires_credentials = True

    def search(self, query: str, max_results: int = 20) -> list[PaperMetadata]:
        raise SourceUnavailableError(
            "NBER source not implemented: TODO: REQUIRES API. Use CrossrefSource for "
            "NBER papers that were later published in an indexed journal."
        )
