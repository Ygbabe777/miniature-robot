"""Dual judge (edge / prop / final): the seven mandatory scenarios A-G, plus the rules that keep it severe."""
import copy
import math

import numpy as np
import pytest

from ofo import judges as JG
from ofo.judge import Thresholds, judge
from ofo.prop import PRESETS
from ofo.stats import deflated_sharpe

ANN = math.sqrt(252)
N_TRIALS, VAR = 100, 0.001


def series(annual_sharpe, days, seed=1, sd=300.0, exact=True):
    """Daily P&L with an EXACT sample Sharpe (so tests do not depend on luck)."""
    z = np.random.default_rng(seed).standard_normal(days)
    if exact:
        z = (z - z.mean()) / z.std(ddof=1)
    return sd * z + annual_sharpe / ANN * sd


def ruleset(compliance=True, first_payout=True, lcb=800.0, ucb=2500.0, stress=300.0, micro=3, p_pass=0.8, fp=0.6):
    gates = [{"id": 5, "name": "Conformità", "status": "PASS" if compliance else "FAIL", "reason": "ok" if compliance else "viola il drawdown trailing"},
             {"id": 6, "name": "Economia", "status": "PASS" if (first_payout and lcb > 0) else "FAIL", "reason": "x", "first_payout_gate": first_payout, "economics_gate": lcb > 0}]
    return {"evaluation": {"micro": micro, "policy": "A", "size": {"micro": micro}, "compliance": {"ok": compliance, "checks": {}},
                           "stress": {"costi_x2": {"npfv_mean": stress}},
                           "metrics": {"p_pass": p_pass, "p_first_payout": fp, "p_first_payout_given_pass": fp / max(p_pass, 1e-9), "p_funded_survival": 0.6,
                                       "p_fail_before_first_payout_given_funded": 0.3, "p_account_failure": 0.2, "drawdown_utilization": 0.5,
                                       "npfv": {"mean": (lcb + ucb) / 2, "lcb95": lcb, "ucb95": ucb}}},
            "gates": {"verdict": "APPROVED" if compliance and first_payout and lcb > 0 else "REJECTED", "gates": gates}}


def report(x, dev=None, **kw):
    """A stored study report, built from the OOS series `x` with the real DSR."""
    x = np.asarray(x, float)
    days = len(x)
    dsr = float(deflated_sharpe(x, N_TRIALS, VAR)) if np.all(np.isfinite(x)) and days > 3 else None
    farming = {"verdict": "APPROVED", "config": {"gates": {"min_p_first_payout": 0.15, "min_first_payout_given_pass": 0.5, "max_p_funded_fail_before_payout": 0.6,
                                                        "min_npfv_lcb95": 0.0, "stress_npfv_min": 0.0, "stress_costs_multiple": 2.0}},
               "by_ruleset": {"R50": ruleset(), "R25": ruleset(lcb=500)}}
    r = {"locked": {"status": "APPROVED", "checks": {"enough_days": True, "dsr": True}, "pnl": float(x.sum()), "dsr": {"value": dsr, "n_trials": N_TRIALS, "var_sr": VAR, "basis": "raw"},
                    "mc_sample": {"days": days, "n_trades": max(30, days), "days_waived": False, "sporadic": False}, "backtest_trades": {"n": 400, "min": 200, "ok": True}},
         "ledger_check": {"ok": True}, "repro": {"verified": True}, "surface": {"checks": {"median_sharpe": True, "plateau": True}, "robust": True},
         "quality": {"checks": {"min_trades": True, "cost_2x": True, "next_open_fill": True}, "n_trades": 400, "sharpe_2x_costs": 0.8, "sharpe_next_open": 0.9},
         "wf_oos_pnl": 5000.0, "wf_plan": {"target_met": True, "windows": 8, "target_windows": 6}, "farming": farming}
    for k, v in kw.items():
        r[k] = v
    return r


def run(r, x, dev=None, **kw):
    return JG.judge_report(r, oos=x, dev=dev, **kw)


STRONG = series(3.0, 250, seed=2)


