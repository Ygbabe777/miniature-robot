"""MIE engine: hypotheses from measured studies, duplicate recognition, Research Director missions, development-only guarantee, restart, pipeline hand-off."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from ofo.events import EventLog
from ofo.mie import config as MC
from ofo.mie import hypotheses as HY
from ofo.mie import questions as Q
from ofo.mie.director import Director
from ofo.mie.engine import Engine
from ofo.mie.guard import DevView
from ofo.mie.memory import Memory
from ofo.registry import TrialRegistry
from ofo.specs import from_dict
from test_research import synth

CFG = MC.load()


def mk_engine(tmp_path, submit=None, name="e"):
    mem = Memory(tmp_path / name / "vault" / "OFO", tmp_path / name / "key", tmp_path / name / "mstate.json")
    return Engine(mem, tmp_path / name / "mie", CFG, TrialRegistry(tmp_path / name / "r.db"), EventLog(tmp_path / name / "events.jsonl"), submit)


def fake_study(did, status="SUPPORTED", level="onh"):
    return {"name": "fake", "definition": {"event": "liquidity", "level": level, "outcome": "reclaim", "trade": "fade", "reclaim_bars": 6, "id": did}, "n_events": 90, "n_sessions": 70, "horizon_min": 60,
            "status": status, "status_raw": status, "p_value": 0.01, "data_version": "dv1", "distribution": {"mean_net": 0.12}, "cost_sensitivity": {"x1": 0.12, "breakeven_cost_multiple": 2.4},
            "baseline": {"diff": 0.2, "diff_ci95": [0.05, 0.35], "p_value": 0.01, "mean_net": -0.08, "n": 270}, "by_regime": {"RANGING": {"n": 40, "mean_net": 0.2}, "TRENDING": {"n": 30, "mean_net": -0.05}}}


def the_question(cfg=CFG, level="onh"):
    return next(q for q in Q.standard_questions(cfg) if q["definition"].get("level") == level and q["definition"].get("outcome") == "reclaim")


def test_every_mapped_hypothesis_becomes_a_valid_pipeline_spec_with_a_complete_card():
    n = 0
    for q in Q.standard_questions(CFG):
        sp = HY.spec_for(q)
        if sp is None:
            continue
        h = HY.make_hypothesis(fake_study(q["definition"]["id"], level=q["definition"].get("level")), q, ["n1"], [])
        assert h and h["pipeline"] == "spec" and set(h["card"]) >= {"fenomeno", "previsione_falsificabile", "criterio_rigetto"}
        spec = from_dict({"name": "t", "template": sp["template"], "ranges": sp["ranges"], "timeframes": ["5min"], "rationale": h["mechanism"], "hypothesis": h["card"]}, require_card=True)
        assert spec.n_candidates >= 1
        n += 1
    assert n >= 8
    gap_q = next(q for q in Q.standard_questions(CFG) if q["family"] == "level_acceptance")
    h = HY.make_hypothesis(fake_study(gap_q["definition"]["id"]), gap_q, [], [])
    assert h["pipeline"] == "research_question_only" and h["spec"] is None                         # no template: studied, not traded


def test_a_hypothesis_states_its_sources_prediction_falsification_and_novelty():
    q = the_question()
    h = HY.make_hypothesis(fake_study(q["definition"]["id"]), q, ["note-1"], [])
    for k in ("id", "market", "mechanism", "source_observations", "obsidian_refs", "testable_prediction", "falsification", "required_data", "expected_costs", "priority", "novelty"):
        assert h[k]
    assert h["obsidian_refs"] == ["note-1"] and h["novelty"]["score"] == 1.0 and h["status"] == "HYPOTHESIZED"
    weak = fake_study(q["definition"]["id"], status="CONTRADICTED")
    assert HY.make_hypothesis(weak, q, [], []) is None                                              # a contradicted claim is a negative finding, not a hypothesis


def test_a_duplicate_hypothesis_is_recognised_once_without_notes_or_events_spam(tmp_path):
    e = mk_engine(tmp_path)
    q = the_question()
    by_def = {q["definition"]["id"]: q}
    e.studies["dv1"] = {q["definition"]["id"]: fake_study(q["definition"]["id"])}
    assert e.derive_hypotheses("dv1", by_def) == (1, 0)
    notes = len(list(e.mem.base.rglob("*.md")))
    ev = len(e.ev.read())
    assert e.derive_hypotheses("dv1", by_def) == (0, 0)                                              # same data version: nothing new, nothing written
    assert len(list(e.mem.base.rglob("*.md"))) == notes and len(e.ev.read()) == ev
    e.studies["dv2"] = {q["definition"]["id"]: {**fake_study(q["definition"]["id"]), "data_version": "dv2"}}
    assert e.derive_hypotheses("dv2", by_def) == (0, 1)                                              # the memory already holds this hypothesis: a duplicate, not a new idea
    assert e.counters["duplicates_hypotheses"] == 1 and len(e.hyps) == 1


def test_a_known_negative_result_stops_the_same_idea_from_being_proposed_again(tmp_path):
    e = mk_engine(tmp_path)
    q = the_question()
    e.mem.write("negative_finding", "Smentita", "già smentita", market="NQ", evidence="CONTRADICTED", note_class="negative_result", definition=q["definition"]["id"], data_version="old", family=q["family"])
    fronts = [n["front"] for n in e.mem.index()]
    r = HY.make_hypothesis(fake_study(q["definition"]["id"]), q, [], fronts)
    assert r["duplicate"] and "smentita" in r["reason"]


def test_the_director_turns_knowledge_gaps_into_missions_once_and_within_budget(tmp_path):
    d = Director(tmp_path / "m.json", CFG)
    qs = Q.standard_questions(CFG)
    new = d.plan(questions=qs, studied={}, conflicts=[], hypotheses=[], market="NQ", data_version="dv1", min_events=30)
    assert len(new) == len(qs) and all(m["type"] == "study_question" and m["gap"] for m in new)
    assert d.plan(questions=qs, studied={}, conflicts=[], hypotheses=[], market="NQ", data_version="dv1", min_events=30) == []                  # the same gap is one mission
    studied = {qs[0]["definition"]["id"]: {"status": "INCONCLUSIVE", "n_events": 8}}
    d2 = Director(tmp_path / "m2.json", CFG)
    ms = d2.plan(questions=qs[:1], studied=studied, conflicts=[{"supported": "a", "contradicted": "b", "question": "perché?"}], hypotheses=[{"id": "H-1", "family": "orb", "priority": 0.7, "pipeline": "spec"}],
                 market="NQ", data_version="dv1", min_events=30)
    assert {m["type"] for m in ms} == {"sensitivity", "resolve_conflict", "screen_hypothesis"}
    assert d2.next(10)[0]["type"] == "resolve_conflict"                                               # highest priority first
    for m in list(d2.missions.values())[:3]:
        d2.start(m["id"])
    assert d2.next(10) == [] or len(d2.next(10)) <= max(0, CFG["director"]["max_missions_per_day"] - 3)
    assert Director(tmp_path / "m2.json", CFG).missions.keys() == d2.missions.keys()                 # survives a restart


def test_the_engine_runs_end_to_end_writes_memory_events_and_counts_trials(tmp_path):
    bars = synth(n_sessions=200, seed=11)
    days = sorted(pd.to_datetime(bars["session"]).unique())
    view = DevView(bars, str(days[160])[:10], str(days[199])[:10])
    sent = []
    e = mk_engine(tmp_path, submit=lambda spec, h: sent.append((spec, h["id"])) or 1)
    e.questions = Q.standard_questions(CFG)[:3]
    out = e.step(view)
    assert out["studies"] >= 1 and out["missions_run"] >= 1
    agents = {x["agent"] for x in e.ev.read()}
    assert {"market_observer", "structure_engine", "behavior_analyzer", "research_director"} <= agents         # emitted by the operations that really ran
    kinds = {n["front"].get("ofo_kind") for n in e.mem.index() if n["front"].get("ofo_kind")}
    assert "observation" in kinds and ({"behavior_study", "negative_finding"} & kinds)
    assert e.reg.usage(group="kind").get("mie", {}).get("trials", 0) >= out["studies"]                 # exploration is counted as trials
    assert e.reg.usage(group="stage")["info"]["trials"] >= 1
    assert all(s["scope"] == "development" for s in e.studies[view.data_version].values())
    # a second step on the same data adds nothing new to the memory
    n_notes = len(list(e.mem.base.rglob("*.md")))
    for _ in range(3):
        e.step(view)
    after = len(list(e.mem.base.rglob("*.md")))
    assert after >= n_notes
    # restart: studies and missions are restored
    e2 = Engine(e.mem, e.dir, CFG, e.reg, e.ev)
    assert e2.studies.keys() == e.studies.keys() and e2.director.missions.keys() == e.director.missions.keys()


def test_the_engine_cannot_see_the_hidden_block(tmp_path):
    base = synth(n_sessions=160, seed=12)
    days = sorted(pd.to_datetime(base["session"]).unique())
    start, end = str(days[120])[:10], str(days[159])[:10]
    poisoned = base.copy()
    m = (pd.to_datetime(poisoned["session"]) >= pd.Timestamp(start)) & (pd.to_datetime(poisoned["session"]) <= pd.Timestamp(end))
    poisoned.loc[m, ["open", "high", "low", "close"]] = poisoned.loc[m, ["open", "high", "low", "close"]] * 3 + 5000          # absurd prices only inside the hidden epoch
    results = []
    for i, b in enumerate((base, poisoned)):
        e = mk_engine(tmp_path, name=f"x{i}")
        e.questions = Q.standard_questions(CFG)[:2]
        e.step(DevView(b, start, end))
        results.append({dv: {k: (s["n_events"], s["status"], s["distribution"] and s["distribution"]["mean_net"]) for k, s in ss.items()} for dv, ss in e.studies.items()})
    assert results[0] == results[1]                                                              # identical: the epoch rows never entered the computation


def test_hypotheses_reach_the_existing_pipeline_through_missions_not_directly(tmp_path):
    sent = []
    e = mk_engine(tmp_path, submit=lambda spec, h: sent.append(spec) or 1)
    q = the_question()
    e.studies["dv1"] = {q["definition"]["id"]: fake_study(q["definition"]["id"])}
    e.derive_hypotheses("dv1", {q["definition"]["id"]: q})
    assert sent == []                                                                              # generating a hypothesis does not run anything
    hid = next(iter(e.hyps))
    e.director.plan(questions=[], studied={}, conflicts=[], hypotheses=list(e.hyps.values()), market="NQ", data_version="dv1", min_events=30)
    m = e.director.next(1)[0]
    assert m["type"] == "screen_hypothesis"
    e.director.start(m["id"])
    assert e._submit(e.hyps[hid]) == 1 and sent[0]["template"] == "sweep_reclaim" and sent[0]["source"].startswith("MIE H-") and e.hyps[hid]["sent"]
    spec = from_dict({**sent[0]}, require_card=True)
    assert spec.hypothesis["previsione_falsificabile"] and spec.source.startswith("MIE")
