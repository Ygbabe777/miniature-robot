"""Usage: python scripts/account_ev.py OOS_PNL.csv [rules/firms.json] [base_contracts]
Compares the plans in firms.json for ONE strategy's out-of-sample daily P&L, scanning
position sizes. Plans with missing fees are skipped and the missing fields are listed."""
import sys

import pandas as pd

from ofo.funded import best_size, load_plans

pnl = pd.read_csv(sys.argv[1], index_col=0)["pnl"].values
path = sys.argv[2] if len(sys.argv) > 2 else "rules/firms.json"
base = int(sys.argv[3]) if len(sys.argv) > 3 else 2
print(f"{len(pnl)} OOS days, base size {base} contracts\n")
for p in load_plans(path):
    tag = "" if p.verified else " [UNVERIFIED rules]"
    if p.missing():
        print(f"{p.firm} {p.plan}{tag}: skipped, fill in {p.missing()}")
        continue
    rows = best_size(pnl, p, base)
    print(f"{p.firm} {p.plan}{tag}")
    for r in rows[:3]:
        lo, hi = r["ev_ci95"]
        print(f"  {r['contracts']:>5.1f} ct: EV ${r['ev']:>7,.0f} (95%: {lo:,.0f}..{hi:,.0f}) | "
              f"P(pass) {r['p_pass']:.0%} | P(payout) {r['p_payout']:.0%} | P(EV>0) {r['p_ev_positive']:.0%}")
