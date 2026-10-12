"""Usage: python scripts/study.py DATA.csv.gz SPECS.json [50k|25k] [MNQ|NQ] [contracts]
Example: python scripts/study.py data/NQ_1min.csv.gz specs/starter.json 50k MNQ 2
Sweeps every parameter combination of each spec, judges the robustness of the whole
surface, and only for robust ones takes one look at the locked block."""
import json
import sys
from pathlib import Path

import pandas as pd

from ofo.backtest import MNQ, NQ
from ofo.optimizer import run_study
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.specs import from_dict

data, specs_path = sys.argv[1], sys.argv[2]
rules = PRESETS[sys.argv[3] if len(sys.argv) > 3 else "50k"]
contract = {"MNQ": MNQ, "NQ": NQ}[sys.argv[4] if len(sys.argv) > 4 else "MNQ"]
n = int(sys.argv[5]) if len(sys.argv) > 5 else 2
bars = pd.read_csv(data, index_col=0, parse_dates=True)
reg = TrialRegistry("data/registry.json")
Path("reports").mkdir(exist_ok=True)
for raw in json.loads(Path(specs_path).read_text())["strategies"]:
    spec = from_dict(raw)
    print(f"\n=== {spec.name}: {spec.n_candidates} candidates ===", flush=True)
    rep = run_study(bars, spec, contract, rules, reg, contracts=n)
    wf = rep.pop("wf_oos_series")
    slug = "".join(c if c.isalnum() else "_" for c in spec.name)
    Path(f"reports/study_{slug}.json").write_text(json.dumps(rep, indent=1, default=str))
    wf.to_csv(f"reports/oos_pnl_{slug}.csv", header=["pnl"])
    s = rep["surface"]
    print(f"  surface: median Sharpe {s['median_sharpe']:+.3f} | {s['frac_positive']:.0%} of combos positive | "
          f"best {s['best_sharpe']:+.3f} | plateau {s['plateau_ratio']:.2f} | robust: {s['robust']}")
    print(f"  failed checks: {[k for k, ok in s['checks'].items() if not ok]}")
    print(f"  walk-forward OOS: {rep['wf_oos_days']} days, pnl ${rep['wf_oos_pnl']:,.0f}")
    print(f"  verdict: {rep['locked']['status']}  {rep['locked'].get('reason', '')}", flush=True)
print(f"\nregistry: {reg.n_trials} trials/looks so far")
