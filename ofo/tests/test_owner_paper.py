"""The owner can send ANY tested strategy to paper; a strategy imported from the owner's own file goes there automatically after its test. Verdicts never change."""
import time

import pytest

from ofo.app.lab import Lab
from ofo.specs import from_dict
from ofo.storage import load_study
from test_research import synth

SPEC = {"name": "EMA paper", "template": "ema_cross", "ranges": {"fast": [5, 9], "slow": [30, 60]}, "timeframes": ["15min"], "rationale": "t"}


def mk(tmp_path):
    csv = tmp_path / "b.csv.gz"
    if not csv.exists():
        synth(n_sessions=260, seed=6).to_csv(csv)
    return Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))


def wait_done(lab, jid, secs=240):
    t0 = time.time()
    while time.time() - t0 < secs:
        j = lab.jobs[jid]
        if j["status"] in ("done", "error"):
            return j
        time.sleep(1)
    raise AssertionError("job did not finish")


def test_any_tested_strategy_goes_to_paper_without_changing_its_verdict(tmp_path):
    lab = mk(tmp_path)
    lab.loop().process(from_dict(SPEC))
    before = load_study(lab.work, "EMA_paper")["report"]["locked"].get("verdict") or load_study(lab.work, "EMA_paper")["report"]["locked"].get("status")
    out = lab.owner_paper("EMA_paper")
    assert out["paper"] is True and out["micro"] == 1 and out["account"] == "50k"
    item = lab.deployments.get("EMA_paper")
    assert item["status"] == "live" and item["owner_paper"] is True and "NON convalidata" in item["note"] and item["forward_plan"]
    assert lab.owner_paper("EMA_paper")["already"] is True                                    # idempotent
    assert (load_study(lab.work, "EMA_paper")["report"]["locked"].get("verdict") or load_study(lab.work, "EMA_paper")["report"]["locked"].get("status")) == before
    assert lab.forward_tick(force=True) in (True, False)                                      # the paper machinery accepts it without error
    assert not [e for e in lab.events.read() if e.get("type") == "error" and "forward" in str(e.get("message"))]
    assert lab.owner_paper("inesistente")["error"]


def test_a_file_imported_by_the_owner_is_tested_and_goes_to_paper_by_itself(tmp_path):
    lab = mk(tmp_path)
    sid = lab._add_pending(from_dict(SPEC), "import", lab.primary)["id"]
    j = wait_done(lab, lab.start(sid)["id"])
    assert j["status"] == "done"
    item = lab.deployments.get(j["slug"])
    assert item and item["owner_paper"] and item["status"] == "live"


def test_other_origins_do_not_go_to_paper_and_the_switch_can_be_turned_off(tmp_path):
    lab = mk(tmp_path)
    sid = lab._add_pending(from_dict(SPEC), "paper", lab.primary)["id"]                        # a downloaded paper, not the owner's file
    j = wait_done(lab, lab.start(sid)["id"])
    assert lab.deployments.get(j["slug"]) is None
    assert lab.set_auto_paper(False)["auto_paper_imports"] is False
    spec2 = from_dict({**SPEC, "name": "EMA paper two", "ranges": {"fast": [6, 10], "slow": [40, 80]}})
    sid2 = lab._add_pending(spec2, "import", lab.primary)["id"]
    j2 = wait_done(lab, lab.start(sid2)["id"])
    assert lab.deployments.get(j2["slug"]) is None
    assert lab.set_auto_paper(True)["auto_paper_imports"] is True
