from ofo.registry import TrialRegistry
import json

import pytest

from ofo.accounts import demo_accounts, derive, load_accounts, validate
from ofo.agents.base import Agent
from ofo.app.lab import Lab
from ofo.backtest import MNQ
from ofo.events import EventLog
from ofo.optimizer import run_study
from ofo.prop import PRESETS
from ofo.specs import from_dict
from test_agents import Scripted
from test_research import synth


def acct(**kw):
    base = {"id": "a", "firm": "F", "plan": "50K", "kind": "evaluation", "start_balance": 50000,
            "balance": 50000, "profit_target": 3000, "max_drawdown": 2000}
    return derive(validate({**base, **kw}))


def test_account_states_from_the_numbers():
    assert acct(balance=51000)["state"] == "ok" and acct(balance=51000)["progress"] == pytest.approx(1 / 3, abs=1e-3)
    assert acct(balance=48400)["state"] == "danger"            # 400 above a 48,000 floor: health 0.2
    assert acct(balance=53100)["state"] == "passed"            # target reached
    assert acct(balance=47900)["state"] == "busted"            # below the floor
    assert acct(balance=50500, status="busted")["state"] == "busted"
    f = acct(kind="funded", balance=52000, floor=50000, payouts=2, profit_target=None)
    assert f["state"] == "ok" and f["progress"] is None and f["payouts"] == 2 and f["health"] == 1.0
    with pytest.raises(ValueError):
        validate({"id": "x"})
    with pytest.raises(ValueError):
        validate({**acct(), "kind": "demo"})


def test_demo_accounts_cover_every_creature_state_and_a_real_file_replaces_them(tmp_path):
    assert {a["state"] for a in demo_accounts()} == {"ok", "danger", "passed", "busted"}
    assert all(a["demo"] for a in demo_accounts())
    p = tmp_path / "accounts.json"
    assert load_accounts(p) == []
    p.write_text(json.dumps({"accounts": [{"id": "1", "firm": "T", "plan": "50K", "kind": "funded",
                                           "start_balance": 50000, "balance": 51000, "max_drawdown": 2000}]}))
    assert load_accounts(p)[0]["firm"] == "T"


def test_lab_serves_demo_then_real_accounts(tmp_path):
    lab = Lab("unused.csv", data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    assert lab.accounts()["demo"] is True
    (tmp_path / "d" / "accounts.json").write_text(json.dumps([{"id": "1", "firm": "T", "plan": "50K", "kind": "evaluation",
        "start_balance": 50000, "balance": 50500, "profit_target": 3000, "max_drawdown": 2000}]))
    out = lab.accounts()
    assert out["demo"] is False and out["accounts"][0]["id"] == "1"


def test_run_study_reports_who_is_working_in_order(tmp_path):
    events = []
    spec = from_dict({"template": "ema_cross", "ranges": {"fast": [5, 9], "slow": [30, 60]},
                      "timeframes": ["15min"], "rationale": "t"})
    run_study(synth(n_sessions=200, seed=4), spec, MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"),
              train_days=60, test_days=10, progress=lambda a, s, m="": events.append((a, s)))
    assert events[:4] == [("optimizer", "working"), ("optimizer", "done"), ("validator", "working"), ("validator", "done")]
    assert ("judge", "working") not in events                    # a failed surface never reaches the hidden block


def test_agent_emits_working_then_done_or_idle(tmp_path):
    ev = EventLog(tmp_path / "e.jsonl")
    Agent("planner", Scripted(planner=['{"a": 1}']), "PLANNER", lambda d: d, ev).run({})
    types = [(e["agent"], e["type"]) for e in ev.read()]
    assert types[0] == ("planner", "working") and types[-1] == ("planner", "done")
    ev2 = EventLog(tmp_path / "e2.jsonl")
    with pytest.raises(Exception):
        Agent("auditor", Scripted(auditor=["junk", "junk"]), "AUDITOR", lambda d: d, ev2, max_attempts=2).run({})
    assert [e["type"] for e in ev2.read()][0] == "working" and [e["type"] for e in ev2.read()][-1] == "idle"


def test_trades_endpoint_hides_the_locked_block_unless_the_judge_looked(tmp_path):
    csv = tmp_path / "bars.csv.gz"
    synth(n_sessions=260, seed=6).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    spec = from_dict({"name": "EMA trades", "template": "ema_cross", "ranges": {"fast": [5, 9], "slow": [30, 60]},
                      "timeframes": ["15min"], "rationale": "t"})
    lab.loop().process(spec)
    out = lab.trades("EMA_trades")
    assert out and out["trades"] and out["summary"]["n"] == len(out["trades"])
    looked = out["locked_visible"]
    if not looked:                                               # normal case here: the Judge never looked
        assert all(t["session"] < out["cut"] for t in out["trades"]) and out["n_hidden"] > 0
    assert all({"entry_t", "exit_t", "side", "pnl", "session", "entry_session"} <= set(t) for t in out["trades"])
    assert lab.trades("does_not_exist") is None
