"""The "ready strategy" dossier: what the owner reads when the Judge approves something.

Everything numeric is computed here, deterministically, from the out-of-sample (hidden block)
daily P&L the Judge already used. The text is templated, not written by an LLM, so it can never
promise more than the numbers say.

Sizing is expressed in micro-contract equivalents (1 mini NQ = 10 MNQ). Scaling the daily P&L
linearly is an approximation: it ignores that a bigger size moves the market a little more
and that commissions per contract differ between mini and micro."""
from __future__ import annotations

import numpy as np

from . import economics as eco

from .prop import PropRules, pass_probability_ci

MICRO_SIZES = (1, 2, 3, 4, 5, 6, 8)
MINI_SIZES = (10, 20)            # 1 and 2 mini contracts, as micro equivalents
MAX_BUST = 0.35                  # a size is only recommended if its bust probability is below this
MINI_MARGIN = 0.03               # minis must beat the best micro size by this much (lower bound)

T = {
    "it": {
        "head": "{name}: pronta per un account {acct}",
        "strat": "Strategia: {doc} ({tmpl}) su barre {tf}, parametri {params}. {why}",
        "src": " Fonte: {src}.",
        "size": "Dimensione consigliata: {n} micro (MNQ){mini}. Probabilità di passare la valutazione: {p:.0%} (intervallo 95%: {lo:.0%}-{hi:.0%}); di bruciare l'account: {b:.0%}. Mediana dei giorni per passare: {d}.",
        "mini_yes": " = {k} mini NQ",
        "risk": "Gestione del rischio: ferma il trading del giorno a -${stop:,.0f} (un terzo del drawdown massimo di ${dd:,.0f}: servono tre giorni così per bruciare l'account). Nel periodo mai visto la perdita peggiore in un giorno a questa dimensione sarebbe stata ${worst:,.0f}, il drawdown massimo ${mdd:,.0f}; lo stop giornaliero sarebbe scattato nel {hit:.0%} dei giorni.",
        "vs": "Mini o micro: {why}",
        "vs_micro": "micro. Con i mini la dimensione minima equivale a 10 micro, troppo grossa per questo drawdown o senza un vantaggio misurabile; i micro permettono di scalare e di ridurre dopo una serie negativa.",
        "vs_mini": "mini. A questa dimensione {k} contratto/i mini fanno meglio dei micro di almeno {m:.0%} sul limite inferiore della probabilità di passare.",
        "exit_on": "Uscite: stop a {s}x ATR e target a {rr} (rapporto rischio/rendimento 1:{rrn}); il grafico dei risultati per ogni coppia è nel dettaglio della strategia. Stop tipico {pts:.1f} punti.",
        "exit_off": "Uscite: nessuno stop/target ha migliorato in modo netto l'uscita propria della strategia, quindi si usa quella. La tabella rischio/rendimento è nel dettaglio.",
        "rpt": " Rischio per operazione con {n} micro: circa ${r:,.0f} ({pdd:.0%} del drawdown massimo).",
        "eco": " Costi stimati, solo indicativi (valutazione ~${fee:,.0f}/mese, attivazione ~${act:,.0f}): costo medio per tentativo ${fees:,.0f}. Probabilità di arrivare a un payout: {pp:.0%} (passi {p:.0%}, poi incassi nel {pg:.0%} dei casi); payout netto medio ${net:,.0f}; valore atteso per tentativo {ev:+,.0f}$ (95%: {lo:+,.0f} / {hi:+,.0f}).",
        "multi": " Più conti, uno dopo l'altro (date diverse): per passarne almeno uno con l'80% di probabilità servono {n80} tentativi, {n90} per il 90%{t3}. Costo atteso fino al primo passaggio, al massimo 10 tentativi: circa ${cost:,.0f}. Conti aperti insieme con gli stessi trade NON sono indipendenti: la probabilità non si moltiplica.",
        "multi_t": "; con 3 conti {p3:.0%}, con 5 {p5:.0%}",
        "cmp": "Conto 50k o 25k? Payout più probabile: {w} ({a:.0%} contro {b:.0%}). Valore atteso per tentativo: 50k {e50:+,.0f}$, 25k {e25:+,.0f}$ (stime con costi indicativi).",
        "caveat": "Avvertenze: stima su {n} giorni mai visti dall'ottimizzatore, dopo {trials} prove totali in questo laboratorio (il Giudice le ha già conteggiate). Le regole dell'account sono quelle di esempio, NON verificate: controllale sul regolamento del tuo prop firm prima di usarle. Una probabilità non è una promessa.",
        "none": "Nessuna dimensione sicura: anche con 1 micro la probabilità di bruciare l'account supera {b:.0%}. Non operarla su questo account.",
    },
    "en": {
        "head": "{name}: ready for a {acct} account",
        "strat": "Strategy: {doc} ({tmpl}) on {tf} bars, parameters {params}. {why}",
        "src": " Source: {src}.",
        "size": "Recommended size: {n} micro (MNQ){mini}. Probability of passing the evaluation: {p:.0%} (95% interval: {lo:.0%}-{hi:.0%}); of busting the account: {b:.0%}. Median days to pass: {d}.",
        "mini_yes": " = {k} mini NQ",
        "risk": "Risk management: stop trading for the day at -${stop:,.0f} (a third of the ${dd:,.0f} max drawdown: it takes three such days to bust the account). On the unseen period the worst single day at this size would have been ${worst:,.0f} and the max drawdown ${mdd:,.0f}; the daily stop would have triggered on {hit:.0%} of days.",
        "vs": "Mini or micro: {why}",
        "vs_micro": "micro. With minis the smallest size equals 10 micros, too big for this drawdown or with no measurable benefit; micros let you scale up and cut back after a losing streak.",
        "vs_mini": "mini. At this size {k} mini contract(s) beat micros by at least {m:.0%} on the lower bound of the pass probability.",
        "exit_on": "Exits: stop at {s}x ATR and target at {rr} (reward:risk 1:{rrn}); results for every pair are in the strategy detail. Typical stop {pts:.1f} points.",
        "exit_off": "Exits: no stop/target clearly improved on the strategy's own exit, so that one is used. The risk/reward table is in the detail.",
        "rpt": " Risk per trade with {n} micro: about ${r:,.0f} ({pdd:.0%} of the max drawdown).",
        "eco": " Estimated costs, indicative only (evaluation ~${fee:,.0f}/month, activation ~${act:,.0f}): average cost per attempt ${fees:,.0f}. Probability of reaching a payout: {pp:.0%} (you pass {p:.0%}, then get paid in {pg:.0%} of cases); average net payout ${net:,.0f}; expected value per attempt {ev:+,.0f}$ (95%: {lo:+,.0f} / {hi:+,.0f}).",
        "multi": " Several accounts, one after another (different dates): to pass at least one with 80% probability you need {n80} attempts, {n90} for 90%{t3}. Expected cost until the first pass, at most 10 attempts: about ${cost:,.0f}. Accounts opened together copying the same trades are NOT independent: the probability does not multiply.",
        "multi_t": "; with 3 accounts {p3:.0%}, with 5 {p5:.0%}",
        "cmp": "50k or 25k? More likely payout: {w} ({a:.0%} vs {b:.0%}). Expected value per attempt: 50k {e50:+,.0f}$, 25k {e25:+,.0f}$ (estimates with indicative costs).",
        "caveat": "Caveats: estimate on {n} days the optimiser never saw, after {trials} total trials in this lab (the Judge already counted them). The account rules are example values, NOT verified: check them against your prop firm's rulebook before relying on them. A probability is not a promise.",
        "none": "No safe size: even 1 micro has a bust probability above {b:.0%}. Do not trade this on this account.",
    },
}


