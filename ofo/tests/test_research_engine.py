import dataclasses
import json

import numpy as np
import pandas as pd
import pytest

from cards import CARD
from ofo import research_config as rc
from ofo.backtest import MNQ
from ofo.optimizer import (SurfaceThresholds, _sweep, plateau_pick, run_study, spec_fingerprint, surface_metrics, wf_plan, effective_trials)
from ofo.prop import PRESETS
from ofo.registry import DuplicateExperiment, TrialRegistry
from ofo.research_budget import ResearchBudget, planned_units
from ofo.search import axes_of, plan_search, plateau_scores, refine_points, spec_overlap
from ofo.specs import SpecError, from_dict
from test_research import synth

CFG = rc.get()
SPEC = {"name": "bo", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40, 60]}, "timeframes": ["15min", "30min"],
        "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"}


def test_config_is_versioned_validated_and_the_baseline_is_the_one_requested():
    assert CFG["timeframes"]["execution"] == ["1min", "5min"] and CFG["timeframes"]["context"] == ["15min"]
    caps = rc.stage_caps(CFG)
    assert caps["screening"] == 1000 and caps["deep_candidates"] == 100 and abs(sum(CFG["budget"]["stage_split"].values()) - 1) < 1e-9
    assert CFG["walk_forward"]["min_windows"] == 6 and CFG["sample"]["min_oos_trades"] == 200 and 0.15 <= CFG["holdout_fraction"] <= 0.20
    assert "+" in CFG["config_version"] and "total_trials" not in CFG["budget"]            # the total budget is NOT here: it stays as it was
    bad = json.loads(json.dumps(CFG)); bad["search_space"]["orb"]["params"]["or_minutes"]["range"] = [1, 9999]
    with pytest.raises(rc.ResearchConfigError):
        rc.validate(bad)
    assert {"D", "GEX/options"} <= {f for f, v in CFG["families"].items() if v["status"] == "blocked_no_data"}


# ---- acceptance 1: the budget never exceeds its limit without an explicit decision ----
def test_1_daily_caps_hold_and_only_an_explicit_decision_raises_them(tmp_path):
    reg = TrialRegistry(tmp_path / "r.db")
    cfg = json.loads(json.dumps(CFG)); cfg["budget"]["screen_per_day"] = 100
    rb = ResearchBudget(reg, cfg)
    spec = from_dict({**SPEC, "ranges": {"lookback": [10, 20, 30, 40]}})                   # 4 x 2 timeframes = 8 candidates
    assert rb.can_start(spec)[0]
    reg.add_trials(95, "earlier", kind="trials", stage="screening", family="E")           # 95 of 100 screening units already used today
    ok, why, detail = rb.can_start(spec)
    assert not ok and why == "screening" and detail["used"]["screening"] == 95
    assert rb.allow_overage(10) == 10 and rb.can_start(spec)[0]                           # an explicit, logged decision
    assert reg.counter("overage_log") == 1


# ---- acceptance 2: every extra test a study generates is counted and attributed to its stage ----
def test_2_all_generated_tests_are_counted_by_stage_and_family(tmp_path):
    reg = TrialRegistry(tmp_path / "r.db")
    bars = synth(n_sessions=520, drift_ar=0.9, seed=5)
    rep = run_study(bars, from_dict(SPEC), MNQ, PRESETS["50k"], reg, dedupe=True)
    u = reg.usage(group="stage")
    assert u["screening"]["trials"] == rep["trials"]["executed"]
    assert "validation" in rep["locked"].get("reason", "") or rep.get("validated") is not None or rep["locked"]["status"]
    if rep.get("validated") is not None:                                                  # the study got past the screening gate
        assert u["validation"]["trials"] >= 28                                            # exit grid + direction variants, counted
        assert any(r["kind"] == "exits" and r["n_trials"] == 28 for r in reg.experiments(50))
    if rep.get("surface_detrended"):
        assert u["robustness"]["evals"] >= rep["n_candidates"] and u["robustness"]["trials"] == 0     # re-runs cost compute, they are not new trials
    kinds = reg.usage(group="kind")
    assert reg.usage(group="family")["E"]["trials"] >= rep["trials"]["executed"]
    assert reg.n_trials == sum(v["trials"] for v in kinds.values()) + reg.data["locked_looks"]   # the budget and the DSR count are one number
    assert [s["stage"] for s in rep["stages"]][:2] == ["hypothesis", "screening"]


