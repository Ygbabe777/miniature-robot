"""Test di risk engine, grounding, decisione gerarchica e debate engine (via pipeline mock)."""
import pytest

from src.config import RiskCfg
from src.council_decision import decide
from src.grounding import deterministic_findings
from src.risk_engine import computed_rr, geometry_problems, merge_assessments, precheck
from src.schemas import (AnalystOutput, Bias, DataQuality, FinalStatus, JudgeIssue, RiskAssessment,
                         RiskOutput, RiskVerdict, Scenario, StrategistOutput)
from src.config import GroundingCfg
from src.pipeline import run_pipeline
from tests.conftest import opts

R = RiskCfg()


def sc(**kw):
    base = dict(id="S1", direction="LONG", setup_type="x", entry_zone=[100, 102], invalidation=96,
                target_1=112, target_2=120, expected_rr=2.0, thesis="t", failure_condition="f",
                confidence=70, evidence_ids=["E001"])
    base.update(kw)
    return Scenario(**base)


def test_rr_uses_worst_case_entry():
    assert computed_rr(sc()) == pytest.approx(10 / 6, abs=0.01)  # (112-102)/(102-96)
    assert computed_rr(sc(direction="SHORT", entry_zone=[100, 102], invalidation=106, target_1=90, target_2=None)) == pytest.approx(10 / 6, abs=0.01)


def test_geometry_problems_detected():
    assert geometry_problems(sc(invalidation=101))
    assert geometry_problems(sc(target_1=101))
    assert geometry_problems(sc(target_2=105, target_1=110))
    assert computed_rr(sc(invalidation=101)) is None


def pre(s, pa=Bias.LONG, of=Bias.LONG, dq=DataQuality.GREEN):
    return precheck([s], pa_bias=pa, of_bias=of, data_quality=dq, rcfg=R)[0]


def test_low_rr_is_veto():
    p = pre(sc(target_1=105))
    assert p.verdict == RiskVerdict.VETO and p.gates["risk"] == "FAIL"


def test_exactly_min_rr_passes():
    # (109-102)/(102-97.3333) -> scegliamo numeri esatti: entry 100, inv 90, t1 115 => 1.5
    s = sc(entry_zone=[99, 100], invalidation=90, target_1=115)
    assert computed_rr(s) == 1.5 and pre(s).verdict == RiskVerdict.APPROVED


def test_invalid_geometry_is_no_trade():
    assert pre(sc(invalidation=105)).verdict == RiskVerdict.NO_TRADE


def test_divergence_is_no_trade_and_partial_is_caution():
    assert pre(sc(), pa=Bias.LONG, of=Bias.SHORT).verdict == RiskVerdict.NO_TRADE
    assert pre(sc(), pa=Bias.LONG, of=Bias.UNKNOWN).verdict == RiskVerdict.APPROVED_WITH_CAUTION
    assert pre(sc(), pa=Bias.NEUTRAL, of=Bias.NEUTRAL).verdict == RiskVerdict.NO_TRADE


def test_red_data_never_approved():
    assert pre(sc(), dq=DataQuality.RED).verdict == RiskVerdict.NO_TRADE


def test_daily_exposure_limit():
    scs = [sc(id=f"S{i}") for i in range(4)]
    out = precheck(scs, pa_bias=Bias.LONG, of_bias=Bias.LONG, data_quality=DataQuality.GREEN,
                   rcfg=RiskCfg(max_daily_r=3.0, risk_per_scenario_r=1.0))
    assert [o.verdict for o in out][:3] == [RiskVerdict.APPROVED] * 3 and out[3].verdict == RiskVerdict.VETO


def _risk_out(verdict):
    return RiskOutput(agent="risk_manager", confidence=70,
                      assessments=[RiskAssessment(scenario_id="S1", verdict=verdict)])


def test_llm_cannot_override_engine_veto():
    p = [pre(sc(target_1=105))]
    m = merge_assessments(p, _risk_out(RiskVerdict.APPROVED))
    assert m[0].verdict == RiskVerdict.VETO


def test_llm_can_be_stricter_and_missing_llm_not_approved():
    p = [pre(sc())]
    assert merge_assessments(p, _risk_out(RiskVerdict.VETO))[0].verdict == RiskVerdict.VETO
    assert merge_assessments(p, None)[0].verdict == RiskVerdict.NOT_APPROVED


