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


def test_a_running_study_reports_its_stage_and_the_optimizer_is_the_one_working_while_it_optimises(tmp_path):
    """'A test is running but the optimizer is idle': the Lab now says which stage a study is in, who waits for whom, and keeps
    the optimizer lit while it chooses timeframe/direction/exits."""
    from ofo.app.lab import Lab
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=520, drift_ar=0.9, seed=5).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    seen, agents = [], []
    sid = lab._add_pending(from_dict(SPEC), "paper")["id"]
    job = lab.start(sid)
    import time
    for _ in range(600):
        j = lab.jobs[job["id"]]
        seen.append((j["status"], j.get("stage"))); agents.append(frozenset(lab.working_agents()))
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert lab.jobs[job["id"]]["status"] == "done"
    stages = [s for st, s in seen if st == "running" and s]
    assert "screening" in stages                                                          # the UI can show the stage
    assert any("optimizer" in a for a in agents)                                           # and the optimizer is shown working
    assert not any(a == frozenset() for st, a in zip(seen, agents) if st[0] == "running")  # never a running study with nobody working
    ev = [(e["agent"], e["type"]) for e in lab.events.tail(400)]
    if ("validator", "working") in ev:
        assert ev.index(("optimizer", "working")) < ev.index(("validator", "working"))


def _lab_with_researcher(tmp_path, reply):
    from ofo.app.lab import Lab
    from test_knowledge import Fake
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=40, seed=2).to_csv(csv)
    return Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"), llm={"researcher": Fake(reply)})


def test_the_researcher_develops_hypotheses_on_its_own_family_by_family_even_while_the_scout_searches(tmp_path):
    """Reported: 'the researcher brings no hypotheses and no tests to develop'. Phase 1 is now a duty of its own."""
    prop = {"name": "Sweep dei minimi", "template": "sweep_reclaim", "timeframes": ["5min"], "hypothesis": CARD,
            "ranges": {"sweep_atr": [0.2, 0.3], "reclaim_bars": [2, 3], "level_set": [2], "ctx": [0]},
            "rationale": "MECCANISMO ... EVIDENZA ... QUANDO NON FUNZIONA ..."}
    lab = _lab_with_researcher(tmp_path, {"strategies": [prop], "concepts": ["liquidity sweep"]})
    lab.busy["research"] = 1                                                       # the Scout is in the middle of a long search...
    st = lab.hypothesis_status(now=10_000.0)
    assert st["ready"] and st["queued"] == 0
    assert lab.hypothesize(now=10_000.0, sync=True) == 1                           # ...and the Researcher still develops a hypothesis
    v = next(iter(lab.pending.values()))
    assert v["origin"] == "hypothesis" and v["spec"].template == "sweep_reclaim" and v["spec"].hypothesis["previsione_falsificabile"]
    assert lab.hypothesis_status(now=10_001.0)["why"].startswith("prossima richiesta")              # at most one request every min_gap_min
    assert any(e["type"] == "proposal" and "💡" in e["message"] for e in lab.events.tail())
    assert lab.registry.usage(group="stage")["hypothesis"]["evals"] >= 1                           # phase-1 consumption is accounted
    brief = lab.hypothesis_brief("C")
    assert "sweep_reclaim" in brief and "Plausible ranges" in brief and "FAMILY C" in brief
    assert lab.hypothesis_status(now=10_000.0 + 3600)["queued"] == 1                               # one test is waiting, below the low-water mark: it may ask again


def test_hypothesis_generation_waits_for_a_low_queue_respects_pauses_and_rotates_families(tmp_path):
    lab = _lab_with_researcher(tmp_path, {"strategies": []})
    for i in range(4):
        lab._add_pending(from_dict({**SPEC, "name": f"q{i}", "ranges": {"lookback": [10 + i]}}), "paper")
    assert lab.hypothesis_status(now=50_000.0)["why"].startswith("coda sufficiente")              # enough tests already waiting
    lab.pending.clear()
    first = lab.pick_family()
    lab._hyp_last = {first: 1.0}
    assert lab.pick_family() != first or len([f for f, v in rc.get()["families"].items() if v["templates"]]) == 1   # round-robin on equal evidence
    assert lab.pick_family() in {"A", "B", "C", "E", "F", "G"}                                    # blocked families (D, GEX) are never asked
    from ofo.app.lab import NoLLM
    lab.llm = NoLLM()
    assert lab.hypothesis_status(now=50_000.0)["why"] == "nessuna AI configurata"