def _evaluate(pnl: np.ndarray, rules: PropRules, base_contracts: int, n_micro: int, seed: int = 0) -> dict:
    mc = pass_probability_ci(pnl * (n_micro / base_contracts), rules, n_outer=40, n_inner=60, seed=seed)
    return {"micro_equiv": n_micro, "p_pass": mc["p_pass"], "p_pass_lo": mc["p_pass_ci95"][0],
            "p_pass_hi": mc["p_pass_ci95"][1], "p_bust": mc["p_bust"], "median_days": mc["median_days_to_pass"]}


def size_table(pnl, rules: PropRules, base_contracts: int) -> list[dict]:
    x = np.asarray(pnl, dtype=float)
    return [_evaluate(x, rules, base_contracts, n) for n in MICRO_SIZES + MINI_SIZES]


def choose(table: list[dict]) -> dict:
    """Best lower bound of the pass probability among sizes that bust rarely. Minis only if clearly better."""
    safe = [r for r in table if r["p_bust"] <= MAX_BUST]
    if not safe:
        return {"pick": None, "instrument": None}
    micro = [r for r in safe if r["micro_equiv"] in MICRO_SIZES]
    mini = [r for r in safe if r["micro_equiv"] in MINI_SIZES]
    best_micro = max(micro, key=lambda r: (r["p_pass_lo"], -r["micro_equiv"])) if micro else None
    best_mini = max(mini, key=lambda r: (r["p_pass_lo"], -r["micro_equiv"])) if mini else None
    if best_mini and (best_micro is None or best_mini["p_pass_lo"] >= best_micro["p_pass_lo"] + MINI_MARGIN):
        return {"pick": best_mini, "instrument": "mini", "margin": best_mini["p_pass_lo"] - (best_micro["p_pass_lo"] if best_micro else 0)}
    return {"pick": best_micro, "instrument": "micro"}


