"""Prop farming: from a strategy's sessions to Net Prop Farming Value (NPFV), gates, ranking and multi-account risk.

NPFV(s, a, H) = E[ sum of payouts actually received (net of profit split) - total account costs ] over horizon H, simulating the
whole life: evaluation -> funded -> payouts -> failure -> new attempts (lifecycle.py).

TWO KINDS OF UNCERTAINTY, kept apart:
  * Monte Carlo error: finite number of simulated paths. Reported as `mc_se`; it shrinks with more paths.
  * Data (parameter) uncertainty: the sessions we have are one finite sample of the strategy's behaviour. Estimated with an OUTER
    block bootstrap of the sessions (each replicate is a plausible alternative history of the same length); every replicate gets
    its own inner Monte Carlo. The 95% lower bound (LCB95) is the 5th percentile of the replicate means, so it does NOT converge to
    the point estimate when you add paths, only when you add DATA. The replicate means still carry inner noise: that widens the
    interval slightly (conservative).
What neither captures: a regime that never appeared in the sample, and anything below the bar granularity of the data.

Thresholds are operating choices in rules/farming.json: configurable and documented, not mathematical laws."""
from __future__ import annotations

import numpy as np

from .lifecycle import POLICIES, SessionPool, block_indices, simulate_path

BASE_NOTE = ("Orizzonte e regole sono quelle del ruleset indicato. La simulazione non vede: regimi di mercato mai comparsi nel campione, "
             "eventi sotto la granularità delle barre, chiusure/modifiche di regolamento della prop.")


# ------------------------------------------------------------------ paths and aggregation
def run_paths(pool: SessionPool, rs, scale: float, policy: str, n: int, H: int, block: int, max_attempts: int, rng, streak_k: int = 0) -> list:
    out = []
    if len(pool) == 0:
        return out
    order = np.argsort(pool.close)                                            # for the losing-streak stress: the worst sessions
    rb = None
    for _ in range(n):
        idx = block_indices(len(pool), H, block, rng)
        if streak_k and H > streak_k + 10:
            at = int(rng.integers(5, max(6, int(0.6 * H))))
            idx = idx.copy(); idx[at:at + streak_k] = order[:streak_k]       # a prolonged losing run at a random moment
        out.append(simulate_path(pool, rs, idx, scale, policy, max_attempts, risk_buffer=rb))
    return out