# ------------------------------------------------------------------ G: passes everything -> APPROVED without arbitrary obstacles
def test_G_a_strategy_that_passes_every_gate_is_approved():
    j = run(report(STRONG), STRONG, dev=series(3.0, 400, seed=3))
    assert j["verdict"] == "APPROVED" and j["legacy_status"] == "APPROVED" and j["diagnosis"] is None and j["best_ruleset"] in ("R50", "R25")
    assert j["edge"]["state"] == "VALID" and not [g for g in j["gates"] if g["gate"].startswith("E") and g["status"] != "PASS"]


# ------------------------------------------------------------------ A: profitable but a thin sample -> INSUFFICIENT_EVIDENCE, never REJECTED
def test_A_profitable_with_an_insufficient_sample_is_insufficient_evidence():
    x = series(4.0, 40, seed=4)
    r = report(x); r["locked"]["mc_sample"] = {"days": 40, "n_trades": 12, "days_waived": False, "sporadic": True}
    j = run(r, x)
    assert x.sum() > 0 and j["verdict"] == JG.INSUFFICIENT_EVIDENCE and j["legacy_status"] == "INSUFFICIENT_EVIDENCE"
    d = j["diagnosis"]
    assert d["classification"] == "insufficient_sample" and d["recoverable"] and any(e["kind"] == "new_independent_observations" for e in d["next_experiments"])
    assert not any(e["needs_locked_block"] for e in d["next_experiments"])


# ------------------------------------------------------------------ B: credible edge, incompatible with the trailing drawdown
def test_B_credible_edge_incompatible_with_the_trailing_drawdown_is_not_archived_as_worthless():
    r = report(STRONG)
    for rid in r["farming"]["by_ruleset"]:
        r["farming"]["by_ruleset"][rid] = ruleset(compliance=False, lcb=-200, ucb=900)
    r["farming"]["verdict"] = "REJECTED"
    j = run(r, STRONG, dev=series(3.0, 400, seed=3))
    assert j["verdict"] == JG.EDGE_VALID_PROP_INCOMPATIBLE and j["edge"]["state"] == "VALID" and j["legacy_status"] == "REJECTED"
    d = j["diagnosis"]
    assert d["recoverable"] and d["classification"] in ("sizing_incompatible", "prop_incompatible") and "drawdown" in d["technical_reason"]
    assert {e["kind"] for e in d["next_experiments"]} & {"alternative_rulesets", "new_risk_config_on_training"}


# ------------------------------------------------------------------ C: a high Sharpe produced by overfitting is never approved
def test_C_a_sharpe_made_by_overfitting_is_not_approved():
    dev = series(3.5, 400, seed=5)
    x = series(-0.6, 150, seed=6)
    j = run(report(x), x, dev=dev)
    assert j["verdict"] == JG.STATISTICALLY_REJECTED and j["diagnosis"]["classification"] == "overfit" and not j["diagnosis"]["recoverable"]
    # a milder decay with a positive unseen block is still not approved
    x2 = series(0.4, 150, seed=7)
    assert run(report(x2), x2, dev=dev)["verdict"] != JG.APPROVED


# ------------------------------------------------------------------ D: high P(pass) but low P(first payout) is not approved for prop farming
def test_D_high_pass_probability_with_a_low_first_payout_probability_is_not_approved():
    r = report(STRONG)
    for rid in r["farming"]["by_ruleset"]:
        r["farming"]["by_ruleset"][rid] = ruleset(first_payout=False, p_pass=0.92, fp=0.05, lcb=-50, ucb=400)
    r["farming"]["verdict"] = "REJECTED"
    j = run(r, STRONG, dev=series(3.0, 400, seed=3))
    assert j["verdict"] == JG.EDGE_VALID_PROP_INCOMPATIBLE
    m = j["prop"]["by_ruleset"]["R50"]["metrics"]
    assert m["p_pass"] > 0.9 and m["p_first_payout"] < 0.1
    assert any(g["gate"] == "P2:R50" and g["status"] == "FAIL" for g in j["gates"])


