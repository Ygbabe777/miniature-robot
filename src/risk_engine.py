"""Motore di rischio deterministico: R:R, geometria, esposizione, convergenza.

Il verdetto finale e' il PIU' SEVERO tra questo motore e il Risk Manager LLM:
un LLM non puo' mai ammorbidire un veto aritmetico.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import RiskCfg
from .intermarket import scenario_gate
from .schemas import (Bias, DataQuality, Direction, RiskAssessment, RiskOutput, RiskVerdict,
                      Scenario, worst_verdict)

GATES = ("data", "structure", "risk", "confluence", "intermarket")


def worst_case_entry(s: Scenario) -> float:
    """Entry peggiore nella zona: estremo alto per LONG, basso per SHORT."""
    return s.entry_zone[1] if s.direction == Direction.LONG else s.entry_zone[0]


def geometry_problems(s: Scenario) -> list[str]:
    lo, hi = s.entry_zone
    probs: list[str] = []
    if s.direction == Direction.LONG:
        if not s.invalidation < lo:
            probs.append("LONG: l'invalidazione deve stare sotto la zona di ingresso")
        if not s.target_1 > hi:
            probs.append("LONG: target_1 deve stare sopra la zona di ingresso")
        if s.target_2 is not None and not s.target_2 > s.target_1:
            probs.append("LONG: target_2 deve stare oltre target_1")
    else:
        if not s.invalidation > hi:
            probs.append("SHORT: l'invalidazione deve stare sopra la zona di ingresso")
        if not s.target_1 < lo:
            probs.append("SHORT: target_1 deve stare sotto la zona di ingresso")
        if s.target_2 is not None and not s.target_2 < s.target_1:
            probs.append("SHORT: target_2 deve stare oltre target_1")
    return probs


def computed_rr(s: Scenario) -> float | None:
    """R:R con entry peggiore; None se la geometria non lo rende calcolabile."""
    if geometry_problems(s):
        return None
    entry = worst_case_entry(s)
    risk = abs(entry - s.invalidation)
    if risk <= 0:
        return None
    return round(abs(s.target_1 - entry) / risk, 2)


@dataclass
class ScenarioPrecheck:
    scenario_id: str
    verdict: RiskVerdict
    computed_rr: float | None
    reasons: list[str]
    gates: dict[str, str] = field(default_factory=dict)  # PASS | WARN | FAIL

    def to_dict(self) -> dict[str, Any]:
        return {"scenario_id": self.scenario_id, "verdict": self.verdict.value,
                "computed_rr": self.computed_rr, "reasons": self.reasons, "gates": self.gates}


def confluence(direction: Direction, pa: Bias, of: Bias) -> tuple[str, str]:
    """(PASS|WARN|FAIL, motivazione) sulla convergenza PA/OF per la direzione dello scenario."""
    want = Bias(direction.value)
    opposite = Bias.SHORT if want == Bias.LONG else Bias.LONG
    biases = [pa, of]
    support = sum(b == want for b in biases)
    oppose = sum(b == opposite for b in biases)
    if support == 2:
        return "PASS", "PA e OF convergono nella direzione dello scenario"
    if oppose >= 1 and support >= 1:
        return "FAIL", "PA e OF divergono: nessuna risoluzione chiara"
    if oppose >= 1:
        return "FAIL", "scenario contro l'evidenza di almeno un analista senza supporto dell'altro"
    if support == 1:
        return "WARN", "solo un analista supporta lo scenario (l'altro neutrale o senza dati)"
    return "FAIL", "nessuna convergenza a supporto dello scenario"


def precheck(scenarios: list[Scenario], *, pa_bias: Bias, of_bias: Bias, data_quality: DataQuality,
             rcfg: RiskCfg, intermarket: dict | None = None) -> list[ScenarioPrecheck]:
    out: list[ScenarioPrecheck] = []
    exposure = 0.0
    for s in scenarios:
        reasons: list[str] = []
        gates = {g: "PASS" for g in GATES}
        verdicts = [RiskVerdict.APPROVED]

        if data_quality == DataQuality.RED:
            gates["data"] = "FAIL"
            verdicts.append(RiskVerdict.NO_TRADE)
            reasons.append("Qualità dati ROSSA: nessuno scenario può essere approvato")

        probs = geometry_problems(s)
        if probs:
            gates["structure"] = "FAIL"
            verdicts.append(RiskVerdict.NO_TRADE)
            reasons.extend(probs)
            reasons.append("Ingresso/invalidazione/target non definibili oggettivamente: NO TRADE")

        rr = computed_rr(s)
        if rr is not None and rr < rcfg.minimum_rr:
            gates["risk"] = "FAIL"
            verdicts.append(RiskVerdict.VETO)
            reasons.append(f"R:R {rr:.2f} inferiore al minimo {rcfg.minimum_rr:.2f}: VETO")
        elif rr is not None:
            reasons.append(f"R:R {rr:.2f} >= minimo {rcfg.minimum_rr:.2f}")
        exposure += rcfg.risk_per_scenario_r
        if exposure > rcfg.max_daily_r:
            gates["risk"] = "FAIL"
            verdicts.append(RiskVerdict.VETO)
            reasons.append(f"Esposizione cumulata {exposure:.1f}R oltre il massimo giornaliero {rcfg.max_daily_r:.1f}R")

        state, why = confluence(s.direction, pa_bias, of_bias)
        gates["confluence"] = state
        if state == "FAIL":
            verdicts.append(RiskVerdict.NO_TRADE)
            reasons.append(f"Convergenza: {why}")
        elif state == "WARN":
            verdicts.append(RiskVerdict.APPROVED_WITH_CAUTION)
            reasons.append(f"Convergenza parziale: {why}")
        im_state, im_why = scenario_gate(s.direction.value, intermarket)
        gates["intermarket"] = im_state
        if im_state == "FAIL":
            verdicts.append(RiskVerdict.NO_TRADE)
            reasons.append(f"Inter-mercato NQ/ES: {im_why}")
        elif im_state == "WARN":
            verdicts.append(RiskVerdict.APPROVED_WITH_CAUTION)
            reasons.append(f"Inter-mercato NQ/ES: {im_why}")
        else:
            reasons.append(f"Inter-mercato NQ/ES: {im_why}")
        if s.confidence < 40:
            verdicts.append(RiskVerdict.APPROVED_WITH_CAUTION)
            reasons.append(f"Confidenza dello scenario bassa ({s.confidence})")

        out.append(ScenarioPrecheck(s.id, worst_verdict(*verdicts), rr, reasons, gates))
    return out


def merge_assessments(pre: list[ScenarioPrecheck], llm: RiskOutput | None) -> list[RiskAssessment]:
    """Unisce precheck e Risk Manager LLM (piu' severo vince). LLM assente -> NOT_APPROVED."""
    by_id = {a.scenario_id: a for a in (llm.assessments if llm else [])}
    merged: list[RiskAssessment] = []
    for p in pre:
        reasons = list(p.reasons)
        if llm is None:
            merged.append(RiskAssessment(scenario_id=p.scenario_id, verdict=RiskVerdict.NOT_APPROVED,
                                         computed_rr=p.computed_rr,
                                         reasons=["Risk Manager non disponibile: scenario NON approvato"] + reasons))
            continue
        a = by_id.get(p.scenario_id)
        if a is None:
            # L'LLM non ha valutato lo scenario: non si presume l'approvazione.
            verdict = worst_verdict(p.verdict, RiskVerdict.APPROVED_WITH_CAUTION)
            reasons.append("Il Risk Manager non ha emesso un verdetto per questo scenario")
            merged.append(RiskAssessment(scenario_id=p.scenario_id, verdict=verdict,
                                         computed_rr=p.computed_rr, reasons=reasons))
            continue
        verdict = worst_verdict(p.verdict, a.verdict)
        if a.verdict != verdict:
            reasons.append(f"Verdetto LLM {a.verdict.value} irrigidito dal motore di rischio a {verdict.value}")
        merged.append(RiskAssessment(scenario_id=p.scenario_id, verdict=verdict, computed_rr=p.computed_rr,
                                     reasons=a.reasons + [r for r in reasons if r not in a.reasons],
                                     evidence_ids=a.evidence_ids))
    return merged