# ---- acceptance 3: duplicates are not run again ----
def test_3_exact_and_near_duplicates_are_not_rerun(tmp_path):
    from ofo.app.lab import Lab
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=40, seed=2).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    a = from_dict({**SPEC, "name": "A", "ranges": {"lookback": [10, 20, 30, 40, 60]}})
    assert lab._add_pending_all(a, "paper") == 1
    assert lab._add_pending_all(from_dict({**SPEC, "name": "A again"}), "paper") == 0                       # exact: same template/ranges/timeframes
    near = from_dict({**SPEC, "name": "A cosmetic", "ranges": {"lookback": [10, 20, 30, 40, 50]}})          # 4 of 6 values shared = 0.67: not near
    assert lab._add_pending_all(near, "paper") == 1
    very_near = from_dict({**SPEC, "name": "A tweak", "ranges": {"lookback": [10, 20, 30, 40, 60, 70]}})    # 5 of 6 shared = 0.83 >= 0.8
    assert lab._add_pending_all(very_near, "paper") == 0
    assert lab.registry.counter("duplicates_exact") == 1 and lab.registry.counter("duplicates_near") == 1
    reg = TrialRegistry(tmp_path / "x.db")
    bars = synth(n_sessions=200, seed=4)
    run_study(bars, from_dict(SPEC), MNQ, PRESETS["50k"], reg, dedupe=True)
    with pytest.raises(DuplicateExperiment):
        run_study(bars, from_dict(SPEC), MNQ, PRESETS["50k"], reg, dedupe=True)
    assert spec_overlap({"a": [1, 2, 3]}, {"a": [1, 2, 3, 4]}) == 0.75 and spec_overlap({"a": [1]}, {"b": [1]}) == 0.0


# ---- acceptance 4: failed and interrupted work survives a restart and is not silently 'already tested' ----
def test_4_a_crashed_study_keeps_its_trials_survives_restart_and_can_be_rerun(tmp_path):
    reg = TrialRegistry(tmp_path / "r.db")
    bars = synth(n_sessions=200, seed=4)
    spec = from_dict(SPEC)
    import ofo.optimizer as opt
    real = opt.surface_metrics
    opt.surface_metrics = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        with pytest.raises(RuntimeError):
            run_study(bars, spec, MNQ, PRESETS["50k"], reg, dedupe=True)
    finally:
        opt.surface_metrics = real
    n_after_crash = reg.n_trials
    row = reg.experiments(1)[0]
    assert row["status"] == "failed" and "boom" in row["reject_reason"] and n_after_crash >= 1           # counted, labelled, not lost
    reopened = TrialRegistry(tmp_path / "r.db")
    assert reopened.n_trials == n_after_crash and reopened.experiments(1)[0]["status"] == "failed"
    rep = run_study(bars, spec, MNQ, PRESETS["50k"], reopened, dedupe=True)                                  # NOT blocked as 'already tested'
    assert rep["locked"]["status"] and reopened.n_trials > n_after_crash                                     # and the retry is counted again
    # a process that died mid-study leaves 'running': recover_stale makes it failed
    eid = reopened.begin_experiment("study:dead", 5, spec_fp="zz", dataset_fp="yy", dedupe=True)
    assert reopened.recover_stale() == 1 and reopened.experiment(eid)["status"] == "failed"


def test_4b_the_research_queue_and_the_study_running_at_shutdown_come_back(tmp_path):
    from ofo.app.lab import Lab
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=40, seed=2).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    lab._add_pending(from_dict({**SPEC, "name": "queued one", "hypothesis": CARD}), "brainstorm")
    lab._running_specs["j1"] = {"spec": from_dict({**SPEC, "name": "was running"}), "origin": "paper", "market": lab.primary}
    lab._persist_queue()
    lab2 = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    names = sorted(v["spec"].name for v in lab2.pending.values())
    assert names == ["queued one", "was running"] and next(v for v in lab2.pending.values() if v["spec"].name == "queued one")["spec"].hypothesis == CARD


# ---- acceptance 5: great Sharpe with fragile parameters is penalised / rejected ----
def test_5_an_isolated_spike_loses_to_a_stable_region_and_fails_the_plateau_gate():
    axes = {"lookback": [10, 20, 30, 40, 50, 60]}
    rows = [{"lookback": v, "tf": "15min", "sharpe": s} for v, s in zip(axes["lookback"], [-0.05, 0.9, -0.1, 0.12, 0.14, 0.13])]   # spike at 20, region at 40-60
    cands = [{"tf": "15min", "params": {"lookback": r["lookback"]}} for r in rows]
    pick = plateau_pick(cands, rows, axes, list(range(len(rows))))
    assert rows[pick]["lookback"] in (40, 50, 60) and rows[pick]["sharpe"] < 0.9              # the region beats the peak
    spike = surface_metrics(rows, ["lookback"], pick=1, axes=axes)
    assert spike["plateau_ratio"] < 0.6 and not spike["checks"]["plateau"] and not spike["robust"]
    region = surface_metrics(rows, ["lookback"], pick=pick, axes=axes)
    assert region["plateau_ratio"] > spike["plateau_ratio"]