# ------------------------------------------------------------------ E: positive expectancy but an interval too wide
def test_E_positive_expectancy_with_a_wide_interval_is_insufficient_evidence_not_rejection():
    x = series(0.9, 100, seed=8)
    j = run(report(x), x, dev=series(0.9, 400, seed=9))
    assert x.mean() > 0 and j["verdict"] == JG.INSUFFICIENT_EVIDENCE
    e2 = next(g for g in j["gates"] if g["gate"] == "E2")
    lo, hi = e2["interval"]
    assert lo < 0 < hi and e2["status"] == "INSUFFICIENT" and "né dimostrato né escluso" in e2["reason"]


# ------------------------------------------------------------------ F: accounting problems never become a statistical verdict
def test_F_accounting_errors_and_non_finite_series_are_execution_failures():
    j = JG.judge_report({"locked": {"status": "REJECTED"}, "accounting_error": "vettoriale e libro mastro non coincidono"})
    assert j["verdict"] == JG.EXECUTION_FAILED and j["diagnosis"]["classification"] == "execution_error" and j["diagnosis"]["recoverable"]
    x = STRONG.copy(); x[10] = float("nan")
    assert run(report(STRONG), x)["verdict"] == JG.EXECUTION_FAILED
    assert run(report(STRONG, ledger_check={"ok": False}), STRONG)["verdict"] == JG.EXECUTION_FAILED
    assert run(report(STRONG, repro={"verified": False}), STRONG)["verdict"] == JG.EXECUTION_FAILED
    assert judge([1.0, float("nan")] * 80, PRESETS["50k"], 10, VAR, Thresholds()).status == "EXECUTION_FAILED"
    assert judge([1.0, float("inf")] * 80, PRESETS["50k"], 10, VAR, Thresholds()).status == "EXECUTION_FAILED"


# ------------------------------------------------------------------ the rules that keep it severe
def test_a_failed_critical_gate_is_never_compensated_by_a_huge_score():
    r = report(STRONG)
    r["farming"]["by_ruleset"] = {"R50": ruleset(compliance=False, lcb=50_000, ucb=90_000)}
    assert run(r, STRONG, dev=series(3.0, 400, seed=3))["verdict"] != JG.APPROVED


def test_approved_is_never_easier_than_the_legacy_farming_review():
    r = report(STRONG); r["farming"]["verdict"] = "REJECTED"
    j = run(r, STRONG, dev=series(3.0, 400, seed=3))
    assert j["verdict"] == JG.INSUFFICIENT_EVIDENCE and "revisione prop-farming" in j["reason"]


def test_statistically_rejected_needs_evidence_against_not_just_the_lack_of_proof():
    x = series(-0.2, 60, seed=11)                                    # short, slightly negative: the data cannot exclude an edge
    r = report(x); r["locked"]["mc_sample"] = {"days": 60, "n_trades": 25, "days_waived": False, "sporadic": False}
    assert run(r, x)["verdict"] == JG.INSUFFICIENT_EVIDENCE
    x2 = series(-2.5, 200, seed=12)                                  # clearly negative on enough data
    assert run(report(x2), x2)["verdict"] == JG.STATISTICALLY_REJECTED


def test_a_plausible_edge_with_enough_sample_is_a_paper_candidate_not_an_approval():
    x = series(2.4, 150, seed=13)
    r = report(x)
    d = float(deflated_sharpe(x, N_TRIALS, VAR)); assert 0.70 <= d < 0.95
    j = run(r, x, dev=series(2.4, 400, seed=14))
    assert j["verdict"] == JG.PAPER_CANDIDATE and j["legacy_status"] == "INSUFFICIENT_EVIDENCE"
    assert any(e["kind"] == "paper_forward" for e in j["diagnosis"]["next_experiments"])