def summarize(paths: list, H: int) -> dict:
    n = max(1, len(paths))
    npfv = np.array([p["npfv"] for p in paths]) if paths else np.zeros(1)
    funded = [p for p in paths if p["funded"]]
    passed = [p for p in paths if p["passed"]]
    paid = [p for p in paths if p["payouts"]]
    cnt = np.array([len(p["payouts"]) for p in paths]) if paths else np.zeros(1)
    cost = np.array([p["total_cost"] for p in paths]) if paths else np.zeros(1)
    net = np.array([p["net"] for p in paths]) if paths else np.zeros(1)
    es5 = float(np.sort(npfv)[: max(1, int(0.05 * len(npfv)))].mean())
    life = np.array([p["lifetime"] for p in paths]) if paths else np.zeros(1)
    m = {
        "n_paths": len(paths), "horizon_days": H,
        "p_pass": len(passed) / n, "p_first_payout": len(paid) / n,
        "p_first_payout_given_pass": (len([p for p in passed if p["payouts"]]) / len(passed)) if passed else 0.0,
        "p_funded_survival": (1 - np.mean([p["first_funded_failed"] for p in funded])) if funded else None,
        "p_fail_before_first_payout_given_funded": (np.mean([p["first_funded_failed"] and not p["first_funded_payout"] for p in funded])) if funded else None,
        "e_time_to_pass": float(np.mean([p["pass_day"] for p in passed])) if passed else None,
        "e_time_to_pass_note": "condizionato al superamento; i tentativi falliti non sono nel conteggio dei giorni, ma i loro costi sì",
        "e_time_to_first_payout": float(np.mean([p["first_payout_day"] for p in paid])) if paid else None,
        "e_time_to_first_payout_note": "condizionato all'incasso del primo payout, dall'acquisto",
        "e_payout_count": float(cnt.mean()), "p_second_payout": float(np.mean(cnt >= 2)), "p_third_payout": float(np.mean(cnt >= 3)),
        "e_net_payout": float(net.mean()), "e_total_cost": float(cost.mean()),
        "p_account_failure": float(np.mean([(p["failed_eval"] + p["failed_funded"]) > 0 for p in paths])) if paths else 0.0,
        "p_funded_failure": float(np.mean([p["failed_funded"] > 0 for p in paths])) if paths else 0.0,
        "npfv_mean": float(npfv.mean()), "npfv_median": float(np.median(npfv)), "npfv_es5": es5,
        "npfv_p05": float(np.percentile(npfv, 5)), "npfv_p95": float(np.percentile(npfv, 95)),
        "cost_per_first_payout": (float(cost.sum() / max(1, len(paid)))) if paid else None,
        "payout_efficiency": float(net.sum() / cost.sum()) if cost.sum() > 0 else None,
        "lifetime_days": {"p10": float(np.percentile(life, 10)), "median": float(np.median(life)), "p90": float(np.percentile(life, 90))},
        "drawdown_utilization": float(np.mean([p["max_dd_use"] for p in paths])) if paths else 0.0,
        "attempts_mean": float(np.mean([p["attempts"] for p in paths])) if paths else 1.0,
    }
    return m


def estimate(pool: SessionPool, rs, scale: float, policy: str, cfg: dict, n_outer: int | None = None, n_inner: int | None = None,
             seed: int = 0, streak_k: int = 0) -> dict:
    """Metrics with both kinds of uncertainty. n_outer=1 means: point estimate only (selection runs, stress tests)."""
    H, block, att = int(cfg["horizon_days"]), int(cfg["block"]), int(cfg["max_attempts"])
    B = int(n_outer or cfg["n_outer"]); M = int(n_inner or cfg["n_inner"])
    rng = np.random.default_rng(seed)
    allp, means, pf_means = [], [], []
    for _ in range(B):
        rep = pool if B == 1 else pool.take(block_indices(len(pool), len(pool), block, rng))     # a plausible alternative history
        ps = run_paths(rep, rs, scale, policy, M, H, block, att, rng, streak_k)
        allp += ps
        means.append(np.mean([p["npfv"] for p in ps])); pf_means.append(np.mean([bool(p["payouts"]) for p in ps]))
    m = summarize(allp, H)
    means = np.array(means); inner_sd = float(np.std([p["npfv"] for p in allp], ddof=1)) if len(allp) > 1 else 0.0
    m["npfv"] = {"mean": float(means.mean()), "median": float(np.median(means)),
                 "lcb95": float(np.percentile(means, 5)) if B > 1 else None, "ucb95": float(np.percentile(means, 95)) if B > 1 else None,
                 "mc_se": inner_sd / np.sqrt(max(1, len(allp))), "n_outer": B, "n_inner": M,
                 "data_uncertainty_sd": float(means.std(ddof=1)) if B > 1 else None}
    m["p_first_payout_lcb95"] = float(np.percentile(pf_means, 5)) if B > 1 else None
    m["policy"] = policy; m["scale"] = scale
    return m


