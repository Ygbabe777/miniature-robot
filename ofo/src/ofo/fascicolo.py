"""The dossier (fascicolo) of an approved strategy, and the board discussion that preceded the verdict.

Everything written here is derived from numbers that were actually computed (the study report and the prop-farming review):
no invented dialogue. Italian, as requested. The owner decides about LIVE: OFO never sends orders."""
from __future__ import annotations

import time

from .lifecycle import POLICIES
from .strategies import TEMPLATES


def _money(x) -> str:
    return "n/d" if x is None else f"{'-' if x < 0 else ''}${abs(x):,.0f}"


def _pct(x) -> str:
    return "n/d" if x is None else f"{x * 100:.0f}%"


def _days(x) -> str:
    return "n/d" if x is None else f"{x:.0f} sessioni"


def entry_logic(report: dict) -> dict:
    t = TEMPLATES.get(report["template"])
    bp = dict((report.get("series") or {}).get("best_params") or {})
    tf = bp.pop("tf", "?")
    ex = (report.get("series") or {}).get("exit")
    direction = (report.get("series") or {}).get("direction") or "both"
    dtxt = {"both": "long e short", "long": "solo long", "short": "solo short"}[direction]
    out = {"template": report["template"], "descrizione": t.doc if t else "", "timeframe": tf, "parametri": bp, "direzione": dtxt,
           "ipotesi": (report.get("rationale") or "")[:600], "fonte": report.get("source") or ""}
    if ex:
        r = ex.get("rr")
        out["uscite"] = (f"stop a {ex['stop_atr']} ATR" + (f", target a 1:{r}" if r else ", nessun target fisso (uscita sul segnale)"))
    else:
        out["uscite"] = "uscita sul segnale della strategia (nessuno stop/target fisso)"
    out["regole_comuni"] = "ingresso alla chiusura della barra che genera il segnale; tutto chiuso prima delle 16:45 ET; nessun ordine reale inviato da OFO"
    return out


def invalidation(fr: dict, ruleset_id: str) -> list[str]:
    """Conditions that would invalidate the approval, each tied to a computed number."""
    ev = fr["by_ruleset"][ruleset_id]["evaluation"]
    m, st = ev["metrics"], ev["stress"]
    cx = next((k for k in st if k.startswith("costi_x")), None)
    out = []
    if cx:
        out.append(f"Costi di esecuzione molto più alti del previsto: con costi ×{cx[len('costi_x'):]} il NPFV medio scende a {_money(st[cx]['npfv_mean'])}.")
    out.append(f"Un'attesa più bassa: con expectancy -25% il NPFV medio è {_money(st['expectancy_-25%']['npfv_mean'])}; con volatilità +25% è {_money(st['volatilita_+25%']['npfv_mean'])}.")
    out.append(f"Una serie negativa prolungata (5 delle peggiori sessioni consecutive): NPFV medio {_money(st['serie_negativa']['npfv_mean'])}, "
               f"probabilità di perdere un account {_pct(st['serie_negativa']['p_account_failure'])}.")
    out.append(f"Un regolamento diverso da quello assunto ({fr['by_ruleset'][ruleset_id]['evaluation']['ruleset']['provenance']}): tutti i valori economici dipendono dal ruleset.")
    out.append(f"Un regime di mercato diverso da quello del campione fuori campione ({fr['n_oos_sessions']} sessioni): la simulazione non può vederlo.")
    out.append("Il paper trading che si discosta dalle attese (gate 7): se il forward smentisce l'edge, la strategia va ritirata.")
    return out


