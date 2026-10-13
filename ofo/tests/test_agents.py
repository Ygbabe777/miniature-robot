import json

import numpy as np
import pytest

from ofo.agents.base import Agent, AgentError, load_prompt
from ofo.agents.loop import ResearchLoop
from ofo.agents.roles import dev_summary, make_auditor, make_planner
from ofo.backtest import MNQ
from ofo.events import EventLog
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.specs import from_dict
from test_research import synth


class Scripted:
    """Fake LLM: answers per role (found in the system prompt) from a queue of replies."""
    def __init__(self, **replies): self.replies = {k: list(v) for k, v in replies.items()}; self.seen = []
    def chat(self, messages, **kw):
        self.seen.append(messages)
        sys_ = messages[0]["content"]
        role = ("researcher" if "RESEARCHER" in sys_ else "planner" if "PLANNER" in sys_ else
                "strategist" if "STRATEGIST" in sys_ else "curator" if "CURATOR" in sys_ else "auditor")
        return self.replies[role].pop(0)


PARENT = from_dict({"template": "ema_cross", "ranges": {"fast": [5, 9, 13], "slow": [30, 60, 90]},
                    "timeframes": ["15min", "30min"], "rationale": "baseline"})
EXTEND = {"decision": "extend", "reason": "il plateau tocca il bordo", "spec": {
    "name": "ema shifted", "template": "ema_cross", "ranges": {"fast": [5, 9], "slow": [90, 120]},
    "timeframes": ["15min"], "rationale": "MECCANISMO ... EVIDENZA ... QUANDO NON FUNZIONA ..."}}


def test_prompts_load_and_have_no_leftover_placeholders():
    for name in ("planner", "auditor"):
        assert "ROLE" in load_prompt(name, LANG="Italian")
    from ofo.papers import system_prompt
    p = system_prompt()
    assert "orb" in p and "random_control" not in p and "{{" not in p


def test_planner_valid_extend_is_accepted():
    out = make_planner(Scripted(planner=[json.dumps(EXTEND)]), PARENT, 3000, 0).run({"x": 1})
    assert out["decision"] == "extend" and out["spec"].template == "ema_cross"


def test_planner_retries_with_the_error_and_can_recover():
    bad = {**EXTEND, "spec": {"name": "x", "template": "orb", "ranges": {"or_minutes": [15, 30]},
                              "timeframes": ["15min"], "rationale": "valid spec, wrong template for extend"}}
    llm = Scripted(planner=[json.dumps(bad), json.dumps(EXTEND)])
    out = make_planner(llm, PARENT, 3000, 0).run({"x": 1})
    assert out["decision"] == "extend"
    assert "same template" in llm.seen[1][-1]["content"]          # it was told exactly what was wrong


@pytest.mark.parametrize("kwargs,why", [
    (dict(trials_left=3, depth=0), "allowance"),                   # budget
    (dict(trials_left=3000, depth=2), "depth"),                    # depth limit for extend
])
def test_planner_limits_are_enforced(kwargs, why):
    llm = Scripted(planner=[json.dumps(EXTEND)] * 3)
    with pytest.raises(AgentError):
        make_planner(llm, PARENT, **kwargs).run({"x": 1})


def test_planner_cannot_grow_the_search():
    big = {**EXTEND, "spec": {**EXTEND["spec"], "ranges": {"fast": list(range(5, 45, 4)), "slow": list(range(50, 400, 25))}}}
    with pytest.raises(AgentError):
        make_planner(Scripted(planner=[json.dumps(big)] * 3), PARENT, 3000, 0).run({})


def test_auditor_output_is_stripped_to_commentary_only():
    reply = json.dumps({"summary": "ok", "risk_flags": ["a"] * 20, "next_step": "x",
                        "verdict": "APPROVED", "approved": True})
    out = make_auditor(Scripted(auditor=[reply])).run({})
    assert set(out) == {"summary", "risk_flags", "next_step"} and len(out["risk_flags"]) == 8


def test_dev_summary_never_contains_locked_block():
    surf = {k: 0 for k in ("n_combos", "median_sharpe", "frac_positive", "best_sharpe", "best",
                           "plateau_ratio", "neighbours_positive", "checks", "robust")}
    rep = {"name": "n", "template": "orb", "rationale": "r", "surface": surf, "surface_detrended": surf,
           "wf_oos_days": 1, "wf_oos_pnl": 1.0, "wf_oos_sharpe_daily": 0.1,
           "locked": {"status": "REJECTED", "pnl": -99999, "p_pass": 0.123}}
    blob = json.dumps(dev_summary(rep, {"trials_left": 5}, 0))
    assert "locked" not in blob and "99999" not in blob and "0.123" not in blob


def test_agent_gives_up_after_max_attempts():
    llm = Scripted(auditor=["nonsense"] * 3)
    with pytest.raises(AgentError):
        Agent("auditor", llm, "AUDITOR", lambda d: d, max_attempts=3).run({})


from cards import CARD  # noqa: E402


def test_loop_ingests_ideas_once_and_logs_events(tmp_path):
    ideas = tmp_path / "ideas"; ideas.mkdir()
    (ideas / "note.txt").write_text("Opening range breakout after the cash open tends to continue.")
    spec = {"name": "ORB small", "template": "orb", "ranges": {"or_minutes": [15, 30]},
            "timeframes": ["15min", "30min"], "rationale": "MECCANISMO ...", "hypothesis": CARD}
    llm = Scripted(researcher=[json.dumps({"strategies": [spec]})] * 2, auditor=[json.dumps({"summary": "s"})] * 4)
    ev = EventLog(tmp_path / "events.jsonl")
    loop = ResearchLoop(synth(n_sessions=260, seed=6), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"),
                        llm, ev, tmp_path / "work")
    res = loop.run_once(ideas)
    assert len(res) == 1 and res[0]["verdict"] != "APPROVED"
    types = [(e["agent"], e["type"]) for e in ev.read()]
    assert ("researcher", "proposal") in types and ("judge", "verdict") in types
    assert (tmp_path / "work" / "reports" / "ORB_small.json").exists()
    assert loop.run_once(ideas) == []                             # same file is never re-read
    loop2 = ResearchLoop(synth(n_sessions=260, seed=6), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"),
                         llm, ev, tmp_path / "work")
    assert loop2.run_once(ideas) == []                            # ...even after a restart