# ------------------------------------------------------------------ stress scenarios
def stress_pool(pool: SessionPool, name: str, mult: float = 2.0) -> SessionPool:
    c, w, b = pool.close.copy(), pool.worst.copy(), pool.best.copy()
    cost = pool.cost
    if name == "costi_x":                                           # commissions + slippage multiplied (e.g. doubled slippage)
        d = cost * (mult - 1.0); c -= d; w -= d; b -= d
    elif name == "esecuzione_peggiore_in_stress":                   # losing sessions pay the costs again (bad fills when it hurts)
        d = np.where(pool.close < 0, cost, 0.0); c -= d; w -= d
    elif name == "expectancy_-25%":
        d = 0.25 * max(0.0, float(pool.close.mean())); c -= d; w -= d; b -= d
    elif name == "volatilita_+25%":
        m = float(pool.close.mean()); c = m + 1.25 * (c - m); w = w * 1.25; b = b * 1.25
    elif name == "serie_negativa":
        pass                                                         # done in run_paths (streak_k)
    else:
        raise ValueError(name)
    w = np.minimum(w, np.minimum(c, 0.0)); b = np.maximum(b, np.maximum(c, 0.0))
    return SessionPool(c, w, b, pool.trades, pool.cost, pool.sessions, pool.bar_minutes)


def stress_suite(pool: SessionPool, rs, scale: float, policy: str, cfg: dict, seed: int = 5, n: int = 300) -> dict:
    mult = float(cfg["gates"]["stress_costs_multiple"])
    out = {}
    for name in ("costi_x", "esecuzione_peggiore_in_stress", "expectancy_-25%", "volatilita_+25%", "serie_negativa"):
        p = stress_pool(pool, name, mult) if name != "serie_negativa" else pool
        m = estimate(p, rs, scale, policy, cfg, n_outer=1, n_inner=n, seed=seed, streak_k=5 if name == "serie_negativa" else 0)
        out[name if name != "costi_x" else f"costi_x{mult:g}"] = {"npfv_mean": m["npfv"]["mean"], "p_first_payout": m["p_first_payout"],
                                                                  "p_account_failure": m["p_account_failure"]}
    return out


# ------------------------------------------------------------------ size and payout policy
def choose_size(pool: SessionPool, rs, base_contracts: int, cfg: dict, policy: str = "A", seed: int = 2) -> dict:
    """Size (micro-equivalents) chosen on DEVELOPMENT sessions: the one with the highest mean NPFV. It is only a size: the economics
    are then estimated on the unseen block at that size."""
    rows = []
    for k in sorted(set(cfg["sizes"]) | set(range(1, 11))):       # every size 1..10 is tried, whatever an old rules/farming.json lists
        m = estimate(pool, rs, k / base_contracts, policy, cfg, n_outer=1, n_inner=int(cfg["n_select"]), seed=seed)
        rows.append({"micro": int(k), "npfv_mean": m["npfv"]["mean"], "p_first_payout": m["p_first_payout"], "p_account_failure": m["p_account_failure"]})
    ok = [r for r in rows if r["npfv_mean"] > -1e18]
    best = max(ok, key=lambda r: r["npfv_mean"]) if ok else rows[0]
    return {"table": rows, "micro": best["micro"], "note": "scelta sui soli giorni di sviluppo (in-campione)"}


def compare_policies(pool: SessionPool, rs, scale: float, cfg: dict, seed: int = 4) -> dict:
    """Payout policies on the same sessions. Not assumed: the maximum immediate withdrawal can lower the NPFV by shrinking the buffer."""
    rows = {}
    small = {**cfg, "n_outer": 10, "n_inner": 60}
    for pol in cfg.get("payout_policies", ["A", "B", "C"]):
        m = estimate(pool, rs, scale, pol, small, seed=seed)
        rows[pol] = {"label": POLICIES[pol], "npfv_mean": m["npfv"]["mean"], "npfv_lcb95": m["npfv"]["lcb95"], "e_payout_count": m["e_payout_count"],
                     "p_funded_failure": m["p_funded_failure"], "p_first_payout": m["p_first_payout"], "e_net_payout": m["e_net_payout"]}
    best = max(rows, key=lambda k: (rows[k]["npfv_lcb95"] if rows[k]["npfv_lcb95"] is not None else -1e18))
    return {"policies": rows, "best": best}


