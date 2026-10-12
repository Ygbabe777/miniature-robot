import json
import time

import numpy as np
import pandas as pd

from ofo import knowledge as kn
from ofo.app.lab import Lab
from ofo.backtest import ES, MES, MGC, GC
from ofo.dossier import build_dossier
from ofo.markets import MARKETS, discover, root_of
from ofo.prop import PRESETS
from ofo.specs import from_dict
from test_autopilot import RULES, make, report
from test_lab import SPEC, bars_csv  # noqa: F401
from test_research import synth


def test_contracts_and_discovery(tmp_path):
    assert root_of("MNQ") == "NQ" and root_of("MES") == "ES" and root_of("MGC") == "GC" and root_of("GC") == "GC"
    assert MES.point_value == 5.0 and MGC.point_value == 10.0 and GC.point_value == 100.0 and ES.point_value == 50.0
    (tmp_path / "ES_1min.csv.gz").write_bytes(b"x"); (tmp_path / "NQ_1min.csv.gz").write_bytes(b"x")
    assert set(discover(tmp_path)) == {"ES", "NQ"} and set(MARKETS) == {"NQ", "ES", "GC"}


def two_markets(tmp_path, bars_csv):
    es = tmp_path / "ES_1min.csv.gz"
    b = synth(n_sessions=200, seed=11)
    b.to_csv(es)
    llm = {"researcher": None}
    from test_agents import Scripted
    llm = {"researcher": Scripted(researcher=["x"]), "planner": Scripted(planner=["x"] * 3),
           "auditor": Scripted(auditor=[json.dumps({"summary": "s"})] * 3)}
    return Lab(bars_csv, data_dir=str(tmp_path / "data"), ideas_dir=str(tmp_path / "ideas"), llm=llm, markets={"ES": str(es)})


def wait_all(lab):
    for _ in range(480):
        if lab.jobs and all(j["status"] in ("done", "error") for j in lab.jobs.values()):
            return
        time.sleep(0.5)
    raise AssertionError("jobs did not finish")


def test_every_strategy_is_tested_on_every_market_and_reports_say_which(tmp_path, bars_csv):
    lab = two_markets(tmp_path, bars_csv)
    assert set(lab.mk) == {"NQ", "ES"} and lab.primary == "NQ"
    assert lab._add_pending_all(from_dict(SPEC), "paper") == 2
    assert lab._add_pending_all(from_dict(SPEC), "paper") == 0                     # no duplicates while queued
    assert sorted(v["market"] for v in lab.pending.values()) == ["ES", "NQ"]
    lab.set_autopilot(enabled=True)
    for _ in range(2):
        assert lab.autopilot_tick() is True
        wait_all(lab)
    names = sorted(j["name"] for j in lab.jobs.values())
    assert names == ["ORB retest small", "ORB retest small [ES]"]                  # unique per market
    st = lab.state()
    assert {x["market"] for x in st["strategies"]} == {"NQ", "ES"} and st["primary"] == "NQ"
    assert [m["sym"] for m in st["markets"]] == ["NQ", "ES"]
    assert lab._add_pending_all(from_dict(SPEC), "paper") == 0                     # both markets already tested it
    es_slug = next(x["slug"] for x in st["strategies"] if x["market"] == "ES")
    tr = lab.trades(es_slug)
    assert tr is None or tr["market"] == "ES"                                       # trades come from the ES file
    assert lab.candles("15min", 3, market="ES")["market"] == "ES" and lab.candles("15min", 3)["market"] == "NQ"


def test_knowledge_remembers_on_which_market_each_family_works():
    def st(name, market, status):
        return {"report": {"name": name, "market": market, "template": "orb", "rationale": "", "source": "", "n_candidates": 1,
                           "surface": {}, "wf_oos_pnl": 0, "locked": {"status": status, "checks": {}}}}
    g = kn.build([st("a", "NQ", "REJECTED"), st("a [ES]", "ES", "APPROVED"), st("a [GC]", "GC", "REJECTED")], {}, __import__("pathlib").Path("/nonexistent"))
    assert g["templates"]["orb"] == {"NQ": {"tested": 1, "approved": 0}, "ES": {"tested": 1, "approved": 1}, "GC": {"tested": 1, "approved": 0}}
    mem = kn.memory_text(g)
    assert "ES 1 tested/1 approved" in mem and "NQ 1 tested/0 approved" in mem and "Results by market" in mem


def test_dossier_uses_the_markets_micro_contract_and_point_value():
    rep = report(150, 250)
    rep["exits"] = {"best_stats": {"median_stop_points": 10.0}, "grid": []}
    rep["series"]["exit"] = {"stop_atr": 1.5, "rr": 2.0}
    d = build_dossier(rep, RULES, 2, 100, market="GC", micro="MGC", point_value=10.0)
    assert d["market"] == "GC" and d["micro"] == "MGC" and "(MGC)" in d["accounts"]["50k"]["text"]
    n = d["accounts"]["50k"]["recommended"]["micro_equiv"]
    assert abs(d["accounts"]["50k"]["risk"]["risk_per_trade"] - 10.0 * 10.0 * n) < 1e-6        # 10 points x $10 x micros
