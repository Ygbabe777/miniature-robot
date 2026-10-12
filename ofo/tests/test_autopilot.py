import json
import time

import numpy as np

from ofo.app.lab import Lab
from ofo.dossier import build_dossier
from ofo.prop import PRESETS
from ofo.specs import from_dict
from test_agents import Scripted
from test_lab import SPEC, bars_csv  # noqa: F401  (fixture)


def report(mean, sd, status="APPROVED", n=125, seed=1):
    rng = np.random.default_rng(seed)
    pnl = rng.normal(mean, sd, n)
    return {"name": "Test strat", "template": "orb", "rationale": "why", "source": "paper X",
            "locked": {"status": status},
            "series": {"locked": [[f"2025-01-{i:03d}", float(v)] for i, v in enumerate(pnl)],
                       "best_params": {"or_minutes": 15, "tf": "15min"}}}


RULES = {"50k": PRESETS["50k"], "25k": PRESETS["25k"]}


def test_dossier_only_for_approved():
    assert build_dossier(report(150, 250, "REJECTED"), RULES, 2, 1000) is None


def test_dossier_has_size_risk_and_instrument_advice():
    d = build_dossier(report(150, 250), RULES, 2, 1747, "it", "trend")
    a = d["accounts"]["50k"]
    assert a["recommended"] is not None and a["instrument"] in ("micro", "mini")
    r = a["recommended"]
    assert 0 <= r["p_pass_lo"] <= r["p_pass"] <= r["p_pass_hi"] <= 1 and r["p_bust"] <= 0.35
    assert a["risk"]["daily_stop"] == PRESETS["50k"].max_drawdown / 3
    assert "micro" in a["text"] and "Gestione del rischio" in a["text"] and "NON verificate" in d["caveat"]
    assert {t["micro_equiv"] for t in a["table"]} >= {1, 10, 20}
    en = build_dossier(report(150, 250), RULES, 2, 1747, "en")
    assert "Risk management" in en["accounts"]["50k"]["text"]


def test_dossier_says_no_safe_size_when_every_size_busts():
    d = build_dossier(report(-80, 900), RULES, 2, 1000)
    a = d["accounts"]["25k"]
    assert a["recommended"] is None and ("Nessuna" in a["text"])


def make(tmp_path, bars_csv, llm=None):
    llm = llm or {"researcher": Scripted(researcher=["x"]), "planner": Scripted(planner=["x"] * 3),
                  "auditor": Scripted(auditor=[json.dumps({"summary": "s"})] * 3)}
    return Lab(bars_csv, data_dir=str(tmp_path / "data"), ideas_dir=str(tmp_path / "ideas"), llm=llm)


def wait_jobs(lab):
    for _ in range(240):
        if lab.jobs and all(j["status"] in ("done", "error") for j in lab.jobs.values()):
            return
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def test_autopilot_runs_pending_without_asking_and_respects_budget(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    lab._add_pending(from_dict(SPEC), "paper")
    lab.set_autopilot(max_trials_per_day=100)
    lab.ap["max_trials_per_day"] = 5                            # below the 16 candidates: must pause
    assert lab.autopilot_tick() is False and lab.autopilot_state()["paused"] == "day" and len(lab.pending) == 1
    lab.ap["max_trials_per_day"] = 100
    assert lab.autopilot_tick() is True
    wait_jobs(lab)
    assert not lab.pending and lab.ap["trials_today"] == 16
    assert list(lab.jobs.values())[0]["status"] == "done"


def test_autopilot_off_does_nothing(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    lab._add_pending(from_dict(SPEC), "paper")
    lab.set_autopilot(enabled=False)
    assert lab.autopilot_tick() is False and len(lab.pending) == 1 and not lab.jobs


def test_working_agents_follow_real_activity_not_timeouts(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    assert lab.working_agents() == set()
    with lab.working("research"):
        lab.events.emit("scout", "working", "x")
        assert lab.working_agents() == {"researcher", "scout"}
        time.sleep(0.01)                                        # however long it takes it stays on
    assert lab.working_agents() == set()
    with lab.working("job"):
        assert lab.working_agents() == {"optimizer"}            # busy engine between lifecycle events
        lab.events.emit("planner", "working", "x")
        assert lab.working_agents() == {"planner"}
    lab.events.emit("planner", "working", "stale")
    assert lab.working_agents() == set()                        # a stale 'working' without activity is ignored


def test_unfinished_proposals_come_back_after_restart(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    sd = lab.work / "specs_auto"
    sd.mkdir(parents=True)
    (sd / "p.json").write_text(json.dumps({"strategies": [SPEC], "rejected": []}))
    assert lab.recover_specs() == 1 and lab.recover_specs() == 0