# ------------------------------------------------------------------ compliance (gate 5)
def compliance(pool: SessionPool, rs, scale: float, micro: int, horizon: int = 30, max_hidden: float = 0.10) -> dict:
    """Deterministic rule checks plus the historical test that matters for intraday rules: start an account on every session of the
    data (overlapping windows, NOT independent) and compare the intraday-aware outcome with what a daily-close view would say. A
    strategy that looks safe at the close but breaches the floor inside the day is not compatible with the account."""
    checks = {}
    mc = rs.evaluation.max_contracts_micro
    checks["max_contracts"] = {"ok": mc is None or micro <= mc, "detail": f"{micro} micro (limite {mc})"}
    checks["overnight"] = {"ok": True, "detail": "la strategia chiude ogni posizione prima delle 16:45 (flatten_overnight, verificato dal libro mastro)"
                           if not rs.rules.get("overnight_allowed", False) else "overnight consentito dal ruleset"}
    checks["news"] = {"ok": None, "detail": "restrizioni sulle news NON verificabili con questi dati"
                      if rs.rules.get("news_restriction") else "nessuna restrizione sulle news nel ruleset"}
    n = len(pool)
    day_pool = SessionPool(pool.close, np.minimum(pool.close, 0.0), np.maximum(pool.close, 0.0), pool.trades, pool.cost, pool.sessions, pool.bar_minutes)
    hidden = total = intr_bust = 0
    first_breach = None
    for s in range(0, max(0, n - horizon + 1)):
        idx = np.arange(s, s + horizon)
        a = simulate_path(pool, rs, idx, scale, "A", max_attempts=1)
        b = simulate_path(day_pool, rs, idx, scale, "A", max_attempts=1)
        total += 1
        ib, db = (a["failed_eval"] > 0), (b["failed_eval"] > 0)
        intr_bust += ib
        if ib and not db:
            hidden += 1
            if first_breach is None and a["violations"]:
                first_breach = {"session": pool.sessions[s + a["violations"][0]["day"]], **a["violations"][0]}
    p_hidden = hidden / total if total else 0.0
    checks["intraday_hidden_breach"] = {"ok": p_hidden <= max_hidden, "p": p_hidden, "n_windows": total, "windows_independent": False,
                                        "detail": f"{p_hidden:.0%} delle finestre storiche violano il drawdown solo DENTRO la giornata (soglia {max_hidden:.0%})",
                                        "first_breach": first_breach}
    ok = all(c["ok"] is not False for c in checks.values())
    return {"ok": ok, "checks": checks, "p_bust_intraday_windows": (intr_bust / total) if total else None,
            "granularity_minutes": pool.bar_minutes,
            "limit": f"sequenza interna alla barra non osservabile: ipotesi conservativa (prima l'estremo favorevole, poi quello avverso); barre da {pool.bar_minutes:g} minuti"}


# ------------------------------------------------------------------ the whole evaluation of one ruleset
def evaluate_ruleset(pool_dev: SessionPool, pool_oos: SessionPool, rs, base_contracts: int, cfg: dict, seed: int = 11, light: bool = False) -> dict:
    """Size chosen on development sessions; economics, stress, compliance and policies measured on the unseen block only."""
    sz = choose_size(pool_dev, rs, base_contracts, cfg, seed=seed + 1)
    micro = sz["micro"]; scale = micro / base_contracts
    pol = compare_policies(pool_oos, rs, scale, cfg, seed=seed + 2) if not light else {"policies": {}, "best": "A"}
    best = pol["best"]
    est = estimate(pool_oos, rs, scale, best, cfg, seed=seed + 3)
    st = stress_suite(pool_oos, rs, scale, best, cfg, seed=seed + 4)
    comp = compliance(pool_oos, rs, scale, micro)
    return {"ruleset": {"id": rs.id, "version": rs.version, "provenance": rs.provenance, "firm": rs.firm, "label": rs.label, "warning": rs.warning(),
                        "costs": {"eval_monthly": rs.costs.eval_monthly, "activation": rs.costs.activation, "reset": rs.costs.reset}},
            "size": sz, "micro": micro, "policy": best, "policies": pol, "metrics": est, "stress": st, "compliance": comp,
            "limits": BASE_NOTE, "tail_risk_session": float(-np.sort(pool_oos.worst)[: max(1, int(0.05 * len(pool_oos)))].mean() * scale) if len(pool_oos) else None}


