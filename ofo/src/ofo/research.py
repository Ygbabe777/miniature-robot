"""Research cycle: candidates -> walk-forward -> one look at the locked set -> verdict."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import Contract, daily_pnl, run_backtest
from .data import resample
from .judge import REJECTED, judge
from .prop import PropRules
from .registry import TrialRegistry
from .stats import sharpe
from .strategies import GRIDS, TIMEFRAMES, flatten_overnight, random_control


def build_candidates(include_control: bool = True, seeds=(1, 2, 3)):
    cands = []
    for fam, (fn, grid) in GRIDS.items():
        for params in grid:
            for tf in TIMEFRAMES:
                cands.append({"family": fam, "fn": fn, "params": params, "tf": tf})
    if include_control:
        for sd in seeds:
            for tf in TIMEFRAMES:
                cands.append({"family": "random_control", "fn": random_control,
                              "params": {"seed": sd}, "tf": tf})
    return cands


def daily_series(bars_by_tf: dict, cand: dict, contract: Contract, contracts: int,
                 slippage_ticks: float) -> pd.Series:
    b = bars_by_tf[cand["tf"]]
    pos = flatten_overnight(cand["fn"](b, **cand["params"]))
    return daily_pnl(run_backtest(b, pos, contract, contracts, slippage_ticks))


def walk_forward(series: dict, train_days: int, test_days: int, min_active: int = 20):
    """series: id -> daily pnl indexed by session. For each window pick the id with the
    best TRAIN Sharpe and keep its TEST pnl. Returns (stitched OOS pnl, picks)."""
    days = sorted(set().union(*[set(s.index) for s in series.values()]))
    out, picks, i = [], [], train_days
    while i + test_days <= len(days):
        tr, te = days[i - train_days:i], days[i:i + test_days]
        best, best_sr = None, -np.inf
        for cid, s in series.items():
            x = s.reindex(tr).fillna(0.0)
            if (x != 0).sum() < min_active:
                continue
            sr = sharpe(x)
            if sr > best_sr:
                best, best_sr = cid, sr
        if best is not None:
            out.append(series[best].reindex(te).fillna(0.0))
            picks.append(best)
        i += test_days
    return (pd.concat(out) if out else pd.Series(dtype=float)), picks


def run_cycle(bars: pd.DataFrame, contract: Contract, rules: PropRules, registry: TrialRegistry,
              contracts: int = 2, slippage_ticks: float = 1.0, oos_fraction: float = 0.25,
              train_days: int = 120, test_days: int = 20, candidates=None) -> dict:
    cands = candidates or build_candidates()
    bars_by_tf = {tf: resample(bars, tf) for tf in {c["tf"] for c in cands}}
    series = {i: daily_series(bars_by_tf, c, contract, contracts, slippage_ticks)
              for i, c in enumerate(cands)}
    sessions = sorted(set().union(*[set(s.index) for s in series.values()]))
    cut = sessions[int(len(sessions) * (1 - oos_fraction))]
    dev = {i: s[s.index < cut] for i, s in series.items()}
    registry.add_trials(len(cands), "cycle candidates")

    dev_sr = {i: sharpe(s.reindex([d for d in sessions if d < cut]).fillna(0.0)) for i, s in dev.items()}
    var_sr = float(np.var(list(dev_sr.values()), ddof=1))
    wf_pnl, picks = walk_forward(dev, train_days, test_days)

    report = {"n_candidates": len(cands), "n_dev_days": int((np.array(sessions) < cut).sum()),
              "n_locked_days": int((np.array(sessions) >= cut).sum()),
              "wf_oos_days": len(wf_pnl), "wf_oos_pnl": float(wf_pnl.sum()) if len(wf_pnl) else 0.0,
              "wf_oos_sharpe_daily": sharpe(wf_pnl) if len(wf_pnl) > 2 else 0.0,
              "families": {}}
    for fam in dict.fromkeys(c["family"] for c in cands):
        ids = [i for i, c in enumerate(cands) if c["family"] == fam]
        best = max(ids, key=lambda i: dev_sr[i])
        entry = {"best_params": cands[best]["params"], "tf": cands[best]["tf"],
                 "dev_sharpe_daily": dev_sr[best], "locked": None}
        if dev_sr[best] > 0 and report["wf_oos_pnl"] > 0:
            locked = series[best][series[best].index >= cut].reindex(
                [d for d in sessions if d >= cut]).fillna(0.0)
            v = judge(locked.values, rules, registry.n_trials + 1, var_sr)
            registry.add_locked_look(f"{fam}:{cands[best]['params']}@{cands[best]['tf']}", v.status)
            entry["locked"] = {"status": v.status, "checks": v.checks,
                               "p_pass": v.mc.get("p_pass"), "p_pass_ci95": v.mc.get("p_pass_ci95"),
                               "p_bust": v.mc.get("p_bust"), "pnl": float(locked.sum())}
        else:
            entry["locked"] = {"status": REJECTED, "reason": "failed dev / walk-forward screen; locked set not touched"}
        report["families"][fam] = entry
    return report