def test_fragility_to_costs_asks_for_robustness_with_the_right_experiment():
    r = report(STRONG)
    r["quality"]["checks"]["cost_2x"] = False; r["quality"]["sharpe_2x_costs"] = -0.3
    j = run(r, STRONG)
    assert j["verdict"] == JG.NEEDS_ROBUSTNESS and j["diagnosis"]["classification"] == "high_costs"
    assert j["diagnosis"]["next_experiments"][0]["kind"] == "execution_stress_test"
    r2 = report(STRONG); r2["surface"]["checks"]["plateau"] = False
    assert run(r2, STRONG)["diagnosis"]["next_experiments"][0]["kind"] == "plateau_analysis"


def test_development_only_failures_have_precise_verdicts():
    pre = {"locked": {"status": "REJECTED", "reason": "failed"}, "surface": {"checks": {"median_sharpe": False, "plateau": True}}, "wf_oos_pnl": -100.0, "wf_plan": {"target_met": True}}
    assert JG.judge_report(pre)["verdict"] == JG.STATISTICALLY_REJECTED
    fragile = {"locked": {"status": "REJECTED"}, "surface": {"checks": {"median_sharpe": True, "plateau": False}}, "wf_oos_pnl": 100.0, "wf_plan": {"target_met": True}}
    assert JG.judge_report(fragile)["verdict"] == JG.NEEDS_ROBUSTNESS
    few = {"locked": {"status": "REJECTED"}, "surface": {"checks": {"median_sharpe": True}}, "wf_oos_pnl": 100.0, "wf_plan": {"target_met": False, "windows": 3, "target_windows": 6}}
    assert JG.judge_report(few)["verdict"] == JG.INSUFFICIENT_EVIDENCE


def test_every_non_approved_verdict_carries_a_complete_diagnosis():
    x = series(0.9, 100, seed=8)
    d = run(report(x), x)["diagnosis"]
    for k in ("failed_gate", "observed", "threshold", "interval", "technical_reason", "classification", "recoverable", "next_experiments", "frozen"):
        assert k in d
    assert d["failed_gate"]["id"] and d["technical_reason"] and d["frozen"] and "Strategy rejected" not in d["technical_reason"]


def test_the_judgment_is_a_deterministic_pure_function():
    r = report(STRONG); before = copy.deepcopy(r)
    a, b = run(r, STRONG, dev=series(3, 400, seed=3)), run(r, STRONG, dev=series(3, 400, seed=3))
    assert a == b and r == before


def test_agents_never_see_numbers_measured_on_the_locked_block():
    x = series(0.9, 100, seed=8)
    v = JG.agent_view(run(report(x), x))
    e2 = next(g for g in v["gates"] if g["gate"] == "E2")
    assert e2["observed"] is None and e2["interval"] is None and v["diagnosis"]["observed"] is None
    assert all(e["needs_locked_block"] is False for e in v["diagnosis"]["next_experiments"])
    assert "Verdetto" in JG.brief(run(report(x), x))


def test_another_variant_on_an_already_used_block_cannot_be_approved():
    j = run(report(STRONG), STRONG, dev=series(3.0, 400, seed=3), lineage_looks=2)
    assert j["verdict"] != JG.APPROVED and any(g["gate"] == "E5" for g in j["gates"])


def test_power_arithmetic_matches_the_audit():
    assert JG.power_needed(90, 1, VAR, 0.95) == pytest.approx(2.77, abs=0.03)
    assert JG.power_needed(90, 100, VAR, 0.95) == pytest.approx(4.04, abs=0.03)