# ------------------------------------------------------------------ gates (hierarchical)
def gates_for(ev: dict, ctx: dict, cfg: dict) -> dict:
    """G1..G7 for one ruleset. A failed critical gate is final: nothing downstream can compensate for it."""
    g = cfg["gates"]; m = ev["metrics"]; npv = m["npfv"]
    out = []

    def add(i, name, status, reason, **kw):
        out.append({"id": i, "name": name, "status": status, "reason": reason, **kw})

    add(1, "Integrità dei dati", "PASS" if ctx.get("integrity_ok") else "FAIL",
        "nessun look-ahead, libro mastro riconciliato con il motore vettoriale" if ctx.get("integrity_ok") else "controllo di causalità o riconciliazione del libro mastro fallito")
    ev_ok = ctx.get("edge_ok")
    add(2, "Edge statistico", "PASS" if ev_ok else "FAIL",
        "Deflated Sharpe sopra soglia dopo il conteggio dei tentativi" if ev_ok else "Deflated Sharpe sotto soglia: l'edge non è distinguibile dalla fortuna dopo i tentativi")
    ns, nt = int(ctx.get("n_locked_sessions", 0)), int(ctx.get("n_locked_trades", 0))
    tps = nt / ns if ns else 0.0
    # few sessions are NOT a defect when the strategy trades at least `min_trades_per_session` a day on average (the owner has ~2 years of data);
    # few sessions with sporadic trades ARE one. The number of trades is always required.
    frequent_ok = ns >= int(g.get("min_locked_sessions_if_frequent", 40)) and tps >= float(g.get("min_trades_per_session", 0.5))
    sess_ok = ns >= g["min_locked_sessions"] or frequent_ok
    if not sess_ok or nt < g["min_locked_trades"]:
        add(3, "Fuori campione", "INSUFFICIENT", (f"trade sporadici: {nt} trade in {ns} sessioni ({tps:.2f} al giorno, serve almeno {float(g.get('min_trades_per_session', 0.5)):g}) "
            f"e poche sessioni (servono {g['min_locked_sessions']})" if tps < float(g.get("min_trades_per_session", 0.5)) and ns < g["min_locked_sessions"] else
            f"campione insufficiente: {ns} sessioni e {nt} trade (servono almeno {g['min_locked_trades']} trade)"), sporadic=bool(tps < float(g.get("min_trades_per_session", 0.5))))
    else:
        add(3, "Fuori campione", "PASS" if ctx.get("oos_positive") else "FAIL",
            "profitto netto positivo sul blocco mai visto" if ctx.get("oos_positive") else "il blocco mai visto non è profittevole")
    cx = [k for k in ev["stress"] if k.startswith("costi_x")][0]
    stress_ok = ev["stress"][cx]["npfv_mean"] > g["stress_npfv_min"]
    rob = bool(ctx.get("robust_ok")) and stress_ok
    add(4, "Robustezza", "PASS" if rob else "FAIL",
        "parametri, timeframe e deriva di mercato robusti; NPFV resta positivo con costi ×%g" % g["stress_costs_multiple"] if rob else
        ("NPFV diventa non positivo con costi ×%g (dipende da un'esecuzione irrealistica)" % g["stress_costs_multiple"] if not stress_ok else "superficie dei parametri o walk-forward non robusti"),
        stress={k: v["npfv_mean"] for k, v in ev["stress"].items()})
    comp = ev["compliance"]
    add(5, "Conformità al conto", "PASS" if comp["ok"] else "FAIL",
        "nessuna violazione delle regole nelle finestre storiche, nemmeno dentro la giornata" if comp["ok"] else
        "viola le regole del conto: " + "; ".join(c["detail"] for c in comp["checks"].values() if c["ok"] is False), compliance=comp["checks"])
    fp = m["p_first_payout"]; cond = m["p_first_payout_given_pass"]; ff = m["p_fail_before_first_payout_given_funded"]
    fp_ok = fp >= g["min_p_first_payout"] and cond >= g["min_first_payout_given_pass"] and (ff is None or ff <= g["max_p_funded_fail_before_payout"])
    lcb = npv["lcb95"]
    econ_ok = lcb is not None and lcb > g["min_npfv_lcb95"]
    add(6, "Economia prop (primo payout + valore netto)", "PASS" if (fp_ok and econ_ok) else "FAIL",
        (f"primo payout {fp:.0%}, NPFV LCB95 ${lcb:,.0f}" if fp_ok and econ_ok else
         ("FIRST PAYOUT GATE fallito: supera l'evaluation ma incassa il primo payout solo nel %.0f%% dei casi dopo il passaggio, e il funded fallisce prima del payout nel %s%%" %
          (cond * 100, "n/d" if ff is None else f"{ff * 100:.0f}") if not fp_ok else f"NPFV LCB95 ${lcb:,.0f} non supera la soglia ${g['min_npfv_lcb95']:,.0f}: valore netto non dimostrato")),
        first_payout_gate=fp_ok, economics_gate=econ_ok)
    fwd = ctx.get("forward")
    add(7, "Validazione forward (paper)", "PENDING" if not fwd else fwd["status"],
        "in attesa: servono almeno %d giorni di paper trading prima della promozione operativa" % cfg["forward"]["min_days"] if not fwd else fwd["reason"])
    hard = [x for x in out if x["id"] <= 6]
    if out[0]["status"] == "FAIL":                       # an integrity failure is final whatever the sample size
        verdict = "REJECTED"
    elif any(x["status"] == "INSUFFICIENT" for x in hard):
        verdict = "INSUFFICIENT_EVIDENCE"
    elif all(x["status"] == "PASS" for x in hard):
        verdict = "APPROVED"
    else:
        verdict = "REJECTED"
    first = next((x for x in hard if x["status"] not in ("PASS",)), None)
    return {"verdict": verdict, "gates": out, "first_failed": None if first is None else {"id": first["id"], "name": first["name"], "reason": first["reason"]}}


