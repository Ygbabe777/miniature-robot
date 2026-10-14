"""Deduplication decisions are facts recorded once: no event/counter per poll, persistent across restarts, invalidated by real changes, never blocking a retest."""
import json
import time

import pytest

from ofo import dedup as DD
from ofo.app.lab import Lab, NoLLM
from ofo.registry import TrialRegistry
from ofo.specs import from_dict
from ofo.storage import save_study
from test_research import synth

SPEC = {"name": "A", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40, 60]}, "timeframes": ["15min", "30min"],
        "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"}
NEAR = {**SPEC, "name": "A tweak", "ranges": {"lookback": [10, 20, 30, 40, 60, 70]}}          # 5 of 6 shared = 0.83: near-equivalent


def mk(tmp_path):
    csv = tmp_path / "b.csv.gz"
    if not csv.exists():
        synth(n_sessions=40, seed=2).to_csv(csv)
    return Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))


def dup_events(lab):
    return [e for e in lab.events.read() if e.get("type") == "duplicate"]


def test_the_first_duplicate_makes_one_event_and_polling_adds_nothing(tmp_path):
    lab = mk(tmp_path)
    assert lab._add_pending_all(from_dict(SPEC), "paper") == 1
    near = from_dict(NEAR)
    for _ in range(25):                                                        # 25 polls of the same starter
        assert lab._add_pending_all(near, "starter") == 0
    assert len(dup_events(lab)) == 1
    assert lab.registry.counter("dedup_near") == 1 and lab.dedup.suppressed["near"] == 24
    assert lab.registry.counter("duplicates_near") == 0                         # the legacy counter is frozen, not mixed with the reliable one


def test_the_decision_survives_a_restart(tmp_path):
    lab = mk(tmp_path)
    lab._add_pending_all(from_dict(SPEC), "paper")
    lab._add_pending_all(from_dict(NEAR), "starter")
    assert len(dup_events(lab)) == 1
    lab2 = mk(tmp_path)                                                         # the lab is restarted on the same folders
    lab2._add_pending_all(from_dict(NEAR), "starter")
    assert len(dup_events(lab2)) == 1 and lab2.registry.counter("dedup_near") == 1


def test_a_really_new_spec_is_accepted(tmp_path):
    lab = mk(tmp_path)
    lab._add_pending_all(from_dict(SPEC), "paper")
    lab._add_pending_all(from_dict(NEAR), "starter")
    other = from_dict({**SPEC, "name": "B", "template": "ema_cross", "ranges": {"fast": [5, 8], "slow": [20, 30]}})
    assert lab._add_pending_all(other, "starter") == 1
    changed = from_dict({**SPEC, "name": "A wide", "ranges": {"lookback": [100, 120, 140, 160]}})            # same template, other values
    assert lab._add_pending_all(changed, "starter") == 1


def test_changing_the_criterion_allows_a_new_decision(tmp_path):
    lab = mk(tmp_path)
    lab._add_pending_all(from_dict(SPEC), "paper")
    near = from_dict(NEAR)
    lab._add_pending_all(near, "starter")
    assert lab.registry.counter("dedup_near") == 1
    lab.NEAR_THRESHOLD = 0.7                                                    # a different near-duplicate criterion = a different rule version
    lab._add_pending_all(near, "starter")
    lab._add_pending_all(near, "starter")
    assert lab.registry.counter("dedup_near") == 2 and len(dup_events(lab)) == 2
    lab.NEAR_THRESHOLD = 0.95                                                   # under a stricter criterion it is no longer a duplicate: it is accepted, not blocked
    assert lab._add_pending_all(near, "starter") == 1


def test_markets_keep_independent_decisions(tmp_path):
    mem = DD.DedupMemory(TrialRegistry(tmp_path / "r.db"))
    rule = DD.rule_version(0.8)
    assert mem.record("NQ", "fp", rule, "near", "done:x", "A", "B") and not mem.record("NQ", "fp", rule, "near", "done:x", "A", "B")
    assert mem.record("ES", "fp", rule, "near", "done:x", "A", "B")           # another market: its own decision
    assert mem.record("NQ", "fp", rule, "near", "done:y", "A", "C")           # another equivalent candidate: a new decision
    assert mem.record("NQ", "fp2", rule, "near", "done:x", "A2", "B")         # another spec
    assert mem.record("NQ", "fp", DD.rule_version(0.7), "near", "done:x", "A", "B")
    assert mem.invalidate(market="ES") == 1 and mem.count() == {"near": 4}