def risk_plan(pnl, rules: PropRules, base_contracts: int, n_micro: int) -> dict:
    x = np.asarray(pnl, dtype=float) * (n_micro / base_contracts)
    stop = rules.max_drawdown / 3
    if rules.daily_loss_limit is not None:
        stop = min(stop, rules.daily_loss_limit * 0.5)
    cum = np.cumsum(x)
    mdd = float(np.max(np.maximum.accumulate(np.concatenate([[0], cum]))[1:] - cum)) if len(x) else 0.0
    return {"daily_stop": float(stop), "max_drawdown_allowed": float(rules.max_drawdown),
            "worst_day": float(x.min()) if len(x) else 0.0, "max_drawdown_oos": mdd,
            "stop_hit_rate": float((x <= -stop).mean()) if len(x) else 0.0}


def build_dossier(report: dict, rules_by_account: dict[str, PropRules], base_contracts: int, trials_total: int,
                  language: str = "it", doc: str = "", audit: dict | None = None, economics: dict | None = None) -> dict | None:
    """Only for APPROVED studies. Returns None for anything else (nothing is ever dressed up)."""
    locked = report.get("locked") or {}
    if locked.get("status") != "APPROVED":
        return None
    series = (report.get("series") or {}).get("locked")
    if not series:
        return None
    pnl = np.array([v for _, v in series], dtype=float)
    L = T["it" if language.lower().startswith("it") else "en"]
    accounts = {}
    for acct, rules in rules_by_account.items():
        table = size_table(pnl, rules, base_contracts)
        ch = choose(table)
        pick = ch["pick"]
        entry = {"rules": {"start_balance": rules.start_balance, "profit_target": rules.profit_target,
                           "max_drawdown": rules.max_drawdown}, "table": table, "instrument": ch["instrument"],
                 "recommended": pick, "verified_rules": False}
        if pick is None:
            worst = min(table, key=lambda r: r["micro_equiv"])
            entry["text"] = L["none"].format(b=worst["p_bust"])
        else:
            entry["risk"] = risk_plan(pnl, rules, base_contracts, pick["micro_equiv"])
            n = pick["micro_equiv"]
            mini = L["mini_yes"].format(k=n // 10) if n % 10 == 0 else ""
            vs = L["vs_mini"].format(k=n // 10, m=ch.get("margin", 0)) if ch["instrument"] == "mini" else L["vs_micro"]
            r = entry["risk"]
            d = pick["median_days"]
            entry["text"] = " ".join([
                L["size"].format(n=n, mini=mini, p=pick["p_pass"], lo=pick["p_pass_lo"], hi=pick["p_pass_hi"],
                                 b=pick["p_bust"], d=f"{d:.0f}" if d else "n/d"),
                L["risk"].format(stop=r["daily_stop"], dd=r["max_drawdown_allowed"], worst=abs(r["worst_day"]),
                                 mdd=r["max_drawdown_oos"], hit=r["stop_hit_rate"]),
                L["vs"].format(why=vs)])
        if pick is not None:
            assume = (economics or eco.ASSUMED).get(acct)
            if assume:
                e = eco.account_economics(pnl, rules, base_contracts, pick["micro_equiv"], assume)
                m = eco.multi_account(pick["p_pass"], pick["p_pass_lo"], pick["p_pass_hi"], e["p_payout"], e["mean_fees"])
                entry["economics"], entry["multi"] = e, m
                t3 = L["multi_t"].format(p3=m["table"][2]["p_pass_any"], p5=m["table"][4]["p_pass_any"]) if m["table"] else ""
                entry["text"] += L["eco"].format(fee=e["eval_fee_monthly"], act=e["activation_fee"], fees=e["mean_fees"],
                                                 pp=e["p_payout"], p=e["p_pass"], pg=e["p_payout_given_pass"], net=e["mean_net_payout"],
                                                 ev=e["ev"], lo=e["ev_ci95"][0], hi=e["ev_ci95"][1])
                entry["text"] += L["multi"].format(n80=m["n_for_80"] or "–", n90=m["n_for_90"] or "–", t3=t3,
                                                   cost=m["table"][-1]["expected_cost"])
        accounts[acct] = entry
    comparison = None
    if all((accounts.get(k) or {}).get("economics") for k in ("50k", "25k")):
        a, b = accounts["50k"]["economics"], accounts["25k"]["economics"]
        w = "50k" if a["p_payout"] >= b["p_payout"] else "25k"
        comparison = L["cmp"].format(w=w, a=max(a["p_payout"], b["p_payout"]), b=min(a["p_payout"], b["p_payout"]),
                                     e50=a["ev"], e25=b["ev"])
    ex = report.get("exits") or {}
    ext = ""
    if (report["series"].get("exit")):
        e = report["series"]["exit"]
        ext = L["exit_on"].format(s=e["stop_atr"], rr=("%sR" % e["rr"]) if e["rr"] else "-", rrn=e["rr"] or "-",
                                  pts=(ex.get("best_stats") or {}).get("median_stop_points") or 0)
        if not e["rr"]:
            ext = ext.replace("e target a - (rapporto rischio/rendimento 1:-)", "senza target").replace(
                "and target at - (reward:risk 1:-)", "with no target")
    elif ex:
        ext = L["exit_off"]
    stop_pts = (ex.get("best_stats") or {}).get("median_stop_points") if report["series"].get("exit") else None
    for a in accounts.values():
        if a.get("recommended") and stop_pts:
            n = a["recommended"]["micro_equiv"]
            r = stop_pts * 2.0 * n                       # 1 MNQ = $2 per index point
            a["risk"]["risk_per_trade"] = r
            a["text"] += L["rpt"].format(n=n, r=r, pdd=r / a["risk"]["max_drawdown_allowed"])
    params = {k: v for k, v in (report["series"].get("best_params") or {}).items() if k != "tf"}
    tf = (report["series"].get("best_params") or {}).get("tf", "?")
    ps = ", ".join(f"{k}={v}" for k, v in params.items())
    best = next((a for a, e in accounts.items() if e["recommended"]), next(iter(accounts)))
    head = L["head"].format(name=report["name"], acct=best)
    strat = L["strat"].format(doc=doc or report["template"], tmpl=report["template"], tf=tf, params=ps,
                              why=(report.get("rationale") or "")[:400])
    strat += " " + ext if ext else ""
    if report.get("source"):
        strat += L["src"].format(src=str(report["source"])[:160])
    caveat = L["caveat"].format(n=len(pnl), trials=trials_total)
    return {"name": report["name"], "template": report["template"], "tf": tf, "params": params, "headline": head,
            "strategy_text": strat, "comparison": comparison, "economics_assumed": True, "exits": {"chosen": report["series"].get("exit"), "grid": ex.get("grid", [])}, "accounts": accounts, "caveat": caveat, "n_oos_days": len(pnl),
            "oos_pnl": float(pnl.sum()), "oos_mean": float(pnl.mean()), "oos_std": float(pnl.std(ddof=1)), "base_contracts": base_contracts, "language": language,
            "auditor": (audit or {}).get("summary"), "risk_flags": (audit or {}).get("risk_flags", [])}