def _decide(**kw):
    pa = AnalystOutput(agent="price_action", confidence=70, bias="LONG")
    of = AnalystOutput(agent="options_flow", confidence=70, bias="LONG")
    st = StrategistOutput(agent="strategist", confidence=70, scenarios=[sc()])
    base = dict(data_quality=DataQuality.GREEN, pa=pa, of=of, strategist=st, risk_ran=True, risk_ok=True,
                assessments=[RiskAssessment(scenario_id="S1", verdict=RiskVerdict.APPROVED)], judge=None,
                judge_ran=False, issues=[], judge_score=90, judge_min_score=60, options_available=True)
    base.update(kw)
    return decide(**base)


def test_decide_approved_and_data_error_priority():
    assert _decide().status == FinalStatus.APPROVED_SETUP
    assert _decide(data_quality=DataQuality.RED).status == FinalStatus.DATA_ERROR


def test_decide_veto_precedence_and_options_cap():
    d = _decide(assessments=[RiskAssessment(scenario_id="S1", verdict=RiskVerdict.VETO)])
    assert d.status == FinalStatus.NO_TRADE
    assert _decide(options_available=False).status == FinalStatus.APPROVED_WITH_CAUTION


def test_decide_failures_never_look_like_success():
    assert _decide(pa=None).status == FinalStatus.REVIEW_REQUIRED
    assert _decide(strategist=None).status == FinalStatus.REVIEW_REQUIRED
    assert _decide(risk_ok=False).status == FinalStatus.REVIEW_REQUIRED


def test_decide_critical_issue_rejects_scenario():
    issue = JudgeIssue(type="unsupported_claim", severity="critical", description="x", scenario_id="S1")
    assert _decide(issues=[issue]).status == FinalStatus.NO_TRADE
    major = JudgeIssue(type="fabricated_level", severity="major", description="x", scenario_id="S1")
    assert _decide(issues=[major]).status == FinalStatus.APPROVED_WITH_CAUTION


EV = [{"id": "E001", "key": "spot", "kind": "price", "value": 100.0},
      {"id": "E002", "key": "a", "kind": "price", "value": 90.0},
      {"id": "E003", "key": "b", "kind": "price", "value": 120.0}]


def test_grounding_flags_fabricated_level_and_unknown_evidence_and_arithmetic():
    st = StrategistOutput(agent="strategist", confidence=50, scenarios=[
        sc(entry_zone=[100, 101], invalidation=90, target_1=135, target_2=None, expected_rr=5.0, evidence_ids=["E001", "E999"])])
    issues = deterministic_findings(evidence=EV, pa=None, of=None, strategist=st, assessments=[],
                                    options_available=True, atr5=2.0, gcfg=GroundingCfg(), minimum_rr=1.5)
    types = {i.type.value for i in issues}
    assert {"unsupported_claim", "fabricated_level", "arithmetic_error"} <= types
    assert any(i.severity.value == "critical" for i in issues)


def test_grounding_detects_options_claim_without_data():
    of = AnalystOutput(agent="options_flow", confidence=60, bias="LONG")
    issues = deterministic_findings(evidence=EV, pa=None, of=of, strategist=None, assessments=[],
                                    options_available=False, atr5=2.0, gcfg=GroundingCfg(), minimum_rr=1.5)
    assert any(i.type.value == "missing_data" for i in issues)


def test_grounding_fact_without_evidence_flagged():
    pa = AnalystOutput(agent="price_action", confidence=60, bias="LONG", facts=[{"text": "dato inventato"}],
                       key_levels=[{"name": "x", "value": 55.5, "evidence_ids": ["E001"]}])
    issues = deterministic_findings(evidence=EV, pa=pa, of=None, strategist=None, assessments=[],
                                    options_available=True, atr5=2.0, gcfg=GroundingCfg(), minimum_rr=1.5)
    assert {"unsupported_claim", "fabricated_level"} <= {i.type.value for i in issues}


# ------------------------------------------------------------------ debate engine end-to-end sui guasti
def run(project, scenario):
    return run_pipeline(opts(project, scenario, write_files=False))


def verdicts(res):
    return res.report["decision"]["scenario_verdicts"]


def test_risk_veto_scenario_blocks_everything(project):
    res = run(project, "risk_veto")
    assert set(verdicts(res).values()) == {"VETO"} and res.status == FinalStatus.NO_TRADE


