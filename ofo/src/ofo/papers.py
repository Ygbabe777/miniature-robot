"""Research papers (PDF or text) -> validated strategy specs, via any OpenAI-compatible LLM.

The LLM may only choose registered templates and numeric ranges. Everything it returns is
validated; invalid proposals are kept in a `rejected` list with the reason, never executed."""
from __future__ import annotations

import json

from .agents.base import load_prompt, parse_json
from .llm.client import LLMError
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
        fam = f" [family {t.family}]" if t.family else ""
        lines.append(f'- "{name}": {t.doc}{fam}. parameters: {params}')
    return "\n".join(lines)


def system_prompt(language: str = "Italian") -> str:
    """Researcher prompt from agents/prompts/researcher.md (edit that file to change behaviour)."""
    return load_prompt("researcher", TEMPLATE_CATALOGUE=_catalogue(), TIMEFRAMES=str(list(TIMEFRAMES)), LANG=language)


CHUNK_CHARS = 20000     # tokens are plentiful, calls are not: fewer, bigger requests


def propose_specs(llm, text: str, source: str = "", max_chunks: int = 6, language: str = "Italian",
                  memory: str = "", info: dict | None = None) -> tuple[list[StrategySpec], list[dict]]:
    """Returns (valid specs, rejected proposals with reasons). `llm` needs .chat(messages).
    memory: what the lab already learned (knowledge.memory_text). info: filled with the concepts the model named."""
    specs, rejected, seen = [], [], set()
    if info is not None:
        info.setdefault("concepts", [])
    for n, chunk in enumerate(select_chunks(chunk_text(text, max_chars=CHUNK_CHARS), max_chunks)):
        msgs = [{"role": "system", "content": system_prompt(language)},
                {"role": "user", "content": (memory + "\n\n" if memory else "") + f"Source: {source}\n\nTEXT (part {n + 1}):\n{chunk}"}]
        for attempt in (1, 2):                      # attempt 2 = ONE repair round when proposals were refused for fixable reasons
            try:
                reply = llm.chat(msgs, temperature=0.2)         # an LLM that is down, blocked by the budget or refusing the key RAISES:
            except LLMError:                                    # the paper must be retried later, not recorded as "read, nothing found"
                raise
            try:
                data = parse_json(reply)
                proposals = data.get("strategies", [])
                if info is not None:
                    from .knowledge import clean_concepts
                    info["concepts"] += [c for c in clean_concepts(data.get("concepts")) if c not in info["concepts"]]
            except Exception as e:                      # bad JSON from a weak model is expected
                rejected.append({"chunk": n, "reason": f"unparseable reply: {e}", "kind": "unparseable"})
                break
            fixable = []
            for p in proposals if isinstance(proposals, list) else []:
                try:
                    p = {**p, "source": p.get("source") or source}
                    # attempt 1 is strict (to get the repair round); attempt 2 accepts an incomplete card and flags it: a refused proposal
                    # produces nothing, while the statistical gates decide the strategy's fate anyway
                    spec = from_dict(p, require_card=True if attempt == 1 else "lenient")
                except (SpecError, TypeError, AttributeError) as e:
                    if attempt == 1 and isinstance(e, SpecError) and "hypothesis" in str(e):
                        fixable.append((p, str(e)))             # the model gets ONE chance to fix what the validator refused
                    else:
                        rejected.append({"proposal": p, "reason": str(e)})
                    continue
                k = (spec.template, json.dumps(spec.ranges, sort_keys=True), tuple(spec.timeframes))
                if k not in seen:
                    seen.add(k)
                    specs.append(spec)
            if not fixable:
                break
            msgs = msgs + [{"role": "assistant", "content": reply},
                           {"role": "user", "content": "These proposals were REJECTED by the validator, fix exactly what is listed and reply again with ONLY "
                            "the corrected JSON object in the same format (keep the valid ones out, never invent numbers):\n"
                            + "\n".join(f"- {p.get('name', '?')}: {why}" for p, why in fixable)}]
    return specs, rejected
