"""Generazione di report (MD + JSON), blocco PANIC-PROOF e trascrizione. Tutto in italiano."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .debate_engine import DebateResult
from .schemas import AgentStatus, Bias, confidence_label
from .security import redact_obj

DISCLAIMER = "ANALISI AI — NESSUN ORDINE AUTOMATICO"
AGENT_LABEL = {
    "price_action": "💙 Price Action", "options_flow": "💜 Options Flow", "strategist": "💚 Strategist",
    "risk_manager": "❤️ Risk Manager", "judge": "⚪ Judge",
}
NO_TRADE_STANDARD = [
    "Qualità dati ROSSA o dati critici mancanti/non validi",
    "R:R inferiore al minimo configurato o invalidazione non oggettiva",
    "Price Action e Options Flow in divergenza senza risoluzione chiara",
    "Struttura opzioni non disponibile e nessuna conferma alternativa",
    "Regime HIGH_VOLATILITY senza conferma di accettazione",
    "Lo scenario richiede una previsione anziché una conferma",
]


def _find(evidence: list[dict[str, Any]], key: str) -> Any:
    return next((e["value"] for e in evidence if e["key"] == key), None)


def build_panic_proof(*, features: dict[str, Any], evidence: list[dict[str, Any]], debate: DebateResult,
                      decision: dict[str, Any], strategist_out: Any, max_daily_r: float,
                      quality: str) -> dict[str, Any]:
    reg = features["regime"] if features else {"state": "UNKNOWN", "reasons": ["Dati non disponibili"]}
    kl = []
    for label, key in (("Max sessione prec.", "prev_high"), ("Min sessione prec.", "prev_low"),
                       ("Max overnight", "overnight_high"), ("Min overnight", "overnight_low"),
                       ("VWAP overnight", "vwap_overnight"), ("POC prec.", "prev_poc"),
                       ("VAH prec.", "prev_vah"), ("VAL prec.", "prev_val"), ("Gamma flip", "options.gamma_flip"),
                       ("Call wall", "options.call_wall"), ("Put wall", "options.put_wall")):
        v = _find(evidence, key)
        if v is not None:
            kl.append({"name": label, "value": v})
    best = None
    if strategist_out is not None and decision.get("selected_scenario_id"):
        best = next((s for s in strategist_out.scenarios if s.id == decision["selected_scenario_id"]), None)
    status = decision["status"]
    out: dict[str, Any] = {
        "market_regime": reg["state"], "regime_reasons": reg["reasons"],
        "primary_bias": decision.get("primary_bias", "NEUTRAL"),
        "secondary_bias": decision.get("secondary_bias", "NESSUNO"),
        "key_levels": kl,
        "no_trade_conditions": list(NO_TRADE_STANDARD),
        "max_daily_risk": f"Massimo {max_daily_r:g}R al giorno",
        "council_status": status, "data_quality": quality,
        "human_decision": "RICHIESTA — il Consiglio non esegue ordini",
    }
    if best is not None:
        out.update(best_setup=f"{best.id} {best.direction.value} — {best.setup_type}",
                   trigger=best.confirmation_required[0] if best.confirmation_required else "Conferma di accettazione",
                   entry_zone=best.entry_zone, invalidation=best.invalidation,
                   target=[t for t in (best.target_1, best.target_2) if t is not None],
                   expected_rr=best.expected_rr,
                   thesis_invalidation=best.failure_condition)
    else:
        out.update(best_setup="NESSUNO — NO TRADE" if status in ("NO_TRADE", "DATA_ERROR", "REVIEW_REQUIRED")
                   else "NESSUNO", trigger="—", entry_zone=None, invalidation=None, target=[],
                   expected_rr=None,
                   thesis_invalidation="Nessuna tesi operativa attiva: attendere nuovi dati o conferme.")
    return out


def build_report(*, run_meta: dict[str, Any], quality: str, data_issues: list[dict[str, str]],
                 features: dict[str, Any] | None, evidence: list[dict[str, Any]], warnings: list[str],
                 debate: DebateResult | None, decision: dict[str, Any], events: list[dict[str, Any]],
                 stages: dict[str, Any], max_daily_r: float) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    strat = None
    if debate is not None:
        for name in ("price_action", "options_flow", "strategist", "risk_manager", "judge"):
            r = debate.results.get(name)
            if r is None:
                agents[name] = {"status": "SKIPPED", "reason": debate.skipped.get(name, "non eseguito"),
                                "output": None, "meta": None}
            else:
                st = "FAILED" if r.output is None else r.output.status.value
                agents[name] = {"status": st, "output": r.output.model_dump(mode="json") if r.output else None,
                                "meta": r.meta.to_dict()}
        strat = debate.output("strategist")
    report = {
        "disclaimer": DISCLAIMER,
        "run": run_meta,
        "data_quality": {"status": quality, "issues": data_issues, "warnings": warnings},
        "market_snapshot": features,
        "evidence": evidence,
        "agents": agents,
        "risk": {
            "precheck": [p.to_dict() for p in debate.precheck] if debate else [],
            "assessments": [a.model_dump(mode="json") for a in debate.assessments] if debate else [],
        },
        "judge": {"score": debate.judge_score if debate else None,
                  "issues": [i.model_dump(mode="json") for i in debate.judge_issues] if debate else []},
        "decision": decision,
        "stages": stages,
        "human_decision": {"status": "PENDING", "note": "Decisione umana separata dall'output AI: vedere journal.csv"},
        "events": events,
    }
    report["panic_proof"] = build_panic_proof(
        features=features or {}, evidence=evidence, debate=debate or DebateResult(), decision=decision,
        strategist_out=strat, max_daily_r=max_daily_r, quality=quality)
    return report


# --------------------------------------------------------------------------- Markdown
def _fmt_list(items: list[str], indent: str = "- ") -> str:
    return "\n".join(f"{indent}{i}" for i in items) if items else "- _nessuno_"


def _agent_block(name: str, a: dict[str, Any]) -> str:
    lines = [f"**Stato:** {a['status']}"]
    meta = a.get("meta")
    if meta:
        fb = " (FALLBACK)" if meta["fallback_used"] else ""
        lines.append(f"**Modello:** richiesto `{meta['requested_model']}` → effettivo `{meta['actual_model']}`{fb} · "
                     f"{meta['duration_ms']} ms · retry {meta['retry_count']}")
    o = a.get("output")
    if not o:
        lines.append("_Nessun output valido: " + (a.get("reason") or "; ".join((meta or {}).get("errors", [])) or "errore") + "_")
        return "\n\n".join(lines)
    if "bias" in o:
        lines.append(f"**Bias:** {o['bias']} · **Confidenza:** {o['confidence']}/100 ({confidence_label(o['confidence'])})")
    else:
        lines.append(f"**Confidenza:** {o['confidence']}/100 ({confidence_label(o['confidence'])})")
    if o.get("facts"):
        lines.append("**FATTI** (dati verificabili)\n" + _fmt_list(
            [f"{c['text']} `{','.join(c['evidence_ids'])}`" for c in o["facts"]]))
    if o.get("interpretations"):
        lines.append("**INTERPRETAZIONI** (ipotesi)\n" + _fmt_list(
            [f"{c['text']} `{','.join(c['evidence_ids'])}`" for c in o["interpretations"]]))
    if o.get("key_levels"):
        lines.append("**Livelli chiave:** " + " · ".join(f"{k['name']} {k['value']}" for k in o["key_levels"]))
    if o.get("warnings"):
        lines.append("**Avvisi**\n" + _fmt_list(o["warnings"]))
    if o.get("reasoning_summary"):
        lines.append(f"_Sintesi:_ {o['reasoning_summary']}")
    return "\n\n".join(lines)


def render_markdown(rep: dict[str, Any]) -> str:
    run, snap = rep["run"], rep["market_snapshot"] or {}
    dec, pp = rep["decision"], rep["panic_proof"]
    dq = rep["data_quality"]
    L: list[str] = []
    L.append("# OFO COUNCIL\n## NQ PRE-MARKET ANALYSIS\n")
    L.append(f"> **{DISCLAIMER}**\n")
    if run["mode"] == "MOCK":
        L.append(f"> ⚠️ **MODALITÀ MOCK** (scenario `{run.get('mock_scenario')}`): le risposte degli agenti sono "
                 f"simulate da regole deterministiche, NON da un LLM.\n")
    if any("SINTETICI" in i["message"] for i in dq["issues"]):
        L.append("> ⚠️ **DATI SINTETICI DI ESEMPIO**: non sono dati di mercato reali.\n")
    L.append(f"**Data:** {run['date']}  \n**Spot:** {snap.get('spot', 'n/d')}  \n"
             f"**Regime di mercato:** {pp['market_regime']}  \n**Run ID:** `{run['run_id']}`  \n"
             f"**Qualità dati:** {dq['status']}\n")

    L.append("## 1. Market Snapshot\n")
    if snap:
        ps, on, at, op = snap["prev_session"], snap["overnight"], snap["atr"], snap["options"]
        L.append("| Metrica | Valore |\n|---|---|")
        rows = [("Spot", snap["spot"]), ("VWAP overnight", on["vwap"]), ("ATR(14) 5m", at["atr_5m"]),
                ("ATR giornaliero", at["atr_daily"]), ("Overnight H/L", f"{on['high']} / {on['low']}"),
                ("Sessione prec. H/L/C", f"{ps['high']} / {ps['low']} / {ps['close']}"),
                ("POC / VAH / VAL prec.", f"{ps['poc']} / {ps['vah']} / {ps['val']}"),
                ("HVN / LVN prec.", f"{ps['hvn']} / {ps['lvn']}"),
                ("Gamma flip / Call wall / Put wall", f"{op['gamma_flip']} / {op['call_wall']} / {op['put_wall']}"),
                ("Proxy regime gamma", op["gamma_regime_proxy"])]
        L.extend(f"| {k} | {v} |" for k, v in rows)
        L.append("\n**Regime:** " + snap["regime"]["state"] + " — " + " ".join(snap["regime"]["reasons"]) + "\n")
    else:
        L.append("_Dati di mercato non disponibili (qualità ROSSA)._\n")
    if dq["issues"]:
        L.append("**Avvisi/errori sui dati**\n" + _fmt_list([f"[{i['severity']}] {i['message']}" for i in dq["issues"]]) + "\n")
    issue_msgs = {i["message"] for i in dq["issues"]}
    extra = [w for w in dq["warnings"] if w not in issue_msgs]
    if extra:
        L.append("**Avvisi di coerenza**\n" + _fmt_list(extra) + "\n")

    sections = [("2. 💙 Price Action", "price_action"), ("3. 💜 Options Flow", "options_flow"),
                ("4. 💚 Strategist", "strategist")]
    for title, key in sections:
        L.append(f"## {title}\n")
        L.append(_agent_block(key, rep["agents"].get(key, {"status": "SKIPPED", "output": None, "meta": None,
                                                           "reason": "non eseguito"})) + "\n")
        if key == "strategist":
            o = rep["agents"].get("strategist", {}).get("output")
            if o:
                if o["no_trade"]:
                    L.append(f"**NO TRADE:** {o['no_trade_reason']}\n")
                for s in o["scenarios"]:
                    L.append(f"### Scenario {s['id']} — {s['direction']} ({s['setup_type']})\n"
                             f"- Zona di ingresso: {s['entry_zone'][0]} – {s['entry_zone'][1]}\n"
                             f"- Invalidazione: {s['invalidation']}\n"
                             f"- Target 1 / Target 2: {s['target_1']} / {s['target_2']}\n"
                             f"- R:R atteso: {s['expected_rr']} · Confidenza: {s['confidence']}/100\n"
                             f"- Conferme richieste: {'; '.join(s['confirmation_required'])}\n"
                             f"- Tesi (SE → ALLORA): {s['thesis']}\n"
                             f"- Condizione di fallimento: {s['failure_condition']}\n"
                             f"- Evidenze: `{', '.join(s['evidence_ids'])}`\n")

    L.append("## 5. ❤️ Risk Manager\n")
    L.append(_agent_block("risk_manager", rep["agents"].get("risk_manager", {"status": "SKIPPED", "output": None, "meta": None,
                                                                            "reason": "non eseguito"})) + "\n")
    if rep["risk"]["assessments"]:
        L.append("| Scenario | Verdetto finale | R:R calcolato | Motivi |\n|---|---|---|---|")
        for a in rep["risk"]["assessments"]:
            L.append(f"| {a['scenario_id']} | **{a['verdict']}** | {a['computed_rr']} | {'; '.join(a['reasons'])} |")
        L.append("\n_Il verdetto finale è il più severo tra motore di rischio deterministico e Risk Manager: un veto non si scavalca._\n")

    L.append("## 6. ⚪ Judge\n")
    L.append(_agent_block("judge", rep["agents"].get("judge", {"status": "SKIPPED", "output": None, "meta": None,
                                                              "reason": "non eseguito"})) + "\n")
    L.append(f"**Punteggio finale:** {rep['judge']['score']}/100\n" if rep["judge"]["score"] is not None else "")
    if rep["judge"]["issues"]:
        L.append("**Problemi rilevati**\n" + _fmt_list(
            [f"[{i['severity']}] {i['type']}: {i['description']}" for i in rep["judge"]["issues"]]) + "\n")

    L.append("## 7. Final Council Decision\n")
    L.append(f"### AI COUNCIL VERDICT: `{dec['status']}`\n")
    L.append(_fmt_list(dec["reasons"]) + "\n")
    L.append("_Logica gerarchica: qualità dati → evidenza → convergenza PA+OF → Strategist → veto Risk → Giudice. "
             "Non è una media di punteggi._\n")

    L.append("## 8. PANIC-PROOF\n")
    L.append("```\n" + render_panic_proof(pp) + "\n```\n")

    L.append("## 9. Human Decision\n")
    L.append("**Raccomandazione AI:** " + dec["status"] + " (solo analisi, nessun ordine automatico).\n")
    L.append("**Decisione umana:** `PENDING` — da registrare nel journal (TAKE SETUP / SKIP / MODIFY / WATCH / NO TRADE). "
             "La decisione umana è registrata separatamente dall'output AI.\n")
    L.append("---\n_Metriche per fase: " + ", ".join(
        f"{k} {v['status']} {v['duration_ms']}ms" for k, v in rep["stages"].items()) + "_\n")
    return "\n".join(L)


def render_panic_proof(pp: dict[str, Any]) -> str:
    bar = "=" * 60
    kl = "\n".join(f"      {k['name']}: {k['value']}" for k in pp["key_levels"]) or "      n/d"
    return "\n".join([
        bar, "PANIC-PROOF", bar,
        f"MARKET REGIME:        {pp['market_regime']}",
        f"STATO CONSIGLIO:      {pp['council_status']}   (qualità dati {pp['data_quality']})",
        f"PRIMARY BIAS:         {pp['primary_bias']}",
        f"SECONDARY BIAS:       {pp['secondary_bias']}",
        "KEY LEVELS:", kl,
        f"BEST SETUP:           {pp['best_setup']}",
        f"TRIGGER:              {pp['trigger']}",
        f"ZONA INGRESSO:        {pp['entry_zone'] if pp['entry_zone'] else '—'}",
        f"INVALIDATION:         {pp['invalidation'] if pp['invalidation'] is not None else '—'}",
        f"TARGET:               {pp['target'] if pp['target'] else '—'}",
        "NO-TRADE CONDITIONS:", *[f"      - {c}" for c in pp["no_trade_conditions"]],
        f"MAX DAILY RISK:       {pp['max_daily_risk']}",
        f"WHAT WOULD INVALIDATE THE THESIS: {pp['thesis_invalidation']}",
        f"HUMAN DECISION REQUIRED: {pp['human_decision']}",
        bar, DISCLAIMER, bar,
    ])


# --------------------------------------------------------------------------- Scrittura
def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_obj(obj), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def build_transcript(*, run_meta: dict[str, Any], debate: DebateResult | None, events: list[dict[str, Any]],
                     stages: dict[str, Any]) -> dict[str, Any]:
    calls = []
    if debate is not None:
        for name, r in debate.results.items():
            evid: list[str] = []
            if r.output is not None:
                for coll in ("facts", "interpretations", "key_levels", "scenarios"):
                    for x in getattr(r.output, coll, []):
                        evid.extend(x.evidence_ids)
            calls.append({**r.meta.to_dict(), "status": r.status.value,
                          "output": r.output.model_dump(mode="json") if r.output else None,
                          "evidence_refs": sorted(set(evid))})
    return {"run": run_meta, "calls": calls, "events": events, "stages": stages}


def write_artifacts(report: dict[str, Any], transcript: dict[str, Any], reports_dir: Path,
                    transcripts_dir: Path) -> dict[str, Path]:
    date = report["run"]["date"]
    md = reports_dir / f"{date}.md"
    js = reports_dir / f"{date}.json"
    tr = transcripts_dir / f"{date}.json"
    reports_dir.mkdir(parents=True, exist_ok=True)
    md.write_text(redact_obj(render_markdown(report)), encoding="utf-8")
    write_json(js, report)
    write_json(tr, transcript)
    return {"report_md": md, "report_json": js, "transcript": tr}
