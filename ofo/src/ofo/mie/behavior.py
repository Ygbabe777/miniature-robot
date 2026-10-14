"""Market Behavior Analyzer: from "it looks like this happens" to a distribution with its uncertainty and its limits.

An EVENT STUDY takes events (each with the time from which it was knowable and a hypothesised trade direction) and measures what followed:
entry at the open of the first 1-minute bar at or after `available_at` (never earlier), exit after H minutes, outcome in ATR units NET of costs,
maximum favourable/adverse excursion on the 1-minute highs/lows. It reports the whole distribution, not a hit rate; compares with a BASELINE (same
time of day, other sessions, same long/short mix); gives session-clustered bootstrap intervals; measures concentration, temporal stability,
dependence between events, sensitivity to costs, to the event definition and to the regime; and lists the limits of the evidence in words.

EVIDENCE STATUS: SUPPORTED only if there are enough events AND sessions, the net difference vs baseline is positive with an interval above zero, both halves of
time agree, the result is not concentrated in a few events, and (across a cycle's studies) it survives a Benjamini-Hochberg false-discovery control.
It means "supported on development data", never "proven". A descriptive study without a claim is OBSERVED."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as st

from .observer import atr, bar_end, derive
from .sessions import minute_of_day


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _atr_lookup(bars1: pd.DataFrame, cfg: dict) -> pd.Series:
    """ATR of 5-minute bars, indexed by the time each bar COMPLETES, so an asof lookup at `available_at` only sees finished bars."""
    b5 = derive(bars1, "5min")
    a = atr(b5, int(cfg["behavior"]["atr_window"]))
    a.index = bar_end(b5.index, "5min")
    return a.dropna()


def _outcomes(bars1: pd.DataFrame, arr: dict, entry_times, signs, atrs, horizon: int, cost_price: float) -> pd.DataFrame:
    idx = bars1.index
    pos = idx.searchsorted(pd.DatetimeIndex(entry_times))
    n = len(idx)
    rows = []
    for p, s, a in zip(pos, signs, atrs):
        if p >= n or p + horizon > n or not a or np.isnan(a):
            rows.append((np.nan, np.nan, np.nan, np.nan))
            continue
        entry = arr["open"][p]
        exit_ = arr["close"][p + horizon - 1]
        hi, lo = arr["high"][p:p + horizon].max(), arr["low"][p:p + horizon].min()
        gross = s * (exit_ - entry) / a
        mfe = (hi - entry) / a if s > 0 else (entry - lo) / a
        mae = (entry - lo) / a if s > 0 else (hi - entry) / a
        rows.append((gross, gross - cost_price / a, mfe, mae))
    return pd.DataFrame(rows, columns=["gross", "net", "mfe", "mae"])


def _cluster_boot(values: np.ndarray, clusters: np.ndarray, B: int, rng) -> np.ndarray:
    """Bootstrap of the mean resampling whole sessions (events in one session are not independent)."""
    u = np.unique(clusters)
    groups = [values[clusters == c] for c in u]
    sums = np.array([g.sum() for g in groups]); cnt = np.array([len(g) for g in groups])
    out = np.empty(B)
    for b in range(B):
        k = rng.integers(0, len(u), len(u))
        out[b] = sums[k].sum() / max(1, cnt[k].sum())
    return out


def study(events: pd.DataFrame, bars1: pd.DataFrame, cfg: dict, name: str, definition: dict | None = None, claim: int | None = +1,
          regimes: pd.DataFrame | None = None, variants: dict | None = None, horizon: int | None = None) -> dict:
    """events: columns available_at, sign (+1 long / -1 short, the hypothesised direction), session. `claim=+1`: the claim is 'the signed outcome is
    positive after costs'; claim=None: descriptive only (status OBSERVED)."""
    bh = cfg["behavior"]
    rng = np.random.default_rng(int(bh["seed"]))
    H = int(horizon or bh["horizons_bars"][1]) * 5                               # horizons are in 5-minute units -> minutes
    cost_price = bh["cost_ticks"] * cfg["structure"]["tick_size"]
    arr = {k: bars1[k].to_numpy() for k in ("open", "high", "low", "close")}
    atrs = _atr_lookup(bars1, cfg)
    ev = events.sort_values("available_at").reset_index(drop=True)
    a_at = atrs.reindex(pd.DatetimeIndex(ev["available_at"]), method="ffill").to_numpy() if len(atrs) else np.full(len(ev), np.nan)
    out_ev = _outcomes(bars1, arr, ev["available_at"], ev["sign"].to_numpy(), a_at, H, cost_price)
    ok = out_ev["net"].notna().to_numpy()
    ev, out_ev = ev[ok].reset_index(drop=True), out_ev[ok].reset_index(drop=True)
    n = len(ev)
    res = {"name": name, "definition": definition or {}, "horizon_min": H, "n_events": n, "n_sessions": int(ev["session"].nunique()) if n else 0,
           "claim": claim, "config_version": cfg["config_version"], "scope": "development"}
    if n < 3:
        res.update(status="INCONCLUSIVE", limits=[f"solo {n} eventi misurabili"], distribution=None)
        return res
    net = out_ev["net"].to_numpy(); gross = out_ev["gross"].to_numpy()
    cl = ev["session"].astype(str).to_numpy()
    boot = _cluster_boot(net, cl, int(bh["bootstrap"]), rng)
    lo_, hi_ = np.percentile(boot, [2.5, 97.5])
    wins = int((net > 0).sum()); wl, wh = wilson(wins, n)
    res["distribution"] = {"mean_net": float(net.mean()), "mean_gross": float(gross.mean()), "median_net": float(np.median(net)), "std": float(net.std(ddof=1)),
                           "skew": float(st.skew(net)), "kurtosis": float(st.kurtosis(net)), "quantiles": {q: float(np.percentile(net, q)) for q in (5, 25, 50, 75, 95)},
                           "mean_ci95": [float(lo_), float(hi_)], "win_rate": wins / n, "win_rate_ci95": [wl, wh],
                           "mfe_median": float(out_ev["mfe"].median()), "mae_median": float(out_ev["mae"].median()),
                           "mfe_q": {q: float(np.percentile(out_ev["mfe"], q)) for q in (25, 50, 75, 95)}, "mae_q": {q: float(np.percentile(out_ev["mae"], q)) for q in (25, 50, 75, 95)}}
    # --- baseline: same minute-of-day window, other sessions, same long/short mix --------------------------------------------------------
    mod = minute_of_day(bars1.index)
    sess = pd.to_datetime(bars1["session"]).dt.strftime("%Y-%m-%d").to_numpy() if "session" in bars1.columns else np.array([str(d)[:10] for d in bars1.index.normalize()])
    ev_sessions = set(ev["session"].astype(str).str[:10])
    not_event = ~pd.Series(sess).isin(ev_sessions).to_numpy()                          # once, not per event
    base_net, base_cl = [], []
    for i in range(n):
        t = pd.Timestamp(ev.loc[i, "available_at"]); m0 = t.hour * 60 + t.minute
        pool = np.flatnonzero((np.abs(mod - m0) <= 15) & not_event)
        if len(pool) == 0:
            continue
        pick = rng.choice(pool, size=int(bh["baseline_per_event"]))
        signs = rng.choice(ev["sign"].to_numpy(), size=len(pick))
        a_b = atrs.reindex(bars1.index[pick], method="ffill").to_numpy() if len(atrs) else np.full(len(pick), np.nan)
        ob = _outcomes(bars1, arr, bars1.index[pick], signs, a_b, H, cost_price)
        v = ob["net"].dropna().to_numpy()
        base_net.append(v); base_cl.append(np.full(len(v), cl[i]))
    if base_net:
        bn = np.concatenate(base_net); bc = np.concatenate(base_cl)
        bmean = float(bn.mean())
        # joint bootstrap by session: events and their own baseline draws travel together
        u = np.unique(cl); diffs = np.empty(int(bh["bootstrap"]))
        ev_by = {c: net[cl == c] for c in u}; ba_by = {c: bn[bc == c] for c in u if (bc == c).any()}
        for b in range(len(diffs)):
            pick_c = u[rng.integers(0, len(u), len(u))]
            e = np.concatenate([ev_by[c] for c in pick_c]); bb = [ba_by[c] for c in pick_c if c in ba_by]
            diffs[b] = e.mean() - (np.concatenate(bb).mean() if bb else bmean)
        diff = float(net.mean() - bmean)
        p = float(2 * min((diffs <= 0).mean(), (diffs >= 0).mean()))
        res["baseline"] = {"mean_net": bmean, "n": int(len(bn)), "diff": diff, "diff_ci95": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))], "p_value": min(1.0, p) if p > 0 else 1.0 / (len(diffs) + 1)}
    else:
        res["baseline"] = None
    # --- concentration, stability, dependence, costs ------------------------------------------------------------------------------------
    pos = np.sort(net[net > 0])[::-1]
    top_n = max(1, int(round(0.1 * n)))
    res["concentration"] = {"top10pct_share_of_gains": float(pos[:top_n].sum() / pos.sum()) if pos.sum() > 0 else None,
                            "best_session_share": (lambda s: float(s.max() / s[s > 0].sum()) if (s > 0).any() else None)(pd.Series(net).groupby(cl).sum().to_numpy())}
    half = n // 2
    qs = np.array_split(np.arange(n), 4)
    res["stability"] = {"first_half": float(net[:half].mean()) if half else None, "second_half": float(net[half:].mean()),
                        "halves_agree_in_sign": bool(half and np.sign(net[:half].mean()) == np.sign(net[half:].mean())),
                        "quarters_positive": int(sum(net[q].mean() > 0 for q in qs if len(q) >= 5)), "quarters_measured": int(sum(len(q) >= 5 for q in qs))}
    res["dependence"] = {"events_per_session": n / max(1, res["n_sessions"]), "effective_n_sessions": res["n_sessions"]}
    cu = gross - net                                                          # cost per event in ATR units
    res["cost_sensitivity"] = {f"x{m}": float((gross - m * cu).mean()) for m in (0, 1, 2, 3)}
    res["cost_sensitivity"]["breakeven_cost_multiple"] = float(gross.mean() / cu.mean()) if cu.mean() > 0 and gross.mean() > 0 else None
    # --- regime dependence ---------------------------------------------------------------------------------------------------------------
    reg = {}
    if regimes is not None and len(regimes):
        labs = regimes["labels"].reindex(pd.DatetimeIndex(ev["available_at"]), method="ffill").fillna("UNCERTAIN").tolist()
        for lab in sorted({x for l in labs for x in str(l).split("|")}):
            m = np.array([lab in str(l).split("|") for l in labs])
            if m.sum() >= max(8, int(bh["min_events"]) // 3):
                reg[lab] = {"n": int(m.sum()), "mean_net": float(net[m].mean())}
    res["by_regime"] = reg
    # --- sensitivity to the event definition -----------------------------------------------------------------------------------------------
    if variants:
        vm = {k: float(v["distribution"]["mean_net"]) for k, v in variants.items() if v.get("distribution")}
        res["definition_sensitivity"] = {"variants": vm, "min": min(vm.values()), "max": max(vm.values()), "same_sign": bool(all(np.sign(x) == np.sign(net.mean()) for x in vm.values()))} if vm else None
    # --- limits + status ---------------------------------------------------------------------------------------------------------------------
    limits = ["campione di sviluppo: nessuna conferma indipendente", "esiti stimati a 1 minuto con costi ipotizzati, non esecuzione reale"]
    if n < bh["min_events"]:
        limits.append(f"pochi eventi ({n} < {bh['min_events']})")
    if res["n_sessions"] < bh["min_events"] // 2:
        limits.append(f"eventi concentrati in {res['n_sessions']} sessioni (osservazioni dipendenti)")
    if res["concentration"]["top10pct_share_of_gains"] and res["concentration"]["top10pct_share_of_gains"] > bh["top_share_max"]:
        limits.append("il guadagno dipende da pochi eventi")
    if not res["stability"]["halves_agree_in_sign"]:
        limits.append("le due metà del periodo non concordano")
    if not res["baseline"]:
        limits.append("baseline non disponibile")
    res["limits"] = limits
    res["p_value"] = res["baseline"]["p_value"] if res["baseline"] else None
    res["status"] = "OBSERVED" if claim is None else _status(res, bh)
    return res


def _status(res: dict, bh: dict) -> str:
    """Status BEFORE multiplicity control; `apply_fdr` can only demote."""
    b = res.get("baseline")
    if res["n_events"] < bh["min_events"] or not b or res["n_sessions"] < bh["min_events"] // 2:
        return "INCONCLUSIVE"
    lo, hi = b["diff_ci95"]
    sgn = res["claim"] or +1
    if sgn * hi < 0 and sgn * lo < 0:
        return "CONTRADICTED"
    top = res["concentration"]["top10pct_share_of_gains"]
    if sgn * lo > 0 and res["stability"]["halves_agree_in_sign"] and (top is None or top <= bh["top_share_max"]) and res["distribution"]["mean_net"] * sgn > 0:
        return "SUPPORTED"
    return "INCONCLUSIVE"


def apply_fdr(studies: list[dict], q: float) -> list[dict]:
    """Benjamini-Hochberg across the studies of ONE batch: a SUPPORTED result whose adjusted p-value exceeds q becomes INCONCLUSIVE. Studies without
    a p-value are untouched. Every study counts, whether or not it looks promising."""
    idx = [i for i, s in enumerate(studies) if s.get("p_value") is not None]
    if not idx:
        return studies
    p = np.array([studies[i]["p_value"] for i in idx]); order = np.argsort(p); m = len(p)
    adj = np.empty(m); run = 1.0
    for rank in range(m - 1, -1, -1):
        run = min(run, p[order[rank]] * m / (rank + 1)); adj[order[rank]] = run
    for j, i in enumerate(idx):
        studies[i]["q_value"] = float(adj[j]); studies[i]["tests_in_batch"] = m
        if studies[i]["status"] == "SUPPORTED" and adj[j] > q:
            studies[i]["status"] = "INCONCLUSIVE"
            studies[i]["limits"] = studies[i].get("limits", []) + [f"non regge il controllo dei falsi positivi su {m} studi (q={adj[j]:.2f} > {q})"]
    return studies
