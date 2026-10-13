"""Parameter study: sweep hundreds of combinations, then ask whether the strategy is
ROBUST, not whether some combination looks great.

The best of N combinations is biased upward by construction. What separates an edge from
a lucky spike is the SHAPE of the performance surface: an edge works across a broad
region of parameters (a plateau), a fluke is an isolated peak surrounded by losers."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .backtest import Contract, daily_pnl
from .data import resample
from .exits import exit_grid, summarize_info
from .judge import REJECTED, judge
from .ledger import AccountingError, reconcile, run_ledger, run_ledger_next_open
from .prop import PRESETS, PropRules
from .prop_intraday import arrays_from_ledger, intraday_report
from .registry import DuplicateExperiment, TrialRegistry
from .research import daily_series, walk_forward
from .specs import StrategySpec
from .stats import sharpe
from .timing import timing_report
from .strategies import TEMPLATES, TIMEFRAMES, flatten_overnight

ENGINE_VERSION = "2"      # bump when the study methodology changes: old experiments stay in the history, re-tests are allowed


@dataclass
class SurfaceThresholds:
    min_median_sharpe: float = 0.0      # the typical combination must make money after costs
    min_frac_positive: float = 0.55
    min_plateau_ratio: float = 0.6      # PARAMETER neighbours of the best must keep >=60% of its Sharpe
    min_neighbours_positive: float = 0.75
    min_tf_positive: float = 0.5        # share of timeframes whose median Sharpe is positive (when several are tested)
    min_trades: int = 100               # development trades of the final candidate
    max_cost_multiple_sharpe: float = 0.0   # Sharpe must stay above this at twice the costs and with next-open fills


def surface_metrics(rows: list[dict], param_names: list[str], th: SurfaceThresholds | None = None) -> dict:
    """rows: [{params..., 'tf': str, 'sharpe': float}].

    PARAMETER robustness: the neighbours of the best combination are the ones that differ by one grid step on exactly
    one parameter AT THE SAME TIMEFRAME. TIMEFRAME robustness is judged separately: the median Sharpe at each
    timeframe, and how many timeframes are positive. Mixing the two hid a strategy that works at one timeframe only."""
    th = th or SurfaceThresholds()
    axes = {n: sorted({r[n] for r in rows}) for n in param_names}
    tfs = [t for t in TIMEFRAMES if t in {r["tf"] for r in rows}]
    key = lambda r: tuple(axes[n].index(r[n]) for n in param_names)
    sr = np.array([r["sharpe"] for r in rows])
    best_i = int(np.argmax(sr))
    best_tf = rows[best_i]["tf"]
    by_key = {key(r): r["sharpe"] for r in rows if r["tf"] == best_tf}
    bk = key(rows[best_i])
    nb = []
    for ax in range(len(param_names)):
        for d in (-1, 1):
            k = list(bk); k[ax] += d
            if tuple(k) in by_key:
                nb.append(by_key[tuple(k)])
    best = float(sr[best_i])
    plateau = float(np.mean(nb) / best) if nb and best > 0 else 0.0
    nb_pos = float(np.mean([v > 0 for v in nb])) if nb else 0.0
    by_tf = {t: {"median_sharpe": float(np.median([r["sharpe"] for r in rows if r["tf"] == t])),
                 "frac_positive": float(np.mean([r["sharpe"] > 0 for r in rows if r["tf"] == t]))} for t in tfs}
    tf_pos = float(np.mean([v["median_sharpe"] > 0 for v in by_tf.values()])) if by_tf else 0.0
    m = {"n_combos": len(rows), "best_sharpe": best, "best": {**{n: rows[best_i][n] for n in param_names}, "tf": best_tf},
         "median_sharpe": float(np.median(sr)), "frac_positive": float((sr > 0).mean()),
         "plateau_ratio": plateau, "neighbours_positive": nb_pos, "n_neighbours": len(nb),
         "by_tf": by_tf, "tf_positive_frac": tf_pos}
    m["checks"] = {
        "median_sharpe": bool(m["median_sharpe"] > th.min_median_sharpe),
        "frac_positive": bool(m["frac_positive"] >= th.min_frac_positive),
        "plateau": bool(plateau >= th.min_plateau_ratio),
        "neighbours_positive": bool(nb_pos >= th.min_neighbours_positive),
        "tf_consistency": bool(len(tfs) < 2 or tf_pos >= th.min_tf_positive),
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


MIN_EXIT_GAIN = 0.03        # daily-Sharpe improvement needed before a stop/target replaces the strategy's own exit


def _session_key(bars: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(bars["session"]) if "session" in bars.columns else pd.Series(bars.index.normalize(), index=bars.index)


def dataset_fingerprint(bars: pd.DataFrame) -> str:
    """Identity of the exact data a study ran on: size, span and a hash of every close and timestamp."""
    h = hashlib.sha256()
    h.update(str(len(bars)).encode()); h.update(str(bars.index[0]).encode()); h.update(str(bars.index[-1]).encode())
    h.update(np.ascontiguousarray(bars["close"].to_numpy(dtype="float64")).tobytes())
    h.update(np.ascontiguousarray(bars.index.asi8).tobytes())
    return h.hexdigest()[:16]


def spec_fingerprint(spec: StrategySpec, contract: Contract, contracts: int, slippage_ticks: float, oos_fraction: float,
                     train_days: int, test_days: int) -> str:
    payload = json.dumps([spec.template, spec.ranges, sorted(spec.timeframes), contract.symbol, contracts, slippage_ticks,
                          oos_fraction, train_days, test_days, ENGINE_VERSION], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _exit_study(dev_bars_tf, cand, base_series, contract, contracts, slippage_ticks, dev_days, registry, name, experiment_id):
    """Which stop distance and reward:risk fit the best candidate? Judged on DEVELOPMENT data only (the bars handed in
    contain nothing else, trade statistics included) and counted in the trial registry (every cell is a trial)."""
    grid = exit_grid()
    base_dev = base_series.reindex(dev_days).fillna(0.0)
    base = {"sharpe": sharpe(base_dev), "pnl": float(base_dev.sum())}
    cells = []
    for g in grid:
        s, info = daily_series(dev_bars_tf, cand, contract, contracts, slippage_ticks, exit=g, with_info=True)
        d = s.reindex(dev_days).fillna(0.0)
        cells.append({**g, "sharpe": sharpe(d), "pnl": float(d.sum()), **summarize_info(info)})
    registry.add_trials(len(grid), f"exits:{name}", experiment_id=experiment_id, kind="exits")
    best = max(cells, key=lambda c: c["sharpe"])
    chosen = best["sharpe"] >= base["sharpe"] + MIN_EXIT_GAIN and best["pnl"] > 0
    better = float(np.mean([c["sharpe"] >= base["sharpe"] for c in cells]))
    return {"baseline": base, "grid": cells, "best": {k: best[k] for k in ("stop_atr", "rr")},
            "best_stats": best, "chosen": bool(chosen), "frac_better_than_baseline": better,
            "n_trials": len(grid), "atr_n": 14}


def nested_walk_forward(bars_by_tf, cands, series, dev_days, contract, contracts, slippage_ticks,
                        train_days: int, test_days: int, min_active: int = 20):
    """Walk-forward of the WHOLE strategy-construction procedure, exits included. In every window the training days
    alone pick the base candidate (parameters + timeframe) and then the stop/target (or none); the next window tests that
    frozen configuration. Stitching the test windows gives an out-of-sample series for the strategy as it will really be
    built, not for an earlier version of it. Series are computed once on development bars and sliced by date: every
    template is causal (tested), so a training slice never contains information from its test window."""
    cache: dict = {}

    def ser(cid, g):
        if g is None:
            return series[cid]
        k = (cid, g["stop_atr"], g["rr"])
        if k not in cache:
            cache[k] = daily_series(bars_by_tf, cands[cid], contract, contracts, slippage_ticks, exit=g)
        return cache[k]

    out, picks, i = [], [], train_days
    while i + test_days <= len(dev_days):
        tr, te = dev_days[i - train_days:i], dev_days[i:i + test_days]
        best, best_sr = None, -np.inf
        for cid, s in series.items():
            x = s.reindex(tr).fillna(0.0)
            if (x != 0).sum() < min_active:
                continue
            sr = sharpe(x)
            if sr > best_sr:
                best, best_sr = cid, sr
        if best is not None:
            top = None
            for g in exit_grid():
                x = ser(best, g).reindex(tr).fillna(0.0)
                cell = (sharpe(x), float(x.sum()), g)
                if top is None or cell[0] > top[0]:
                    top = cell
            chosen = top[2] if (top[0] >= best_sr + MIN_EXIT_GAIN and top[1] > 0) else None
            out.append(ser(best, chosen).reindex(te).fillna(0.0))
            picks.append((best, chosen))
        i += test_days
    return (pd.concat(out) if out else pd.Series(dtype=float)), picks


def _ledger_daily(bars: pd.DataFrame, led) -> pd.Series:
    r = pd.DataFrame({"pnl": led.pnl})
    if "session" in bars.columns:
        r["session"] = bars["session"]
    return daily_pnl(r)


def quality_checks(dev_bars_tf, cand, exit, contract, contracts, slippage_ticks, dev_days, th: SurfaceThresholds) -> dict:
    """False-positive filters for the FINAL candidate on development data: enough trades to mean something, still
    profitable at twice the costs, still profitable when fills happen at the next open instead of the signal close."""
    b = dev_bars_tf[cand["tf"]]
    pos = flatten_overnight(cand["fn"](b, **cand["params"]))
    sh = lambda led: sharpe(_ledger_daily(b, led).reindex(dev_days).fillna(0.0))
    base = run_ledger(b, pos, contract, contracts, slippage_ticks, exit)
    dbl = dataclasses.replace(contract, commission_per_side=contract.commission_per_side * 2)
    tri = dataclasses.replace(contract, commission_per_side=contract.commission_per_side * 3)
    q = {"n_trades": len(base.trades) + (1 if base.open_trade else 0), "sharpe_1x": sh(base),
         "sharpe_2x_costs": sh(run_ledger(b, pos, dbl, contracts, slippage_ticks * 2, exit)),
         "sharpe_3x_costs": sh(run_ledger(b, pos, tri, contracts, slippage_ticks * 3, exit)),
         "sharpe_next_open": sh(run_ledger_next_open(b, pos, contract, contracts, slippage_ticks, exit))}
    q["checks"] = {"min_trades": bool(q["n_trades"] >= th.min_trades),
                   "cost_2x": bool(q["sharpe_2x_costs"] > th.max_cost_multiple_sharpe),
                   "next_open_fill": bool(q["sharpe_next_open"] > th.max_cost_multiple_sharpe)}
    q["ok"] = all(q["checks"].values())
    return q


def causality_probe(fn, params: dict, bars_tf: pd.DataFrame, cuts=(0.35, 0.6, 0.85)) -> bool:
    """True if the strategy's positions on a truncated history equal the first part of its positions on the full history:
    the signal at bar t then depends on bars up to t only. Cut points fall mid-session on purpose."""
    full = flatten_overnight(fn(bars_tf, **params)).to_numpy()
    for f in cuts:
        m = max(50, int(len(bars_tf) * f))
        part = flatten_overnight(fn(bars_tf.iloc[:m], **params)).to_numpy()
        if not np.array_equal(part, full[:m]):
            return False
    return True


def guarded_full_series(bars, cand, exit, contract, contracts, slippage_ticks, dev_days, final_dev, name):
    """The locked series needs the history before it (indicator warm-up), so the FULL bars are used here, for this one
    candidate only. Three guards run first: (1) the strategy is causal (probe at several mid-session cut points),
    (2) development P&L does not change when later data is added, (3) the vectorised engine matches the execution ledger
    bar by bar. Any failure raises AccountingError: no verdict is built on such numbers."""
    tf = cand["tf"]
    full_tf = {tf: resample(bars, tf)}
    if not causality_probe(cand["fn"], cand["params"], full_tf[tf]):
        raise AccountingError(f"{name}: the strategy's signals change when later bars are removed (lookahead)")
    s_full = daily_series(full_tf, cand, contract, contracts, slippage_ticks, exit=exit)
    if not np.allclose(s_full.reindex(dev_days).fillna(0.0).to_numpy(), final_dev.to_numpy(), atol=1e-6):
        raise AccountingError(f"{name}: development P&L changed when later data was added (lookahead in the strategy)")
    pos_full = flatten_overnight(cand["fn"](full_tf[tf], **cand["params"]))
    rc = reconcile(full_tf[tf], pos_full, contract, contracts, slippage_ticks, exit)
    if not rc["ok"]:
        raise AccountingError(f"{name}: vectorised engine and execution ledger disagree: {rc}")
    return s_full, rc, pos_full, full_tf[tf]


def run_study(bars: pd.DataFrame, spec: StrategySpec, contract: Contract, rules: PropRules,
              registry: TrialRegistry, contracts: int = 2, slippage_ticks: float = 1.0,
              oos_fraction: float = 0.25, train_days: int = 120, test_days: int = 20,
              surface_th: SurfaceThresholds | None = None, progress=None, dedupe: bool = False,
              market: str | None = None, judge_th=None) -> dict:
    """Sweep every combination of `spec`, judge the surface, walk-forward the whole procedure, and only if everything
    survives take ONE look at the locked block.

    ISOLATION: the final `oos_fraction` of the sessions is cut away before anything is computed. The sweep, the surface,
    the exit study, the nested walk-forward, the quality checks and the beta check receive development bars only. The
    locked block is first touched, for the single final candidate, inside the Judge's look."""
    th = surface_th or SurfaceThresholds()
    say = progress or (lambda agent, state, msg="": None)
    key = _session_key(bars)
    sessions = sorted(key.unique())
    cut = sessions[int(len(sessions) * (1 - oos_fraction))]
    dev_days = [d for d in sessions if d < cut]
    dev_bars = bars[np.asarray(key < cut)]                        # <- from here on the hidden block does not exist
    dfp = dataset_fingerprint(bars)
    eid = registry.begin_experiment(f"study:{spec.name}", spec.n_candidates, spec_fp=spec_fingerprint(
        spec, contract, contracts, slippage_ticks, oos_fraction, train_days, test_days), dataset_fp=dfp,
        market=market, dedupe=dedupe, details={"template": spec.template, "oos_start": str(cut)[:10]})
    say("optimizer", "working", f"sweep of {spec.n_candidates} candidates")
    cands, series = _sweep(dev_bars, spec, contract, contracts, slippage_ticks)
    dev = series
    rows = [{**c["params"], "tf": c["tf"],
             "sharpe": sharpe(dev[i].reindex(dev_days).fillna(0.0))} for i, c in enumerate(cands)]
    surf = surface_metrics(rows, list(spec.ranges), th)
    say("optimizer", "done", "sweep finished")
    say("validator", "working", "walk-forward and market-drift check")
    best_i = max(range(len(cands)), key=lambda i: rows[i]["sharpe"])
    tfs_dev = {tf: resample(dev_bars, tf) for tf in spec.timeframes}
    exits_rep = _exit_study({cands[best_i]["tf"]: tfs_dev[cands[best_i]["tf"]]}, cands[best_i], series[best_i], contract,
                            contracts, slippage_ticks, dev_days, registry, spec.name, eid)
    best_exit = exits_rep["best"] if exits_rep["chosen"] else None
    # Walk-forward. For a surface that already looks robust it is NESTED (parameters, timeframe and stop/target are all
    # re-chosen on each training window); for the rest the cheap entry-only version is shown, and the study is rejected anyway.
    if surf["robust"]:
        wf, picks = nested_walk_forward(tfs_dev, cands, series, dev_days, contract, contracts, slippage_ticks, train_days, test_days)
        wf_kind = "nested"
    else:
        wf, _ = walk_forward(series, train_days, test_days)
        wf_kind, picks = "entry_only", []
    wf_pnl = float(wf.sum()) if len(wf) else 0.0
    final_dev = daily_series({cands[best_i]["tf"]: tfs_dev[cands[best_i]["tf"]]}, cands[best_i], contract, contracts,
                             slippage_ticks, exit=best_exit).reindex(dev_days).fillna(0.0)
    # How fast could this strategy pass a 25k and a 50k challenge? For EVERY study, on development data only (in-sample).
    goal_days = judge_th.eval_days if judge_th is not None else 5
    goal_rate = judge_th.min_pass_rate if judge_th is not None else 0.30
    timing = timing_report(final_dev.to_numpy(), {k: PRESETS[k] for k in ("25k", "50k")}, contracts, goal_days, goal_rate, "development")
    quality = None
    if surf["robust"]:
        quality = quality_checks(tfs_dev, cands[best_i], best_exit, contract, contracts, slippage_ticks, dev_days, th)
    pair = lambda ser: [[str(d)[:10], float(v)] for d, v in ser.items()]
    report = {"name": spec.name, "template": spec.template, "rationale": spec.rationale,
              "source": spec.source, "n_candidates": len(cands), "surface": surf, "quality": quality, "timing": timing,
              "wf_kind": wf_kind, "wf_oos_days": len(wf), "wf_oos_pnl": wf_pnl,
              "wf_oos_sharpe_daily": sharpe(wf) if len(wf) > 2 else 0.0,
              "n_locked_days": len(sessions) - len(dev_days), "wf_oos_series": wf,
              "experiment_id": eid, "dataset_fp": dfp, "engine_version": ENGINE_VERSION,
              # chart data. dev_best is IN-SAMPLE (best of N combinations): the UI labels it so.
              "series": {"cut": str(cut)[:10], "wf_oos": pair(wf),
                         "dev_best": pair(final_dev), "locked": None,
                         "best_params": {**cands[best_i]["params"], "tf": cands[best_i]["tf"]}, "exit": best_exit},
              "exits": exits_rep}
    # Beta check: only for surfaces that already look robust (it re-runs the dev sweep on a detrended copy).
    surf_d = None
    if surf["robust"] and wf_pnl > 0:
        _, series_d = _sweep(detrend(dev_bars), spec, contract, contracts, slippage_ticks)
        rows_d = [{**c["params"], "tf": c["tf"],
                   "sharpe": sharpe(series_d[i].reindex(dev_days).fillna(0.0))} for i, c in enumerate(cands)]
        surf_d = surface_metrics(rows_d, list(spec.ranges), th)
    report["surface_detrended"] = surf_d
    say("validator", "done", "validation finished")
    if surf["robust"] and wf_pnl > 0 and surf_d["robust"] and quality["ok"]:
        say("judge", "working", "looking at the hidden block")
        cand, tf = cands[best_i], cands[best_i]["tf"]
        s_full, rc, pos_full, b_full = guarded_full_series(bars, cand, best_exit, contract, contracts, slippage_ticks, dev_days, final_dev, spec.name)
        report["ledger_check"] = rc
        locked_days = [d for d in sessions if d >= cut]
        locked = s_full.reindex(locked_days).fillna(0.0)
        var_sr = float(np.var([r["sharpe"] for r in rows], ddof=1))
        v = judge(locked.values, rules, registry.n_trials + 1, var_sr, judge_th)
        registry.add_locked_look(f"{spec.name}:{cand['params']}@{tf}", v.status, experiment_id=eid, dataset_fp=dfp,
                                 oos_start=str(cut)[:10])
        # the human may only see the locked block once the Judge has already used its look
        report["series"]["locked"] = pair(locked)
        report["locked"] = {"status": v.status, "checks": v.checks, "pnl": float(locked.sum()),
                            "timing": timing_report(locked.to_numpy(), {k: PRESETS[k] for k in ("25k", "50k")}, contracts,
                                                    goal_days, goal_rate, "unseen_block"),
                            "p_pass": v.mc.get("p_pass"), "p_pass_ci95": v.mc.get("p_pass_ci95"),
                            "p_bust": v.mc.get("p_bust"), "best_size": v.mc.get("best_size"), "params": cand["params"],
                            "tf": tf, "exit": best_exit, "prop_model": "daily_simplified"}
        # The Judge above is DETERMINISTIC and uses the simplified daily account model. After its look, the same unseen
        # block is replayed bar by bar with unrealised P&L and intraday drawdown rules, for the owner to read.
        try:
            led_full = run_ledger(b_full, pos_full, contract, contracts, slippage_ticks, best_exit)
            report["locked"]["intraday"] = intraday_report(arrays_from_ledger(b_full, led_full, only_from=cut), rules)
        except Exception as e:                                           # informational: never lose a finished study
            report["locked"]["intraday"] = {"error": str(e)[:200]}
    else:
        why = [k for k, ok in surf["checks"].items() if not ok]
        if wf_pnl <= 0:
            why.append("walk_forward_pnl" if wf_kind == "nested" else "walk_forward_pnl_entry_only")
        if quality is not None and not quality["ok"]:
            why += [f"quality_{k}" for k, ok in quality["checks"].items() if not ok]
        if surf_d is not None and not surf_d["robust"]:
            bad = [k for k, ok in surf_d["checks"].items() if not ok]
            why.append(f"fragile_without_market_drift: surface fails {bad} once the trend is removed")
        report["locked"] = {"status": REJECTED, "reason": f"failed {why}; locked block not touched"}
    return report
