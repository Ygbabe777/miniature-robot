"""Which size to trade? Answered per account (25k and 50k) from the strategy's daily P&L.

An honest remark first: profit / max-drawdown is the SAME at every size (both scale linearly with the number of contracts),
so that ratio cannot choose a size by itself. What a size changes is how much of the account's drawdown allowance the
strategy's worst stretch eats. So the recommendation is the LARGEST size whose historical max drawdown stays within
`DD_HEADROOM` of the account's allowance (and whose worst day stays within `DAY_HEADROOM`), because a larger size earns
proportionally more at the same profit/drawdown ratio. The table shows, for every size: profit, max drawdown, the ratio,
how much of the allowance the drawdown uses, the worst day, and the chance to pass / bust within the evaluation window.

Basis: daily P&L (intraday drawdown inside a day is not visible here: the intraday simulator checks the final candidate)."""
from __future__ import annotations

import numpy as np

from .prop import PropRules, _resample_blocks, simulate_account
from .timing import SIZES

DD_HEADROOM = 0.60          # historical max drawdown may use at most this share of the account's drawdown allowance
DAY_HEADROOM = 0.40         # ... and the worst day this share of it


def max_drawdown(daily) -> float:
    c = np.cumsum(np.asarray(daily, dtype=float))
    return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], c]))[1:] - c)) if len(c) else 0.0


def size_study(pnl, accounts: dict[str, PropRules], base_contracts: int, window: int = 5, n_sims: int = 400,
               block: int = 3, seed: int = 3, basis: str = "development") -> dict:
    x0 = np.asarray(pnl, dtype=float)
    out = {"basis": basis, "in_sample": basis == "development", "window_days": window, "dd_headroom": DD_HEADROOM,
           "day_headroom": DAY_HEADROOM, "ratio_note": "profit/drawdown is identical at every size", "accounts": {}}
    for acct, rules in accounts.items():
        rows = []
        for n in SIZES:
            x = x0 * (n / base_contracts)
            dd, net = max_drawdown(x), float(x.sum())
            worst = float(-x.min()) if len(x) and x.min() < 0 else 0.0
            rng = np.random.default_rng(seed)
            passed = busted = 0
            for _ in range(n_sims):
                o = simulate_account(_resample_blocks(x, window, block, rng), rules)
                passed += o.status == "passed"; busted += o.status == "busted"
            rows.append({"micro": n, "net_pnl": net, "max_dd": dd, "profit_dd": (net / dd) if dd > 0 else None,
                         "dd_use": dd / rules.max_drawdown, "worst_day": worst, "day_use": worst / rules.max_drawdown,
                         "p_pass_window": passed / n_sims, "p_bust_window": busted / n_sims,
                         "within_limits": bool(dd <= DD_HEADROOM * rules.max_drawdown and worst <= DAY_HEADROOM * rules.max_drawdown)})
        ok = [r for r in rows if r["within_limits"]]
        rec = max(ok, key=lambda r: r["micro"]) if ok else None
        out["accounts"][acct] = {"table": rows, "recommended": rec["micro"] if rec else None,
                                 "allowance": rules.max_drawdown, "target": rules.profit_target,
                                 "note": None if rec else "even 1 micro uses too much of the drawdown allowance"}
    return out
