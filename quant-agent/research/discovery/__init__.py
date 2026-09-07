from .arxiv_source import ArxivSource
from .base import DiscoverySource, SourceUnavailableError
from .crossref_source import CrossrefSource
from .semantic_scholar_source import SemanticScholarSource
from .unavailable_sources import NBERSource, SSRNSource

#: Query families the Research Discovery Agent cycles through (spec section 3).
DEFAULT_QUERY_FAMILIES = [
    "market microstructure trading strategy",
    "statistical arbitrage",
    "order flow imbalance",
    "volatility forecasting",
    "futures momentum",
    "intraday momentum",
    "mean reversion equity futures",
    "regime detection financial markets",
    "machine learning financial markets prediction",
    "limit order book prediction",
    "price discovery futures",
    "volume profile trading",
    "liquidity imbalance",
    "market impact execution",
    "execution algorithms",
    "factor investing momentum",
    "behavioral finance anomaly",
]

ENABLED_SOURCES: list[DiscoverySource] = [ArxivSource(), CrossrefSource(), SemanticScholarSource()]
DISABLED_SOURCES: list[DiscoverySource] = [SSRNSource(), NBERSource()]

__all__ = [
    "ArxivSource", "CrossrefSource", "SemanticScholarSource", "SSRNSource", "NBERSource",
    "DiscoverySource", "SourceUnavailableError", "DEFAULT_QUERY_FAMILIES",
    "ENABLED_SOURCES", "DISABLED_SOURCES",
]
