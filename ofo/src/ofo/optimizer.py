"""Parameter study: sweep hundreds of combinations, then ask whether the strategy is
ROBUST, not whether some combination looks great.

The best of N combinations is biased upward by construction. What separates an edge from
a lucky spike is the SHAPE of the performance surface: an edge works across a broad
region of parameters (a plateau), a fluke is an isolated peak surrounded by losers."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .backtest import Contract, daily_pnl
from .data import resample
from .exits import exit_grid, summarize_info
from .trades import extract_trades
from .judge import REJECTED, judge
from .ledger import AccountingError, reconcile, run_ledger, run_ledger_next_open
from .prop import PRESETS, PropRules
from .prop_intraday import arrays_from_ledger, intraday_report
from .registry import DuplicateExperiment, TrialRegistry
from .research import daily_series, walk_forward
from .specs import StrategySpec
from .stats import sharpe
from . import farming as FM, repro, research_config as rcfg_mod, search as SR
from .lifecycle import pool_from_ledger
from .rulesets import load_farming_config, load_rulesets
from .sizing import size_study
from .timing import timing_report
from . import evidence as EV
from . import finalists as FIN
from . import judges as JG
from .nearmiss import consistency, is_favorite
from .roles import classify_roles
from .schedule import schedule_study
from .strategies import DIRECTIONS, TEMPLATES, TIMEFRAMES, directional, flatten_overnight, scheduled, version_fn

ENGINE_VERSION = "3"      # bump when the study methodology changes: old experiments stay in the history, re-tests are allowed


@dataclass
class SurfaceThresholds:
    min_median_sharpe: float = 0.0      # the typical combination must make money after costs
    min_frac_positive: float = 0.55
    min_plateau_ratio: float = 0.6      # PARAMETER neighbours of the best must keep >=60% of its Sharpe
    min_neighbours_positive: float = 0.75
    min_tf_positive: float = 0.5        # share of timeframes whose median Sharpe is positive (when several are tested)
    min_trades: int = 160               # development trades of the final candidate (the whole backtest needs 200: ~80% of them are development)
    max_cost_multiple_sharpe: float = 0.0   # Sharpe must stay above this at twice the costs and with next-open fills


def surface_metrics(rows: list[dict], param_names: list[str], th: SurfaceThresholds | None = None, pick: int | None = None,
                    axes: dict | None = None) -> dict:
    """rows: [{params..., 'tf': str, 'sharpe': float}].

    PARAMETER robustness: the neighbours of the best combination are the ones that differ by one grid step on exactly
    one parameter AT THE SAME TIMEFRAME. TIMEFRAME robustness is judged separately: the median Sharpe at each
    timeframe, and how many timeframes are positive. Mixing the two hid a strategy that works at one timeframe only."""
    th = th or SurfaceThresholds()
    axes = axes or {n: sorted({r[n] for r in rows}) for n in param_names}      # the TRUE grid, so 'neighbour' means one real step
    tfs = [t for t in TIMEFRAMES if t in {r["tf"] for r in rows}]
    key = lambda r: tuple(axes[n].index(r[n]) for n in param_names)
    sr = np.array([r["sharpe"] for r in rows])
    best_i = int(np.argmax(sr)) if pick is None else int(pick)      # `pick`: the candidate the Optimizer chose (multi-criteria)
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


def _sweep(bars, spec, contract, contracts, slippage_ticks, rcfg: dict | None = None, dev_days=None):
    """Evaluate the spec's candidates on DEVELOPMENT bars. A grid within the template's cap is tested whole, in random order; a larger
    one is staged (coarse grid + stratified random, then local refinement around promising NEIGHBOURHOODS). Returns
    (cands, series, log). With dev_days given, a conservative sequential rule may stop early: only if even the BEST candidate
    evaluated so far has a 95% upper bound of Sharpe below zero (the unexecuted configurations are then not trials)."""
    rcfg = rcfg or rcfg_mod.get()
    t = TEMPLATES[spec.template]
    tfs = list(spec.timeframes)
    bars_by_tf = {tf: resample(bars, tf) for tf in tfs}
    cap = spec.max_configs or rcfg_mod.max_configs_for(spec.template, rcfg)
    plan = SR.plan_search(spec, cap, rcfg["search"], seed=int(rcfg["search"].get("seed", 7)))
    axes = plan["axes"]
    valid = {SR.key_of(c, axes) for c in spec.combos()}
    cands, series, sharpes = [], {}, {}
    log = {"mode": plan["mode"], "cap_per_timeframe": cap, "initial": 0, "refined": 0, "early_stopped": False, "rule": None}

    def ev(combo, tf, stage):
        i = len(cands)
        c = {"family": spec.name, "fn": t.fn, "params": combo, "tf": tf, "stage": stage}
        cands.append(c)
        series[i] = daily_series(bars_by_tf, c, contract, contracts, slippage_ticks)
        if dev_days is not None:
            sharpes[(tf, SR.key_of(combo, axes))] = sharpe(series[i].reindex(dev_days).fillna(0.0))

    es = rcfg["early_stop"]
    n_days = len(dev_days) if dev_days is not None else 0
    check_at = max(int(es["min_configs"]), int(es["min_fraction"] * len(plan["initial"])))
    for k, combo in enumerate(plan["initial"], 1):
        for tf in tfs:
            ev(combo, tf, "initial")
        if dev_days is not None and k == check_at and k < len(plan["initial"]):
            best = max(sharpes.values())
            if SR.sharpe_upper_bound(best, n_days, float(es["z"])) < 0:
                log["early_stopped"] = True
                log["rule"] = f"after {k}/{len(plan['initial'])} configurations the best Sharpe {best:+.3f} has a {es['z']}-sigma upper bound below zero"
                break
    log["initial"] = len(cands)
    if plan["mode"] == "staged" and dev_days is not None and not log["early_stopped"]:
        budget = plan["refine_budget"] * len(tfs)
        for tf, key in SR.refine_points(sharpes, axes, valid, int(rcfg["search"].get("refine_top", 3)) * len(tfs), budget):
            ev(SR.combo_of(key, axes), tf, "refine")
        log["refined"] = len(cands) - log["initial"]
    log["executed"] = len(cands)
    return cands, series, log


def effective_trials(series: dict, dev_days) -> float:
    """Effective number of independent candidates: participation ratio of the eigenvalues of the correlation matrix of their daily
    P&L. Related variants are NOT independent evidence; this is the estimate shown next to the raw count (informational)."""
    cols = [series[i].reindex(dev_days).fillna(0.0).to_numpy() for i in series]
    if len(cols) < 3:
        return float(len(cols))
    m = np.corrcoef(np.vstack(cols))
    m = np.nan_to_num(m, nan=0.0)
    lam = np.clip(np.linalg.eigvalsh(m), 0, None)
    return float(lam.sum() ** 2 / max((lam ** 2).sum(), 1e-12))


def plateau_pick(cands, rows, axes, idx: list[int]) -> int:
    """Within one timeframe: the candidate with the best NEIGHBOURHOOD score (half own Sharpe + half the mean of its one-step
    neighbours), ties by Sharpe. Never the isolated peak."""
    sh = {(cands[i]["tf"], SR.key_of({n: rows[i][n] for n in axes}, axes)): rows[i]["sharpe"] for i in idx}
    sc = SR.plateau_scores(sh, axes)
    return max(idx, key=lambda i: (sc[(cands[i]["tf"], SR.key_of({n: rows[i][n] for n in axes}, axes))], rows[i]["sharpe"]))


def wf_plan(n_dev_days: int, rcfg: dict, train_days: int | None, test_days: int | None) -> dict:
    """Walk-forward geometry. Explicit arguments win. Otherwise aim for at least `min_windows` chronological OOS windows: shorten the
    test windows (never below min_test_days), then the training window (never below 60 sessions). If the data still cannot give that
    many windows the plan says so (`target_met` False): a documented limit, not something to hide."""
    wf = rcfg["walk_forward"]
    if train_days is not None and test_days is not None:
        tr, te = train_days, test_days
        return {"train_days": tr, "test_days": te, "windows": max(0, (n_dev_days - tr) // te), "target_windows": int(wf["min_windows"]),
                "target_met": max(0, (n_dev_days - tr) // te) >= int(wf["min_windows"]), "explicit": True}
    tr, te, want = int(wf["train_days"]), int(wf["test_days"]), int(wf["min_windows"])
    if (n_dev_days - tr) // te < want:
        te = max(int(wf["min_test_days"]), (n_dev_days - tr) // want)
        if (n_dev_days - tr) // te < want:
            tr = max(60, n_dev_days - want * te)
    w = max(0, (n_dev_days - tr) // te)
    return {"train_days": tr, "test_days": te, "windows": w, "target_windows": want, "target_met": w >= want, "explicit": False}


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
                     train_days: int, test_days: int, cost_model: str = "", retest_no: int = 0) -> str:
    """Identity of an experiment: strategy + search design + execution/cost model + validation geometry + engine version. A new
    cost model, other execution rules or another holdout is a NEW experiment (the old one is never overwritten)."""
    payload = json.dumps([spec.template, spec.ranges, sorted(spec.timeframes), spec.max_configs, contract.symbol, contracts, slippage_ticks,
                          oos_fraction, train_days, test_days, ENGINE_VERSION, cost_model] + ([f"retest{retest_no}"] if retest_no else []),
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _exit_study(dev_bars_tf, cand, base_series, contract, contracts, slippage_ticks, dev_days, registry, name, experiment_id, meta=None,
                informational=False, stage="validation"):
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
    if informational:                      # shown for every study, but nothing is selected from it: evaluations, not trials
        registry.add_trials(0, f"exits_info:{name}", experiment_id=experiment_id, kind="compute", stage="info", n_evals=len(grid), **(meta or {}))
    else:
        registry.add_trials(len(grid), f"exits:{name}", experiment_id=experiment_id, kind="exits", stage=stage, **(meta or {}))
    best = max(cells, key=lambda c: c["sharpe"])
    chosen = (not informational) and best["sharpe"] >= base["sharpe"] + MIN_EXIT_GAIN and best["pnl"] > 0
    better = float(np.mean([c["sharpe"] >= base["sharpe"] for c in cells]))
    stop_only = max((c for c in cells if c["rr"] is None), key=lambda c: c["sharpe"])        # a stop loss is MANDATORY for every strategy
    return {"baseline": base, "grid": cells, "best": {k: best[k] for k in ("stop_atr", "rr")},
            "stop_only": {"stop_atr": stop_only["stop_atr"], "rr": None}, "stop_only_stats": stop_only,
            "best_stats": best, "chosen": bool(chosen), "frac_better_than_baseline": better,
            "n_trials": len(grid), "atr_n": 14, "informational": bool(informational)}


MIN_SCHEDULE_GAIN = 0.05        # the scheduled version must beat the unscheduled one by this much Sharpe in the real development backtest


def _schedule_study(dev_bars, fcand, exit_, base_dev, tfs_dev, contract, contracts, slippage_ticks, dev_days, registry, name, eid, meta,
                    informational=False):
    """Best entry window and trades-per-day cap, development trades only. Applied only if the real backtest of the scheduled version beats
    the unscheduled one; always reported (in-sample), also for a study that never got past the screening (informational, nothing applied)."""
    trades = extract_trades(dev_bars, fcand["fn"], fcand["params"], fcand["tf"], contract, contracts, slippage_ticks, exit_)
    st = schedule_study(trades)
    sched = {"window": [st["window"]["from"], st["window"]["to"]] if st["window"] else None, "max_per_day": st["max_per_day"]}
    out = {**st, "chosen": False, "applied": None, "informational": bool(informational), "in_sample": True, "baseline": None, "with": None}
    if not trades or not (sched["window"] or sched["max_per_day"]):
        out["note"] = "nessuna finestra o limite batte l'intera sessione sui dati di sviluppo" if trades else "nessun trade sui dati di sviluppo"
    else:
        s_new = daily_series({fcand["tf"]: tfs_dev[fcand["tf"]]}, {**fcand, "fn": scheduled(fcand["fn"], sched)}, contract, contracts, slippage_ticks,
                             exit=exit_).reindex(dev_days).fillna(0.0)
        out["baseline"] = {"sharpe": sharpe(base_dev), "pnl": float(base_dev.sum())}
        out["with"] = {"sharpe": sharpe(s_new), "pnl": float(s_new.sum())}
        out["chosen"] = bool((not informational) and out["with"]["sharpe"] >= out["baseline"]["sharpe"] + MIN_SCHEDULE_GAIN and out["with"]["pnl"] > 0)
        out["applied"] = sched if out["chosen"] else None
    n_try = int(st.get("n_scanned", 0)) + int(st.get("n_caps", 0))
    if informational:
        registry.add_trials(0, f"schedule_info:{name}", experiment_id=eid, kind="compute", stage="info", n_evals=max(1, n_try), **meta)
    else:
        registry.add_trials(n_try, f"schedule:{name}", experiment_id=eid, kind="schedule", stage="validation", **meta)
    out["n_trials"] = 0 if informational else n_try
    return out


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


DIRECTION_REL_MARGIN = 0.10  # a one-sided version must beat the two-sided one by 10% of NPFV (plus $1) ...
DIRECTION_CHUNKS = 3         # ... and the dropped side must lose money in at least 2 of these equal slices of the development days


def farming_context(farming: dict | None = None) -> dict:
    """Rulesets + thresholds used to judge strategies as prop-farming candidates (rules/rulesets/*.json, rules/farming.json)."""
    from pathlib import Path
    if farming:
        return farming
    here = Path(__file__).resolve().parent.parent.parent / "rules"
    for root in (here, Path("rules")):
        if (root / "rulesets").is_dir():
            allr = load_rulesets(root / "rulesets")
            cfg = load_farming_config(root / "farming.json")
            return {"cfg": cfg, "rulesets": [allr[i] for i in cfg["rulesets"] if i in allr]}
    return {"cfg": load_farming_config("/nonexistent"), "rulesets": []}


def _profile(cand, bars_b, pnl, dev_days_str, rows_sharpe, contract, accounts, contracts, slippage_ticks, goal_days, goal_rate, farm=None):
    """Metrics and selection score of ONE concrete version of the strategy (a timeframe, a direction). The score is the mean
    development NPFV over the rulesets (prop farming value of the whole account life), not a blend of ratios."""
    tm = timing_report(pnl, accounts, contracts, goal_days, goal_rate, "development")
    led = run_ledger(bars_b, flatten_overnight(cand["fn"](bars_b, **cand["params"])), contract, contracts, slippage_ticks, None)
    ds = set(dev_days_str)
    tr = [t for t in led.trades if t["entry_session"][:10] in ds]
    n_tr = len(tr)
    days_with = len({t["entry_session"][:10] for t in tr}) / max(1, len(ds))
    wr = float(np.mean([t["pnl"] > 0 for t in tr])) if n_tr else 0.0
    c = np.cumsum(pnl); dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], c]))[1:] - c)) if len(c) else 0.0
    streak = cur = 0
    for v in pnl:
        cur = cur + 1 if v < 0 else 0; streak = max(streak, cur)
    pr = {a: r["p_pass_within"].get(str(goal_days)) for a, r in tm["accounts"].items()}
    med = {a: r["median_days"] for a, r in tm["accounts"].items()}
    net = float(pnl.sum()); pos = float((pnl > 0).mean())
    score, comp = 0.0, {}
    if farm and farm["rulesets"] and net > 0 and rows_sharpe > 0:
        pool = pool_from_ledger(bars_b, led)
        vals = []
        for rs in farm["rulesets"]:
            sz = FM.choose_size(pool, rs, contracts, farm["cfg"])
            vals.append(max(r["npfv_mean"] for r in sz["table"]))
        comp = {"npfv_dev_mean": float(np.mean(vals))}
        score = max(0.0, comp["npfv_dev_mean"])
    elif net > 0 and rows_sharpe > 0:
        score = float(net)                                           # no ruleset available: plain development profit
    return {"sharpe": float(rows_sharpe), "net_pnl": net, "max_dd": dd, "profit_dd": (net / dd) if dd > 0 else None, "positive_days": pos,
            "losing_streak": int(streak), "n_trades": n_tr, "trades_per_day": n_tr / max(1, len(ds)), "days_with_trade": days_with,
            "trade_win_rate": wr, "pass_rate": pr, "median_days": med, "min_days": {a: r["min_days_seen"] for a, r in tm["accounts"].items()},
            "components": comp, "score": float(score)}


def _loses_in_most_slices(pnl) -> bool:
    chunks = np.array_split(np.asarray(pnl, dtype=float), DIRECTION_CHUNKS)
    return sum(float(ch.sum()) < 0 for ch in chunks) >= 2


def timeframe_study(cands, series, rows, dev_days, bars_by_tf, contract, contract_accounts, contracts, slippage_ticks,
                    goal_days, goal_rate, farm=None, axes: dict | None = None) -> dict:
    """Best version of the strategy on EACH timeframe AND each direction (both sides / long only / short only), development
    data only (entry-only exits). The version that goes on is the one with the highest development NPFV: the expected net value of
    a whole prop account life (evaluation, funded, payouts, failures, costs) averaged over the rulesets. No weighted blend of
    Sharpe / profit factor / win rate. A version that does not make money is never chosen while another one does.

    Direction: a one-sided version replaces the two-sided one only if its NPFV is DIRECTION_REL_MARGIN higher AND the side it drops
    loses money overall AND in at least 2 of 3 equal slices of the development days (so a lucky half cannot decide it).
    The two extra variants per timeframe are counted as trials by the caller."""
    out, ds = {}, [str(d)[:10] for d in dev_days]
    for tf in TIMEFRAMES:
        idx = [i for i, c in enumerate(cands) if c["tf"] == tf]
        if not idx:
            continue
        bi = plateau_pick(cands, rows, axes, idx) if axes else max(idx, key=lambda i: rows[i]["sharpe"])   # a region, never the isolated peak
        b = bars_by_tf[tf]
        variants = {}
        for d in DIRECTIONS:
            cd = {**cands[bi], "fn": directional(cands[bi]["fn"], d), "direction": d}
            ser = series[bi] if d == "both" else daily_series({tf: b}, cd, contract, contracts, slippage_ticks)
            pnl = ser.reindex(dev_days).fillna(0.0).to_numpy()
            variants[d] = (_profile(cd, b, pnl, ds, sharpe(pnl) if len(pnl) > 2 else 0.0, contract, contract_accounts, contracts,
                                    slippage_ticks, goal_days, goal_rate, farm), pnl)
        chosen = "both"
        for d, other in (("long", "short"), ("short", "long")):
            beats = variants[d][0]["score"] >= variants["both"][0]["score"] * (1 + DIRECTION_REL_MARGIN) + 1.0
            drops_loser = variants[other][0]["net_pnl"] < 0 and _loses_in_most_slices(variants[other][1])
            if beats and drops_loser and variants[d][0]["score"] > variants[chosen][0]["score"]:
                chosen = d
        prof = variants[chosen][0]
        out[tf] = {"best_params": dict(cands[bi]["params"]), "cand_index": int(bi), "direction": chosen, "n_combos": len(idx),
                   "median_sharpe": float(np.median([rows[i]["sharpe"] for i in idx])), **prof,
                   "by_direction": {d: {k: variants[d][0][k] for k in ("sharpe", "net_pnl", "max_dd", "n_trades", "trade_win_rate", "score")}
                                    for d in DIRECTIONS}}
    best = None
    if out:
        best = max(out, key=lambda k: (out[k]["score"], out[k]["sharpe"]))
    return {"timeframes": out, "best_tf": best, "best_direction": out[best]["direction"] if best else "both", "window_days": goal_days,
            "in_sample": True, "criterion": "NPFV medio di sviluppo (vita completa dell'account, in-campione)", "chosen_by": "score" if best and out[best]["score"] > 0 else "sharpe",
            "n_extra_trials": 2 * len(out)}


class _Stages:
    """Per-stage record of one study: seconds, units and the gate each stage applied (the lab's elimination statistics)."""
    def __init__(self, on_stage=None):
        self.rows, self._t, self.on_stage = [], None, on_stage

    def start(self, name: str, detail: str = "") -> None:
        self._t = (name, time.time())
        if self.on_stage:
            try:
                self.on_stage(name, detail)                  # the Lab shows which stage the running study is in
            except Exception:
                pass

    def end(self, units: int, gate: str | None = None, passed: bool | None = None, **kw) -> None:
        name, t0 = self._t
        self.rows.append({"stage": name, "seconds": round(time.time() - t0, 2), "units": int(units), "gate": gate, "passed": passed, **kw})


def run_study(*args, **kwargs) -> dict:
    """Run one study (see _run_study). If anything raises, the experiment is closed as 'failed': it keeps its trial count, it is not
    silently 'already tested', and it can be run again."""
    holder: dict = {}
    t0 = time.time()
    try:
        return _run_study(*args, _holder=holder, **kwargs)
    except DuplicateExperiment:
        raise
    except BaseException as e:
        reg = args[4] if len(args) > 4 else kwargs.get("registry")
        if holder.get("eid") and reg is not None:
            try:
                reg.finish_experiment(holder["eid"], "failed", reject_reason=f"{type(e).__name__}: {str(e)[:300]}", compute_s=time.time() - t0)
            except Exception:
                pass
        raise


def _run_study(bars: pd.DataFrame, spec: StrategySpec, contract: Contract, rules: PropRules,
               registry: TrialRegistry, contracts: int = 2, slippage_ticks: float = 1.0,
               oos_fraction: float | None = None, train_days: int | None = None, test_days: int | None = None,
               surface_th: SurfaceThresholds | None = None, progress=None, dedupe: bool = False,
               market: str | None = None, judge_th=None, all_timeframes: bool = False, farming: dict | None = None,
               research: dict | None = None, _holder: dict | None = None, on_stage=None, retest_no: int = 0) -> dict:
    """The five-stage protocol of one study.

      1 HYPOTHESIS  the spec is already validated (whitelisted template, bounded parameters, hypothesis card)
      2 SCREENING   staged search on development data, plateau-aware choice, surface gates (cheap; most studies end here)
      3 VALIDATION  timeframe/direction by development NPFV, exits, nested walk-forward with the planned windows, quality checks
      4 ROBUSTNESS  the same candidates on a detrended copy of the data (beta check)
      5 AUDIT       causality + ledger reconciliation, reproducibility from metadata, then ONE look at the locked block, the Judge
                    and the prop-farming review. Nothing in stage 5 can change the strategy.

    ISOLATION: the final `oos_fraction` of the sessions is cut away before anything is computed. Stages 2-4 receive development bars
    only; the locked block is first touched, for the single final candidate, inside stage 5."""
    t_start = time.time()
    rcfg = research or rcfg_mod.get()
    th = surface_th or SurfaceThresholds()
    if all_timeframes:                                                        # the Optimizer runs the configured execution timeframes
        spec = dataclasses.replace(spec, timeframes=rcfg_mod.execution_timeframes(rcfg))
    oos_fraction = rcfg["holdout_fraction"] if oos_fraction is None else oos_fraction
    say = progress or (lambda agent, state, msg="": None)
    stages = _Stages(on_stage)
    key = _session_key(bars)
    sessions = sorted(key.unique())
    proto = rcfg.get("protocol") or {}
    fin = epoch = None
    if proto.get("enforce"):                                      # PREREGISTERED PROTOCOL: the hidden block is a frozen epoch; later sessions are reserved for forward evidence
        fin = FIN.Finalists(registry)
        epoch = fin.epoch_for(market or "NQ", sessions, oos_fraction, int(proto.get("max_finalists", 3)))
        cut = next(d for d in sessions if str(d)[:10] == epoch["start"])
        sessions = [d for d in sessions if str(d)[:10] <= epoch["end"]]
    else:
        cut = sessions[int(len(sessions) * (1 - oos_fraction))]
    dev_days = [d for d in sessions if d < cut]
    dev_bars = bars[np.asarray(key < cut)]                        # <- from here on the hidden block does not exist
    plan = wf_plan(len(dev_days), rcfg, train_days, test_days)
    train_days, test_days = plan["train_days"], plan["test_days"]
    dfp = dataset_fingerprint(bars)
    cfg_v, code_v = rcfg["config_version"], repro.code_version()
    cost_model = repro.cost_model_descriptor(contract, contracts, slippage_ticks, str(rcfg.get("cost_model_version", "1")))
    family = spec.family
    hyp_fp = hashlib.sha256(json.dumps([spec.template, sorted(spec.timeframes)]).encode()).hexdigest()[:12]
    eid = registry.begin_experiment(f"study:{spec.name}", spec.n_candidates, spec_fp=spec_fingerprint(
        spec, contract, contracts, slippage_ticks, oos_fraction, train_days, test_days, cost_model, retest_no), dataset_fp=dfp,
        market=market, dedupe=dedupe, stage="screening", family=family, hyp_fp=hyp_fp, config_version=cfg_v, code_version=code_v, cost_model=cost_model,
        details={"template": spec.template, "oos_start": str(cut)[:10], "ranges": spec.ranges, "timeframes": spec.timeframes,
                 "max_configs": spec.max_configs, "hypothesis": spec.hypothesis, "wf_plan": plan, "holdout_fraction": oos_fraction})
    if _holder is not None:
        _holder["eid"] = eid
    meta = {"family": family, "config_version": cfg_v, "code_version": code_v}
    stages.start("hypothesis", spec.name); stages.end(0, "spec validata (template in whitelist, parametri entro i limiti, scheda ipotesi)", True, family=family)

    # ---------------- stage 2: screening ----------------
    stages.start("screening", f"{spec.n_candidates} candidati")
    say("optimizer", "working", f"screening of up to {spec.n_candidates} candidates")
    cands, series, slog = _sweep(dev_bars, spec, contract, contracts, slippage_ticks, rcfg, dev_days)
    registry.set_executed(eid, len(cands))                       # planned trials were registered up front; unexecuted ones are released
    rows = [{**c["params"], "tf": c["tf"], "sharpe": sharpe(series[i].reindex(dev_days).fillna(0.0))} for i, c in enumerate(cands)]
    axes = SR.axes_of(spec)
    n_eff = effective_trials(series, dev_days)
    keyf = lambda j: (cands[j]["tf"], SR.key_of({n: rows[j][n] for n in axes}, axes))
    pscore = SR.plateau_scores({keyf(j): rows[j]["sharpe"] for j in range(len(cands))}, axes)
    best_i = max(range(len(cands)), key=lambda j: (pscore[keyf(j)], rows[j]["sharpe"]))        # the best REGION, not the highest peak
    surf = surface_metrics(rows, list(spec.ranges), th, pick=best_i, axes=axes)
    wf0, _ = walk_forward(series, train_days, test_days)          # cheap entry-only walk-forward, shown for rejected surfaces
    stages.end(len(cands), "superficie robusta (mediana, frazione positiva, plateau, vicini, timeframe)", bool(surf["robust"]),
               search=slog, effective_trials=round(n_eff, 1))
    say("optimizer", "done", "screening finished")
    say("validator", "working", "surface gates")
    trial_block = {"planned": spec.n_candidates, "executed": len(cands), "released": spec.n_candidates - len(cands),
                   "effective_independent_estimate": round(n_eff, 1), "raw_registered_after_screening": registry.n_trials,
                   "note": "i test correlati non sono prove indipendenti: il Giudice usa il conteggio grezzo (prudente); la stima effettiva è informativa"}
    pair = lambda ser: [[str(d)[:10], float(v)] for d, v in ser.items()]
    goal_days = judge_th.eval_days if judge_th is not None else 5
    goal_rate = judge_th.min_pass_rate if judge_th is not None else 0.30
    base_report = {"name": spec.name, "template": spec.template, "family": family, "hypothesis": spec.hypothesis,
                   "hypothesis_missing": list(spec.hypothesis_missing), "rationale": spec.rationale,
                   "source": spec.source, "n_candidates": len(cands), "surface": surf, "base_contracts": contracts, "search": slog, "trials": trial_block,
                   "wf_plan": plan, "n_locked_days": len(sessions) - len(dev_days), "experiment_id": eid, "dataset_fp": dfp,
                   "engine_version": ENGINE_VERSION, "config_version": cfg_v, "code_version": code_v, "retest_no": int(retest_no),
                   "spec": dataclasses.asdict(spec), "surface_detrended": None, "quality": None, "timing": None, "sizing": None, "by_timeframe": None, "exits": None,
                   "wf_kind": "entry_only", "wf_oos_days": len(wf0), "wf_oos_pnl": float(wf0.sum()) if len(wf0) else 0.0,
                   "wf_oos_sharpe_daily": sharpe(wf0) if len(wf0) > 2 else 0.0, "wf_oos_series": wf0}

    jctx: dict = {}                                                  # series the dual judge may read (set when the unseen block was judged)

    def finish(report: dict, status: str = "completed") -> dict:
        report["stages"] = stages.rows
        lk = report["locked"]
        # DUAL JUDGE (edge / prop / final): a pure function of this report. The legacy status stays visible as `legacy_status`.
        try:
            j = JG.judge_report(report, oos=jctx.get("oos"), dev=jctx.get("dev"), cfg=JG.cfg_from(rcfg), lineage_looks=int(jctx.get("lineage", 0)))
            report["judgment"] = j
            lk["legacy_status"] = lk.get("status")
            lk["verdict"] = j["verdict"]
            lk["status"] = j["legacy_status"]
            lk["judge_reason"] = j["reason"]
            if j["verdict"] != "APPROVED" and not lk.get("reason"):
                lk["reason"] = j["reason"]
            if j["verdict"] != "APPROVED" and not report.get("gate_failed"):
                report["gate_failed"] = ((j.get("diagnosis") or {}).get("failed_gate") or {}).get("label")
            if fin is not None and jctx.get("finalist_id"):
                fin.record_verdict(jctx["finalist_id"], j["verdict"])
            report["near_miss"] = ({"gate": "edge", "dsr": (j["edge"]["metrics"] or {}).get("dsr"), "needed": rcfg.get("judge", {}).get("confidence", 0.95), "why": j["reason"]}
                                   if j["verdict"] == "PAPER_CANDIDATE" else None)
        except Exception as e:                                       # never lose a finished study because the diagnosis failed
            report["judgment_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        report["evidence"] = evidence_ladder(report)
        ser_ = report.get("series") or {}
        vals_ = [v for _, v in (ser_.get("dev_best") or [])] + [v for _, v in (ser_.get("locked") or [])]
        report["consistency"] = consistency(vals_) if vals_ else None
        report["favorite"] = bool(report["consistency"] and is_favorite(report["consistency"], bool((report.get("locked") or {}).get("checks", {}).get("pass_rate")), rcfg.get("favorites", {})))
        report["roles"] = classify_roles(report)
        results = {"survived_screening": bool(surf["robust"]), "validated": bool(report.get("validated")), "locked_status": lk.get("status"),
                   "approved": lk.get("status") == "APPROVED",
                   "false_positive": bool(report.get("validated")) and lk.get("status") not in ("APPROVED", None) and "checks" in lk,
                   "reject_reason": lk.get("reason") or (lk.get("failed_gate") or {}).get("reason"), "gate_failed": report.get("gate_failed"),
                   "n_evals": sum(r["units"] for r in stages.rows), "stages": stages.rows, "best_tf": (report.get("by_timeframe") or {}).get("best_tf")}
        registry.finish_experiment(eid, "early_stopped" if slog["early_stopped"] else status, executed=len(cands), results=results,
                                   reject_reason=results["reject_reason"], compute_s=time.time() - t_start,
                                   details={"entered_validation": bool(surf["robust"]), "early_stop_rule": slog["rule"]})
        report["trials"]["raw_registered_final"] = registry.n_trials
        report["compute_s"] = round(time.time() - t_start, 1)
        return report

    rescue = None
    if not surf["robust"]:
        why = [k for k, ok in surf["checks"].items() if not ok]
        # EXIT RESCUE. The strategy was screened with ITS OWN exit. A fixed stop + target (R:R 1:1, 2:1, 0.5:1 ...) is an exit too, often a better
        # one: it is tried on the best candidate (28 combinations, counted as trials) and, if one beats the strategy's own exit, ALL candidates are
        # re-screened with it (counted again). Only if that surface is robust does the study go on; otherwise the rejection stands.
        try:
            cand0, tf0 = cands[best_i], cands[best_i]["tf"]
            tfd0 = {tf0: resample(dev_bars, tf0)}
            ex0 = _exit_study(tfd0, cand0, series[best_i], contract, contracts, slippage_ticks, dev_days, registry, spec.name, eid, meta, stage="screening")
            ex0["rescue_tried"] = bool(ex0["chosen"])
            base_report["exits"] = ex0
            if ex0["chosen"]:
                bx = ex0["best"]
                tfs_all = {tf: resample(dev_bars, tf) for tf in spec.timeframes}
                series_x = {i: daily_series(tfs_all, c, contract, contracts, slippage_ticks, exit=bx) for i, c in enumerate(cands)}
                rows_x = [{**c["params"], "tf": c["tf"], "sharpe": sharpe(series_x[i].reindex(dev_days).fillna(0.0))} for i, c in enumerate(cands)]
                registry.add_trials(len(cands), f"exit_rescreen:{spec.name}", experiment_id=eid, dataset_fp=dfp, market=market, kind="exits",
                                    stage="screening", **meta)
                keyx = lambda j: (cands[j]["tf"], SR.key_of({n: rows_x[j][n] for n in axes}, axes))
                psx = SR.plateau_scores({keyx(j): rows_x[j]["sharpe"] for j in range(len(cands))}, axes)
                bi_x = max(range(len(cands)), key=lambda j: (psx[keyx(j)], rows_x[j]["sharpe"]))
                surf_x = surface_metrics(rows_x, list(spec.ranges), th, pick=bi_x, axes=axes)
                base_report["rescue"] = {"exit": bx, "surface": surf_x, "robust": bool(surf_x["robust"]), "n_trials": len(cands),
                                         "note": "stessa ricerca, uscita fissa stop/target invece dell'uscita della strategia (in-campione, contata come prove)"}
                if surf_x["robust"]:
                    rescue = {"exit": bx, "surf": surf_x, "rows": rows_x, "best_i": bi_x}
            else:
                base_report["rescue"] = {"exit": None, "robust": False, "note": "nessuna combinazione stop/target batte l'uscita della strategia"}
        except Exception as e:                                # a diagnostic must never turn a clean rejection into a failed study
            base_report["rescue"] = {"error": f"{type(e).__name__}: {str(e)[:120]}", "robust": False}
            say("optimizer", "idle", f"uscite alternative non calcolate: {type(e).__name__}")
    if not surf["robust"] and rescue is None:
        base_report["gate_failed"] = "screening: " + ", ".join(why)
        base_report["timing"] = timing_report(series[best_i].reindex(dev_days).fillna(0.0).to_numpy(), {k: PRESETS[k] for k in ("25k", "50k")}, contracts,
                                              goal_days, goal_rate, "development")      # every study reports days-to-pass (screening candidate, entry-only)
        base_report["series"] = {"cut": str(cut)[:10], "wf_oos": pair(wf0), "dev_best": [], "locked": None,
                                 "best_params": {**cands[best_i]["params"], "tf": cands[best_i]["tf"]}, "direction": "both", "exit": None}
        try:                                                  # the schedule table is shown for every study; here nothing is selected from it
            cand0, tf0 = cands[best_i], cands[best_i]["tf"]
            tfd0 = {tf0: resample(dev_bars, tf0)}
            base_report["schedule"] = _schedule_study(dev_bars, cand0, None, series[best_i].reindex(dev_days).fillna(0.0), tfd0, contract, contracts,
                                                      slippage_ticks, dev_days, registry, spec.name, eid, meta, informational=True)
        except Exception as e:
            base_report["schedule"] = None
            say("optimizer", "idle", f"tabella oraria non calcolata: {type(e).__name__}")
        base_report["locked"] = {"status": REJECTED, "reason": f"failed {why}; locked block not touched"}
        say("validator", "done", "screening gate failed")
        return finish(base_report)
    if rescue is not None:                                    # the surface is judged with the exit that rescued it; the rest of the protocol is unchanged
        surf, rows, best_i = rescue["surf"], rescue["rows"], rescue["best_i"]
        say("optimizer", "working", "uscita fissa stop/target: la strategia prosegue")

    # ---------------- stage 3: validation ----------------
    stages.start("validation", "timeframe, direzione e uscite")
    say("optimizer", "working", "timeframe, direction and exits by development NPFV")       # the Optimizer's own work: choosing the best version
    farm = farming_context(farming)
    tfs_dev = {tf: resample(dev_bars, tf) for tf in spec.timeframes}
    by_tf = timeframe_study(cands, series, rows, dev_days, tfs_dev, contract, {k: PRESETS[k] for k in ("25k", "50k")}, contracts,
                            slippage_ticks, goal_days, goal_rate, farm, axes)
    best_i = by_tf["timeframes"][by_tf["best_tf"]]["cand_index"] if by_tf["best_tf"] else best_i
    surf = surface_metrics(rows, list(spec.ranges), th, pick=best_i, axes=axes)           # the surface at the candidate that goes on
    direction = by_tf["best_direction"]
    fcand = {**cands[best_i], "fn": directional(cands[best_i]["fn"], direction), "direction": direction}   # the version that goes on
    registry.add_trials(by_tf["n_extra_trials"], f"direction:{spec.name}", experiment_id=eid, dataset_fp=dfp, market=market, kind="direction",
                        stage="validation", **meta)
    base_ser = series[best_i] if direction == "both" else daily_series({fcand["tf"]: tfs_dev[fcand["tf"]]}, fcand, contract, contracts, slippage_ticks)
    exits_rep = _exit_study({fcand["tf"]: tfs_dev[fcand["tf"]]}, fcand, base_ser, contract, contracts, slippage_ticks, dev_days, registry,
                            spec.name, eid, meta)
    best_exit = exits_rep["best"] if exits_rep["chosen"] else exits_rep["stop_only"]       # no fixed exit beats the strategy's own: it keeps ITS exit, but ALWAYS with a stop loss
    exits_rep["mandatory_stop"] = not exits_rep["chosen"]
    base_dev_s = daily_series({fcand["tf"]: tfs_dev[fcand["tf"]]}, fcand, contract, contracts, slippage_ticks, exit=best_exit).reindex(dev_days).fillna(0.0)
    sched_rep = _schedule_study(dev_bars, fcand, best_exit, base_dev_s, tfs_dev, contract, contracts, slippage_ticks, dev_days, registry, spec.name, eid, meta)
    schedule = sched_rep["applied"]
    if schedule:
        fcand = {**fcand, "fn": scheduled(fcand["fn"], schedule), "schedule": schedule}        # the version that goes on trades only then and this often
    say("optimizer", "done", "best version chosen")
    say("validator", "working", "nested walk-forward and quality checks")
    wf, picks = nested_walk_forward(tfs_dev, cands, series, dev_days, contract, contracts, slippage_ticks, train_days, test_days)
    wf_kind = "nested"
    registry.add_trials(0, f"walk_forward:{spec.name}", experiment_id=eid, kind="compute", stage="validation", n_evals=max(1, len(wf) // max(1, test_days)),
                        **meta)
    wf_pnl = float(wf.sum()) if len(wf) else 0.0
    final_dev = daily_series({fcand["tf"]: tfs_dev[fcand["tf"]]}, fcand, contract, contracts,
                             slippage_ticks, exit=best_exit).reindex(dev_days).fillna(0.0)
    timing = timing_report(final_dev.to_numpy(), {k: PRESETS[k] for k in ("25k", "50k")}, contracts, goal_days, goal_rate, "development")
    sizing = size_study(final_dev.to_numpy(), {k: PRESETS[k] for k in ("25k", "50k")}, contracts, goal_days, basis="development")
    quality = quality_checks(tfs_dev, fcand, best_exit, contract, contracts, slippage_ticks, dev_days, th)
    ablation = ablation_study(fcand, spec.template, tfs_dev, best_exit, contract, contracts, slippage_ticks, dev_days, registry, eid, spec.name, meta)
    val_ok = bool(surf["robust"] and wf_pnl > 0 and quality["ok"] and plan["target_met"] and (rescue is None or best_exit is not None))      # a rescue must end with an exit chosen
    stages.end(28 + by_tf["n_extra_trials"] + sched_rep["n_trials"] + len(wf), "walk-forward annidato positivo, qualità, finestre OOS pianificate", val_ok,
               wf_windows=plan["windows"], wf_target_met=plan["target_met"])
    report = {**base_report, "quality": quality, "timing": timing, "by_timeframe": by_tf, "sizing": sizing, "ablation": ablation, "validated": val_ok,
              "wf_kind": wf_kind, "wf_oos_days": len(wf), "wf_oos_pnl": wf_pnl, "wf_oos_sharpe_daily": sharpe(wf) if len(wf) > 2 else 0.0,
              "surface": surf, "wf_oos_series": wf,
              "series": {"cut": str(cut)[:10], "wf_oos": pair(wf), "dev_best": pair(final_dev), "locked": None,
                         "best_params": {**cands[best_i]["params"], "tf": cands[best_i]["tf"]}, "direction": direction, "exit": best_exit,
                         "schedule": schedule},
              "exits": exits_rep, "schedule": sched_rep, "rescue": base_report.get("rescue")}
    say("validator", "done", "validation finished")

    # ---------------- stage 4: robustness ----------------
    surf_d = None
    if val_ok:
        stages.start("robustness", "beta check")
        t_b = time.time()
        cands_d = cands
        bars_d = {tf: resample(detrend(dev_bars), tf) for tf in spec.timeframes}
        series_d = {i: daily_series(bars_d, c, contract, contracts, slippage_ticks) for i, c in enumerate(cands_d)}
        rows_d = [{**c["params"], "tf": c["tf"], "sharpe": sharpe(series_d[i].reindex(dev_days).fillna(0.0))} for i, c in enumerate(cands_d)]
        surf_d = surface_metrics(rows_d, list(spec.ranges), th, axes=axes)
        registry.add_trials(0, f"beta_check:{spec.name}", experiment_id=eid, kind="compute", stage="robustness", n_evals=len(cands_d),
                            compute_s=time.time() - t_b, **meta)
        stages.end(len(cands_d), "stessa superficie senza deriva di mercato (beta check)", bool(surf_d["robust"]))
    report["surface_detrended"] = surf_d

    if val_ok and surf_d["robust"]:
        # ---------------- stage 5: audit + the single look at the locked block ----------------
        stages.start("audit", "audit e blocco bloccato")
        say("judge", "working", "audit, reproducibility and the look at the hidden block")
        cand, tf = fcand, fcand["tf"]
        if fin is not None:                                      # REGISTER the finalist BEFORE anything is computed on the hidden block
            fpay = {"spec": dataclasses.asdict(spec), "params": dict(cand["params"]), "tf": tf, "direction": direction, "schedule": schedule, "exit": best_exit,
                    "contracts": contracts, "slippage_ticks": slippage_ticks,
                    "contract": {"symbol": contract.symbol, "tick_size": contract.tick_size, "tick_value": contract.tick_value, "commission_per_side": contract.commission_per_side},
                    "code_version": code_v, "config_version": cfg_v, "dataset_fp": dataset_fingerprint(bars[np.asarray(key <= pd.Timestamp(epoch["end"]))]),
                    "sizing": {"base_contracts": contracts, "rule": "choose_size on development sessions", "sizes": list(farm["cfg"].get("sizes", []))},
                    "rulesets": [f"{r.id} v{r.version} ({r.provenance})" for r in farm["rulesets"]]}
            try:
                reg_ = fin.register(epoch, fpay, FIN_lineage(spec))
                jctx["finalist_id"] = reg_["finalist"]["id"]
                report["finalist"] = {**reg_["finalist"], "repeat": reg_["repeat"], "epoch": epoch, "payload_fingerprint": reg_["finalist"]["fingerprint"]}
            except (FIN.BlockFull, FIN.LineageBlocked) as e:
                kind = "lineage" if isinstance(e, FIN.LineageBlocked) else "block_full"
                report["shelved"] = {"kind": kind, "reason": str(e), "epoch": epoch}
                report["gate_failed"] = "finalist_slot"
                report["locked"] = {"status": "INSUFFICIENT_EVIDENCE", "reason": f"blocco hidden non disponibile ({kind}): {e}; il blocco non è stato toccato"}
                stages.end(2, "registro dei finalisti: nessuno slot", False)
                return finish(report)
        try:
            s_full, rc, pos_full, b_full = guarded_full_series(bars, cand, best_exit, contract, contracts, slippage_ticks, dev_days, final_dev, spec.name)
        except AccountingError as e:                             # a verdict is never built on broken numbers: EXECUTION_FAILED, the locked block was not read
            report["accounting_error"] = str(e)[:300]
            report["gate_failed"] = "accounting"
            report["locked"] = {"status": REJECTED, "reason": f"AccountingError: {str(e)[:200]}; blocco bloccato non toccato"}
            stages.end(2, "controllo contabile fallito", False)
            return finish(report)
        report["ledger_check"] = rc
        locked_days = [d for d in sessions if d >= cut]
        locked = s_full.reindex(locked_days).fillna(0.0)
        meta_c = {"template": spec.template, "params": dict(cand["params"]), "tf": tf, "direction": direction, "schedule": schedule, "exit": best_exit, "contracts": contracts,
                  "slippage_ticks": slippage_ticks, "contract": {"symbol": contract.symbol, "tick_size": contract.tick_size, "tick_value": contract.tick_value,
                                                                  "commission_per_side": contract.commission_per_side}}
        report["repro"] = {"meta": meta_c, "dataset_fp": dfp, "code_version": code_v, "config_version": cfg_v, "cost_model": cost_model,
                           "engine_version": ENGINE_VERSION, "oos_fraction": oos_fraction, "wf_plan": plan,
                           "seeds": {"search": rcfg["search"].get("seed", 7), "farming": farm["cfg"].get("seed")},
                           "rulesets": [f"{r.id} v{r.version} ({r.provenance})" for r in farm["rulesets"]],
                           "digest_dev": repro.digest(final_dev), "digest_locked": repro.digest(locked), "spec": dataclasses.asdict(spec),
                           "trial_budget": {"planned": spec.n_candidates, "executed": len(cands), "registry_total": registry.n_trials}}
        try:                                                      # recompute from the stored metadata ONLY and compare
            report["repro"]["verified"] = bool(repro.candidate_digest(bars, meta_c, dev_days) == report["repro"]["digest_dev"] and
                                               repro.candidate_digest(bars, meta_c, locked_days) == report["repro"]["digest_locked"])
        except Exception as e:
            report["repro"]["verified"] = False; report["repro"]["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        var_sr = float(np.var([r["sharpe"] for r in rows], ddof=1))
        led_full = run_ledger(b_full, pos_full, contract, contracts, slippage_ticks, best_exit)
        n_locked_trades = sum(1 for t_ in led_full.trades if str(t_.get("entry_session"))[:10] >= str(cut)[:10])
        dsr_mode = rcfg.get("dsr_trials", "raw")                  # raw: every trial ever registered (strictest) | locked_looks: only strategies that were tested on the unseen block | effective: this study's independent trials
        n_dsr = (int(registry.data["locked_looks"]) + 1 if dsr_mode == "locked_looks" else max(2, int(round(n_eff))) + 1 if dsr_mode == "effective" else registry.n_trials + 1)
        k_planned = int(epoch["max_finalists"]) if epoch else int(proto.get("max_finalists", 3))
        modes = EV.dsr_modes(locked.values, var_sr=var_sr, n_raw=registry.n_trials + 1, locked_looks=int(registry.data["locked_looks"]), k_planned=k_planned, n_eff=n_eff)
        official = dsr_mode == "holdout_null"
        v = judge(locked.values, rules, n_dsr, var_sr, judge_th, n_trades=n_locked_trades,
                  dsr_value=(modes["holdout_null"]["value"] if official else None), dsr_n=(k_planned if official else None),
                  dsr_var=(modes["holdout_null"]["V"] if official else None))
        holdout_null = {"value": modes["holdout_null"]["value"], "k": k_planned, "sr0_annual": modes["holdout_null"]["sr0_annual"], "T": len(locked)}
        look_label = f"{spec.name}:{cand['params']}@{tf}"
        jctx.update(oos=np.asarray(locked.values, float), dev=np.asarray(final_dev.values if hasattr(final_dev, "values") else final_dev, float),
                    lineage=JG.lineage_looks_for(registry, spec.name, look_label, str(cut)[:10]))
        registry.add_locked_look(f"{spec.name}:{cand['params']}@{tf}", v.status, experiment_id=eid, dataset_fp=dfp,
                                 oos_start=str(cut)[:10])
        # the human may only see the locked block once the Judge has already used its look
        report["series"]["locked"] = pair(locked)
        report["locked"] = {"status": v.status, "checks": v.checks, "pnl": float(locked.sum()),
                            "timing": timing_report(locked.to_numpy(), {k: PRESETS[k] for k in ("25k", "50k")}, contracts,
                                                    goal_days, goal_rate, "unseen_block"),
                            "sizing": size_study(locked.to_numpy(), {k: PRESETS[k] for k in ("25k", "50k")}, contracts, goal_days, basis="unseen_block"),
                            "p_pass": v.mc.get("p_pass"), "p_pass_ci95": v.mc.get("p_pass_ci95"),
                            "p_bust": v.mc.get("p_bust"), "best_size": v.mc.get("best_size"), "mc_sample": v.mc.get("sample"), "dsr": {**(v.mc.get("dsr") or {"value": modes["holdout_null"]["value"] if official else None, "n_trials": k_planned if official else n_dsr, "var_sr": modes["holdout_null"]["V"] if official else float(var_sr), "needed": judge_th.min_dsr}), "basis": dsr_mode, "n_trials_raw": registry.n_trials + 1, "holdout_null": holdout_null}, "dsr_modes": modes, "official_dsr": dsr_mode, "params": cand["params"],
                            "tf": tf, "exit": best_exit, "prop_model": "daily_simplified"}
        # The Judge above is DETERMINISTIC and uses the simplified daily account model. After its look, the same unseen
        # block is replayed bar by bar with unrealised P&L and intraday drawdown rules, for the owner to read.
        led_full = None
        try:
            led_full = run_ledger(b_full, pos_full, contract, contracts, slippage_ticks, best_exit)
            report["locked"]["intraday"] = intraday_report(arrays_from_ledger(b_full, led_full, only_from=cut), rules)
        except Exception as e:                                           # informational: never lose a finished study
            report["locked"]["intraday"] = {"error": str(e)[:200]}
        # PROP FARMING REVIEW (gates 1-7 of the factory): same unseen block, same look. It replaces the challenge-passing view as
        # the basis of the final status; the legacy daily-model verdict stays visible as `judge_status`.
        report["locked"]["judge_status"] = v.status
        try:
            led_full = led_full or run_ledger(b_full, pos_full, contract, contracts, slippage_ticks, best_exit)
            pool_dev_f, pool_oos_f = FM.split_pool(pool_from_ledger(b_full, led_full), str(cut)[:10])
            report["locked"]["oos_evidence"] = oos_evidence(pool_oos_f, locked, rcfg["sample"]["min_oos_trades"])
            ctx = {"integrity_ok": bool(rc.get("ok")) and bool(report["repro"].get("verified")), "edge_ok": bool(v.checks.get("dsr")),
                   "oos_positive": bool(locked.sum() > 0), "robust_ok": True, "forward": None}
            fr = FM.review(pool_dev_f, pool_oos_f, farm["rulesets"], contracts, farm["cfg"], ctx, spec.template) if farm["rulesets"] else None
            if fr is not None:
                report["farming"] = json.loads(json.dumps(fr, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
                report["locked"]["status"] = fr["verdict"]
                report["locked"]["failed_gate"] = fr["first_failed"]
                report["locked"]["suited"] = fr["suited"]
                report["gate_failed"] = (fr["first_failed"] or {}).get("name")
        except Exception as e:                                           # never lose a finished study: fall back to the legacy verdict
            report["locked"]["farming_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if not report["repro"].get("verified") and report["locked"]["status"] == "APPROVED":
            report["locked"]["status"] = REJECTED                        # a result that cannot be reproduced from its metadata is not promoted
            report["locked"]["reason"] = "not reproducible from the saved metadata"
            report["gate_failed"] = "reproducibility"
        # the WHOLE backtest (development + unseen block) must contain at least `min_backtest_trades` trades: fewer cannot be approved
        n_bt = len(led_full.trades) if led_full is not None else None
        min_bt = int(rcfg["sample"].get("min_backtest_trades", 200))
        report["locked"]["backtest_trades"] = {"n": n_bt, "min": min_bt, "ok": bool(n_bt is not None and n_bt >= min_bt)}
        if n_bt is not None and n_bt < min_bt and report["locked"]["status"] == "APPROVED":
            report["locked"]["status"] = "INSUFFICIENT_EVIDENCE"
            report["locked"]["reason"] = f"solo {n_bt} trade in tutto il backtest (ne servono almeno {min_bt}): campione insufficiente"
            report["gate_failed"] = "backtest_trades"
        stages.end(2, "causalità, libro mastro, riproducibilità, blocco nascosto, Giudice, prop farming", report["locked"]["status"] == "APPROVED")
    else:
        why = [k for k, ok in surf["checks"].items() if not ok]
        if wf_pnl <= 0:
            why.append("walk_forward_pnl")
        if not plan["target_met"]:
            why.append(f"walk_forward_windows_{plan['windows']}_of_{plan['target_windows']}")
        if not quality["ok"]:
            why += [f"quality_{k}" for k, ok in quality["checks"].items() if not ok]
        if surf_d is not None and not surf_d["robust"]:
            bad = [k for k, ok in surf_d["checks"].items() if not ok]
            why.append(f"fragile_without_market_drift: surface fails {bad} once the trend is removed")
        report["gate_failed"] = ("validation: " if surf_d is None else "robustness: ") + ", ".join(why)
        report["locked"] = {"status": REJECTED, "reason": f"failed {why}; locked block not touched"}
    return finish(report)


def FIN_lineage(spec) -> str:
    """The line of research a finalist belongs to: template + name without trailing version numbers. A variant of a line already judged on an epoch
    cannot be registered on it (it would be a re-test of a strategy modified after the hidden block was seen)."""
    base = re.sub(r"[\s_\-]*(v\d+|\d+)$", "", spec.name.lower()).strip()
    return f"{spec.template}:{base}"


def evidence_ladder(report: dict) -> dict:
    """Four DIFFERENT kinds of evidence, never mixed: screening (development sweep), walk-forward (nested, development), the locked
    out-of-sample block (one look) and forward (paper, filled by the Lab). Screening results are not statistical confirmation."""
    lk = report.get("locked") or {}
    return {"screening": {"label": "screening (dati di sviluppo, NON è una conferma)", "median_sharpe": (report.get("surface") or {}).get("median_sharpe"),
                          "robust": (report.get("surface") or {}).get("robust"), "n": report.get("n_candidates")},
            "walk_forward": {"label": "walk-forward (sviluppo, finestre cronologiche)", "kind": report.get("wf_kind"), "windows": (report.get("wf_plan") or {}).get("windows"),
                             "oos_pnl": report.get("wf_oos_pnl")} if report.get("wf_kind") == "nested" else None,
            "locked_oos": {"label": "blocco bloccato (un solo sguardo)", "status": lk.get("status"), "pnl": lk.get("pnl")} if "checks" in lk else None,
            "forward": None}


def oos_evidence(pool, locked, min_trades: int) -> dict:
    """What the unseen block can support: trade count against the target, serial dependence, profit concentration, and an uncertainty
    interval for the mean daily P&L from a block bootstrap (days are not independent)."""
    x = np.asarray(locked, dtype=float)
    n_tr = int(pool.trades.sum())
    pos = np.sort(x[x > 0])[::-1]
    conc = float(pos[: max(1, int(0.05 * len(x)))].sum() / pos.sum()) if len(pos) and pos.sum() > 0 else None
    ac = float(np.corrcoef(x[:-1], x[1:])[0, 1]) if len(x) > 5 and x.std() > 0 else None
    rng = np.random.default_rng(1)
    means = [float(x[FM.block_indices(len(x), len(x), 3, rng)].mean()) for _ in range(400)] if len(x) > 5 else []
    return {"n_trades": n_tr, "target_trades": int(min_trades), "target_met": n_tr >= min_trades, "n_sessions": int(len(x)),
            "top5pct_days_share_of_profit": conc, "lag1_autocorrelation": ac,
            "mean_daily_pnl_ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))] if means else None,
            "note": "il campione è indicativo: numero di trade, dipendenza seriale, concentrazione e intervallo contano insieme"}


def ablation_study(fcand, template, bars_by_tf, exit_, contract, contracts, slippage_ticks, dev_days, registry, eid, name, meta) -> list:
    """Switch each optional filter of the final candidate OFF (development data) and report what it was worth. A filter that does not
    improve the Sharpe is dead weight (and a degree of freedom). The variants are counted as trials."""
    filters = TEMPLATES[template].filters
    out = []
    for f in filters:
        if fcand["params"].get(f) in (None, 0):
            continue
        off = {**fcand, "params": {**fcand["params"], f: 0}}
        s_on = sharpe(daily_series(bars_by_tf, fcand, contract, contracts, slippage_ticks, exit=exit_).reindex(dev_days).fillna(0.0))
        s_off = sharpe(daily_series(bars_by_tf, off, contract, contracts, slippage_ticks, exit=exit_).reindex(dev_days).fillna(0.0))
        out.append({"filter": f, "sharpe_with": float(s_on), "sharpe_without": float(s_off), "delta": float(s_on - s_off), "useful": bool(s_on > s_off)})
    if out:
        registry.add_trials(len(out), f"ablation:{name}", experiment_id=eid, kind="ablation", stage="validation", **meta)
    return out


def farming_for_study(bars, report: dict, series: dict, contract, contracts: int, farm: dict | None = None, slippage_ticks: float = 1.0,
                      forward: dict | None = None) -> dict:
    """Review a study the Judge already looked at with the prop-farming criteria, WITHOUT changing a single out-of-sample number:
    the final candidate (parameters, timeframe, direction, stop/target) is replayed on the full data through the execution ledger,
    its session data are split at the study's own cut, and the same review used for new studies is applied."""
    farm = farming_context(farm)
    bp = dict(series["best_params"]); tf = bp.pop("tf")
    fn = version_fn(TEMPLATES[report["template"]].fn, series.get("direction"), series.get("schedule"))
    b = bars if tf == "1min" else resample(bars, tf)
    pos = flatten_overnight(fn(b, **bp))
    exit_ = series.get("exit")
    rc = reconcile(b, pos, contract, contracts, slippage_ticks, exit_)
    led = run_ledger(b, pos, contract, contracts, slippage_ticks, exit_)
    dev, oos = FM.split_pool(pool_from_ledger(b, led), str(series["cut"])[:10])
    lk = report.get("locked") or {}
    surf, q = report.get("surface") or {}, report.get("quality")
    sd = report.get("surface_detrended")
    ctx = {"integrity_ok": bool(rc["ok"]), "edge_ok": bool((lk.get("checks") or {}).get("dsr")), "oos_positive": float(lk.get("pnl") or 0.0) > 0,
           "robust_ok": bool(surf.get("robust")) and float(report.get("wf_oos_pnl") or 0) > 0 and bool(sd is None or sd.get("robust")) and bool(q is None or q.get("ok")),
           "forward": forward}
    fr = FM.review(dev, oos, farm["rulesets"], contracts, farm["cfg"], ctx, report["template"])
    return json.loads(json.dumps(fr, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
