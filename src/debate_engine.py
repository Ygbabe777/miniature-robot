"""Motore del dibattito: PA || OF -> Strategist -> Risk -> Judge -> decisione finale.

Ogni passaggio emette eventi reali sull'EventBus; la UI e' solo una visualizzazione di questi eventi.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from .agents import AgentResult, AgentRunner
from .config import AppConfig
from .council_decision import CouncilDecision, decide
from .council_events import EventType
from .event_bus import EventBus
from .grounding import deterministic_findings, penalty
from .risk_engine import ScenarioPrecheck, merge_assessments, precheck
from .schemas import (AgentMessage, AgentStatus, AnalystOutput, Bias, DataQuality, JudgeIssue,
                      JudgeOutput, MessageType, RiskAssessment, RiskOutput, RiskVerdict,
                      StrategistOutput)

TASKS = {
    "price_action": "Analisi di VWAP, volume profile, ATR e livelli di sessione",
    "options_flow": "Analisi di gamma flip, call wall e put wall",
    "strategist": "Sintesi di PA e OF in scenari condizionali (max 2)",
    "risk_manager": "Verifica R:R, esposizione e condizioni di no-trade",
    "judge": "Verifica di coerenza e ancoraggio ai dati dell'intera catena",
}


@dataclass
class DebateInputs:
    run_id: str
    date: str
    mode: str
    features: dict[str, Any]
    evidence: list[dict[str, Any]]
    data_quality: DataQuality
    warnings: list[str]


@dataclass
class DebateResult:
    results: dict[str, AgentResult] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    precheck: list[ScenarioPrecheck] = field(default_factory=list)
    assessments: list[RiskAssessment] = field(default_factory=list)
    judge_issues: list[JudgeIssue] = field(default_factory=list)
    judge_score: int | None = None
    decision: CouncilDecision | None = None
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def output(self, agent: str) -> Any:
        r = self.results.get(agent)
        return r.output if r and r.output is not None and r.status != AgentStatus.FAILED else None


StageCb = Callable[[str, str, int, str], None]


class DebateEngine:
    def __init__(self, cfg: AppConfig, runner: AgentRunner, bus: EventBus, clock: Any, *,
                 parallel: bool = True, on_stage: StageCb | None = None) -> None:
        self.cfg, self.runner, self.bus, self.clock = cfg, runner, bus, clock
        self.parallel = parallel
        self.on_stage = on_stage or (lambda *a: None)

    # ------------------------------------------------------------------ utilita'
    def _payload(self, inp: DebateInputs) -> dict[str, Any]:
        return {"run": {"run_id": inp.run_id, "date": inp.date, "instrument": "NQ", "mode": inp.mode},
                "data_quality": inp.data_quality.value, "warnings": inp.warnings,
                "features": inp.features, "evidence": inp.evidence}

    def _stage_done(self, res: DebateResult, name: str, res_obj: AgentResult | None, status: str,
                    detail: str = "") -> None:
        meta = res_obj.meta if res_obj else None
        res.stages[name] = {
            "status": status, "duration_ms": meta.duration_ms if meta else 0,
            "error": "; ".join(meta.errors) if meta and meta.errors else (detail or None),
            "output_validation": meta.validation_status if meta else "NOT_RUN",
        }
        self.on_stage(name, status, res.stages[name]["duration_ms"], detail)

    def _publish(self, agent: str, r: AgentResult, before_finish: Callable[[], None] | None = None) -> None:
        """Pubblica gli eventi reali di un agente terminato."""
        if r.output is None or r.status == AgentStatus.FAILED:
            self.bus.emit(EventType.AGENT_FAILED, agent=agent, source="system",
                          message="Agente non disponibile: " + ("; ".join(r.meta.errors) or "errore sconosciuto"),
                          ui_action="FAILED", data={"meta": _public_meta(r)})
            return
        out = r.output
        for m in out.messages:
            self._emit_message(agent, m)
        if before_finish:
            before_finish()
        bias = getattr(out, "bias", None)
        self.bus.emit(EventType.AGENT_FINISHED, agent=agent, source="system", ui_action="FINISHED",
                      data={"confidence": out.confidence, "status": out.status.value,
                            "bias": bias.value if isinstance(bias, Bias) else None,
                            "model": r.meta.actual_model, "requested_model": r.meta.requested_model,
                            "fallback_used": r.meta.fallback_used, "duration_ms": r.meta.duration_ms,
                            "warnings": out.warnings, "summary": out.reasoning_summary,
                            "key_evidence": _key_evidence(out)})

    def _emit_message(self, agent: str, m: AgentMessage) -> None:
        t = m.message_type
        etype = {MessageType.CHALLENGE: EventType.AGENT_CHALLENGE, MessageType.DISAGREEMENT: EventType.AGENT_CHALLENGE,
                 MessageType.AGREEMENT: EventType.AGENT_AGREEMENT}.get(t, EventType.AGENT_MESSAGE)
        target = m.addressed_to  # un agent id, oppure 'all'/'human'/None
        self.bus.emit(etype, agent=agent, target_agent=target, message=m.message, message_type=t,
                      evidence_ids=m.evidence_ids, source="agent", ui_action="SPEAK")

    # ------------------------------------------------------------------ orchestrazione
    def run(self, inp: DebateInputs) -> DebateResult:
        res = DebateResult()
        base = self._payload(inp)
        rules = {"minimum_rr": self.cfg.risk.minimum_rr, "max_daily_r": self.cfg.risk.max_daily_r,
                 "risk_per_scenario_r": self.cfg.risk.risk_per_scenario_r}
        options_available = bool(inp.features["options"]["available"])
        atr5 = inp.features["atr"]["atr_5m"]

        # ---------------- Round 1: analisti
        analysts = ("price_action", "options_flow")
        for a in analysts:
            self.bus.emit(EventType.AGENT_STARTED, agent=a, ui_action="MOVE_TO_DESK",
                          message=TASKS[a], data={"model": self.cfg.model_for(a), "task": TASKS[a]})
            self.bus.emit(EventType.AGENT_THINKING, agent=a, ui_action="THINK")
        if self.parallel:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {a: pool.submit(self.runner.run, a, base) for a in analysts}
                for a in analysts:  # pubblicazione in ordine fisso
                    res.results[a] = futures[a].result()
                    self._publish(a, res.results[a])
                    self._stage_done(res, a, res.results[a], _stage_status(res.results[a]))
        else:
            for a in analysts:
                res.results[a] = self.runner.run(a, base)
                self._publish(a, res.results[a])
                self._stage_done(res, a, res.results[a], _stage_status(res.results[a]))

        pa: AnalystOutput | None = res.output("price_action")
        of: AnalystOutput | None = res.output("options_flow")
        self._convergence_event(pa, of)

        # ---------------- Round 2: strategist
        self.bus.emit(EventType.ROUND_STARTED, ui_action="COUNCIL_ROUND", message="Round 2: sintesi dello Strategist",
                      data={"round": 2})
        strat: StrategistOutput | None = None
        if pa is None and of is None:
            res.skipped["strategist"] = "Price Action e Options Flow non disponibili"
            self._stage_done(res, "strategist", None, "SKIPPED", res.skipped["strategist"])
            self.bus.emit(EventType.AGENT_FAILED, agent="strategist", ui_action="FAILED",
                          message="Strategist non eseguito: nessun input analitico disponibile.")
        else:
            self.bus.emit(EventType.AGENT_STARTED, agent="strategist", ui_action="MOVE_TO_COUNCIL",
                          message=TASKS["strategist"], data={"model": self.cfg.model_for("strategist"), "task": TASKS["strategist"]})
            self.bus.emit(EventType.AGENT_THINKING, agent="strategist", ui_action="THINK")
            payload = {**base, "risk_rules": rules, "max_scenarios": self.cfg.debate.max_scenarios,
                       "upstream": {"price_action": pa.model_dump(mode="json") if pa else None,
                                    "options_flow": of.model_dump(mode="json") if of else None}}
            r = self.runner.run("strategist", payload)
            res.results["strategist"] = r
            strat = res.output("strategist")

            def announce_scenarios() -> None:
                for s in (strat.scenarios if strat else []):
                    self.bus.emit(EventType.SCENARIO_CREATED, agent="strategist", ui_action="SHOW_SCENARIO",
                                  source="agent", evidence_ids=s.evidence_ids,
                                  message=(f"{s.id} {s.direction.value} | entry {s.entry_zone[0]}-{s.entry_zone[1]} | "
                                           f"inv {s.invalidation} | T1 {s.target_1} | R:R {s.expected_rr}"),
                                  data={"scenario": s.model_dump(mode="json")})

            self._publish("strategist", r, announce_scenarios)
            self._stage_done(res, "strategist", r, _stage_status(r))

        # ---------------- Risk
        pa_b = pa.bias if pa else Bias.UNKNOWN
        of_b = of.bias if of and of.status == AgentStatus.OK else Bias.UNKNOWN
        risk_ran, risk_ok, risk_out = False, True, None
        if strat is not None and strat.scenarios:
            risk_ran = True
            res.precheck = precheck(strat.scenarios, pa_bias=pa_b, of_bias=of_b,
                                    data_quality=inp.data_quality, rcfg=self.cfg.risk)
            self.bus.emit(EventType.AGENT_STARTED, agent="risk_manager", ui_action="MOVE_TO_COUNCIL",
                          message=TASKS["risk_manager"], data={"model": self.cfg.model_for("risk_manager"), "task": TASKS["risk_manager"]})
            self.bus.emit(EventType.AGENT_THINKING, agent="risk_manager", ui_action="THINK")
            payload = {**base, "risk_rules": rules, "scenarios": [s.model_dump(mode="json") for s in strat.scenarios],
                       "risk_engine_precheck": [p.to_dict() for p in res.precheck],
                       "upstream": {"price_action": pa.model_dump(mode="json") if pa else None,
                                    "options_flow": of.model_dump(mode="json") if of else None}}
            r = self.runner.run("risk_manager", payload)
            res.results["risk_manager"] = r
            risk_out = res.output("risk_manager")
            risk_ok = risk_out is not None
            self._publish("risk_manager", r)
            res.assessments = merge_assessments(res.precheck, risk_out)
            by_pre = {p.scenario_id: p for p in res.precheck}
            for a in res.assessments:
                bad = a.verdict in (RiskVerdict.VETO, RiskVerdict.NO_TRADE, RiskVerdict.NOT_APPROVED)
                self.bus.emit(
                    EventType.RISK_VETO if a.verdict in (RiskVerdict.VETO, RiskVerdict.NOT_APPROVED) else EventType.RISK_VERDICT,
                    agent="risk_manager", target_agent="strategist", source="system",
                    message_type=MessageType.VETO if a.verdict == RiskVerdict.VETO else MessageType.DECISION,
                    ui_action="VETO" if a.verdict in (RiskVerdict.VETO, RiskVerdict.NOT_APPROVED) else ("WARN" if bad else "APPROVE"),
                    message=f"Scenario {a.scenario_id}: {a.verdict.value}" + (f" — {a.reasons[0]}" if a.reasons else ""),
                    evidence_ids=a.evidence_ids,
                    data={"scenario_id": a.scenario_id, "verdict": a.verdict.value, "computed_rr": a.computed_rr,
                          "gates": by_pre[a.scenario_id].gates if a.scenario_id in by_pre else {}})
            self._stage_done(res, "risk_manager", r, _stage_status(r))
        else:
            res.skipped["risk_manager"] = "Nessuno scenario da valutare" if strat is not None else "Strategist non disponibile"
            self._stage_done(res, "risk_manager", None, "SKIPPED", res.skipped["risk_manager"])
            self.bus.emit(EventType.AGENT_FINISHED, agent="risk_manager", ui_action="SKIPPED",
                          message=res.skipped["risk_manager"], data={"status": "SKIPPED"})

        # ---------------- Judge
        judge_out: JudgeOutput | None = None
        judge_ran = False
        issues: list[JudgeIssue] = []
        if pa is None and of is None and strat is None:
            res.skipped["judge"] = "Nessun output da valutare"
            self._stage_done(res, "judge", None, "SKIPPED", res.skipped["judge"])
        else:
            judge_ran = True
            self.bus.emit(EventType.JUDGE_STARTED, agent="judge", ui_action="MOVE_TO_CENTER",
                          message=TASKS["judge"], data={"model": self.cfg.model_for("judge"), "task": TASKS["judge"]})
            self.bus.emit(EventType.AGENT_THINKING, agent="judge", ui_action="THINK")
            det = deterministic_findings(evidence=inp.evidence, pa=pa, of=of, strategist=strat,
                                         assessments=res.assessments, options_available=options_available,
                                         atr5=atr5, gcfg=self.cfg.grounding, minimum_rr=self.cfg.risk.minimum_rr)
            payload = {**base,
                       "chain": {"price_action": pa.model_dump(mode="json") if pa else None,
                                 "options_flow": of.model_dump(mode="json") if of else None,
                                 "strategist": strat.model_dump(mode="json") if strat else None},
                       "risk_verdicts": [a.model_dump(mode="json") for a in res.assessments],
                       "deterministic_findings": [i.model_dump(mode="json") for i in det]}
            r = self.runner.run("judge", payload)
            res.results["judge"] = r
            judge_out = res.output("judge")
            self._publish("judge", r)
            issues = list(det)
            if judge_out is not None:
                seen = {(i.type, i.description) for i in issues}
                issues += [i for i in judge_out.issues if (i.type, i.description) not in seen]
                res.judge_score = min(judge_out.score, 100 - penalty(det))
            res.judge_issues = issues
            self.bus.emit(EventType.JUDGE_FINISHED, agent="judge", ui_action="JUDGE_DONE", source="system",
                          message=(f"Punteggio {res.judge_score}/100" if judge_out else "Giudice non disponibile"),
                          data={"score": res.judge_score, "verdict": judge_out.verdict.value if judge_out else None,
                                "issues": len(issues)})
            self._stage_done(res, "judge", r, _stage_status(r))

        # ---------------- Decisione finale (gerarchica)
        res.decision = decide(
            data_quality=inp.data_quality, pa=pa, of=of, strategist=strat, risk_ran=risk_ran, risk_ok=risk_ok,
            assessments=res.assessments, judge=judge_out, judge_ran=judge_ran, issues=issues,
            judge_score=res.judge_score, judge_min_score=self.cfg.debate.judge_min_score,
            options_available=options_available)
        self.bus.emit(EventType.FINAL_DECISION, ui_action="FINAL", source="system",
                      message=f"Decisione del Consiglio: {res.decision.status.value}",
                      data={"status": res.decision.status.value, "reasons": res.decision.reasons,
                            "selected_scenario_id": res.decision.selected_scenario_id,
                            "scenario_verdicts": res.decision.scenario_verdicts})
        return res

    def _convergence_event(self, pa: AnalystOutput | None, of: AnalystOutput | None) -> None:
        """Evento di sistema deterministico sulla convergenza PA/OF."""
        if pa is None or of is None:
            return
        pb = pa.bias
        ob = of.bias if of.status == AgentStatus.OK else Bias.UNKNOWN
        directional = (Bias.LONG, Bias.SHORT)
        if pb in directional and pb == ob:
            self.bus.emit(EventType.AGENT_AGREEMENT, agent="price_action", target_agent="options_flow",
                          message_type=MessageType.AGREEMENT, ui_action="SYSTEM_CHECK",
                          message=f"Convergenza verificata dal sistema: PA e OF indicano entrambi {pb.value}.")
        elif pb in directional and ob in directional:
            self.bus.emit(EventType.AGENT_CHALLENGE, agent="price_action", target_agent="options_flow",
                          message_type=MessageType.DISAGREEMENT, ui_action="SYSTEM_CHECK",
                          message=f"Divergenza rilevata dal sistema: PA {pb.value} vs OF {ob.value}.")
        elif ob == Bias.UNKNOWN:
            self.bus.emit(EventType.AGENT_MESSAGE, agent="price_action", target_agent="options_flow",
                          message_type=MessageType.WARNING, ui_action="SYSTEM_CHECK",
                          message="Nessuna conferma dalle opzioni: Options Flow privo di dati utilizzabili.")
        else:
            self.bus.emit(EventType.AGENT_MESSAGE, agent="price_action", target_agent="options_flow",
                          message_type=MessageType.ANALYSIS, ui_action="SYSTEM_CHECK",
                          message=f"Nessuna convergenza direzionale (PA {pb.value}, OF {ob.value}).")


def _stage_status(r: AgentResult) -> str:
    return "FAILED" if r.output is None or r.status == AgentStatus.FAILED else r.status.value


def _key_evidence(out: Any) -> list[str]:
    ids: list[str] = []
    for coll in (getattr(out, "facts", []), getattr(out, "key_levels", []), getattr(out, "interpretations", [])):
        for x in coll:
            for e in x.evidence_ids:
                if e not in ids:
                    ids.append(e)
    return ids[:8]


def _public_meta(r: AgentResult) -> dict[str, Any]:
    m = r.meta
    return {"requested_model": m.requested_model, "actual_model": m.actual_model, "retry_count": m.retry_count,
            "failure_reason": m.failure_reason, "errors": m.errors}