# ------------------------------------------------------------------ integration with the optimizer and the Lab
def test_a_real_study_carries_the_judgment_and_a_consistent_legacy_status(tmp_path):
    from ofo.backtest import MNQ
    from ofo.optimizer import run_study
    from ofo.registry import TrialRegistry
    from ofo.specs import from_dict
    from test_research import synth
    spec = {"name": "bo", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40, 60]}, "timeframes": ["15min", "30min"], "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"}
    rep = run_study(synth(n_sessions=300, drift_ar=0.0, seed=11), from_dict(spec), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.db"), dedupe=True)
    j, lk = rep["judgment"], rep["locked"]
    assert j["verdict"] in JG.VERDICTS and lk["verdict"] == j["verdict"] and lk["status"] == j["legacy_status"]
    if j["verdict"] != "APPROVED":
        assert j["diagnosis"]["failed_gate"] and j["diagnosis"]["technical_reason"] and j["diagnosis"]["next_experiments"] is not None


def test_an_accounting_error_inside_a_study_is_an_execution_failure_not_a_crashed_job(tmp_path, monkeypatch):
    from ofo import optimizer as O
    from ofo.backtest import MNQ
    from ofo.ledger import AccountingError
    from ofo.registry import TrialRegistry
    from ofo.specs import from_dict
    from test_research import synth

    def boom(*a, **k):
        raise AccountingError("vettoriale e libro mastro non coincidono")
    monkeypatch.setattr(O, "guarded_full_series", boom)
    spec = {"name": "bo2", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40, 60]}, "timeframes": ["15min", "30min"], "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"}
    reg = TrialRegistry(tmp_path / "r.db")
    # make the study reach the audit stage: every surface counts as robust
    real = O.surface_metrics
    def robust(*a, **k):
        o = real(*a, **k); o["robust"] = True; o["checks"] = {x: True for x in o["checks"]}; return o
    monkeypatch.setattr(O, "surface_metrics", robust)
    rep = O.run_study(synth(n_sessions=400, drift_ar=0.9, seed=5), from_dict(spec), MNQ, PRESETS["50k"], reg, dedupe=True)
    assert rep.get("accounting_error")                               # the study did reach the audit stage
    if True:
        assert rep["judgment"]["verdict"] == "EXECUTION_FAILED" and rep["locked"]["verdict"] == "EXECUTION_FAILED"
        assert reg.data["locked_looks"] == 0                          # the unseen block was never looked at


def test_rejudging_changes_no_out_of_sample_number_and_never_unapproves_silently(tmp_path):
    import json
    from ofo.app.lab import Lab
    from ofo.storage import load_study, save_study
    from test_research import synth
    bars = synth(n_sessions=120, seed=3)
    p = tmp_path / "NQ.csv.gz"; bars.to_csv(p, compression="gzip")
    lab = Lab(str(p), data_dir=str(tmp_path / "data"), ideas_dir=str(tmp_path / "ideas"))
    x = STRONG
    rep = report(x); rep.update(name="approved old", template="breakout", n_candidates=5, market="NQ")
    rep["surface"].update(median_sharpe=0.5, frac_positive=0.7, plateau_ratio=0.8, robust=True)
    rep["farming"]["by_ruleset"] = {"R50": ruleset(compliance=False, lcb=-5, ucb=10)}
    rep["farming"]["verdict"] = "REJECTED"
    ser = {"cut": "2025-01-01", "locked": [[f"2025-{i:05d}", float(v)] for i, v in enumerate(x)], "dev_best": [[f"2024-{i:05d}", float(v)] for i, v in enumerate(series(3, 300, seed=9))]}
    slug = save_study(lab.work, {**rep, "series": ser})
    before = json.dumps(load_study(lab.work, slug)["series"], sort_keys=True)
    out = lab.rejudge(slug)
    d = load_study(lab.work, slug)
    assert out["verdict"] == "EDGE_VALID_PROP_INCOMPATIBLE" and d["report"]["locked"]["verdict"] == out["verdict"]
    assert json.dumps(d["series"], sort_keys=True) == before and d["report"]["locked"]["pnl"] == pytest.approx(float(x.sum()))
    assert "conflict" in d["report"]["judgment"]                   # it was APPROVED with the old judge: flagged, not silently demoted
    assert d["report"]["locked"]["status"] == "APPROVED"
    v = lab.judgment(slug)
    assert v["deterministic"] and v["judgment"]["gates"] and lab.promote_to_paper(slug).get("error")      # not a PAPER_CANDIDATE: refused


def test_dsr_counts_the_looks_at_the_unseen_block_not_every_trial_ever_registered():
    from ofo import research_config as rc
    assert rc.get()["dsr_trials"] == "locked_looks"
    # the threshold itself did not move
    assert JG.DEFAULTS["confidence"] == 0.95 and rc.get()["judge"]["confidence"] == 0.95