def test_risk_veto_partial_primary_vetoed_secondary_independent(project):
    res = run(project, "risk_veto_partial")
    assert verdicts(res)["S1"] == "VETO"
    assert any(e["event_type"] == "RISK_VETO" for e in res.report["events"])


def test_strategist_disagreement_gives_no_trade(project):
    res = run(project, "strategist_disagreement")
    assert res.status == FinalStatus.NO_TRADE
    assert res.report["agents"]["risk_manager"]["status"] == "SKIPPED"
    ev = res.report["events"]
    assert any(e["event_type"] == "AGENT_CHALLENGE" for e in ev)


def test_options_missing_degrades_to_caution(project_no_options):
    res = run(project_no_options, "ok")
    of = res.report["agents"]["options_flow"]
    assert of["status"] == "INSUFFICIENT_DATA" and of["output"]["bias"] == "UNKNOWN"
    assert res.status in (FinalStatus.APPROVED_WITH_CAUTION, FinalStatus.NO_TRADE)  # mai APPROVED_SETUP
    assert res.report["data_quality"]["status"] == "YELLOW"
    assert not any(i["type"] == "missing_data" for i in res.report["judge"]["issues"])  # OF e' stato onesto


@pytest.mark.parametrize("scenario,failed", [("price_action_down", "price_action"), ("options_flow_down", "options_flow"),
                                             ("strategist_down", "strategist"), ("risk_down", "risk_manager"),
                                             ("judge_down", "judge")])
def test_any_agent_failure_requires_review(project, scenario, failed):
    res = run(project, scenario)
    assert res.report["agents"][failed]["status"] == "FAILED"
    assert res.status == FinalStatus.REVIEW_REQUIRED
    assert any(e["event_type"] == "AGENT_FAILED" for e in res.report["events"])


def test_risk_failure_makes_all_scenarios_not_approved(project):
    res = run(project, "risk_down")
    assert set(verdicts(res).values()) == {"NOT_APPROVED"}


def test_strategist_failure_produces_no_scenarios(project):
    res = run(project, "strategist_down")
    assert res.report["agents"]["risk_manager"]["status"] == "SKIPPED" and verdicts(res) == {}


def test_judge_failure_does_not_look_successful(project):
    res = run(project, "judge_down")
    assert res.report["judge"]["score"] is None and res.status == FinalStatus.REVIEW_REQUIRED


def test_judge_rejection_downgrades(project):
    res = run(project, "judge_rejection")
    assert res.status == FinalStatus.REVIEW_REQUIRED and res.report["judge"]["score"] == 25


def test_hallucinated_level_and_evidence_are_caught(project):
    res = run(project, "hallucinated_level")
    assert any(i["type"] == "fabricated_level" for i in res.report["judge"]["issues"])
    assert res.status == FinalStatus.APPROVED_WITH_CAUTION
    res = run(project, "unsupported_evidence")
    assert res.status == FinalStatus.NO_TRADE
    assert any(i["type"] == "unsupported_claim" and i["severity"] == "critical" for i in res.report["judge"]["issues"])


def test_arithmetic_error_downgrades(project):
    res = run(project, "wrong_rr")
    assert any(i["type"] == "arithmetic_error" for i in res.report["judge"]["issues"])
    assert res.status == FinalStatus.APPROVED_WITH_CAUTION


def test_red_data_skips_agents_and_reports_data_error(project):
    (project.path("data_dir") / "levels.json").write_text('{"date": "2026-10-08", "spot": 0}')
    res = run(project, "ok")
    assert res.status == FinalStatus.DATA_ERROR and res.report["data_quality"]["status"] == "RED"
    assert all(a["status"] == "SKIPPED" for a in res.report["agents"].values()) or not res.report["agents"]
    assert res.report["panic_proof"]["best_setup"].startswith("NESSUNO")


def test_parallel_analysts_gives_same_result(project):
    from src.pipeline import PipelineOptions
    import random
    from src.clock import FakeClock
    from src.mock_llm import MockLLMClient
    o = PipelineOptions(date="2026-10-08", mock=True, cfg=project, clock=FakeClock(), rng=random.Random(7),
                        run_id="TEST-RUN", write_files=False, client=MockLLMClient("ok"))
    # forza l'esecuzione parallela con client mock thread-safe per sola lettura
    project.debate.parallel_analysts = True
    from src.debate_engine import DebateEngine  # noqa: F401
    res = run_pipeline(o)
    assert res.status == FinalStatus.APPROVED_SETUP
