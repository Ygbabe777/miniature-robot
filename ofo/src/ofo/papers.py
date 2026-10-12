"""Research papers (PDF or text) -> validated strategy specs, via any OpenAI-compatible LLM.

The LLM may only choose registered templates and numeric ranges. Everything it returns is
validated; invalid proposals are kept in a `rejected` list with the reason, never executed."""
from __future__ import annotations

import json

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


def _catalogue() -> str:
    lines = []
    for name, t in TEMPLATES.items():
        if t.control:
            continue
        params = ", ".join(f"{p} ({typ.__name__}, {lo}..{hi})" for p, (typ, lo, hi) in t.params.items())
        lines.append(f'- "{name}": {t.doc}. parameters: {params}')
    return "\n".join(lines)


SYSTEM = f"""You turn trading research into testable strategy specifications.
You may ONLY use these templates:
{_catalogue()}

Reply with a single JSON object and nothing else:
{{"strategies":[{{"name":"short name","template":"<one of the templates>",
 "ranges":{{"<param>":{{"min":..,"max":..,"step":..}} or {{"values":[..]}} for EVERY parameter}},
 "timeframes":[subset of {list(TIMEFRAMES)}],
 "rationale":"WHY this should have an edge, quoting what the text claims",
 "source":"title/section of the text"}}]}}

Rules: propose only what the text actually supports; never invent results or numbers the
text does not contain; if nothing in the text maps to a template, reply {{"strategies":[]}}.
Sweep REASONABLE ranges around values the text mentions, not a single point."""


def parse_json(reply: str) -> dict:
    a, b = reply.find("{"), reply.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in reply")
    return json.loads(reply[a:b + 1])


def propose_specs(llm, text: str, source: str = "", max_chunks: int = 6) -> tuple[list[StrategySpec], list[dict]]:
    """Returns (valid specs, rejected proposals with reasons). `llm` needs .chat(messages)."""
    specs, rejected, seen = [], [], set()
    for n, chunk in enumerate(chunk_text(text)[:max_chunks]):
        msgs = [{"role": "system", "content": SYSTEM},
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
