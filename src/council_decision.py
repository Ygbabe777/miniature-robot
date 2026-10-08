"""Decisione finale gerarchica del Consiglio (NON una media di punteggi).

DATA QUALITY -> EVIDENCE -> CONVERGENZA PA+OF -> STRATEGIST -> RISK VETO -> JUDGE -> STATO FINALE
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import (AgentStatus, AnalystOutput, Bias, DataQuality, FinalStatus, JudgeIssue,
                      JudgeOutput, JudgeVerdict, RiskAssessment, RiskVerdict, Scenario, Severity,
                      StrategistOutput, VERDICT_SEVERITY)

ACTIONABLE = (RiskVerdict.APPROVED, RiskVerdict.APPROVED_WITH_CAUTION)


@dataclass
class CouncilDecision:
    status: FinalStatus
    reasons: list[str]
    selected_scenario_id: str | None = None
    scenario_verdicts: dict[str, str] = field(default_factory=dict)
    primary_bias: str = "NEUTRAL"
    secondary_bias: str = "NESSUNO"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "reasons": self.reasons,
                "selected_scenario_id": self.selected_scenario_id,
                "scenario_verdicts": self.scenario_verdicts,
                "primary_bias": self.primary_bias, "secondary_bias": self.secondary_bias}


def _downgrade(verdict: RiskVerdict, issues: list[JudgeIssue], scenario_id: str) -> tuple[RiskVerdict, list[str]]:
    notes: list[str] = []
    mine = [i for i in issues if i.scenario_id == scenario_id]
    if any(i.severity == Severity.CRITICAL for i in mine):
        if VERDICT_SEVERITY[verdict] < VERDICT_SEVERITY[RiskVerdict.NO_TRADE]:
            verdict = RiskVerdict.NO_TRADE
        notes.append(f"Scenario {scenario_id} respinto: affermazioni critiche non supportate")
    elif any(i.severity == Severity.MAJOR for i in mine) and verdict == RiskVerdict.APPROVED:
        verdict = RiskVerdict.APPROVED_WITH_CAUTION
        notes.append(f"Scenario {scenario_id} declassato a cautela: problemi di ancoraggio rilevati")
    return verdict, notes


def _biases(pa: AnalystOutput | None, of: AnalystOutput | None) -> tuple[Bias, Bias]:
    return (pa.bias if pa and pa.status == AgentStatus.OK else Bias.UNKNOWN,
            of.bias if of and of.status == AgentStatus.OK else Bias.UNKNOWN)


def decide(*, data_quality: DataQuality, pa: AnalystOutput | None, of: AnalystOutput | None,
           strategist: StrategistOutput | None, risk_ran: bool, risk_ok: bool,
           assessments: list[RiskAssessment], judge: JudgeOutput | None, judge_ran: bool,
           issues: list[JudgeIssue], judge_score: int | None, judge_min_score: int,
           options_available: bool) -> CouncilDecision:
    reasons: list[str] = []

    # 1. Qualita' dati
    if data_quality == DataQuality.RED:
        return CouncilDecision(FinalStatus.DATA_ERROR,
                               ["Qualità dati ROSSA: dati critici mancanti o non validi. Nessuno scenario può essere approvato."])

    # 2. Guasti: un'analisi parziale non deve sembrare un'analisi riuscita
    pa_ok = pa is not None and pa.status != AgentStatus.FAILED
    of_ok = of is not None and of.status != AgentStatus.FAILED
    failures = []
    if not pa_ok:
        failures.append("Price Action non disponibile")
    if not of_ok:
        failures.append("Options Flow non disponibile")
    if strategist is None:
        failures.append("Strategist non disponibile: nessuno scenario generato")
    if risk_ran and not risk_ok:
        failures.append("Risk Manager non disponibile: tutti gli scenari NON approvati")
    if judge_ran and judge is None:
        failures.append("Giudice non disponibile")
    if failures:
        return CouncilDecision(FinalStatus.REVIEW_REQUIRED, failures + ["Revisione umana richiesta."],
                               scenario_verdicts={a.scenario_id: a.verdict.value for a in assessments})

    pa_b, of_b = _biases(pa, of)
    scenarios: list[Scenario] = strategist.scenarios if strategist else []

    # 3. Strategist: NO TRADE e' un esito valido
    if not scenarios:
        why = strategist.no_trade_reason if strategist and strategist.no_trade_reason else "nessuno scenario supportato dai dati"
        bias = pa_b.value if pa_b == of_b and pa_b in (Bias.LONG, Bias.SHORT) else "NEUTRAL"
        return CouncilDecision(FinalStatus.NO_TRADE, [f"Lo Strategist non propone scenari: {why.rstrip('.')}."],
                               primary_bias=bias)

    # 4. Risk veto + declassamenti del Giudice
    by_id = {a.scenario_id: a for a in assessments}
    final_verdicts: dict[str, RiskVerdict] = {}
    for s in scenarios:
        a = by_id.get(s.id)
        v = a.verdict if a else RiskVerdict.NOT_APPROVED
        v, notes = _downgrade(v, issues, s.id)
        reasons.extend(notes)
        final_verdicts[s.id] = v
        if v == RiskVerdict.VETO:
            reasons.append(f"Scenario {s.id}: VETO del Risk Manager (non scavalcabile)")
    verdicts_out = {k: v.value for k, v in final_verdicts.items()}
    candidates = [s for s in scenarios if final_verdicts[s.id] in ACTIONABLE]
    if not candidates:
        reasons.append("Nessuno scenario approvato dal Risk Manager: NO TRADE.")
        bias = pa_b.value if pa_b == of_b and pa_b in (Bias.LONG, Bias.SHORT) else "NEUTRAL"
        return CouncilDecision(FinalStatus.NO_TRADE, reasons, scenario_verdicts=verdicts_out, primary_bias=bias)

    best = sorted(candidates, key=lambda s: (VERDICT_SEVERITY[final_verdicts[s.id]], -s.confidence))[0]
    others = [s for s in scenarios if s.id != best.id]
    status = (FinalStatus.APPROVED_SETUP if final_verdicts[best.id] == RiskVerdict.APPROVED
              else FinalStatus.APPROVED_WITH_CAUTION)

    # 5. Cap per qualita' evidenza
    if not options_available:
        if status == FinalStatus.APPROVED_SETUP:
            status = FinalStatus.APPROVED_WITH_CAUTION
        reasons.append("Dati opzioni non disponibili: setup al massimo con cautela.")
    if data_quality == DataQuality.YELLOW:
        reasons.append("Qualità dati GIALLA: alcuni dati opzionali mancanti o avvisi attivi.")

    # 6. Giudice: puo' solo declassare
    if judge is not None:
        if judge.verdict == JudgeVerdict.REJECTED or (judge_score is not None and judge_score < judge_min_score):
            reasons.append(f"Giudice: catena non sufficientemente solida (punteggio {judge_score}/100, "
                           f"verdetto {judge.verdict.value}).")
            return CouncilDecision(FinalStatus.REVIEW_REQUIRED, reasons + ["Revisione umana richiesta."],
                                   scenario_verdicts=verdicts_out, selected_scenario_id=best.id,
                                   primary_bias=best.direction.value)

    reasons.insert(0, f"Scenario selezionato {best.id} ({best.direction.value}): "
                      f"{final_verdicts[best.id].value}.")
    secondary = "NESSUNO"
    if others:
        secondary = others[0].direction.value
    return CouncilDecision(status, reasons, selected_scenario_id=best.id, scenario_verdicts=verdicts_out,
                           primary_bias=best.direction.value, secondary_bias=secondary)
