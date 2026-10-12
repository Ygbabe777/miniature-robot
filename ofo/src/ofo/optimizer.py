"""Parameter study: sweep hundreds of combinations, then ask whether the strategy is
ROBUST, not whether some combination looks great.

The best of N combinations is biased upward by construction. What separates an edge from
a lucky spike is the SHAPE of the performance surface: an edge works across a broad
region of parameters (a plateau), a fluke is an isolated peak surrounded by losers."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .backtest import Contract
from .data import resample
from .judge import REJECTED, judge
from .prop import PropRules
from .registry import TrialRegistry
from .research import daily_series, walk_forward
from .specs import StrategySpec
from .stats import sharpe
from .strategies import TEMPLATES, TIMEFRAMES


@dataclass
class SurfaceThresholds:
    min_median_sharpe: float = 0.0      # the typical combination must make money after costs
    min_frac_positive: float = 0.55
    min_plateau_ratio: float = 0.6      # neighbours of the best must keep >=60% of its Sharpe
    min_neighbours_positive: float = 0.75


def surface_metrics(rows: list[dict], param_names: list[str], th: SurfaceThresholds | None = None) -> dict:
    """rows: [{params..., 'tf': str, 'sharpe': float}]. Neighbours differ by one grid step
    on exactly one axis (parameter or timeframe)."""
    th = th or SurfaceThresholds()
    axes = {n: sorted({r[n] for r in rows}) for n in param_names}
    axes["tf"] = [t for t in TIMEFRAMES if t in {r["tf"] for r in rows}]
    names = list(axes)
    key = lambda r: tuple(axes[n].index(r[n]) for n in names)
    by_key = {key(r): r["sharpe"] for r in rows}
    sr = np.array([r["sharpe"] for r in rows])
    best_i = int(np.argmax(sr))
    bk = key(rows[best_i])
    nb = []
    for ax in range(len(names)):
        for d in (-1, 1):
            k = list(bk); k[ax] += d
            if tuple(k) in by_key:
                nb.append(by_key[tuple(k)])
    best = float(sr[best_i])
    plateau = float(np.mean(nb) / best) if nb and best > 0 else 0.0
    nb_pos = float(np.mean([v > 0 for v in nb])) if nb else 0.0
    m = {"n_combos": len(rows), "best_sharpe": best, "best": {n: rows[best_i][n] for n in names},
         "median_sharpe": float(np.median(sr)), "frac_positive": float((sr > 0).mean()),
         "plateau_ratio": plateau, "neighbours_positive": nb_pos, "n_neighbours": len(nb)}
    m["checks"] = {
        "median_sharpe": bool(m["median_sharpe"] > th.min_median_sharpe),
        "frac_positive": bool(m["frac_positive"] >= th.min_frac_positive),
        "plateau": bool(plateau >= th.min_plateau_ratio),
        "neighbours_positive": bool(nb_pos >= th.min_neighbours_positive),
    }
    m["robust"] = all(m["checks"].values())
    return m


def detrend(bars: pd.DataFrame) -> pd.DataFrame:
    """Remove the average per-bar drift from every price. A strategy whose profit was just
    being long in a rising market (beta) loses it here; a real edge survives."""
    n = len(bars)
    shift = (bars["close"].iloc[-1] - bars["close"].iloc[0]) / max(n - 1, 1) * np.arange(n)
    out = bars.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = bars[c] - shift
    return out


def _sweep(bars, spec, contract, contracts, slippage_ticks):
    t = TEMPLATES[spec.template]
    cands = [{"family": spec.name, "fn": t.fn, "params": p, "tf": tf}
             for p in spec.combos() for tf in spec.timeframes]
    bars_by_tf = {tf: resample(bars, tf) for tf in spec.timeframes}
    series = {i: daily_series(bars_by_tf, c, contract, contracts, slippage_ticks)
              for i, c in enumerate(cands)}
    return cands, series


def run_study(bars: pd.DataFrame, spec: StrategySpec, contract: Contract, rules: PropRules,
              registry: TrialRegistry, contracts: int = 2, slippage_ticks: float = 1.0,
              oos_fraction: float = 0.25, train_days: int = 120, test_days: int = 20,
              surface_th: SurfaceThresholds | None = None) -> dict:
    """Sweep every combination of `spec`, judge the surface, walk-forward, and only if
    both survive take ONE look at the locked block."""
    cands, series = _sweep(bars, spec, contract, contracts, slippage_ticks)
    sessions = sorted(set().union(*[set(s.index) for s in series.values()]))
    cut = sessions[int(len(sessions) * (1 - oos_fraction))]
    dev_days = [d for d in sessions if d < cut]
    dev = {i: s[s.index < cut] for i, s in series.items()}
    registry.add_trials(len(cands), f"study:{spec.name}")

    rows = [{**c["params"], "tf": c["tf"],
             "sharpe": sharpe(dev[i].reindex(dev_days).fillna(0.0))} for i, c in enumerate(cands)]
    surf = surface_metrics(rows, list(spec.ranges), surface_th)
    wf, _ = walk_forward(dev, train_days, test_days)
    wf_pnl = float(wf.sum()) if len(wf) else 0.0

    report = {"name": spec.name, "template": spec.template, "rationale": spec.rationale,
              "source": spec.source, "n_candidates": len(cands), "surface": surf,
              "wf_oos_days": len(wf), "wf_oos_pnl": wf_pnl,
              "wf_oos_sharpe_daily": sharpe(wf) if len(wf) > 2 else 0.0,
              "n_locked_days": len(sessions) - len(dev_days), "wf_oos_series": wf}
    # Beta check: only for surfaces that already look robust (it re-runs the dev sweep).
    surf_d = None
    if surf["robust"] and wf_pnl > 0:
        key = pd.to_datetime(bars["session"]) if "session" in bars.columns else bars.index.normalize()
        dev_bars = bars[np.asarray(key < cut)]
        cands_d, series_d = _sweep(detrend(dev_bars), spec, contract, contracts, slippage_ticks)
        rows_d = [{**c["params"], "tf": c["tf"],
                   "sharpe": sharpe(series_d[i].reindex(dev_days).fillna(0.0))} for i, c in enumerate(cands_d)]
        surf_d = surface_metrics(rows_d, list(spec.ranges), surface_th)
    report["surface_detrended"] = surf_d
    if surf["robust"] and wf_pnl > 0 and surf_d["robust"]:
        best_i = max(range(len(cands)), key=lambda i: rows[i]["sharpe"])
        locked_days = [d for d in sessions if d >= cut]
        locked = series[best_i].reindex(locked_days).fillna(0.0)
        var_sr = float(np.var([r["sharpe"] for r in rows], ddof=1))
        v = judge(locked.values, rules, registry.n_trials + 1, var_sr)
        registry.add_locked_look(f"{spec.name}:{cands[best_i]['params']}@{cands[best_i]['tf']}", v.status)
        report["locked"] = {"status": v.status, "checks": v.checks, "pnl": float(locked.sum()),
                            "p_pass": v.mc.get("p_pass"), "p_pass_ci95": v.mc.get("p_pass_ci95"),
                            "p_bust": v.mc.get("p_bust"), "params": cands[best_i]["params"],
                            "tf": cands[best_i]["tf"]}
    else:
        why = [k for k, ok in surf["checks"].items() if not ok]
        if wf_pnl <= 0:
            why.append("walk_forward_pnl")
        if surf_d is not None and not surf_d["robust"]:
            why.append("drift_only: edge disappears once the market trend is removed")
        report["locked"] = {"status": REJECTED, "reason": f"failed {why}; locked block not touched"}
    return report