def test_staged_search_stays_within_the_cap_and_refines_around_neighbourhoods_not_peaks():
    spec = from_dict({"name": "x", "template": "trend_pullback", "rationale": "r", "timeframes": ["5min"],
                      "ranges": {"ema_fast": {"min": 8, "max": 34, "step": 5}, "ema_slow": {"min": 40, "max": 150, "step": 20},
                                 "pullback_z": {"min": 0.5, "max": 2.0, "step": 0.25}, "ctx": {"values": [0, 1]}}})
    assert len(spec.combos()) > spec.planned_configs() == 72 and spec.n_candidates == 72
    plan = plan_search(spec, 72, CFG["search"])
    assert plan["mode"] == "staged" and len(plan["initial"]) + plan["refine_budget"] == 72 and len({json.dumps(c, sort_keys=True) for c in plan["initial"]}) == len(plan["initial"])
    axes = axes_of(spec)
    valid = {tuple(axes[n].index(c[n]) for n in axes) for c in spec.combos()}
    sh = {("5min", (1, 1, 1, 0)): 0.9, ("5min", (1, 1, 2, 0)): 0.8, ("5min", (1, 2, 1, 0)): 0.85, ("5min", (4, 3, 4, 1)): 1.5}   # a region and a lonely peak
    pts = refine_points(sh, axes, valid, top=1, budget=10)
    region = [(1, 1, 1, 0), (1, 1, 2, 0), (1, 2, 1, 0)]
    one_step = lambda a, b: sum(x != y for x, y in zip(a, b)) == 1
    assert pts and all(k[0] == "5min" for k in pts) and all(any(one_step(k[1], r) for r in region) for k in pts)
    assert not any(one_step(k[1], (4, 3, 4, 1)) for k in pts)                       # nothing is refined around the lonely peak
    small = from_dict(SPEC)
    assert plan_search(small, 48, CFG["search"])["mode"] == "grid"                         # a grid that fits the cap is tested whole


def test_early_stop_only_when_even_the_best_candidate_is_clearly_negative_and_releases_unexecuted_trials(tmp_path):
    bars = synth(n_sessions=200, seed=4)
    spec = from_dict({"name": "lots", "template": "breakout", "rationale": "r", "timeframes": ["15min"],
                      "ranges": {"lookback": {"min": 10, "max": 120, "step": 5}}, "max_configs": 23})
    key = pd.to_datetime(bars["session"]); days = sorted(key.unique())
    cands, series, log = _sweep(bars, spec, MNQ, 2, 1.0, CFG, days)
    assert log["executed"] == len(cands) <= 23 and (not log["early_stopped"] or log["rule"])
    # never stops on a noisy early estimate: with a strongly positive candidate the rule cannot fire
    from ofo.search import sharpe_upper_bound
    assert sharpe_upper_bound(0.2, 100) > 0 and sharpe_upper_bound(-0.5, 400) < 0 and sharpe_upper_bound(-0.05, 60) > 0


# ---- acceptance 6: an extreme result does not decide the allocation ----
def test_6_one_extreme_result_barely_moves_the_budget_and_unexplored_families_keep_priority(tmp_path):
    reg = TrialRegistry(tmp_path / "r.db")
    rb = ResearchBudget(reg, CFG)
    lucky = reg.begin_experiment("study:lucky", 100, spec_fp="a", dataset_fp="d", dedupe=True, family="C")
    reg.finish_experiment(lucky, "completed", results={"survived_screening": True, "validated": True, "locked_status": "APPROVED", "approved": True}, compute_s=5)
    st = rb.family_stats()["C"]
    assert 0.05 < st["rate"] < 0.12                                    # one study moved a 5% prior to under 10%, not to 100%
    mk = lambda fam, name: {"id": name, "spec": from_dict({**SPEC, "name": name, "template": {"C": "sweep_reclaim", "F": "overnight_gap", "A": "orb"}[fam],
                                                           "ranges": {"C": {"sweep_atr": [0.2], "reclaim_bars": [3], "level_set": [2], "ctx": [0]},
                                                                      "F": {"gap_atr": [0.5], "mode": [0], "exit_min": [120]}, "A": {"or_minutes": [30]}}[fam]})}
    order = [x["id"] for x in rb.rank([mk("C", "c1"), mk("F", "f1"), mk("A", "a1")])]
    assert order[0] != "c1"                                            # the family with the lucky result is not served first
    for _ in range(40):                                                # and a family that never survives keeps a small, non-zero rate
        e = reg.begin_experiment("study:dud", 10, spec_fp=f"dud{_}", dataset_fp="d", dedupe=True, family="F"); reg.finish_experiment(e, "completed", results={"survived_screening": False}, compute_s=5)
    assert rb.family_stats()["F"]["rate"] < rb.family_stats()["C"]["rate"] and rb.family_stats()["F"]["rate"] > 0