def test_an_ai_failure_while_developing_hypotheses_is_reported_not_swallowed(tmp_path):
    from ofo.app.lab import Lab
    from ofo.llm.client import LLMError
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=40, seed=2).to_csv(csv)

    class Down:
        def chat(self, *a, **k):
            raise LLMError("key refused")
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"), llm={"researcher": Down()})
    assert lab.hypothesize(now=1000.0, sync=True) == 0
    log = lab.researcher_status()["hypothesis"]["log"][-1]
    assert "key refused" in log["error"] and any(e["type"] == "error" and "AI non utilizzabile" in e["message"] for e in lab.events.tail())


def test_two_studies_run_one_after_the_other_and_the_waiting_one_says_who_it_waits_for(tmp_path):
    import time
    from ofo.app.lab import Lab
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=300, drift_ar=0.9, seed=5).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    a = lab.start(lab._add_pending(from_dict({**SPEC, "name": "first"}), "paper")["id"])
    for _ in range(100):                                                       # wait until the first one holds the engine
        if lab.engine_state().get("kind") == "study":
            break
        time.sleep(0.1)
    b = lab.start(lab._add_pending(from_dict({**SPEC, "name": "second", "ranges": {"lookback": [15, 25, 35]}}), "paper")["id"])
    time.sleep(0.3)
    eng = lab.engine_state()
    assert lab.jobs[b["id"]]["status"] == "queued" and eng["kind"] == "study" and eng["name"] == "first"      # it says WHO it waits for
    for _ in range(900):
        if all(lab.jobs[j["id"]]["status"] in ("done", "error") for j in (a, b)):
            break
        time.sleep(0.3)
    assert [lab.jobs[j["id"]]["status"] for j in (a, b)] == ["done", "done"] and lab.engine_state()["kind"] is None


def test_the_engine_tests_strategies_while_the_researcher_is_busy_researching(tmp_path):
    """Reported: the Researcher kept 'working' and Optimizer/Validator/Judge were never called. Researching must not stop testing."""
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    lab.ap["enabled"] = True
    lab.busy["research"] = 1                                                      # the Scout/Researcher is in the middle of a long search
    started = []
    lab.start = lambda sid: started.append(sid) or {"id": "x"}
    assert not lab.pending
    assert lab.autopilot_tick() is True and len(started) == 1                      # starters are seeded and the first one is handed to the engine
    d = lab.researcher_doing()                                                     # and what the Researcher does is visible, not a black box
    assert "reading" in d["now"] and isinstance(d["events"], list)


def test_the_researcher_rereads_the_obsidian_notes_before_proposing_but_never_quotes_verdicts(tmp_path):
    """The vault is the agents' brain: the owner's words steer the hypotheses; strategy notes (verdicts, locked results) stay out."""
    from ofo import obsidian
    vault = tmp_path / "vault"; vault.mkdir()
    root = obsidian.ensure_vault(vault)
    (root / "Idee" / "Mie.md").write_text("Penso che dopo uno sweep dei minimi con volume alto il rientro sia piu affidabile.", encoding="utf-8")
    (root / "Strategie").mkdir(exist_ok=True)
    (root / "Strategie" / "ORB x.md").write_text("---\ntags: [ofo]\n---\n# ORB x\nVerdetto: APPROVED OOS sharpe 9.99\n" + obsidian.END + "\n## Le mie note\nmi piace la regola del range stretto", encoding="utf-8")
    prop = {"name": "Sweep dal vault", "template": "sweep_reclaim", "timeframes": ["5min"], "hypothesis": CARD,
            "ranges": {"sweep_atr": [0.2, 0.3], "reclaim_bars": [2, 3], "level_set": [2], "ctx": [0]},
            "rationale": "MECCANISMO ... EVIDENZA ... QUANDO NON FUNZIONA ..."}
    lab = _lab_with_researcher(tmp_path, {"strategies": [prop], "concepts": []})
    lab.obs["vault"] = str(vault)
    text, n = lab.vault_brief()
    assert n >= 2 and "sweep dei minimi con volume alto" in text and "range stretto" in text      # the owner's words, in the brain
    assert "APPROVED" not in text and "9.99" not in text                                           # no verdicts / locked results leak into the prompt
    assert lab.hypothesize(now=10_000.0, sync=True) == 1
    sent = "\n".join(m["content"] for msgs in lab.llm["researcher"].msgs for m in msgs)
    assert "OBSIDIAN MEMORY" in sent and "sweep dei minimi con volume alto" in sent and "9.99" not in sent
    assert any("Obsidian: rileggo" in e["message"] for e in lab.events.tail())


