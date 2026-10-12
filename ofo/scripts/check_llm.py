"""Usage: python scripts/check_llm.py
Checks that your LLM endpoint works and can follow the strict-JSON format the agents need.
Needs:  OFO_LLM_BASE_URL  (e.g. http://localhost:3001/v1)   OFO_LLM_API_KEY   [OFO_LLM_MODEL]"""
import time

from ofo.agents.base import parse_json
from ofo.llm.client import LLMClient, LLMError
from ofo.specs import from_dict

llm = LLMClient()
print(f"endpoint: {llm.base_url} | model: {llm.model}")
prompt = ('Reply with ONE JSON object and nothing else, no markdown: {"strategies":[{"name":"x",'
          '"template":"orb","ranges":{"or_minutes":{"min":10,"max":60,"step":10}},'
          '"rationale":"MECCANISMO ... EVIDENZA ... QUANDO NON FUNZIONA ..."}]}')
ok = 0
for i in range(3):
    t = time.time()
    try:
        reply = llm.chat([{"role": "user", "content": prompt}], temperature=0.2)
        spec = from_dict(parse_json(reply)["strategies"][0])
        ok += 1
        print(f"  try {i + 1}: OK in {time.time() - t:.1f}s ({spec.n_candidates} candidates)")
    except LLMError as e:
        print(f"  try {i + 1}: ENDPOINT ERROR: {e}")
    except Exception as e:
        print(f"  try {i + 1}: bad format ({e}); reply starts: {reply[:120]!r}")
print(f"\n{ok}/3 valid. " + ("Good enough: the agents retry on format errors." if ok >= 2
                              else "Too unreliable for the agents: try another model."))
