"""Usage: python scripts/ingest_paper.py PAPER.pdf [OUT.json]
Needs FreeLLMAPI (or any OpenAI-compatible endpoint) running and:
  OFO_LLM_BASE_URL (e.g. http://localhost:3001/v1)   OFO_LLM_API_KEY   [OFO_LLM_MODEL]
Reads the PDF, asks the LLM for strategy specs, validates them, writes specs/<name>.json."""
import dataclasses
import json
import sys
from pathlib import Path

from ofo.llm.client import LLMClient
from ofo.papers import extract_pdf_text, propose_specs

pdf = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("specs") / f"{pdf.stem}.json"
text = extract_pdf_text(str(pdf))
print(f"{len(text)} characters extracted from {pdf.name}")
specs, rejected = propose_specs(LLMClient(), text, source=pdf.name)
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps({"strategies": [dataclasses.asdict(s) for s in specs],
                           "rejected": rejected}, indent=1))
print(f"{len(specs)} valid specs, {len(rejected)} rejected -> {out}")
for s in specs:
    print(f"  {s.name}: {s.template}, {s.n_candidates} candidates | {s.rationale[:100]}")
for r in rejected:
    print("  REJECTED:", r["reason"])
