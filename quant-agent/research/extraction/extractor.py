"""Paper Understanding Engine (spec section 4).

`extract()` always returns a `PaperUnderstanding` with `facts` (claimed
traceable to the paper's own text) kept structurally separate from
`interpretations` (AI-generated inferences) — see
`strategies/schemas/research.py` for why that separation is a hard
schema constraint rather than a convention.

Two extraction paths:

1. LLM-backed (`ANTHROPIC_API_KEY` set): the model is given the paper's
   title + abstract and instructed to fill the `PaperFacts`/
   `PaperInterpretations` JSON schema, explicitly told to leave a field
   empty/null rather than invent detail the abstract doesn't support.
2. Rule-based fallback (no key configured): cheap keyword/regex heuristics
   over the abstract. This is explicitly LOWER QUALITY and is labeled as
   such via `extraction_method="rule_based_fallback"` and a low
   `extraction_confidence` — nothing downstream should treat it as
   equivalent to a real read of the paper.
"""
from __future__ import annotations

import re

from strategies.schemas.research import (
    PaperFacts,
    PaperInterpretations,
    PaperMetadata,
    PaperUnderstanding,
)

from .llm_client import LLMClient, LLMResponseError, get_llm_client

_SYSTEM_PROMPT = """You are a meticulous quantitative-finance research assistant. Given a paper's \
title and abstract, extract a structured understanding as JSON with exactly two top-level keys: \
"facts" and "interpretations".

"facts" must contain ONLY content directly stated or clearly implied by the abstract text itself \
(research_question, data, assets, timeframe, features, signals, entry_conditions, exit_conditions, \
position_sizing, risk_management, statistical_methods, performance_metrics, limitations, \
stated_transaction_cost_assumptions). If the abstract does not mention something, use an empty \
string, empty list, or null — never invent detail.

"interpretations" is where YOUR OWN inference goes (hypothesis, economic_intuition, \
market_mechanism, potential_biases, survivorship_bias_risk, look_ahead_bias_risk, \
replication_difficulty ["low"|"medium"|"high"], strategy_ideas).

Return ONLY the JSON object, no prose, no markdown code fences."""


def _build_user_prompt(paper: PaperMetadata) -> str:
    return f"Title: {paper.title}\n\nAbstract:\n{paper.abstract or '(no abstract available)'}"


def extract_with_llm(paper: PaperMetadata, client: LLMClient) -> PaperUnderstanding:
    raw = client.complete_json(_SYSTEM_PROMPT, _build_user_prompt(paper))
    facts = PaperFacts(**raw.get("facts", {}))
    interpretations = PaperInterpretations(**raw.get("interpretations", {}))
    return PaperUnderstanding(
        paper_id=paper.paper_id,
        facts=facts,
        interpretations=interpretations,
        extraction_method=f"llm:{client.model}",
        extraction_confidence=0.75,
    )


_ASSET_PATTERNS = {
    "NQ": r"\bnasdaq[- ]?100\b|\bnq\b", "ES": r"\bs&p ?500\b|\bes\b\bfutures\b",
    "equities": r"\bequit(y|ies)\b|\bstocks?\b", "futures": r"\bfutures?\b",
    "options": r"\boptions?\b", "FX": r"\bforeign exchange\b|\bfx\b|\bcurrenc",
    "crypto": r"\bcrypto|\bbitcoin\b",
}
_TIMEFRAME_PATTERNS = {
    "intraday": r"\bintraday\b|\bhigh.frequency\b|\bminute\b",
    "daily": r"\bdaily\b|\bday\b",
    "monthly": r"\bmonthly\b",
}
_METHOD_PATTERNS = [
    "regression", "machine learning", "neural network", "random forest", "gradient boosting",
    "ols", "garch", "var model", "cointegration", "bootstrap", "cross-validation",
]


def extract_rule_based(paper: PaperMetadata) -> PaperUnderstanding:
    text = (paper.abstract or "").lower()

    assets = [name for name, pattern in _ASSET_PATTERNS.items() if re.search(pattern, text)]
    timeframe_matches = [name for name, pattern in _TIMEFRAME_PATTERNS.items() if re.search(pattern, text)]
    methods = [m for m in _METHOD_PATTERNS if m in text]

    facts = PaperFacts(
        research_question=paper.title,
        data="unknown — not extractable from abstract alone (rule-based fallback)",
        assets=assets,
        timeframe=timeframe_matches[0] if timeframe_matches else "unknown",
        statistical_methods=methods,
        limitations=["Extracted via rule-based fallback (no LLM configured) — treat with low confidence."],
    )
    interpretations = PaperInterpretations(
        hypothesis="TODO: REQUIRES API — configure ANTHROPIC_API_KEY for a real hypothesis extraction.",
        economic_intuition="Not inferred (rule-based fallback has no reasoning capability).",
        market_mechanism="Not inferred (rule-based fallback has no reasoning capability).",
        survivorship_bias_risk="unknown",
        look_ahead_bias_risk="unknown",
        replication_difficulty="high",
        strategy_ideas=[],
    )
    return PaperUnderstanding(
        paper_id=paper.paper_id,
        facts=facts,
        interpretations=interpretations,
        extraction_method="rule_based_fallback",
        extraction_confidence=0.25,
    )


def extract(paper: PaperMetadata) -> PaperUnderstanding:
    client = get_llm_client()
    if client is None:
        return extract_rule_based(paper)
    try:
        return extract_with_llm(paper, client)
    except LLMResponseError:
        return extract_rule_based(paper)
