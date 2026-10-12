"""Usage: python scripts/research_cycle.py DATA_CSV_GZ [50k|25k] [MNQ|NQ] [contracts]
Example: python scripts/research_cycle.py data/NQ_1min.csv.gz 50k MNQ 2
Writes reports/cycle.json and prints a summary.
"""
import json
import sys
from pathlib import Path

import pandas as pd

from ofo.backtest import MNQ, NQ
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.research import run_cycle

path = sys.argv[1]
rules = PRESETS[sys.argv[2] if len(sys.argv) > 2 else "50k"]
contract = {"MNQ": MNQ, "NQ": NQ}[sys.argv[3] if len(sys.argv) > 3 else "MNQ"]
n = int(sys.argv[4]) if len(sys.argv) > 4 else 2
bars = pd.read_csv(path, index_col=0, parse_dates=True)
rep = run_cycle(bars, contract, rules, TrialRegistry("data/registry.json"), contracts=n)
Path("reports").mkdir(exist_ok=True)
Path("reports/cycle.json").write_text(json.dumps(rep, indent=1, default=str))
print(json.dumps(rep, indent=1, default=str))
