"""Research papers (PDF or text) -> validated strategy specs, via any OpenAI-compatible LLM.

The LLM may only choose registered templates and numeric ranges. Everything it returns is
validated; invalid proposals are kept in a `rejected` list with the reason, never executed."""
from __future__ import annotations

import json

from .agents.base import load_prompt, parse_json
from .specs import SpecError, StrategySpec, from_dict
from .strategies import TEMPLATES, TIMEFRAMES


def extract_pdf_text(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:                        # pragma: no cover
        raise RuntimeError("install pypdf:  pip install pypdf") from e
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


def chunk_text(text: str, max_chars: int = 6000, overlap: int = 300) -> list[str]:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + max_chars])
        i += max_chars - overlap
    return out


KEYWORDS = ("entry", "exit", "signal", "strategy", "backtest", "parameter", "threshold", "breakout",
            "momentum", "reversion", "mean-revert", "volatility", "trend", "opening", "range", "vwap",
            "stop", "profit", "return", "sharpe", "intraday", "futures", "index", "moving average",
            "lookback", "window", "rule", "trade", "trading")


def relevance(chunk: str) -> int:
    low = chunk.lower()
    return sum(low.count(k) for k in KEYWORDS)


def select_chunks(chunks: list[str], k: int) -> list[str]:
    """Send the LLM only the k most strategy-relevant chunks, in original order. References,
    acknowledgements and boilerplate score ~0 and cost nothing."""
    if len(chunks) <= k:
        return [c for c in chunks if relevance(c) > 0] or chunks[:1]
    top = sorted(range(len(chunks)), key=lambda i: -relevance(chunks[i]))[:k]
    return [chunks[i] for i in sorted(top) if relevance(chunks[i]) > 0]


def _catalogue() -> str:
    lines = []
    for name, t in TEMPLATES.items():
        if t.control:
            continue
        params = ", ".join(f"{p} ({typ.__name__}, {lo}..{hi})" for p, (typ, lo, hi) in t.params.items())
        lines.append(f'- "{name}": {t.doc}. parameters: {params}')
    return "\n".join(lines)


def system_prompt(language: str = "Italian") -> str:
    """Researcher prompt from agents/prompts/researcher.md (edit that file to change behaviour)."""
    return load_prompt("researcher", TEMPLATE_CATALOGUE=_catalogue(), TIMEFRAMES=str(list(TIMEFRAMES)), LANG=language)


CHUNK_CHARS = 20000     # tokens are plentiful, calls are not: fewer, bigger requests


def propose_specs(llm, text: str, source: str = "", max_chunks: int = 6, language: str = "Italian") -> tuple[list[StrategySpec], list[dict]]:
    """Returns (valid specs, rejected proposals with reasons). `llm` needs .chat(messages)."""
    specs, rejected, seen = [], [], set()
    for n, chunk in enumerate(select_chunks(chunk_text(text, max_chars=CHUNK_CHARS), max_chunks)):
        msgs = [{"role": "system", "content": system_prompt(language)},
                {"role": "user", "content": f"Source: {source}\n\nTEXT (part {n + 1}):\n{chunk}"}]
        try:
            data = parse_json(llm.chat(msgs, temperature=0.2))
            proposals = data.get("strategies", [])
        except Exception as e:                      # bad JSON from a weak model is expected
            rejected.append({"chunk": n, "reason": f"unparseable reply: {e}"})
            continue
        for p in proposals if isinstance(proposals, list) else []:
            try:
                p = {**p, "source": p.get("source") or source}
                spec = from_dict(p)
            except (SpecError, TypeError, AttributeError) as e:
                rejected.append({"proposal": p, "reason": str(e)})
                continue
            k = (spec.template, json.dumps(spec.ranges, sort_keys=True), tuple(spec.timeframes))
            if k not in seen:
                seen.add(k)
                specs.append(spec)
    return specs, rejected