def test_budget_explain_names_the_binding_constraint_in_trials_not_tokens(tmp_path):
    from ofo.app.lab import Lab
    from test_research import synth
    p = tmp_path / "NQ.csv.gz"; synth(n_sessions=60, seed=3).to_csv(p, compression="gzip")
    lab = Lab(str(p), data_dir=str(tmp_path / "data"), ideas_dir=str(tmp_path / "ideas"))
    e = lab.budget_explain()
    assert "non token" in e["unit"] and e["used"]["screening"] == 0 and e["caps"]["screening"] == 1000
    assert "budget" in lab.diag()


def test_yesterdays_budget_pause_is_not_restored_on_restart(tmp_path):
    import json
    from ofo.app.lab import Lab
    from test_research import synth
    p = tmp_path / "NQ.csv.gz"; synth(n_sessions=60, seed=3).to_csv(p, compression="gzip")
    d = tmp_path / "data"; d.mkdir()
    (d / "autopilot.json").write_text(json.dumps({"v": 2, "enabled": True, "paused": "day", "day": "2020-01-01"}), encoding="utf-8")
    lab = Lab(str(p), data_dir=str(d), ideas_dir=str(tmp_path / "ideas"))
    assert not lab.ap.get("paused") and lab.autopilot_state()["paused"] is None
    lab.ap["paused"] = "day"; lab._save_ap()
    assert "paused" not in json.loads((d / "autopilot.json").read_text(encoding="utf-8"))


def test_one_study_does_not_spend_the_whole_daily_screening_budget(tmp_path):
    """A 12-candidate study used to book ~1200 'screening' evaluations (informational time-window sweeps), pausing the autopilot after ONE test."""
    from ofo.backtest import MNQ
    from ofo.optimizer import run_study
    from ofo.registry import TrialRegistry
    from ofo.research_budget import ResearchBudget, planned_units
    from ofo.specs import from_dict
    from test_research import synth
    reg = TrialRegistry(tmp_path / "r.db")
    spec = from_dict({"name": "x", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40, 60, 80]}, "timeframes": ["5min", "15min"], "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"})
    rb = ResearchBudget(reg)
    run_study(synth(n_sessions=300, drift_ar=0.0, seed=11), spec, MNQ, PRESETS["50k"], reg, dedupe=True)
    plan = planned_units(spec, rb.cfg)
    assert rb.used()["screening"] <= plan["screening"] + 28 + 5            # candidates + the exit grid, nothing else
    assert rb.can_start(spec)[0]
    # databases written by earlier versions are repaired when opened
    reg._con.execute("UPDATE experiments SET stage='screening' WHERE label LIKE 'schedule_info:%'"); reg._con.commit()
    assert rb.used()["screening"] > 1000
    assert ResearchBudget(TrialRegistry(tmp_path / "r.db")).used()["screening"] <= plan["screening"] + 33


def test_by_default_no_daily_trial_cap_ever_pauses_the_lab_but_trials_are_still_counted(tmp_path):
    import json
    from ofo import research_config as rc
    from ofo.registry import TrialRegistry
    from ofo.research_budget import ResearchBudget
    from ofo.specs import from_dict
    assert rc.get()["budget"]["enforce_daily_caps"] is False
    reg = TrialRegistry(tmp_path / "r.db")
    spec = from_dict({"name": "x", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40]}, "timeframes": ["5min", "15min"], "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"})
    reg.add_trials(5000, "lots", kind="trials", stage="screening", family="E")
    rb = ResearchBudget(reg)
    assert rb.can_start(spec)[0] and reg.n_trials >= 5000                    # never blocked, always counted (the Deflated Sharpe still sees them)
    cfg = json.loads(json.dumps(rc.get())); cfg["budget"]["enforce_daily_caps"] = True
    assert not ResearchBudget(reg, cfg).can_start(spec)[0]                  # the old behaviour is one switch away