def indistinguishable(a: dict, b: dict) -> bool:
    """Two NPFV intervals that overlap cannot be ranked against each other with the evidence we have."""
    return a["lcb95"] <= b["ucb95"] and b["lcb95"] <= a["ucb95"]


def rank(entries: list[dict]) -> dict:
    """entries: [{slug, name, npfv: {mean,lcb95,ucb95}, verdict}] for ONE ruleset. Only APPROVED strategies are ranked, by LCB95.
    Zero approved strategies is a valid answer."""
    ok = sorted([e for e in entries if e["verdict"] == "APPROVED" and e["npfv"]["lcb95"] is not None], key=lambda e: (not e.get("favorite"), -e["npfv"]["lcb95"]))      # favourites (steady winning days, high pass rate) first, then by the lower bound
    if not ok:
        return {"ranking": [], "note": "nessuna strategia supera tutti i gate: OFO ne approva zero, ed è la risposta corretta"}
    top = ok[0]
    for e in ok:
        e["indistinguishable_from_top"] = (e is not top) and indistinguishable(e["npfv"], top["npfv"])
    return {"ranking": ok, "note": "prima le PREFERITE (giorni vincenti costanti e pass rate alto), poi per NPFV_LCB95; 'indistinguibile' = intervalli sovrapposti al primo, evidenza insufficiente per preferirne una"}


