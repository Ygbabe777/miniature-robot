"""Knowledge-driven hypotheses: a falsifiable claim derived from something the lab MEASURED, with its sources and a novelty check.

A card is built from a behavior study (never from a language model's recollection). It states the market, the economic mechanism, the observations and memory notes it
comes from, a testable prediction, the criterion that would falsify it, the data it needs, the expected execution costs, a priority and a novelty assessment.
It becomes a `StrategySpec` for the EXISTING pipeline only when a template exists for it; otherwise it stays a research question (and is studied, not traded).
Nothing here touches the Judge or the hidden block: the evidence behind a card is development-only by construction (guard.DevView)."""
from __future__ import annotations

from .. import research_config as RC
from .schema import fingerprint

MECHANISM = {
    "sweep_reclaim": ("Stop e ordini di liquidità si accumulano oltre i livelli strutturali (massimi/minimi del giorno precedente e overnight). Lo sweep li consuma; se il prezzo rientra "
                      "rapidamente, la spinta direzionale era di breve durata e il livello torna ad attrarre prezzo."),
    "level_acceptance": ("Quando il prezzo viene accettato oltre un livello (chiusure consecutive), il livello perde significato di resistenza/supporto e il movimento può proseguire."),
    "orb": ("Il range d'apertura riassume lo squilibrio iniziale tra offerta e domanda; una rottura accettata segnala che lo squilibrio persiste nella sessione."),
    "orb_failure": ("Una rottura del range d'apertura che rientra segnala assenza di partecipazione oltre il range e favorisce il ritorno verso il suo interno."),
    "gap": ("Il gap rispetto all'overnight riflette inventario sbilanciato in ingresso nella sessione cash: o viene smaltito (fade) o proseguito dai partecipanti informati (continuation)."),
}
TEMPLATE_OF = {"sweep_reclaim": "sweep_reclaim", "orb": "orb", "orb_failure": "orb_retest", "gap": "overnight_gap"}


