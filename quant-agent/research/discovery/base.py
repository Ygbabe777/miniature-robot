"""DiscoverySource abstraction (spec section 3).

Every research source the Research Discovery Agent queries implements this
interface, so adding a new legitimate source never touches agent code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from strategies.schemas.research import PaperMetadata


class DiscoverySource(ABC):
    name: str
    requires_credentials: bool = False

    @abstractmethod
    def search(self, query: str, max_results: int = 20) -> list[PaperMetadata]:
        """Returns candidate papers for `query`. Must raise, not return an
        empty list silently, if the source is unreachable or misconfigured
        — a silent empty result would look identical to "no papers found"
        and hide an outage from the Research Discovery Agent."""
        raise NotImplementedError


class SourceUnavailableError(RuntimeError):
    pass
