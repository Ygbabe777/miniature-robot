"""Dual deterministic Judge: EDGE JUDGE, PROP JUDGE, FINAL JUDGE.

Severe, not blunt. The legacy Judge answered one question ("approved or not") with one number mix. Three different questions hide in it:

* EDGE JUDGE   - after realistic costs, is there a statistical advantage that luck cannot explain? Independent of any prop firm's rules.
* PROP JUDGE   - given that edge, is it economically compatible with a ruleset + position size + payout policy (evaluation, first payout,
                 funded survival, net value of the whole cycle)? Evaluated per ruleset: an edge that fits no firm today may fit another.
* FINAL JUDGE  - a fixed precedence over the two. No score compensates for a failed critical gate. APPROVED is never easier than before:
                 it additionally requires the legacy farming review to approve the same strategy.

Everything here is a PURE FUNCTION of a stored study report (and, when available, its daily P&L series): no LLM, no randomness, no I/O,
no access to the locked block beyond the numbers the Judge already used. A stored study can be re-judged without touching its OOS result.

Verdicts: APPROVED, PAPER_CANDIDATE, INSUFFICIENT_EVIDENCE, NEEDS_ROBUSTNESS, EDGE_VALID_PROP_INCOMPATIBLE, EXECUTION_FAILED,
STATISTICALLY_REJECTED. STATISTICALLY_REJECTED requires evidence AGAINST the strategy; the mere absence of proof is INSUFFICIENT_EVIDENCE.
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np
from scipy import stats as st

from . import evidence as EV
from .stats import deflated_sharpe, probabilistic_sharpe, sharpe

ANN = math.sqrt(252)

APPROVED = "APPROVED"
PAPER_CANDIDATE = "PAPER_CANDIDATE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NEEDS_ROBUSTNESS = "NEEDS_ROBUSTNESS"
EDGE_VALID_PROP_INCOMPATIBLE = "EDGE_VALID_PROP_INCOMPATIBLE"
EXECUTION_FAILED = "EXECUTION_FAILED"
STATISTICALLY_REJECTED = "STATISTICALLY_REJECTED"
VERDICTS = (APPROVED, PAPER_CANDIDATE, INSUFFICIENT_EVIDENCE, NEEDS_ROBUSTNESS, EDGE_VALID_PROP_INCOMPATIBLE, EXECUTION_FAILED, STATISTICALLY_REJECTED)

# legacy three-valued status kept for everything that predates the dual judge (dossier, deployments, filters)
LEGACY = {APPROVED: "APPROVED", PAPER_CANDIDATE: "INSUFFICIENT_EVIDENCE", INSUFFICIENT_EVIDENCE: "INSUFFICIENT_EVIDENCE", NEEDS_ROBUSTNESS: "REJECTED",
          EDGE_VALID_PROP_INCOMPATIBLE: "REJECTED", EXECUTION_FAILED: "REJECTED", STATISTICALLY_REJECTED: "REJECTED"}

DEFAULTS = {
    "confidence": 0.95,              # APPROVED needs a Deflated Sharpe at least this high (also on the configured trial basis)
    "candidate_dsr": 0.70,           # PAPER_CANDIDATE needs at least this (never an approval: ~12-30% of no-edge strategies reach it alone)
    "candidate_psr0": 0.90,          # ... and P(true Sharpe > 0) at least this on the unseen block
    "min_useful_sharpe": 0.50,       # annual Sharpe below which an edge is not worth farming
    "against_psr0": 0.20,            # evidence against: P(true Sharpe > 0) at most this on the unseen block, with the sample sufficient
    "decay_z": 1.64,                 # evidence against: development -> unseen Sharpe drop beyond one-sided 95% sampling noise (overfit signature)
    "max_sharpe_ci_width": 8.0,      # sanity floor on the 95% interval of the annual Sharpe (DSR is the real test; this only refuses a degenerate estimate)
    "min_oos_days": 80,
    "min_oos_trades": 15,
    "min_backtest_trades": 100,
    "ci_z": 1.96,
}

FROZEN = ["blocco OOS bloccato: mai usato per scegliere o calibrare", "soglie del Judge e livelli di confidenza: non si cambiano per far passare questa strategia",
          "conteggio dei tentativi (registro): ogni variante nuova è un tentativo in più", "regole e provenienza dei ruleset: non si assumono regole più favorevoli"]

CLASS_LABEL = {"no_edge": "nessun edge dimostrato", "insufficient_sample": "campione insufficiente", "wide_interval": "incertezza troppo ampia",
               "overfit": "firma di overfitting", "high_costs": "fragile ai costi", "parametric_fragility": "fragilità parametrica",
               "prop_incompatible": "incompatibile con il ruleset", "sizing_incompatible": "dimensionamento incompatibile",
               "economics_unproven": "valore economico non dimostrato", "execution_error": "errore di esecuzione/contabilità", "ok": "ok"}


def cfg_from(research_cfg: dict | None) -> dict:
    return {**DEFAULTS, **((research_cfg or {}).get("judge") or {})}


# ------------------------------------------------------------------ statistics (analytic, deterministic)
def _finite(x) -> tuple[np.ndarray, int]:
    a = np.asarray(list(x), dtype=float) if x is not None else np.zeros(0)
    bad = int((~np.isfinite(a)).sum())
    return a, bad


def sharpe_interval(x, z: float = 1.96) -> dict:
    """Annualised Sharpe with its sampling interval (Lo/Mertens: skew and kurtosis aware). {sr, lo, hi, se, n}"""
    x = np.asarray(x, dtype=float); n = len(x)
    if n < 5 or float(np.std(x, ddof=1)) == 0.0:
        return {"sr": 0.0, "lo": None, "hi": None, "se": None, "n": n}
    sr = sharpe(x)
    skew, kurt = float(st.skew(x)), float(st.kurtosis(x, fisher=False))
    var = max(1e-12, (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (n - 1))
    se = math.sqrt(var)
    return {"sr": sr * ANN, "lo": (sr - z * se) * ANN, "hi": (sr + z * se) * ANN, "se": se * ANN, "n": n}


def power_needed(days: int, n_trials: int, var_sr: float, p: float) -> float:
    """Annual Sharpe a strategy must really have for its EXPECTED deflated Sharpe to reach p over `days` unseen sessions."""
    from .stats import expected_max_sharpe
    return float((expected_max_sharpe(n_trials, var_sr) + st.norm.ppf(p) / math.sqrt(max(2, days - 1))) * ANN)


# ------------------------------------------------------------------ one diagnostic row
def _row(gate, label, status, observed=None, threshold=None, interval=None, reason="", cls="ok", next_exp=None, recoverable=True, on_locked=False, scope="edge", **kw):
    return {"gate": gate, "label": label, "status": status, "observed": observed, "threshold": threshold, "interval": interval, "reason": reason,
            "classification": cls, "next_experiment": next_exp, "recoverable": bool(recoverable), "frozen": FROZEN if status != "PASS" else [],
            "uses_locked": bool(on_locked), "scope": scope, **kw}


def _num(v, d=2):
    return "n/d" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v:.{d}f}"


# ------------------------------------------------------------------ EDGE JUDGE
def edge_judge(report: dict, oos=None, dev=None, cfg: dict | None = None, lineage_looks: int = 0) -> dict:
    """Prop-independent. state: VALID | PLAUSIBLE | INSUFFICIENT | NEEDS_ROBUSTNESS | AGAINST | INVALID_EXECUTION. Rows explain each gate."""
    c = {**DEFAULTS, **(cfg or {})}
    lk = report.get("locked") or {}
    rows: list[dict] = []
    touched = "checks" in lk                                                 # the Judge actually looked at the unseen block
    x, nbad = _finite(oos) if oos is not None else (np.zeros(0), 0)

    # --- E0 integrity / accounting -------------------------------------------------------------------------------
    problems = []
    if report.get("accounting_error"):
        problems.append(f"errore di contabilità: {report['accounting_error']}")
    if nbad:
        problems.append(f"{nbad} valori non finiti (NaN/inf) nella serie fuori campione")
    rc = report.get("ledger_check")
    if touched and rc is not None and not rc.get("ok", True):
        problems.append("il libro mastro non riconcilia con il motore vettoriale")
    if touched and (report.get("repro") or {}).get("verified") is False:
        problems.append("risultato non riproducibile dai metadati salvati")
    if lk.get("farming_error"):
        problems.append(f"revisione prop non eseguibile: {lk['farming_error']}")
    dsr_v = (lk.get("dsr") or {}).get("value")
    if touched and dsr_v is not None and not math.isfinite(float(dsr_v)):
        problems.append("Deflated Sharpe non finito")
    rows.append(_row("E0", "Integrità ed esecuzione", "FAIL" if problems else "PASS", observed=len(problems), threshold=0,
                     reason="; ".join(problems) if problems else "contabilità, libro mastro, riproducibilità e numeri finiti: in ordine",
                     cls="execution_error" if problems else "ok", next_exp="correggere l'errore di contabilità/esecuzione e rilanciare lo stesso studio: nessun verdetto statistico è valido finché non è risolto" if problems else None,
                     recoverable=True, on_locked=False))
    if problems:
        return {"state": "INVALID_EXECUTION", "rows": rows, "metrics": {}}

    # --- robustness evidence from the development side (never needs the locked block) -------------------------------
    rob_fail, dev_against = [], []
    surf = report.get("surface") or {}
    for k, ok in (surf.get("checks") or {}).items():
        if ok:
            continue
        if k in ("median_sharpe", "frac_positive"):                    # most of the swept grid loses money even in development: evidence against the idea
            dev_against.append(f"la maggior parte delle combinazioni perde anche sullo sviluppo (controllo '{k}')")
        else:
            rob_fail.append(("parametric_fragility", f"superficie dei parametri: controllo '{k}' fallito"))
    sd = report.get("surface_detrended")
    if sd and not sd.get("robust", True):
        rob_fail.append(("parametric_fragility", "il profitto sparisce togliendo la deriva di mercato (beta, non edge)"))
    q = report.get("quality") or {}
    for k, ok in (q.get("checks") or {}).items():
        if not ok and k == "cost_2x":
            rob_fail.append(("high_costs", f"con costi e slippage ×2 lo Sharpe di sviluppo diventa {_num(q.get('sharpe_2x_costs'))}"))
        elif not ok and k == "next_open_fill":
            rob_fail.append(("high_costs", f"riempiendo all'apertura successiva lo Sharpe di sviluppo diventa {_num(q.get('sharpe_next_open'))}"))
    wf_pnl = report.get("wf_oos_pnl")
    wf_bad = wf_pnl is not None and wf_pnl <= 0
    plan = report.get("wf_plan") or {}
    wf_enough = bool(plan.get("target_met"))
    if wf_bad and wf_enough:
        dev_against.append(f"walk-forward cronologico non profittevole su tutte le finestre pianificate (P&L {wf_pnl:,.0f})")
    wf_short = bool(plan) and plan.get("target_met") is False
    q_trades_short = (q.get("checks") or {}).get("min_trades") is False

    # --- E1 sample -----------------------------------------------------------------------------------------------
    smp = lk.get("mc_sample") or {}
    days = int(len(x)) if len(x) else int(smp.get("days") or 0)
    trades = int(smp.get("n_trades") or 0)
    waived = bool(smp.get("days_waived"))
    bt = lk.get("backtest_trades") or {}
    sample_notes = []
    if touched:
        if days < c["min_oos_days"] and not waived:
            sample_notes.append(f"{days} sessioni fuori campione (ne servono {c['min_oos_days']}, o ≥ un trade a settimana)")
        if trades < c["min_oos_trades"]:
            sample_notes.append(f"{trades} trade fuori campione (ne servono {c['min_oos_trades']})")
        if bt and not bt.get("ok", True):
            sample_notes.append(f"{bt.get('n')} trade in tutto il backtest (ne servono {bt.get('min', c['min_backtest_trades'])})")
        if smp.get("sporadic"):
            sample_notes.append("trade sporadici (meno di uno a settimana)")
    else:
        if wf_short:
            sample_notes.append(f"finestre walk-forward {plan.get('windows')} su {plan.get('target_windows')} pianificate")
        if q_trades_short:
            sample_notes.append(f"{q.get('n_trades')} trade nel backtest di sviluppo: troppo pochi per giudicare")
    sample_ok = not sample_notes
    rows.append(_row("E1", "Campione", "PASS" if sample_ok else "INSUFFICIENT", observed={"giorni_oos": days, "trade_oos": trades, "trade_backtest": bt.get("n")},
                     threshold={"giorni_oos": c["min_oos_days"], "trade_oos": c["min_oos_trades"], "trade_backtest": bt.get("min", c["min_backtest_trades"])},
                     reason="; ".join(sample_notes) if sample_notes else "campione sufficiente: giorni, trade fuori campione e trade totali del backtest",
                     cls="ok" if sample_ok else "insufficient_sample", on_locked=touched,
                     next_exp=None if sample_ok else "raccogliere nuove osservazioni INDIPENDENTI: più dati storici non visti o giorni di paper trading in avanti (non rivedere lo stesso blocco)"))

    # --- E2 edge: DSR with interval ---------------------------------------------------------------------------------
    m: dict = {"days": days, "trades": trades}
    state_against: list[str] = list(dev_against)
    if touched:
        dsr_cfg = lk.get("dsr") or {}
        dsr = float(dsr_v) if dsr_v is not None else None
        iv = sharpe_interval(x, c["ci_z"]) if len(x) else {"sr": None, "lo": None, "hi": None, "se": None, "n": days}
        psr0 = float(probabilistic_sharpe(x, 0.0)) if len(x) > 3 else None
        pnl = float(lk.get("pnl") or 0.0)
        m.update({"dsr": dsr, "psr0": psr0, "sharpe_annual": iv["sr"], "sharpe_ci95": [iv["lo"], iv["hi"]], "oos_pnl": pnl, "n_trials": dsr_cfg.get("n_trials"),
                  "dsr_basis": dsr_cfg.get("basis"), "power_needed_sharpe_for_threshold": (EV.required_observed_sharpe(max(3, days), int(dsr_cfg.get("n_trials") or 1), c["confidence"], margin=EV.Z_MARGIN)
                                                if dsr_cfg.get("basis") == "holdout_null" else power_needed(max(2, days), int(dsr_cfg.get("n_trials") or 1), float(dsr_cfg.get("var_sr") or 0.001), c["confidence"])) if days > 2 else None})
        edge_pass = dsr is not None and dsr >= c["confidence"] and pnl > 0
        # evidence AGAINST (needs data, not the absence of data)
        if sample_ok and psr0 is not None and iv["sr"] is not None and iv["sr"] <= 0 and psr0 <= c["against_psr0"]:
            state_against.append(f"sul blocco mai visto P(Sharpe vero > 0) = {psr0:.0%} ≤ {c['against_psr0']:.0%} e il risultato è non positivo ({_num(iv['sr'])} annuo)")
        if sample_ok and wf_bad and iv["sr"] is not None and iv["sr"] <= 0:
            state_against.append("due campioni indipendenti (walk-forward e blocco mai visto) sono entrambi non positivi")
        decay = None
        if dev is not None and len(dev) > 20 and iv["sr"] is not None and iv["se"] is not None:
            dv = sharpe_interval(np.asarray(dev, float), c["ci_z"])
            if dv["se"]:
                decay = (dv["sr"] - iv["sr"]) / math.sqrt(dv["se"] ** 2 + iv["se"] ** 2)
                m.update({"sharpe_dev": dv["sr"], "decay_z": decay})
                if sample_ok and dv["sr"] > 0 and decay >= c["decay_z"] and (psr0 or 0) < 0.5:
                    state_against.append(f"firma di overfitting: Sharpe sviluppo {dv['sr']:.2f} → blocco mai visto {iv['sr']:.2f} (calo z = {decay:.1f} ≥ {c['decay_z']})")
        if state_against:
            rows.append(_row("E2", "Edge statistico", "FAIL", observed=dsr, threshold=c["confidence"], interval=[iv["lo"], iv["hi"]],
                             reason="evidenza CONTRO: " + "; ".join(state_against), cls="overfit" if any("overfitting" in s for s in state_against) else "no_edge",
                             next_exp="archiviare con il motivo; riaprire solo con un'ipotesi economica diversa e nuovi dati indipendenti", recoverable=False, on_locked=True))
        elif edge_pass:
            rows.append(_row("E2", "Edge statistico", "PASS", observed=dsr, threshold=c["confidence"], interval=[iv["lo"], iv["hi"]],
                             reason=f"Deflated Sharpe {dsr:.2f} ≥ {c['confidence']:.2f} (Sharpe annuo {_num(iv['sr'])}, IC95 [{_num(iv['lo'])}, {_num(iv['hi'])}])", on_locked=True))
        else:
            need = m["power_needed_sharpe_for_threshold"]
            why = (f"Deflated Sharpe {_num(dsr)} < {c['confidence']:.2f}: l'edge non è né dimostrato né escluso. Sharpe annuo osservato {_num(iv['sr'])}, IC95 [{_num(iv['lo'])}, {_num(iv['hi'])}]"
                   + (f"; con {days} sessioni e {m['n_trials']} tentativi servirebbe uno Sharpe vero ≈ {need:.1f} per superare la soglia" if need else "") +
                   ("" if pnl > 0 else "; il blocco mai visto non è profittevole"))
            rows.append(_row("E2", "Edge statistico", "INSUFFICIENT", observed=dsr, threshold=c["confidence"], interval=[iv["lo"], iv["hi"]], reason=why,
                             cls="insufficient_sample" if (iv["lo"] is not None and iv["lo"] <= 0 < (iv["hi"] or 0)) else "no_edge",
                             next_exp="nuove osservazioni indipendenti (paper trading in avanti o dati non ancora visti): l'intervallo si stringe solo con più dati", on_locked=True))
        # E3 precision of the estimate
        width = None if iv["lo"] is None else iv["hi"] - iv["lo"]
        m["ci_width"] = width
        prec_ok = width is not None and width <= c["max_sharpe_ci_width"]
        rows.append(_row("E3", "Precisione della stima", "PASS" if prec_ok else "INSUFFICIENT", observed=width, threshold=c["max_sharpe_ci_width"],
                         interval=[iv["lo"], iv["hi"]], cls="ok" if prec_ok else "wide_interval", on_locked=True,
                         reason=(f"intervallo dello Sharpe annuo largo {_num(width)} ≤ {c['max_sharpe_ci_width']:g}" if prec_ok else
                                 f"intervallo dello Sharpe annuo largo {_num(width)} > {c['max_sharpe_ci_width']:g}: un'aspettativa positiva con questa incertezza non è un'evidenza"),
                         next_exp=None if prec_ok else "più osservazioni indipendenti: la larghezza scala con 1/√giorni"))
        # E4 development -> unseen decay
        if decay is not None:
            ok4 = decay < c["decay_z"]
            rows.append(_row("E4", "Degrado sviluppo → blocco mai visto", "PASS" if ok4 else "FAIL", observed=decay, threshold=c["decay_z"], on_locked=True,
                             reason=(f"calo dello Sharpe entro il rumore campionario (z = {decay:.1f})" if ok4 else f"calo dello Sharpe oltre il rumore (z = {decay:.1f}): tipico di un edge che era selezione"),
                             cls="ok" if ok4 else "overfit", next_exp=None if ok4 else "plateau analysis dei parametri sullo sviluppo e verifica con dati non ancora visti", recoverable=not state_against))
        # lineage: iterating on the same unseen block
        if lineage_looks > 0:
            rows.append(_row("E5", "Iterazioni sullo stesso blocco bloccato", "INSUFFICIENT", observed=lineage_looks, threshold=0, on_locked=True,
                             reason=f"questa linea di ricerca ha già usato {lineage_looks} sguardi sul blocco bloccato con varianti diverse: un'altra variante non può essere APPROVATA sullo stesso blocco",
                             cls="insufficient_sample", next_exp="aspettare dati nuovi non ancora visti (o paper trading): il blocco attuale è 'consumato' per questa linea"))
    else:
        iv = None
    if dev_against and not touched:
        rows.append(_row("E2", "Edge statistico (sviluppo)", "FAIL", reason="evidenza CONTRO sullo sviluppo: " + "; ".join(dev_against), cls="no_edge",
                         next_exp="archiviare con il motivo; riaprire solo con un'ipotesi economica diversa", recoverable=False))
    sh = report.get("shelved")
    if sh:
        rows.append(_row("E7", "Slot sul blocco hidden (protocollo preregistrato)", "INSUFFICIENT", observed=None, threshold=None, reason=sh.get("reason", ""),
                         cls="insufficient_sample", recoverable=True,
                         next_exp=("attendere una nuova epoca: servono sessioni nuove oltre il blocco attuale (paper trading nel frattempo); il blocco attuale è riservato ai finalisti già registrati"
                                   if sh.get("kind") == "block_full" else "una variante di una linea già giudicata non è una prova indipendente: serve un'ipotesi diversa o dati nuovi")))
    # --- E6 robustness / costs (development only) ----------------------------------------------------------------------
    fragile = [r for r in rob_fail if r[0] in ("parametric_fragility", "high_costs")]
    cls6 = "high_costs" if any(k == "high_costs" for k, _ in fragile) else "parametric_fragility"
    rows.append(_row("E6", "Robustezza (parametri, costi, deriva)", "FAIL" if fragile else "PASS", observed=len(fragile), threshold=0,
                     reason="; ".join(t for _, t in fragile) if fragile else "superficie dei parametri, costi ×2 e riempimento all'apertura successiva: reggono",
                     cls=cls6 if fragile else "ok", recoverable=True, scope="edge",
                     next_exp=(("stress test di esecuzione: costi ×1.5/×2/×3, slippage maggiore e riempimento ritardato sullo sviluppo" if cls6 == "high_costs" else
                                "plateau analysis: griglia più fine intorno ai parametri scelti e controllo dei vicini sullo sviluppo") if fragile else None)))

    # --- state -----------------------------------------------------------------------------------------------------------
    edge_row = next((r for r in rows if r["gate"] == "E2"), None)
    if state_against:
        state = "AGAINST"
    elif wf_bad:
        state = "NEEDS_ROBUSTNESS"                                 # negative walk-forward on too few windows: not evidence yet, ask for more stress tests
        rows.append(_row("E6b", "Walk-forward di sviluppo", "FAIL", observed=wf_pnl, threshold=0, reason=f"walk-forward non profittevole (P&L {wf_pnl:,.0f}) con finestre insufficienti per escludere l'edge",
                         cls="parametric_fragility", next_exp="completare le finestre walk-forward pianificate (stesso sviluppo, nessun blocco bloccato)"))
    elif fragile:
        state = "NEEDS_ROBUSTNESS"
    elif not touched or sh:
        state = "INSUFFICIENT"
    elif not sample_ok or lineage_looks > 0:
        state = "INSUFFICIENT"
    else:
        wid = next(r for r in rows if r["gate"] == "E3")
        dec = next((r for r in rows if r["gate"] == "E4"), None)
        if edge_row["status"] == "PASS" and wid["status"] == "PASS" and (dec is None or dec["status"] == "PASS"):
            state = "VALID"
        else:
            dsr = m.get("dsr"); psr0 = m.get("psr0")
            plaus = (dsr is not None and dsr >= c["candidate_dsr"] and psr0 is not None and psr0 >= c["candidate_psr0"] and m.get("oos_pnl", 0) > 0
                     and not (dec is not None and dec["status"] == "FAIL"))
            state = "PLAUSIBLE" if plaus else "INSUFFICIENT"
    return {"state": state, "rows": rows, "metrics": m, "against": state_against}


# ------------------------------------------------------------------ PROP JUDGE
def prop_judge(report: dict, cfg: dict | None = None) -> dict:
    """Per ruleset (and the size + payout policy chosen on development): COMPATIBLE | INCOMPATIBLE | INCONCLUSIVE | UNKNOWN."""
    fr = report.get("farming") or {}
    by = fr.get("by_ruleset") or {}
    gcfg = ((fr.get("config") or {}).get("gates")) or {}
    out = {}
    for rid, e in by.items():
        ev, gt = e.get("evaluation") or {}, {g["id"]: g for g in (e.get("gates") or {}).get("gates", [])}
        m = ev.get("metrics") or {}
        npv = m.get("npfv") or {}
        size = (ev.get("size") or {})
        micro = ev.get("micro")
        rows, status = [], "COMPATIBLE"

        def add(row):
            nonlocal status
            rows.append(row)
            if row["status"] == "FAIL":
                status = "INCOMPATIBLE"
            elif row["status"] in ("INSUFFICIENT", "PENDING") and status == "COMPATIBLE":
                status = "INCONCLUSIVE"

        comp = (ev.get("compliance") or {})
        g5 = gt.get(5, {})
        add(_row(f"P1:{rid}", "Conformità alle regole del conto (drawdown, limiti)", g5.get("status", "PASS" if comp.get("ok") else "FAIL"),
                 observed=m.get("drawdown_utilization"), threshold=1.0, reason=g5.get("reason", ""),
                 cls="ok" if g5.get("status") == "PASS" else ("sizing_incompatible" if (micro or 1) > 1 else "prop_incompatible"), scope="prop", on_locked=True,
                 next_exp=None if g5.get("status") == "PASS" else ("nuova configurazione di rischio (taglia/stop più piccoli) sviluppata SOLO sul periodo di sviluppo" if (micro or 1) > 1 else
                                                                  "provare ruleset alternativi (trailing statico/EOD, limiti diversi): a taglia minima viola ancora le regole"), recoverable=True))
        g6 = gt.get(6, {})
        fp, cond, ff = m.get("p_first_payout"), m.get("p_first_payout_given_pass"), m.get("p_fail_before_first_payout_given_funded")
        fp_ok = bool(g6.get("first_payout_gate", False))
        add(_row(f"P2:{rid}", "Primo payout", "PASS" if fp_ok else "FAIL", observed={"p_first_payout": fp, "dato_il_passaggio": cond, "fallisce_prima_del_payout": ff},
                 threshold={"p_first_payout": gcfg.get("min_p_first_payout"), "dato_il_passaggio": gcfg.get("min_first_payout_given_pass"), "fallisce_prima": gcfg.get("max_p_funded_fail_before_payout")},
                 reason=(f"primo payout {_num(fp, 2)}: passa la valutazione ma poi incassa poco o fallisce da funded" if not fp_ok else f"primo payout {fp:.0%}"),
                 cls="ok" if fp_ok else "prop_incompatible", scope="prop", on_locked=True,
                 next_exp=None if fp_ok else "politica di payout alternativa (A/B/C) e taglia più bassa sviluppate sullo sviluppo; altrimenti ruleset diverso"))
        lcb, ucb, mean = npv.get("lcb95"), npv.get("ucb95"), npv.get("mean")
        if lcb is not None and lcb > float(gcfg.get("min_npfv_lcb95", 0.0)):
            st_, cls = "PASS", "ok"; why = f"valore netto del ciclo: media ${mean:,.0f}, LCB95 ${lcb:,.0f}"
        elif ucb is not None and ucb <= 0:
            st_, cls = "FAIL", "prop_incompatible"; why = f"valore netto del ciclo negativo anche nel caso migliore (UCB95 ${ucb:,.0f})"
        else:
            st_, cls = "INSUFFICIENT", "economics_unproven"; why = f"valore netto non dimostrato: media ${(mean or 0):,.0f}, intervallo 95% [${(lcb or 0):,.0f}, ${(ucb or 0):,.0f}] include lo zero"
        add(_row(f"P3:{rid}", "Valore netto del ciclo (NPFV)", st_, observed=mean, threshold=gcfg.get("min_npfv_lcb95", 0.0), interval=[lcb, ucb], reason=why, cls=cls, scope="prop", on_locked=True,
                 next_exp=None if st_ == "PASS" else ("nuove osservazioni indipendenti: l'incertezza sul valore netto si riduce solo con più sessioni" if st_ == "INSUFFICIENT" else "ruleset con costi/regole diverse")))
        stress = ev.get("stress") or {}
        cx = next((k for k in stress if k.startswith("costi_x")), None)
        if cx:
            s_ok = stress[cx].get("npfv_mean", 0) > float(gcfg.get("stress_npfv_min", 0.0))
            add(_row(f"P4:{rid}", f"Valore netto con costi ×{gcfg.get('stress_costs_multiple', 2):g}", "PASS" if s_ok else "FAIL", observed=stress[cx].get("npfv_mean"), threshold=gcfg.get("stress_npfv_min", 0.0),
                     reason="il valore del ciclo resta positivo con costi maggiorati" if s_ok else "il valore netto del ciclo non regge costi maggiorati", cls="ok" if s_ok else "high_costs", scope="prop", on_locked=True,
                     next_exp=None if s_ok else "stress test di esecuzione e ricerca di una variante con meno operazioni/costi"))
        out[rid] = {"status": status, "rows": rows, "size_micro": micro, "policy": ev.get("policy"),
                    "metrics": {"p_pass": m.get("p_pass"), "p_first_payout": fp, "p_funded_survival": m.get("p_funded_survival"), "npfv_mean": mean, "npfv_lcb95": lcb,
                                "p_account_failure": m.get("p_account_failure"), "drawdown_utilization": m.get("drawdown_utilization"),
                                "e_time_to_pass": m.get("e_time_to_pass"), "e_time_to_first_payout": m.get("e_time_to_first_payout")},
                    "legacy_gates": (e.get("gates") or {}).get("verdict")}
    compat = [r for r, v in out.items() if v["status"] == "COMPATIBLE"]
    incon = [r for r, v in out.items() if v["status"] == "INCONCLUSIVE"]
    status = "COMPATIBLE" if compat else ("INCONCLUSIVE" if incon else ("INCOMPATIBLE" if out else "UNKNOWN"))
    best = max(compat, key=lambda r: out[r]["metrics"]["npfv_lcb95"] or -1e18) if compat else None
    return {"status": status, "by_ruleset": out, "compatible": compat, "inconclusive": incon, "best": best}


# ------------------------------------------------------------------ FINAL JUDGE
def judge_report(report: dict, oos=None, dev=None, cfg: dict | None = None, lineage_looks: int = 0) -> dict:
    """The combined, deterministic judgment of a stored study. See the module docstring for the precedence."""
    c = {**DEFAULTS, **(cfg or {})}
    edge = edge_judge(report, oos, dev, c, lineage_looks)
    prop = prop_judge(report, c) if edge["state"] != "INVALID_EXECUTION" else {"status": "UNKNOWN", "by_ruleset": {}, "compatible": [], "inconclusive": [], "best": None}
    fr = report.get("farming") or {}
    legacy_farming = fr.get("verdict")
    state, pstat = edge["state"], prop["status"]
    why = ""
    if state == "INVALID_EXECUTION":
        verdict, why = EXECUTION_FAILED, "errore di esecuzione o di contabilità: nessun giudizio statistico è valido"
    elif state == "AGAINST":
        verdict, why = STATISTICALLY_REJECTED, "evidenza statistica contro la strategia: " + "; ".join(edge["against"] or [r["reason"] for r in edge["rows"] if r["status"] == "FAIL"][:1])
    elif state == "NEEDS_ROBUSTNESS":
        verdict, why = NEEDS_ROBUSTNESS, "l'edge non è escluso ma non regge gli stress test sullo sviluppo: " + "; ".join(r["reason"] for r in edge["rows"] if r["status"] == "FAIL" and r["gate"].startswith("E6"))
    elif state == "VALID":
        if pstat == "COMPATIBLE":
            if fr and legacy_farming != "APPROVED":                               # APPROVED is never easier than the legacy review
                verdict, why = INSUFFICIENT_EVIDENCE, "edge valido e ruleset compatibile, ma la revisione prop-farming completa non approva ancora"
            else:
                verdict, why = APPROVED, "edge statisticamente credibile dopo i costi e compatibile con almeno un ruleset (taglia e politica scelte sullo sviluppo)"
        elif pstat == "INCOMPATIBLE":
            verdict, why = EDGE_VALID_PROP_INCOMPATIBLE, "l'edge è credibile ma nessun ruleset configurato lo rende conveniente: non è senza valore, serve un ruleset o un rischio diversi"
        else:
            verdict, why = INSUFFICIENT_EVIDENCE, "edge credibile; il valore economico sul ruleset non è ancora dimostrato (intervallo che include lo zero)"
    elif state == "PLAUSIBLE":
        if pstat in ("COMPATIBLE", "INCONCLUSIVE", "UNKNOWN"):
            verdict, why = PAPER_CANDIDATE, "edge plausibile (non dimostrato), campione sufficiente e nessuna incompatibilità con la prop: la prova più informativa è il paper trading in avanti"
        else:
            verdict, why = INSUFFICIENT_EVIDENCE, "edge plausibile ma non dimostrato, e incompatibile con i ruleset provati"
    else:
        verdict, why = INSUFFICIENT_EVIDENCE, "evidenza insufficiente: l'edge non è né dimostrato né escluso"
        mm = [r["reason"] for r in edge["rows"] if r["status"] in ("INSUFFICIENT",)][:2]
        if mm:
            why += " — " + "; ".join(mm)

    rows = list(edge["rows"])
    for rid, v in prop["by_ruleset"].items():
        rows += v["rows"]
    passed = [r for r in rows if r["status"] == "PASS"]
    failed = [r for r in rows if r["status"] != "PASS"]
    order = {"FAIL": 0, "INSUFFICIENT": 1, "PENDING": 2}
    failed.sort(key=lambda r: (order.get(r["status"], 3), 0 if r["scope"] == "edge" else 1, r["gate"]))
    det = failed[0] if failed else None
    diagnosis = None
    if verdict != APPROVED:
        diagnosis = {
            "failed_gate": None if det is None else {"id": det["gate"], "label": det["label"]},
            "observed": None if det is None else det["observed"], "threshold": None if det is None else det["threshold"],
            "interval": None if det is None else det["interval"], "technical_reason": why if det is None else det["reason"],
            "classification": (det or {}).get("classification") or "no_edge", "classification_label": CLASS_LABEL.get((det or {}).get("classification") or "no_edge"),
            "recoverable": verdict not in (STATISTICALLY_REJECTED,) and all(r["recoverable"] for r in failed[:1]) if failed else True,
            "next_experiments": next_experiments({"verdict": verdict, "gates": rows, "edge": edge, "prop": prop}),
            "frozen": FROZEN, "other_problems": [{"gate": r["gate"], "label": r["label"], "reason": r["reason"]} for r in failed[1:6]],
        }
    stage = EV.stage_of({"verdict": verdict, "prop": {"compatible": prop["compatible"]}, "reason": why}, report.get("forward_test"))
    return {"version": 1, "stage": stage, "verdict": verdict, "legacy_status": LEGACY[verdict], "reason": why, "edge": {"state": edge["state"], "metrics": edge["metrics"]},
            "prop": {k: v for k, v in prop.items()}, "gates": rows, "passed": [{"gate": r["gate"], "label": r["label"]} for r in passed],
            "failed": [{"gate": r["gate"], "label": r["label"], "status": r["status"]} for r in failed], "diagnosis": diagnosis,
            "best_ruleset": prop["best"], "legacy_farming": legacy_farming, "thresholds": {k: c[k] for k in DEFAULTS}, "lineage_looks": lineage_looks}


# ------------------------------------------------------------------ feedback to the Researcher
def next_experiments(j: dict) -> list[dict]:
    """Targeted experiments for the recoverable failures. Each is DEVELOPMENT-side or needs genuinely new data; none re-reads the locked block."""
    out, seen = [], set()
    mapping = {"insufficient_sample": ("new_independent_observations", False), "wide_interval": ("new_independent_observations", False),
               "high_costs": ("execution_stress_test", True), "parametric_fragility": ("plateau_analysis", True),
               "prop_incompatible": ("alternative_rulesets", True), "sizing_incompatible": ("new_risk_config_on_training", True),
               "economics_unproven": ("new_independent_observations", False), "overfit": ("new_hypothesis", True), "execution_error": ("fix_execution", True)}
    for r in j.get("gates", []):
        if r["status"] == "PASS":
            continue
        kind = mapping.get(r["classification"])
        if not kind or kind[0] in seen or j["verdict"] == STATISTICALLY_REJECTED and r["classification"] not in ("no_edge", "overfit"):
            continue
        seen.add(kind[0])
        out.append({"kind": kind[0], "gate": r["gate"], "text": r["next_experiment"], "needs_locked_block": False, "development_only": kind[1],
                    "registers_trials": True})
    if j["verdict"] == EDGE_VALID_PROP_INCOMPATIBLE and "alternative_rulesets" not in seen:
        out.append({"kind": "alternative_rulesets", "gate": "P", "text": "provare l'edge su ruleset alternativi o con una configurazione di rischio diversa", "needs_locked_block": False, "development_only": True, "registers_trials": True})
    if j["verdict"] == PAPER_CANDIDATE:
        out.append({"kind": "paper_forward", "gate": "E2", "text": "paper trading in avanti: nuovi giorni davvero fuori campione", "needs_locked_block": False, "development_only": False, "registers_trials": False})
    return out


def agent_view(judgment: dict) -> dict:
    """What agents (Researcher, Optimizer...) may read. Numbers measured on the locked block are withheld: an agent that sees them could tune a
    variant until the unseen block approves it. Gate names, classes and the next experiment stay; development-side numbers stay."""
    j = deepcopy(judgment)
    for r in j.get("gates", []):
        if r.get("uses_locked"):
            r["observed"] = r["threshold"] = r["interval"] = None
    d = j.get("diagnosis")
    if d:
        fg = d.get("failed_gate") or {}
        locked_gate = next((r for r in j["gates"] if r["gate"] == fg.get("id")), {}).get("uses_locked", False)
        if locked_gate:
            d["observed"] = d["threshold"] = d["interval"] = None
            d["technical_reason"] = "(numeri del blocco bloccato non mostrati agli agenti) " + d["classification_label"]
    j["edge"] = {"state": j["edge"]["state"]}
    j["prop"] = {"status": j["prop"]["status"], "compatible": j["prop"]["compatible"]}
    for r in j.get("gates", []):
        if r.get("uses_locked"):
            r["reason"] = r["classification"]
    return {k: j[k] for k in ("verdict", "diagnosis", "gates", "edge", "prop")}


def brief(judgment: dict) -> str:
    """One paragraph for the Researcher's memory (Italian): what failed, whether it is recoverable, and what to try."""
    d = judgment.get("diagnosis")
    if not d:
        return f"Verdetto {judgment['verdict']}."
    ex = "; ".join(e["text"] for e in d["next_experiments"] if e.get("text")) or "nessun esperimento utile"
    return (f"Verdetto {judgment['verdict']} — {d['classification_label']}. Cancello: {(d['failed_gate'] or {}).get('label', 'n/d')}. "
            f"{'Recuperabile' if d['recoverable'] else 'Non recuperabile'}. Prossimi esperimenti: {ex}. Non si cambiano: blocco OOS, soglie, registro dei tentativi.")


def lineage_looks_for(registry, spec_name: str, label: str, oos_start: str) -> int:
    """Looks at THIS unseen block by DIFFERENT variants of the same line of research (same spec name)."""
    try:
        con = registry._con
        rows = con.execute("SELECT label FROM locked_looks WHERE label LIKE ? AND oos_start = ?", (f"{spec_name}:%", oos_start)).fetchall()
    except Exception:
        return 0
    return sum(1 for (lab,) in rows if lab != label)


def legacy_status(verdict: str) -> str:
    return LEGACY[verdict]
