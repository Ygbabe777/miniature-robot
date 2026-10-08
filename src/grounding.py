"""Controlli di ancoraggio deterministici usati dal Giudice (nessun nuovo fatto di mercato)."""
from __future__ import annotations

from typing import Any

from .config import GroundingCfg
from .risk_engine import computed_rr, geometry_problems
from .schemas import (AgentStatus, AnalystOutput, Bias, IssueType, JudgeIssue, RiskAssessment,
                      RiskVerdict, Scenario, Severity, StrategistOutput)

PENALTY = {Severity.MINOR: 3, Severity.MAJOR: 10, Severity.CRITICAL: 25}


def tolerance(atr5: float | None, gcfg: GroundingCfg) -> float:
    return max(gcfg.tolerance_points_min, gcfg.tolerance_atr_mult * (atr5 or 0.0))


def _near_any(value: float, levels: list[float], tol: float) -> bool:
    return any(abs(value - lv) <= tol for lv in levels)


def evidence_price_values(evidence: list[dict[str, Any]]) -> list[float]:
    vals: list[float] = []
    for e in evidence:
        if e["kind"] == "price":
            vals.extend(float(v) for v in (e["value"] if isinstance(e["value"], list) else [e["value"]]))
    return vals


def deterministic_findings(
    *, evidence: list[dict[str, Any]], pa: AnalystOutput | None, of: AnalystOutput | None,
    strategist: StrategistOutput | None, assessments: list[RiskAssessment], options_available: bool,
    atr5: float | None, gcfg: GroundingCfg, minimum_rr: float,
) -> list[JudgeIssue]:
    ids = {e["id"] for e in evidence}
    price_vals = evidence_price_values(evidence)
    tol = tolerance(atr5, gcfg)
    issues: list[JudgeIssue] = []

    def check_ids(agent: str, where: str, evid: list[str], severity: Severity,
                  scenario_id: str | None = None) -> None:
        bad = [i for i in evid if i not in ids]
        if bad:
            issues.append(JudgeIssue(type=IssueType.UNSUPPORTED_CLAIM, severity=severity, target_agent=agent,
                                     scenario_id=scenario_id, evidence_ids=bad,
                                     description=f"{where}: evidenze inesistenti {', '.join(bad)}"))

    for agent_id, out in (("price_action", pa), ("options_flow", of)):
        if out is None or out.status == AgentStatus.FAILED:
            continue
        for f in out.facts:
            if not f.evidence_ids:
                issues.append(JudgeIssue(type=IssueType.UNSUPPORTED_CLAIM, severity=Severity.MAJOR,
                                         target_agent=agent_id,
                                         description=f"Fatto senza evidenza: «{f.text[:80]}»"))
            check_ids(agent_id, "fatto", f.evidence_ids, Severity.MAJOR)
        for it in out.interpretations:
            check_ids(agent_id, "interpretazione", it.evidence_ids, Severity.MINOR)
        for kl in out.key_levels:
            check_ids(agent_id, f"livello {kl.name}", kl.evidence_ids, Severity.MAJOR)
            if not _near_any(kl.value, price_vals, 0.26):
                issues.append(JudgeIssue(type=IssueType.FABRICATED_LEVEL, severity=Severity.MAJOR,
                                         target_agent=agent_id, evidence_ids=kl.evidence_ids,
                                         description=f"Livello «{kl.name}» = {kl.value} non presente nelle evidenze"))
    if of is not None and of.status == AgentStatus.OK and not options_available:
        issues.append(JudgeIssue(type=IssueType.MISSING_DATA, severity=Severity.CRITICAL,
                                 target_agent="options_flow",
                                 description="Options Flow riporta analisi OK ma i dati opzioni non sono disponibili"))

    scen: list[Scenario] = strategist.scenarios if strategist else []
    for s in scen:
        check_ids("strategist", f"scenario {s.id}", s.evidence_ids, Severity.CRITICAL, s.id)
        for label, val in (("invalidazione", s.invalidation), ("target_1", s.target_1),
                           ("target_2", s.target_2)):
            if val is not None and not _near_any(val, price_vals, tol):
                issues.append(JudgeIssue(
                    type=IssueType.FABRICATED_LEVEL, severity=Severity.MAJOR, target_agent="strategist",
                    scenario_id=s.id, description=(f"Scenario {s.id}: {label} {val} non vicino (±{tol:.1f} pt) "
                                                   f"ad alcun livello presente nei dati")))
        for p in geometry_problems(s):
            issues.append(JudgeIssue(type=IssueType.CONTRADICTORY_CLAIM, severity=Severity.CRITICAL,
                                     target_agent="strategist", scenario_id=s.id,
                                     description=f"Scenario {s.id}: {p}"))
        rr = computed_rr(s)
        if rr is not None and abs(rr - s.expected_rr) > gcfg.rr_tolerance:
            issues.append(JudgeIssue(
                type=IssueType.ARITHMETIC_ERROR, severity=Severity.MAJOR, target_agent="strategist",
                scenario_id=s.id,
                description=f"Scenario {s.id}: expected_rr {s.expected_rr} ≠ R:R calcolato {rr}"))
    for a in assessments:
        if a.verdict == RiskVerdict.APPROVED and a.computed_rr is not None and a.computed_rr < minimum_rr:
            issues.append(JudgeIssue(type=IssueType.CONTRADICTORY_CLAIM, severity=Severity.CRITICAL,
                                     target_agent="risk_manager", scenario_id=a.scenario_id,
                                     description="Scenario approvato con R:R sotto il minimo"))
    if (pa and of and pa.bias in (Bias.LONG, Bias.SHORT) and of.bias in (Bias.LONG, Bias.SHORT)
            and pa.bias != of.bias):
        issues.append(JudgeIssue(type=IssueType.CONTRADICTORY_CLAIM, severity=Severity.MINOR,
                                 target_agent="strategist",
                                 description=f"Bias opposti: PA {pa.bias.value} vs OF {of.bias.value}"))
    return issues


def penalty(issues: list[JudgeIssue]) -> int:
    return sum(PENALTY[i.severity] for i in issues)