def test_only_extremely_important_things_are_said_out_loud_and_not_repeated(tmp_path):
    from ofo.events import EventLog
    ev = EventLog(tmp_path / "e.jsonl")
    assert ev.alert("researcher", "ai_down", "AI non raggiungibile") is True
    assert ev.alert("researcher", "ai_down", "AI non raggiungibile") is False                       # same problem: not shouted again within the cooldown
    assert ev.alert("judge", "approved:x", "approvata", 0.0) and ev.alert("judge", "approved:x", "approvata", 0.0)   # each approval is announced
    assert [e["type"] for e in ev.tail()].count("alert") == 3


def test_loading_the_big_price_file_does_not_freeze_the_job_start_or_the_state(tmp_path, monkeypatch):
    """Reported: 'in attesa che il motore sia libero (sta per partire)' for ever. The first load of the 1-minute file held the lab-wide lock,
    so the job thread could not even register as running (and /api/state hung) until the file was read."""
    import threading, time
    import pandas as pd
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    ctx = lab.ctx(None); ctx.reset(); ctx._bars = None
    real = pd.read_csv

    def slow(*a, **k):
        time.sleep(2.0)
        return real(*a, **k)
    monkeypatch.setattr(pd, "read_csv", slow)
    threading.Thread(target=ctx.bars, daemon=True).start()
    time.sleep(0.3)                                                        # the slow load is in progress
    t0 = time.time()
    with lab.working("job"):                                               # what _run_job does first
        pass
    lab.state()                                                            # what the page polls every second
    assert time.time() - t0 < 1.0


def test_a_damaged_event_line_does_not_break_the_feed_and_threads_do_not_interleave(tmp_path):
    """Reported: GET /api/events?after=0 answered 500 for ever (one unreadable line in the log took the whole feed down)."""
    import threading
    from ofo.events import EventLog
    ev = EventLog(tmp_path / "e.jsonl")
    ts = [threading.Thread(target=lambda k=k: [ev.emit("lab", "x", "m" * 3000 + str(k)) for _ in range(40)]) for k in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(ev.read()) == 240                                                   # every line whole
    with ev.path.open("a", encoding="utf-8") as f:
        f.write('{"t": "x", "agent": "lab", "typ\n')                                # a half-written line
    ev.emit("lab", "after", "still readable")
    got = ev.read()
    assert len(got) == 241 and got[-1]["type"] == "after"


def test_the_research_panel_reads_only_keys_the_server_really_sends(tmp_path):
    import re
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    rep = lab.research_report()
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "src/ofo/app/static/ui.js").read_text(encoding="utf-8")
    body = js[js.index("async function loadResearch"):js.index("const rcfgStages")]
    missing = sorted(k for k in set(re.findall(r"\bd\.([a-z_]+)", body)) if k != "error" and k not in rep)
    assert not missing, missing


def test_starter_strategies_in_the_queue_do_not_stop_the_researcher_from_proposing(tmp_path):
    """Reported: 'the researcher never proposes anything'. ~20 classic starters keep the queue above the low-water mark for hours."""
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    assert lab.seed_specs() > 4                                                    # the classic families fill the queue
    st = lab.hypothesis_status(now=10_000.0)
    assert st["ready"] and st["queued"] == 0                                       # only the Researcher's OWN proposals count


def test_a_browser_that_disconnects_mid_answer_makes_no_noise_and_a_real_failure_prints_its_cause(tmp_path, capsys):
    import threading, urllib.request, urllib.error, socket
    from ofo.app.server import make_server
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    lab.state = lambda: (_ for _ in ()).throw(RuntimeError("boom-from-state"))
    srv = make_server(lab, port=8794); threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = socket.create_connection(("127.0.0.1", 8794)); s.sendall(b"GET /api/state HTTP/1.1\r\nHost: x\r\nX-OFO: 1\r\n\r\n"); s.close()   # hangs up at once
        r = urllib.request.Request("http://127.0.0.1:8794/api/state", headers={"X-OFO": "1"})
        try:
            urllib.request.urlopen(r, timeout=10)
        except urllib.error.HTTPError as e:
            assert e.code == 500
    finally:
        srv.shutdown()
    out = capsys.readouterr()
    assert "boom-from-state" in out.out and "Traceback" in out.out                  # the real cause is printed
    assert "ConnectionAbortedError" not in out.err and "BrokenPipe" not in out.err   # the disconnect is silent


