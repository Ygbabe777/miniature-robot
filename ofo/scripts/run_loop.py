"""Usage: python scripts/run_loop.py DATA.csv.gz [50k|25k] [MNQ|NQ] [contracts] [--once]
Reads new PDFs / .txt / .md dropped into ideas/, runs the agents, and keeps watching.
Needs the LLM endpoint:  OFO_LLM_BASE_URL  OFO_LLM_API_KEY  [OFO_LLM_MODEL]
Progress is appended to data/events.jsonl (what the 3D Lab will display)."""
import sys

import pandas as pd

from ofo.agents.loop import ResearchLoop
from ofo.backtest import MNQ, NQ
from ofo.events import EventLog
from ofo.llm.client import LLMClient
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.specs import from_dict

args = [a for a in sys.argv[1:] if not a.startswith("--")]
once = "--once" in sys.argv
bars = pd.read_csv(args[0], index_col=0, parse_dates=True)
rules = PRESETS[args[1] if len(args) > 1 else "50k"]
contract = {"MNQ": MNQ, "NQ": NQ}[args[2] if len(args) > 2 else "MNQ"]
n = int(args[3]) if len(args) > 3 else 2
loop = ResearchLoop(bars, contract, rules, TrialRegistry("data/registry.json"), LLMClient(),
                    EventLog("data/events.jsonl"), "data/loop", contracts=n)
if once:
    for r in loop.run_once("ideas"):
        print(r["spec"], "->", r["verdict"], "| planner:", r["decision"])
else:
    print("watching ideas/ ... (Ctrl+C to stop)")
    loop.run_forever("ideas")
