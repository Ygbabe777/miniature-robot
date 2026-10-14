"""30-day learning cycle: weekly focus, five distinct quantities, restart, forward tracking, inconclusive report on thin data, scope separation."""
import json

import pandas as pd
import pytest

from ofo.events import EventLog
from ofo.mie import config as MC
from ofo.mie import questions as Q
from ofo.mie.cycle import LearningCycle
from ofo.mie.engine import Engine
from ofo.mie.guard import DevView
from ofo.mie.memory import Memory
from ofo.registry import TrialRegistry
from test_research import synth

CFG = MC.load()
DAY = 86400.0


def mk(tmp_path, name="c"):
    mem = Memory(tmp_path / name / "vault" / "OFO", tmp_path / name / "key", tmp_path / name / "ms.json")
    e = Engine(mem, tmp_path / name / "mie", CFG, TrialRegistry(tmp_path / name / "r.db"), EventLog(tmp_path / name / "ev.jsonl"))
    e.questions = Q.standard_questions(CFG)[:3]
    return e, LearningCycle(e, tmp_path / name / "mie" / "cycle.json", CFG)


def view_of(n_sessions, cut_hidden=(0.8, 0.9), seed=21):
    bars = synth(n_sessions=n_sessions, seed=seed)
    days = sorted(pd.to_datetime(bars["session"]).unique())
    return DevView(bars, str(days[int(n_sessions * cut_hidden[0])])[:10], str(days[int(n_sessions * cut_hidden[1])])[:10]), bars


def test_the_weekly_focus_follows_the_cycle_without_being_a_rigid_block(tmp_path):
    e, c = mk(tmp_path)
    c.start(now=0.0)
    assert [c.focus(now=d * DAY) for d in (1, 6, 8, 14, 16, 22, 24, 29)] == ["baseline", "baseline", "discovery", "discovery", "hypothesis", "hypothesis", "consolidation", "consolidation"]
    assert c.elapsed_days(now=3 * DAY) == pytest.approx(3.0)


def test_a_tick_observes_studies_and_counts_the_five_quantities_separately(tmp_path):
    e, c = mk(tmp_path)
    view, _ = view_of(180)
    c.start(now=0.0)
    out = c.tick(view, now=10 * DAY)
    assert out["focus"] == "discovery" and out["studies"] >= 1
    k = c.counts(view, now=10 * DAY)
    assert set(k) >= {"lab_days_elapsed", "market_sessions_development", "historical_bars_1min", "events_observed", "forward_sessions", "hypotheses"}
    assert k["lab_days_elapsed"] == pytest.approx(10.0, abs=0.1) and k["market_sessions_development"] != k["lab_days_elapsed"]
    assert k["forward_sessions"] == c.state["forward_sessions_seen"] == len(view.forward["session"].unique())
    st = c.status(now=10 * DAY)
    assert st["status"] == "active" and st["lab_days_elapsed"] == pytest.approx(10.0, abs=0.1)


def test_restart_restores_the_cycle_without_repeating_work(tmp_path):
    e, c = mk(tmp_path)
    view, _ = view_of(180)
    c.start(now=0.0)
    c.tick(view, now=2 * DAY)
    done_before = dict(c.state["done"])
    studies_before = c.state["studies_run"]
    e2 = Engine(e.mem, e.dir, CFG, e.reg, e.ev); e2.questions = e.questions
    c2 = LearningCycle(e2, c.path, CFG)
    assert c2.state["id"] == c.state["id"] and c2.state["status"] == "active" and c2.state["done"] == done_before and c2.state["studies_run"] == studies_before
    assert c2.focus(now=2 * DAY) == c.focus(now=2 * DAY)
    c2.tick(view, now=3 * DAY)
    assert c2.state["id"] == "C001"                                                              # same cycle, not a new one


def test_the_report_is_written_once_at_the_end_with_the_six_statuses_and_never_says_proven(tmp_path):
    e, c = mk(tmp_path)
    view, _ = view_of(220)
    c.start(now=0.0)
    for d in (5, 12, 20):
        c.tick(view, now=d * DAY)
    out = c.tick(view, now=31 * DAY)
    assert "report" in out and c.state["status"] == "completed"
    n_reports = [n for n in e.mem.index() if n["front"].get("ofo_kind") == "monthly_report"]
    c.tick(view, now=32 * DAY)                                                                     # a completed cycle does not write again
    assert len([n for n in e.mem.index() if n["front"].get("ofo_kind") == "monthly_report"]) == len(n_reports) == 1
    text = n_reports[0]["body"]
    for h in ("OBSERVED", "HYPOTHESIZED", "TESTED", "SUPPORTED", "INCONCLUSIVE", "CONTRADICTED"):
        assert f"## {h}" in text
    assert "PROVEN" not in text and "proven" not in text.lower().replace("non dimostrato", "") and "giorni di attività del Lab" in text and "sessioni di mercato disponibili" in text
    assert "Prossimo ciclo" in text


def test_a_month_with_few_observations_produces_an_inconclusive_report_not_a_discovery(tmp_path):
    e, c = mk(tmp_path)
    view, _ = view_of(25, cut_hidden=(0.6, 0.8))
    c.start(now=0.0)
    c.tick(view, now=5 * DAY)
    out = c.tick(view, now=31 * DAY)
    assert out["report"]["thin"] is True
    body = [n for n in e.mem.index() if n["front"].get("ofo_kind") == "monthly_report"][0]
    assert "REPORT INCONCLUDENTE" in body["body"] and body["front"]["ofo_evidence"] == "INCONCLUSIVE"
    sup = body["body"].split("## SUPPORTED")[1].split("##")[0]
    assert "nessuno" in sup                                                                       # nothing is presented as supported


def test_forward_observations_use_only_sessions_after_the_epoch_and_are_labelled_forward(tmp_path):
    e, c = mk(tmp_path)
    bars = synth(n_sessions=320, seed=31)
    days = sorted(pd.to_datetime(bars["session"]).unique())
    view = DevView(bars, str(days[200])[:10], str(days[260])[:10])
    c.start(now=0.0)
    for d in (3, 9, 15, 21):
        c.tick(view, now=d * DAY)
    fw = [n for n in e.mem.index() if n["front"].get("ofo_kind") == "forward_observation"]
    assert c.state["forward_sessions_seen"] == len(days) - 261
    assert fw or all(s["status"] not in ("SUPPORTED", "INCONCLUSIVE") for s in e.studies[view.data_version].values())
    for n in fw:
        assert n["front"]["ofo_scope"] == "forward" and "FORWARD" in n["body"]
    # forward data never changes the development studies: same studies as without forward sessions
    short = DevView(bars.iloc[: bars.index.get_loc(bars[pd.to_datetime(bars["session"]) > pd.Timestamp(str(days[260])[:10])].index[0])], str(days[200])[:10], str(days[260])[:10])
    e2, c2 = mk(tmp_path, "c2")
    c2.start(now=0.0)
    for d in (3, 9, 15, 21):
        c2.tick(short, now=d * DAY)
    f = lambda eng, v: {k: (s["n_events"], s["status"]) for k, s in eng.studies[v.data_version].items()}
    assert view.data_version == short.data_version and f(e, view) == f(e2, short)