def test_a_test_stuck_in_the_queue_with_a_free_engine_says_where_it_is_blocked(tmp_path):
    """Reported for days: 'waiting for the engine (about to start)'. A silent wait is now a visible diagnosis."""
    import threading, time
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    lab.STUCK_AFTER_S = 0.5
    release, orig = threading.Event(), lab.working

    def blocked_registration(kind):                                              # something keeps the job thread from ever registering
        release.wait(60)
        return orig(kind)
    lab.working = blocked_registration
    sid = lab._add_pending(from_dict({**SPEC, "name": "stuck one"}), "paper")["id"]
    job = lab.start(sid)
    time.sleep(1.2)
    eng = lab.engine_state()
    assert eng["busy"] is False and eng["stuck"]["job"] == "stuck one" and eng["stuck"]["alive"]
    assert any("blocked_registration" in w for w in eng["stuck"]["where"])      # it names the place
    release.set()
    for _ in range(300):
        if lab.jobs[job["id"]]["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert lab.jobs[job["id"]]["status"] == "done" and lab.engine_state().get("stuck") is None


def test_the_state_snapshot_of_jobs_is_a_copy_not_a_live_view(tmp_path):
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    lab.jobs["j"] = {"id": "j", "name": "x", "status": "running", "_stuck_reported": True}
    snap = lab.state()["jobs"][-1]
    lab.jobs["j"]["status"] = "done"
    assert snap["status"] == "running" and "_stuck_reported" not in snap


def test_diag_answers_even_when_the_lab_lock_is_held_and_names_the_threads(tmp_path):
    import json, threading, urllib.request
    from ofo.app.server import make_server
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    srv = make_server(lab, port=8795); threading.Thread(target=srv.serve_forever, daemon=True).start()
    lab.lock.acquire()                                                           # the situation in which /api/state would hang
    try:
        d = json.loads(urllib.request.urlopen("http://127.0.0.1:8795/api/diag", timeout=5).read())
    finally:
        lab.lock.release(); srv.shutdown()
    assert d["lab_lock_held"] is True and d["engine_lock_held"] is False and d["build"] and d["threads"]


def test_state_answers_while_the_lab_lock_is_held(tmp_path):
    import threading
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    out = {}
    lab.lock.acquire()
    try:
        t = threading.Thread(target=lambda: out.update(s=lab.state())); t.start(); t.join(8)
        assert not t.is_alive() and "jobs" in out["s"] and "engine" in out["s"]
    finally:
        lab.lock.release()


def test_atomic_write_survives_windows_style_sharing_violations_and_many_writers(tmp_path, monkeypatch):
    """Found with the owner's /api/diag: a job 'queued' with NO thread, because writing data/queue.json raised inside start() (Windows
    refuses replace() on a file another thread/process holds, or when two writers share one '.tmp')."""
    import threading
    from pathlib import Path
    from ofo import storage
    target = tmp_path / "queue.json"
    real, calls = Path.replace, {"n": 0}

    def flaky(self, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(self, dst)
    monkeypatch.setattr(Path, "replace", flaky)
    storage.atomic_write(target, '{"a": 1}')                                      # retried, not raised
    monkeypatch.setattr(Path, "replace", real)
    assert json.loads(target.read_text()) == {"a": 1}
    errs = []

    def writer(k):
        try:
            for i in range(30):
                storage.atomic_write(target, json.dumps({"k": k, "i": i, "pad": "x" * 4000}))
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=writer, args=(k,)) for k in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and "pad" in json.loads(target.read_text())                   # always a whole, valid file
    assert not list(tmp_path.glob("*.tmp"))


def test_a_study_still_starts_when_the_queue_file_cannot_be_written_and_a_failed_start_is_an_error_not_a_ghost(tmp_path, monkeypatch):
    import time
    lab = _lab_with_researcher(tmp_path, {"strategies": [], "concepts": []})
    from ofo.app import lab as labmod

    def deny(*a, **k):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(labmod, "atomic_write", deny)                              # the queue file is unwritable (like on the owner's PC)
    sid = lab._add_pending(from_dict({**SPEC, "name": "still runs"}), "paper")["id"]
    job = lab.start(sid)
    assert job["status"] in ("queued", "running", "done")
    for _ in range(300):
        if lab.jobs[job["id"]]["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert lab.jobs[job["id"]]["status"] == "done"
    assert any("coda non salvata" in e["message"] for e in lab.events.tail())      # said, not silent
    (tmp_path / "b").mkdir()
    lab2 = _lab_with_researcher(tmp_path / "b", {"strategies": [], "concepts": []})
    monkeypatch.setattr(lab2, "_run_job", lambda *a, **k: None)
    monkeypatch.setattr(labmod.threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("can't start new thread")))
    sid2 = lab2._add_pending(from_dict({**SPEC, "name": "ghost"}), "paper")["id"]
    j2 = lab2.start(sid2)
    assert j2["status"] == "error" and "avvio non riuscito" in j2["error"]         # never 'queued' for ever