def why_approved(fr: dict, rid: str) -> list[str]:
    ev = fr["by_ruleset"][rid]["evaluation"]; m = ev["metrics"]; n = m["npfv"]
    return [f"Ha superato tutti i gate da 1 a 6 sul ruleset {ev['ruleset']['id']} v{ev['ruleset']['version']}.",
            f"Valore netto atteso {_money(n['mean'])} (mediana {_money(n['median'])}), con limite inferiore al 95% {_money(n['lcb95'])}: positivo anche nello scenario sfavorevole dei dati.",
            f"Primo payout incassato nel {_pct(m['p_first_payout'])} dei percorsi; dopo il passaggio dell'evaluation, nel {_pct(m['p_first_payout_given_pass'])}.",
            f"Il funded sopravvive fino a fine orizzonte nel {_pct(m['p_funded_survival'])} dei casi in cui viene attivato.",
            f"Resta positivo con costi ×{next(k for k in ev['stress'] if k.startswith('costi_x'))[7:]} e non viola il drawdown intraday nelle finestre storiche."]


def build(slug: str, report: dict, extra: dict | None = None) -> dict | None:
    fr = report.get("farming")
    if not fr or not fr.get("by_ruleset"):
        return None
    rid = fr.get("best_ruleset") or next(iter(fr["by_ruleset"]))
    ev = fr["by_ruleset"][rid]["evaluation"]; gt = fr["by_ruleset"][rid]["gates"]
    m, n = ev["metrics"], ev["metrics"]["npfv"]
    approved = report["locked"]["status"] == "APPROVED"
    per_rs = {}
    for k, v in fr["by_ruleset"].items():
        e = v["evaluation"]
        per_rs[k] = {"verdetto": v["gates"]["verdict"], "prima_barriera_fallita": v["gates"]["first_failed"], "ruleset": e["ruleset"],
                     "size_micro": e["micro"], "policy": e["policy"], "npfv": e["metrics"]["npfv"], "p_first_payout": e["metrics"]["p_first_payout"],
                     "p_pass": e["metrics"]["p_pass"], "costo_atteso": e["metrics"]["e_total_cost"]}
    doc = {
        "slug": slug, "nome": report["name"], "mercato": report.get("market") or "NQ", "creato": time.strftime("%Y-%m-%d %H:%M"),
        "stato": report["locked"]["status"], "stato_judge_legacy": report["locked"].get("judge_status") or report["locked"].get("legacy_status"),
        "ruleset_adatti": fr.get("suited", []), "ruleset_migliore": rid, "archetipo": fr.get("archetype"),
        "logica_ingresso": entry_logic(report),
        "risultati": {"npfv": n, "p_pass": m["p_pass"], "p_first_payout": m["p_first_payout"], "p_first_payout_lcb95": m.get("p_first_payout_lcb95"),
                      "p_first_payout_given_pass": m["p_first_payout_given_pass"], "p_funded_survival": m["p_funded_survival"],
                      "e_time_to_pass": m["e_time_to_pass"], "e_time_to_first_payout": m["e_time_to_first_payout"], "e_payout_count": m["e_payout_count"],
                      "p_second_payout": m["p_second_payout"], "p_third_payout": m["p_third_payout"], "e_net_payout": m["e_net_payout"],
                      "e_total_cost": m["e_total_cost"], "cost_per_first_payout": m["cost_per_first_payout"], "payout_efficiency": m["payout_efficiency"],
                      "p_account_failure": m["p_account_failure"], "npfv_es5": m["npfv_es5"], "lifetime_days": m["lifetime_days"],
                      "drawdown_utilization": m["drawdown_utilization"], "size_micro": ev["micro"], "policy": ev["policy"],
                      "policy_label": POLICIES[ev["policy"]], "stress": ev["stress"], "policies_dev": ev["policies"].get("policies"),
                      "oos_pnl": report["locked"].get("pnl"), "oos_sessions": fr["n_oos_sessions"], "granularita_barre_min": fr.get("granularity_minutes")},
        "barriere": gt["gates"], "esito_per_ruleset": per_rs,
        "perche": (why_approved(fr, rid) if approved else
                   [f"Respinta alla barriera {gt['first_failed']['id']} ({gt['first_failed']['name']}): {gt['first_failed']['reason']}"] if gt.get("first_failed") else []),
        "condizioni_invalidanti": invalidation(fr, rid),
        "portafoglio": fr.get("portfolio"), "avvisi": ([ev["ruleset"]["warning"]] if ev["ruleset"]["warning"] else []) +
                  ([f"Scheda ipotesi incompleta: il Researcher non ha dichiarato {', '.join(report['hypothesis_missing'])}."] if report.get("hypothesis_missing") else []),
        "limiti": ev["limits"], "scheda_ipotesi": report.get("hypothesis") or {}, "scheda_mancante": report.get("hypothesis_missing") or [],
        "verbale": board_review(report),
    }
    if extra:
        doc.update(extra)
    return doc