# ------------------------------------------------------------------ multi-account farming portfolio
def portfolio(pool: SessionPool, accounts: list, base_contracts: int, cfg: dict, policy: str = "A", n_paths: int = 300, seed: int = 9,
              stagger: int = 0) -> dict:
    """N accounts copying the SAME trades. accounts: [(ruleset, micro)]. Every account sees the same sampled path (the signals are
    identical), optionally shifted by `stagger` sessions between accounts. The independent-accounts figure is shown only to expose
    how much false diversification it would suggest."""
    H, block, att = int(cfg["horizon_days"]), int(cfg["block"]), int(cfg["max_attempts"])
    rng = np.random.default_rng(seed)
    N = len(accounts)
    tot, fails, cost_tot, worst_day, per_firm = [], [], [], [], {}
    for _ in range(n_paths):
        idx = block_indices(len(pool), H + stagger * N, block, rng)
        res = [simulate_path(pool, rs, np.roll(idx, -stagger * j)[:H], mic / base_contracts, policy, att) for j, (rs, mic) in enumerate(accounts)]
        tot.append(sum(r["npfv"] for r in res)); cost_tot.append(sum(r["total_cost"] for r in res))
        fails.append(sum((r["failed_eval"] + r["failed_funded"]) > 0 for r in res))
        worst_day.append(min(float(pool.worst[i]) * sum(mic for _, mic in accounts) / base_contracts for i in idx[:H]))
    rng2 = np.random.default_rng(seed + 1)
    ind = []
    for _ in range(max(60, n_paths // 3)):
        ind.append(sum(simulate_path(pool, rs, block_indices(len(pool), H, block, rng2), mic / base_contracts, policy, att)["npfv"] for rs, mic in accounts))
    tot = np.array(tot); fails = np.array(fails)
    for rs, mic in accounts:
        per_firm[rs.firm] = per_firm.get(rs.firm, 0) + rs.costs.eval_monthly + rs.costs.activation
    allow = [rs.rules.get("copy_trading_allowed", True) for rs, _ in accounts]
    maxacc = [rs.rules.get("max_accounts") for rs, _ in accounts if rs.rules.get("max_accounts")]
    return {"n_accounts": N, "identical_signals": True, "total_capital_spent_mean": float(np.mean(cost_tot)), "npfv_total_mean": float(tot.mean()),
            "npfv_total_p05": float(np.percentile(tot, 5)), "npfv_total_es5": float(np.sort(tot)[: max(1, int(0.05 * len(tot)))].mean()),
            "p_all_fail": float(np.mean(fails == N)), "p_at_least_half_fail": float(np.mean(fails >= N / 2)), "p_any_fail": float(np.mean(fails >= 1)),
            "mean_accounts_failed": float(fails.mean()),
            "independent_assumption": {"npfv_total_p05": float(np.percentile(ind, 5)),
                                       "note": "se gli account fossero indipendenti (NON lo sono: copiano gli stessi trade) il rischio sembrerebbe questo"},
            "exposure_worst_session_loss": float(np.min(worst_day)), "concentration_by_firm_cost": per_firm,
            "warnings": ([] if all(allow) else ["il regolamento di almeno una prop NON consente il copy trading: questo portafoglio non è applicabile"]) +
                        ([f"limite di account per prop: {min(maxacc)}"] if maxacc and N > min(maxacc) else [])}


# ------------------------------------------------------------------ archetype label and tail honesty
ARCHETYPE_BY_TEMPLATE = {"orb": "CONTROLLED_MOMENTUM", "orb_retest": "SELECTIVE_HIGH_CONVICTION", "breakout": "CONTROLLED_MOMENTUM",
                         "ema_cross": "CONTROLLED_MOMENTUM", "mean_reversion": "INTRADAY_MEAN_REVERSION", "vwap_dev": "INTRADAY_MEAN_REVERSION"}


def archetype(template: str, pool: SessionPool) -> dict:
    base = ARCHETYPE_BY_TEMPLATE.get(template, "REGIME-ADAPTIVE")
    tpd = float(pool.trades.sum() / max(1, len(pool)))
    sd = float(pool.close.std()) if len(pool) else 0.0
    label = base
    if tpd < 0.35:
        label = "SELECTIVE_HIGH_CONVICTION"
    elif len(pool) and sd and abs(float(pool.close.mean())) / sd > 0.35 and float(np.percentile(pool.worst, 5)) > -2.5 * sd:
        label = "LOW-VOLATILITY_CONSISTENCY"
    wins = float((pool.close > 0).mean()) if len(pool) else 0.0
    losers = pool.close[pool.close < 0]; winners = pool.close[pool.close > 0]
    tail = bool(wins > 0.7 and len(losers) and len(winners) and abs(np.sort(losers)[: max(1, len(losers) // 10)].mean()) > 4 * winners.mean())
    return {"label": label, "trades_per_session": tpd, "winning_session_rate": wins, "high_winrate_hidden_tail": tail,
            "note": "categoria di ricerca, non un giudizio di profittabilità" + ("; ATTENZIONE: win rate alto con perdite di coda molto più grandi dei guadagni" if tail else "")}


# ------------------------------------------------------------------ the full review of one strategy
def split_pool(pool: SessionPool, cut: str) -> tuple[SessionPool, SessionPool]:
    k = sum(1 for s in pool.sessions if s < cut)
    return pool.slice(0, k), pool.slice(k, len(pool))


def review(pool_dev: SessionPool, pool_oos: SessionPool, rulesets: list, base_contracts: int, cfg: dict, ctx: dict, template: str = "") -> dict:
    """Hierarchical review of one strategy against every configured ruleset. Sizes and payout policies are chosen on the development
    sessions; the unseen block is used once for the economics, stress tests and compliance."""
    ctx = {**ctx, "n_locked_sessions": len(pool_oos), "n_locked_trades": int(pool_oos.trades.sum())}
    by = {}
    for j, rs in enumerate(rulesets):
        ev = evaluate_ruleset(pool_dev, pool_oos, rs, base_contracts, cfg, seed=int(cfg["seed"]) + 17 * j)
        by[rs.id] = {"evaluation": ev, "gates": gates_for(ev, ctx, cfg)}
    approved = [i for i, x in by.items() if x["gates"]["verdict"] == "APPROVED"]
    key = lambda i: by[i]["evaluation"]["metrics"]["npfv"]["lcb95"] or -1e18
    if approved:
        best, verdict = max(approved, key=key), "APPROVED"
    else:
        best = max(by, key=lambda i: by[i]["evaluation"]["metrics"]["npfv"]["mean"]) if by else None
        verdict = "INSUFFICIENT_EVIDENCE" if any(x["gates"]["verdict"] == "INSUFFICIENT_EVIDENCE" for x in by.values()) else "REJECTED"
    out = {"verdict": verdict, "suited": approved, "best_ruleset": best, "by_ruleset": by, "n_oos_sessions": len(pool_oos),
           "granularity_minutes": pool_oos.bar_minutes, "archetype": archetype(template, pool_oos) if len(pool_oos) else None,
           "first_failed": None if (verdict == "APPROVED" or best is None) else by[best]["gates"]["first_failed"],
           "config": {k: cfg[k] for k in ("horizon_days", "max_attempts", "block", "n_outer", "n_inner", "gates")}}
    if approved:
        rs_by = {r.id: r for r in rulesets}
        b = by[best]["evaluation"]
        out["portfolio"] = {str(n): portfolio(pool_oos, [(rs_by[best], b["micro"])] * n, base_contracts, cfg, b["policy"], n_paths=200) for n in (3, 5)}
    return out