def test_an_authorised_retest_is_not_blocked_and_releases_what_it_covered(tmp_path, monkeypatch):
    lab = mk(tmp_path)
    started = []
    monkeypatch.setattr(lab, "start", lambda sid, lang=None: started.append(sid) or {"id": sid})
    spec = from_dict(SPEC)
    rep = {"name": "A", "template": "breakout", "n_candidates": 10, "market": lab.primary, "spec": json.loads(json.dumps(__import__("dataclasses").asdict(spec))),
           "surface": {"median_sharpe": 0.1, "robust": False, "frac_positive": 0.5, "plateau_ratio": 0.5, "checks": {}},
           "locked": {"status": "REJECTED"}, "series": None}
    slug = save_study(lab.work, rep)
    lab._add_pending_all(from_dict(NEAR), "starter")                          # (queues NEAR: A is only a saved report here)
    eq = DD.equivalent_fp(spec.template, spec.timeframes, spec.ranges)
    lab.dedup.record(lab.primary, "other", DD.rule_version(lab.NEAR_THRESHOLD), "near", f"done:{eq}", "X", "A")
    before = lab.dedup.count().get("near", 0)
    out = lab.retest(slug)
    assert out.get("retest_no") == 1 and started                                # not blocked by any deduplication
    assert lab.dedup.count().get("near", 0) == before - 1                              # the decision that pointed at the retested study was released


def test_the_lab_does_not_loop_events_when_the_queue_is_empty(tmp_path):
    lab = mk(tmp_path)
    assert isinstance(lab.llm, NoLLM)
    n = lab.seed_specs()
    assert n > 4
    for sid, v in list(lab.pending.items()):                                    # everything gets tested: starters become 'done'
        lab.loop(v.get("market")).done_specs.add(__import__("ofo.agents.loop", fromlist=["x"])._fingerprint(v["spec"]))
    lab.pending.clear()
    lab._refill()
    lab._note_work_state()
    first = (len(lab.events.read()), lab.registry.counter("dedup_exact"), lab.registry.counter("dedup_near"))
    for _ in range(40):                                                         # 40 scheduler ticks with an empty queue
        lab._refill()
        lab._note_work_state()
    assert (len(lab.events.read()), lab.registry.counter("dedup_exact"), lab.registry.counter("dedup_near")) == first      # no events, no counters from polling


def test_work_states_reflect_the_real_backend_conditions(tmp_path):
    lab = mk(tmp_path)
    assert lab.work_state()["state"] == "RESEARCH_BLOCKED" and "chiave" in lab.work_state()["why"]       # no AI configured
    from test_agents import Scripted
    lab.llm = {"researcher": Scripted(researcher=["x"]), "planner": Scripted(planner=["x"]), "auditor": Scripted(auditor=["x"])}
    lab._last_hyp = time.time() - 24 * 3600
    assert lab.work_state()["state"] in ("WAITING_RESEARCH", "IDLE")
    lab._hyp_running = True
    assert lab.work_state()["state"] == "RESEARCH_RUNNING"
    lab._hyp_running = False
    lab._llm_error = (time.time(), "RateLimited: 429")
    assert lab.work_state()["state"] == "RESEARCH_BLOCKED" and "429" in lab.work_state()["why"]
    lab._llm_error = None
    lab._last_hyp = time.time()
    lab._hyp_log = [{"family": "E", "proposals": 0, "produced": 2, "duplicates": 2, "error": None}]
    ws = lab.work_state()
    assert ws["state"] == "NO_NEW_HYPOTHESES" and "2 scartate" in ws["why"]
    lab._add_pending(from_dict(SPEC), "paper")
    assert lab.work_state()["state"] == "ACTIVE"


def test_diag_explains_why_there_are_no_new_studies(tmp_path):
    lab = mk(tmp_path)
    lab._add_pending_all(from_dict(SPEC), "paper"); lab._add_pending_all(from_dict(NEAR), "starter")
    d = lab.diag()
    r = d["research"]
    assert set(r) >= {"state", "researcher_active", "ai", "calls", "hypothesis", "budget", "provider_error", "dedup"}
    assert r["dedup"]["counters"]["near"] == 1 and r["dedup"]["legacy_counters_inflated"]["near"] == 0 and r["dedup"]["recent"][0]["kind"] == "near"
    assert set(r["calls"]) == {"hypothesize", "brainstorm", "connect_ideas"} and lab.autopilot_state()["work_state"]["state"]
    assert lab.invalidate_dedup()["invalidated"] == 1 and lab.dedup.count() == {}


def test_a_starter_equivalent_to_a_completed_study_is_announced_once_exactly_like_the_reported_bug(tmp_path):
    lab = mk(tmp_path)
    eid = lab.registry.begin_experiment("study:Opening in-spread limit", 10, spec_fp="s1", dataset_fp="d1", market=lab.primary, dedupe=True, stage="screening",
                                        details={"template": SPEC["template"], "ranges": SPEC["ranges"], "timeframes": SPEC["timeframes"]})
    lab.registry.finish_experiment(eid, "completed", executed=10, results={}, reject_reason=None, compute_s=1.0, details={})
    starter = from_dict({**SPEC, "name": "ORB sweep", "ranges": {"lookback": [10, 20, 30, 40, 60, 70]}})
    for _ in range(30):
        assert lab._add_pending_all(starter, "starter") == 0
    ev = dup_events(lab)
    assert len(ev) == 1 and "Opening in-spread limit" in ev[0]["message"] and lab.registry.counter("dedup_near") == 1
    assert lab.registry.counter("duplicates_near") == 0
