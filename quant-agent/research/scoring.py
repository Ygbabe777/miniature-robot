"""Paper scoring (spec section 3) and quality-control rejection rules (section 37).

Scoring is heuristic and transparent by design — every component score is
computed from an observable signal (source reputation, citation count,
recency, presence of an abstract, etc.) and the reasoning string names
exactly which signals drove the total, so a human reviewer can audit *why*
a paper scored the way it did rather than trusting an opaque number.

`extraction_confidence`-style nuance applies here too: this is NOT a
substitute for actually reading the paper (`research/extraction`). It is a
triage step that decides which papers are worth spending an LLM call (or a
human researcher's time) to actually read.
"""
from __future__ import annotations

from datetime import date, datetime

from config.thresholds import PaperScoringWeights, PromotionThresholds
from strategies.schemas.research import PaperMetadata, PaperScore, PaperScoreBreakdown

_SOURCE_QUALITY = {
    # Peer-reviewed journal sources score highest; arXiv/preprints lower but not zero.
    "arxiv": 0.55,
    "semantic_scholar": 0.60,
    "ssrn": 0.60,
    "nber": 0.75,
}


def _source_quality(source: str) -> float:
    if source.startswith("journal:"):
        return 0.9
    return _SOURCE_QUALITY.get(source, 0.4)


def _citation_quality(citation_count: int | None) -> float:
    if citation_count is None:
        return 0.4  # unknown, not zero — many good preprints are simply uncounted
    if citation_count >= 200:
        return 1.0
    if citation_count >= 50:
        return 0.8
    if citation_count >= 10:
        return 0.6
    if citation_count >= 1:
        return 0.45
    return 0.3


def _recency(publication_date: str | None) -> float:
    if not publication_date:
        return 0.5
    try:
        year = int(publication_date[:4])
    except ValueError:
        return 0.5
    age = date.today().year - year
    if age <= 1:
        return 1.0
    if age <= 3:
        return 0.85
    if age <= 7:
        return 0.65
    if age <= 15:
        return 0.4
    return 0.2


def _methodological_rigor(abstract: str | None) -> float:
    """Crude proxy from the abstract's own language until a full-text read
    happens in `research/extraction`. Looks for terms that signal a
    quantitative, out-of-sample-aware methodology vs. purely narrative claims.
    """
    if not abstract:
        return 0.3
    text = abstract.lower()
    positive_terms = [
        "out-of-sample", "statistically significant", "robust", "backtest", "empirical",
        "regression", "cross-validation", "p-value", "confidence interval", "bootstrap",
        "transaction cost", "walk-forward",
    ]
    hits = sum(1 for t in positive_terms if t in text)
    return min(1.0, 0.3 + 0.1 * hits)


def _reproducibility(abstract: str | None, doi: str | None) -> float:
    score = 0.4
    if doi:
        score += 0.2
    if abstract and ("data" in abstract.lower() or "dataset" in abstract.lower()):
        score += 0.2
    if abstract and ("code" in abstract.lower() or "replicat" in abstract.lower()):
        score += 0.2
    return min(1.0, score)


def _statistical_significance(abstract: str | None) -> float:
    if not abstract:
        return 0.3
    text = abstract.lower()
    if "p-value" in text or "significant at" in text or "t-statistic" in text:
        return 0.8
    if "significant" in text:
        return 0.6
    return 0.35


def _market_relevance(topics: list[str]) -> float:
    relevant = {
        "momentum", "mean reversion", "volatility", "microstructure", "order flow", "liquidity",
        "execution", "futures", "factor", "regime",
    }
    text = " ".join(topics).lower()
    return 0.9 if any(term in text for term in relevant) else 0.5


def _data_quality(abstract: str | None) -> float:
    if not abstract:
        return 0.4
    text = abstract.lower()
    if any(term in text for term in ["survivorship", "point-in-time", "tick data", "high-frequency"]):
        return 0.9
    return 0.55


def score_paper(paper: PaperMetadata, weights: PaperScoringWeights | None = None) -> PaperScore:
    weights = weights or PaperScoringWeights()
    breakdown = PaperScoreBreakdown(
        source_quality=_source_quality(paper.source),
        citation_quality=_citation_quality(paper.citation_count),
        methodological_rigor=_methodological_rigor(paper.abstract),
        reproducibility=_reproducibility(paper.abstract, paper.doi),
        statistical_significance=_statistical_significance(paper.abstract),
        market_relevance=_market_relevance(paper.topics),
        recency=_recency(paper.publication_date),
        data_quality=_data_quality(paper.abstract),
    )
    w = weights.model_dump()
    total = sum(getattr(breakdown, k) * w[k] for k in w)

    reasoning = "; ".join(f"{k}={getattr(breakdown, k):.2f}(w={w[k]:.2f})" for k in w)

    rejected, rejection_reason = _check_rejection(paper, breakdown)

    return PaperScore(
        paper_id=paper.paper_id,
        breakdown=breakdown,
        total_score=float(total),
        reasoning=reasoning,
        rejected=rejected,
        rejection_reason=rejection_reason,
    )


def _check_rejection(paper: PaperMetadata, breakdown: PaperScoreBreakdown) -> tuple[bool, str | None]:
    """Spec section 3 rejection rules. Each check is a concrete, named
    reason — never a bare "low score" rejection."""
    if not paper.abstract:
        return True, "No abstract available — insufficient information to assess methodology."
    if breakdown.methodological_rigor < 0.35:
        return True, "Abstract shows no indication of empirical/statistical methodology (insufficient methodology)."
    if breakdown.reproducibility < 0.35 and not paper.doi:
        return True, "No DOI and no data/code indication — cannot reasonably be reproduced."
    return False, None


def rank_papers(papers: list[PaperMetadata], thresholds: PromotionThresholds | None = None) -> list[PaperScore]:
    thresholds = thresholds or PromotionThresholds()
    scores = [score_paper(p, thresholds.paper_scoring_weights) for p in papers]
    scores.sort(key=lambda s: s.total_score, reverse=True)
    return scores