# ---- acceptance 7: the optimizer cannot reach the locked block ----
def test_7_the_sweep_and_validation_only_ever_receive_development_bars(tmp_path, monkeypatch):
    import ofo.optimizer as opt
    seen = []
    real_sweep = opt._sweep
    monkeypatch.setattr(opt, "_sweep", lambda b, *a, **k: (seen.append(b.index.max()), real_sweep(b, *a, **k))[1])
    bars = synth(n_sessions=300, drift_ar=0.9, seed=5)
    rep = run_study(bars, from_dict(SPEC), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.db"))
    cut = pd.Timestamp(rep["series"]["cut"])
    assert seen and all(pd.Timestamp(ts).normalize() < cut + pd.Timedelta(days=1) and pd.Timestamp(ts) < cut + pd.Timedelta(hours=18) for ts in seen)
    assert len(bars[pd.to_datetime(bars["session"]) >= cut]) > 0                       # a locked block really exists
    assert abs(rep["n_locked_days"] / 300 - CFG["holdout_fraction"]) < 0.02


# ---- acceptance 8: a new cost model is a new, traceable experiment ----
def test_8_a_new_cost_model_or_execution_rule_creates_a_new_experiment(tmp_path):
    from ofo import repro
    spec = from_dict(SPEC)
    a = spec_fingerprint(spec, MNQ, 2, 1.0, 0.18, 120, 20, repro.cost_model_descriptor(MNQ, 2, 1.0, "1"))
    b = spec_fingerprint(spec, MNQ, 2, 1.0, 0.18, 120, 20, repro.cost_model_descriptor(MNQ, 2, 1.0, "2"))
    c = spec_fingerprint(spec, MNQ, 2, 2.0, 0.18, 120, 20, repro.cost_model_descriptor(MNQ, 2, 2.0, "1"))
    assert len({a, b, c}) == 3
    reg = TrialRegistry(tmp_path / "r.db")
    e1 = reg.begin_experiment("s", 10, spec_fp=a, dataset_fp="d", dedupe=True, cost_model="m1", config_version="1.0+x", code_version="c1")
    e2 = reg.begin_experiment("s", 10, spec_fp=b, dataset_fp="d", dedupe=True, cost_model="m2", config_version="1.0+x", code_version="c1")
    assert e1 != e2 and reg.experiment(e1)["cost_model"] == "m1" and reg.experiment(e2)["cost_model"] == "m2"        # history never overwritten
    with pytest.raises(DuplicateExperiment):
        reg.begin_experiment("s", 10, spec_fp=a, dataset_fp="d", dedupe=True)


# ---- acceptance 9: results can be reproduced from the saved metadata ----
def test_9_a_result_is_reproduced_from_its_saved_metadata_alone(tmp_path):
    from ofo import repro
    bars = synth(n_sessions=520, drift_ar=0.9, seed=5)
    rep = run_study(bars, from_dict(SPEC), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.db"))
    assert "repro" in rep, rep["locked"]                                                   # the study reached the audit stage
    r = json.loads(json.dumps(rep["repro"], default=str))                                  # what is saved on disk
    assert r["verified"] and r["code_version"] and r["config_version"] and r["dataset_fp"] and r["cost_model"] and r["seeds"]["search"] is not None
    key = pd.to_datetime(bars["session"]); days = sorted(key.unique()); cut = pd.Timestamp(rep["series"]["cut"])
    assert repro.candidate_digest(bars, r["meta"], [d for d in days if d < cut]) == r["digest_dev"]
    tampered = {**r["meta"], "params": {**r["meta"]["params"], "lookback": r["meta"]["params"]["lookback"] + 7}}
    assert repro.candidate_digest(bars, tampered, [d for d in days if d < cut]) != r["digest_dev"]    # missing metadata would be caught


# ---- acceptance 10: a day with zero valid candidates ends without lowering any standard ----
def test_10_a_day_with_zero_valid_candidates_changes_no_threshold(tmp_path):
    from ofo.app.lab import Lab
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=200, seed=4).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    before = (dataclasses.asdict(lab.judge_thresholds()), dataclasses.asdict(SurfaceThresholds()), json.dumps(lab.rbudget.cfg["budget"], sort_keys=True))
    for name in ("one", "two"):
        sid = lab._add_pending(from_dict({**SPEC, "name": name, "ranges": {"lookback": [10, 20, 30, 40, 60 if name == "one" else 80]}}), "paper")["id"]
        job = lab.start(sid)
        for _ in range(240):
            if lab.jobs[job["id"]]["status"] in ("done", "error"):
                break
            import time; time.sleep(0.5)
        assert lab.jobs[job["id"]]["status"] == "done"
    rr = lab.research_report()
    assert rr["today"]["approved"] == 0 and rr["today"]["studies"] == 2 and rr["today"]["thresholds_changed"] is False
    after = (dataclasses.asdict(lab.judge_thresholds()), dataclasses.asdict(SurfaceThresholds()), json.dumps(lab.rbudget.cfg["budget"], sort_keys=True))
    assert before == after and rr["approved"] == 0
    assert rr["stage_flow"]["screening"]["entered"] == 2 and rr["mean_study_s"] is not None


def test_walk_forward_plan_targets_six_windows_and_says_when_it_cannot():
    assert wf_plan(246, CFG, None, None)["target_met"]
    p = wf_plan(200, CFG, None, None)
    assert p["windows"] >= 6 and p["target_met"] and p["test_days"] < 20                 # shorter test windows to reach six
    short = wf_plan(90, CFG, None, None)
    assert not short["target_met"] and short["windows"] < 6                               # a documented limit, not hidden
    assert wf_plan(246, CFG, 60, 10)["explicit"] is True


def test_hypothesis_card_is_required_for_proposals_and_complete_cards_are_kept():
    with pytest.raises(SpecError):
        from_dict(SPEC, require_card=True)
    ok = from_dict({**SPEC, "hypothesis": CARD}, require_card=True)
    assert ok.hypothesis["criterio_rigetto"] and ok.family == "E"
    with pytest.raises(SpecError):
        from_dict({**SPEC, "hypothesis": {**CARD, "segnale": "x"}}, require_card=True)


def test_blocked_families_have_no_templates_and_effective_trials_are_reported():
    for f in ("D", "GEX/options"):
        assert CFG["families"][f]["templates"] == []
    rng = np.random.default_rng(0)
    base = rng.normal(size=120)
    s = {i: pd.Series(base + 0.05 * rng.normal(size=120), index=range(120)) for i in range(10)}      # ten near-identical candidates
    assert effective_trials(s, list(range(120))) < 3
    ind = {i: pd.Series(rng.normal(size=120), index=range(120)) for i in range(10)}
    assert effective_trials(ind, list(range(120))) > 6


def test_the_whole_chain_paper_to_queue_works_with_a_model_that_omits_the_card(tmp_path):
    """file in ideas/ -> Researcher (LLM) -> proposal -> near-duplicate / budget checks -> pending queue, with an incomplete card."""
    from ofo.app.lab import Lab
    from test_knowledge import Fake
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=40, seed=2).to_csv(csv)
    prop = {"name": "ORB 15-45", "template": "orb", "ranges": {"or_minutes": {"min": 15, "max": 45, "step": 5}}, "timeframes": ["5min", "15min"],
            "rationale": "MECCANISMO ... EVIDENZA ... QUANDO NON FUNZIONA ..."}
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"), llm={"researcher": Fake({"strategies": [prop], "concepts": ["opening range"]})})
    (lab.ideas / "p.txt").write_text("Opening range breakout after the cash open tends to continue. " * 30)
    assert lab.researcher_status()["unread"] == 1
    lab._ingest_now()
    names = [v["spec"].name for v in lab.pending.values()]
    assert names == ["ORB 15-45"] and next(iter(lab.pending.values()))["spec"].hypothesis_missing
    st = lab.researcher_status()
    assert st["unread"] == 0 and st["recent"][-1]["proposals"] == 1 and st["queue"] == 1