def _vals(template: str, param: str, around=None, n: int = 4) -> list:
    sp = RC.get()["search_space"][template]["params"][param]
    lo, hi, step, init = sp["range"][0], sp["range"][1], sp["step"], sp["init"]
    c = init if around is None else around
    vals = sorted({round(min(hi, max(lo, c + k * step)), 6) for k in range(-(n // 2), n // 2 + 1)})
    return [int(v) if isinstance(init, int) else float(v) for v in vals]


def spec_for(question: dict) -> dict | None:
    """A StrategySpec skeleton for questions that map to a whitelisted template. Parameters come from the question's own definition, not from a search."""
    d, fam = question["definition"], question["family"]
    tname = TEMPLATE_OF.get(fam)
    if not tname:
        return None
    if fam == "sweep_reclaim":
        ranges = {"sweep_atr": _vals(tname, "sweep_atr"), "reclaim_bars": _vals(tname, "reclaim_bars", d.get("reclaim_bars") or None, 3),
                  "level_set": [0 if d["level"] in ("pdh", "pdl") else 1], "ctx": [0, 1]}
    elif fam == "orb":
        ranges = {"or_minutes": _vals(tname, "or_minutes")}
    elif fam == "orb_failure":
        ranges = {"or_minutes": _vals(tname, "or_minutes"), "retest_bars": _vals(tname, "retest_bars"), "tol": _vals(tname, "tol")}
    else:
        ranges = {"gap_atr": _vals(tname, "gap_atr"), "mode": [1 if d["trade"] == "continue" else 0], "exit_min": _vals(tname, "exit_min")}
    return {"template": tname, "ranges": ranges}


def card_from_study(study: dict, question: dict, note_ids: list[str]) -> dict:
    """The hypothesis card (Italian, the 10 CARD_FIELDS plus provenance) built from a measured study."""
    d = study["definition"]
    dist, base = study.get("distribution") or {}, study.get("baseline") or {}
    lo, hi = (base.get("diff_ci95") or [None, None])
    num = f"{study['n_events']} eventi in {study['n_sessions']} sessioni, esito netto medio {dist.get('mean_net', 0):+.3f} ATR, differenza vs baseline {base.get('diff', 0):+.3f} ATR" \
          + (f" (IC95 {lo:+.3f}, {hi:+.3f})" if lo is not None else "")
    fam = question["family"]
    side = d.get("trade", "fade")
    card = {
        "fenomeno": f"{question['text']} Osservato in sviluppo: {num}.",
        "persistenza": MECHANISM.get(fam, "Meccanismo da specificare."),
        "condizioni_funziona": "Regimi in cui lo studio mostra esito netto positivo: " + (", ".join(k for k, v in (study.get("by_regime") or {}).items() if v["mean_net"] > 0) or "nessun regime isolato con campione sufficiente") + ".",
        "condizioni_fallisce": "Regimi con esito non positivo: " + (", ".join(k for k, v in (study.get("by_regime") or {}).items() if v["mean_net"] <= 0) or "non determinati") + "; costi superiori a quelli ipotizzati; campioni piccoli.",
        "segnale": f"Livello {d.get('level', 'apertura')} ({d.get('outcome', d.get('event'))}) classificato solo dopo la chiusura della finestra di osservazione: nessun dato futuro.",
        "regola_ingresso": f"Ingresso {'contro' if side == 'fade' else 'con'} il movimento, all'apertura della prima barra successiva alla disponibilità del segnale.",
        "regola_uscita": f"Uscita dopo {study['horizon_min']} minuti (da ottimizzare solo su dati di sviluppo con stop e target della pipeline).",
        "costi_limiti": f"Costi ipotizzati {study.get('cost_sensitivity', {}).get('x1', 0):+.3f} ATR netti; punto di pareggio sui costi ×{(study.get('cost_sensitivity') or {}).get('breakeven_cost_multiple')}; slippage reale non misurato.",
        "previsione_falsificabile": f"L'esito netto per evento resta positivo e la differenza vs baseline ha intervallo di confidenza sopra zero su dati di sviluppo successivi alla definizione.",
        "criterio_rigetto": "Intervallo della differenza vs baseline che include zero o è negativo, metà temporali discordanti, o esito concentrato in pochi eventi.",
    }
    return card


def novelty(definition_id: str, family: str, level: str | None, existing: list[dict]) -> dict:
    """How much NEW knowledge a hypothesis would add compared with what the memory already holds (existing: front matter of related notes)."""
    best, why = 0.0, "nessuna nota correlata"
    for f in existing:
        if f.get("ofo_definition") == definition_id:
            kind = f.get("ofo_kind")
            if f.get("ofo_evidence") == "CONTRADICTED":
                return {"score": 0.0, "duplicate": True, "reason": f"già smentita (nota {f.get('ofo_id')}) su {f.get('ofo_data_version')}"}
            if kind in ("hypothesis", "mission", "strategy_study"):
                return {"score": 0.0, "duplicate": True, "reason": f"ipotesi già formulata (nota {f.get('ofo_id')})"}
            best, why = max(best, 0.4), f"definizione già studiata (nota {f.get('ofo_id')}) su altri dati"
        elif f.get("ofo_family") == family and f.get("ofo_level") == level:
            best, why = max(best, 0.6), "stessa famiglia e livello con definizione diversa"
        elif f.get("ofo_family") == family:
            best, why = max(best, 0.3), "stessa famiglia"
    return {"score": round(1.0 - best, 3), "duplicate": False, "reason": why}


def make_hypothesis(study: dict, question: dict, evidence_note_ids: list[str], existing_fronts: list[dict], market: str = "NQ") -> dict | None:
    """A hypothesis from one study, or None if it adds no knowledge (contradicted, too weak, or a duplicate)."""
    st = study.get("status")
    base = study.get("baseline") or {}
    lo, hi = (base.get("diff_ci95") or [None, None])
    promising = st == "SUPPORTED" or (st == "INCONCLUSIVE" and lo is not None and (base.get("diff") or 0) > 0 and (study.get("p_value") or 1) <= 0.25 and study["n_events"] >= 15)
    if not promising:
        return None
    d = study["definition"]
    nov = novelty(d["id"], question["family"], d.get("level"), existing_fronts)
    if nov["duplicate"]:
        return {"duplicate": True, "reason": nov["reason"], "definition": d["id"]}
    effect = max(0.0, (lo or 0.0)) + 0.5 * max(0.0, base.get("diff") or 0.0)
    hid = "H-" + fingerprint({"d": d["id"], "m": market, "dv": study.get("data_version")})[:8]
    priority = round(nov["score"] * min(1.0, 0.35 + effect * 4 + (0.3 if st == "SUPPORTED" else 0.0)), 3)
    spec = spec_for(question)
    return {"id": hid, "market": market, "family": question["family"], "definition": d["id"], "level": d.get("level"), "status": "HYPOTHESIZED",
            "mechanism": MECHANISM.get(question["family"], ""), "source_observations": [question["text"]], "source_studies": [study["name"]], "obsidian_refs": evidence_note_ids,
            "testable_prediction": f"esito netto > 0 con IC della differenza vs baseline sopra zero su dati non usati per definirla ({d.get('outcome')} su {d.get('level')})",
            "falsification": "IC della differenza che include zero o negativo; stabilità temporale assente; concentrazione in pochi eventi",
            "required_data": ["barre a 1 minuto con volume", "sessioni e livelli causali (MIE)"], "expected_costs": f"{study['cost_sensitivity']['x1']:+.3f} ATR netti ipotizzati",
            "priority": priority, "novelty": nov, "card": card_from_study(study, question, evidence_note_ids), "spec": spec,
            "pipeline": "spec" if spec else "research_question_only"}