def test_loop_planner_sees_dev_only_and_queue_respects_depth(tmp_path, monkeypatch):
    import ofo.agents.loop as L
    surf = {"n_combos": 9, "median_sharpe": 0.05, "frac_positive": 0.8, "best_sharpe": 0.1, "best": {},
            "plateau_ratio": 0.9, "neighbours_positive": 1.0, "n_neighbours": 4, "robust": True,
            "checks": {"plateau": True}}
    def fake_study(bars, spec, *a, **k):
        return {"name": spec.name, "template": spec.template, "rationale": spec.rationale, "source": "",
                "n_candidates": spec.n_candidates, "surface": surf, "surface_detrended": surf,
                "wf_oos_days": 100, "wf_oos_pnl": 5000.0, "wf_oos_sharpe_daily": 0.1, "n_locked_days": 50,
                "wf_oos_series": None,
                "locked": {"status": "REJECTED", "checks": {"dsr": False}, "pnl": -123456.0,
                           "p_pass": 0.4242, "p_pass_ci95": (0.1, 0.7), "p_bust": 0.5}}
    monkeypatch.setattr(L, "run_study", fake_study)
    llm = Scripted(planner=[json.dumps(EXTEND)] * 5, auditor=[json.dumps({"summary": "s"})] * 5)
    loop = ResearchLoop(None, MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"), llm,
                        EventLog(tmp_path / "e.jsonl"), tmp_path / "w", max_depth=1)
    loop.enqueue(PARENT)
    res = loop.run_once()
    planner_msgs = [m for m in llm.seen if "PLANNER" in m[0]["content"]]
    assert planner_msgs and all("123456" not in json.dumps(m) and "0.4242" not in json.dumps(m) for m in planner_msgs)
    auditor_msgs = [m for m in llm.seen if "AUDITOR" in m[0]["content"]]
    assert any("123456" in json.dumps(m) for m in auditor_msgs)    # the auditor DOES see the locked verdict
    assert len(res) == 2                                           # parent + one extension (max_depth=1)


def test_strategist_builds_the_orb_retest_spec_the_user_asked_for():
    from ofo.agents.roles import make_strategist
    reply = json.dumps({"reply": "Fatto: 144 combinazioni.", "template_request": None, "spec": {
        "name": "ORB con retest", "template": "orb_retest",
        "ranges": {"or_minutes": {"min": 15, "max": 45, "step": 5}, "retest_bars": {"values": [3, 6, 10, 15]},
                   "tol": {"values": [0.0, 0.15, 0.3]}},
        "timeframes": ["5min", "15min"], "rationale": "MECCANISMO ... EVIDENZA idea dell'utente ... QUANDO NON FUNZIONA ..."}})
    out = make_strategist(Scripted(strategist=[reply])).run({"message": "creami una strategia ORB con i retest"})
    assert out["spec"].template == "orb_retest" and out["spec"].n_candidates == min(7 * 4 * 3, 60) * 2      # 84 combinations, capped at the template's 60 (staged search) x 2 timeframes


def test_strategist_missing_logic_becomes_a_template_request_not_a_wrong_spec():
    from ofo.agents.roles import make_strategist
    reply = json.dumps({"reply": "Manca un modello con trailing stop.", "spec": None, "template_request": {
        "name": "orb_trailing_stop", "description": "ORB + stop che segue il prezzo a N ATR"}})
    out = make_strategist(Scripted(strategist=[reply])).run({"message": "ORB con trailing stop"})
    assert out["spec"] is None and out["template_request"]["name"] == "orb_trailing_stop"


def test_strategist_rejects_both_or_garbage_and_never_invents_templates():
    from ofo.agents.roles import make_strategist
    both = json.dumps({"reply": "x", "spec": {"template": "orb"}, "template_request": {"name": "a", "description": "b"}})
    bad_tpl = json.dumps({"reply": "x", "spec": {"template": "magic_ai", "ranges": {}, "rationale": "r"}})
    for r in (both, bad_tpl):
        with pytest.raises(AgentError):
            make_strategist(Scripted(strategist=[r, r, r])).run({"message": "x"})
    ok = make_strategist(Scripted(strategist=[json.dumps({"reply": "Ciao!"})])).run({"message": "ciao"})
    assert ok["spec"] is None and ok["template_request"] is None and ok["reply"] == "Ciao!"


def test_language_reaches_every_agent_prompt():
    from ofo.agents.roles import make_strategist
    from ofo.papers import system_prompt
    for maker in (lambda: make_planner(Scripted(), PARENT, 100, 0, language="English"),
                  lambda: make_auditor(Scripted(), language="English"),
                  lambda: make_strategist(Scripted(), language="English")):
        sys_ = maker().system
        assert "English" in sys_ and "{{" not in sys_
        assert "reply` in Italian" not in sys_ and "write it in Italian" not in sys_   # no hard-coded language
    assert "in Italian" not in make_planner(Scripted(), PARENT, 100, 0, language="English").system
    assert "English" in system_prompt("English") and "{{" not in system_prompt("English")
    assert "Italian" in make_auditor(Scripted()).system                 # default stays Italian