# ------------------------------------------------------------------ the board discussion (derived from the numbers)
def board_review(report: dict) -> list[dict]:
    fr = report.get("farming")
    if not fr or not fr.get("by_ruleset"):
        return []
    rid = fr.get("best_ruleset"); by = fr["by_ruleset"][rid]; ev, gt = by["evaluation"], by["gates"]
    m, n = ev["metrics"], ev["metrics"]["npfv"]; st = ev["stress"]; comp = ev["compliance"]
    surf = report.get("surface", {})
    lines = []
    ff = m["p_fail_before_first_payout_given_funded"]
    risk = (f"Rischio: l'evaluation si supera nel {_pct(m['p_pass'])} dei percorsi, ma quello che conta è il primo payout: {_pct(m['p_first_payout'])}. "
            f"Dopo l'attivazione il funded cade prima di incassare nel {_pct(ff)} dei casi; il {_pct(m['p_account_failure'])} dei percorsi perde almeno un account. "
            f"Utilizzo medio del drawdown {_pct(m['drawdown_utilization'])}.")
    lines.append({"agent": "risk", "role": "Risk Manager", "text": risk})
    q = report.get("quality") or {}
    lines.append({"agent": "strategist", "role": "Strategist",
                  "text": (f"Edge: Sharpe mediano della superficie {surf.get('median_sharpe', 0):.3f}, plateau dei parametri {_pct(surf.get('plateau_ratio'))}, "
                           f"{surf.get('n_combos', '?')} combinazioni provate. Timeframe scelta {report['series']['best_params'].get('tf', '?')}, "
                           f"direzione {report['series'].get('direction', 'both')}. Archetipo: {(fr.get('archetype') or {}).get('label', 'n/d')}.")})
    rc = report.get("ledger_check") or {}
    lines.append({"agent": "auditor", "role": "Auditor",
                  "text": (f"Integrità: libro mastro {'riconciliato' if rc.get('ok') else 'NON riconciliato'} con il motore vettoriale; causalità verificata su più punti di taglio; "
                           f"i tentativi contati nel registro sono {report.get('n_candidates', '?')} + varianti di uscita e direzione. "
                           f"Granularità dei dati: barre da {fr.get('granularity_minutes') or 0:g} minuti, sequenza interna alla barra risolta in modo conservativo.")})
    lines.append({"agent": "planner", "role": "Planner",
                  "text": (f"Compatibilità col conto {ev['ruleset']['label']} ({ev['ruleset']['id']}, {ev['ruleset']['provenance']}): size consigliata {ev['micro']} micro, "
                           f"politica di payout «{POLICIES[ev['policy']]}», costo atteso {_money(m['e_total_cost'])}, "
                           f"costo per ogni primo payout {_money(m['cost_per_first_payout'])}.")})
    cx = next((k for k in st if k.startswith("costi_x")), None)
    jd = (f"Giudice: NPFV medio {_money(n['mean'])}, limite inferiore 95% {_money(n['lcb95'])}; con costi doppi {_money(st[cx]['npfv_mean']) if cx else 'n/d'}. "
          f"Verdetto deterministico: {fr['verdict']}.")
    if gt.get("first_failed"):
        jd += f" Prima barriera fallita: {gt['first_failed']['id']} — {gt['first_failed']['name']}: {gt['first_failed']['reason']}"
    lines.append({"agent": "judge", "role": "Judge", "text": jd})
    return lines
