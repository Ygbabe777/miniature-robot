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
    def st2(name, market, status, robust):
        r = st(name, market, status)
        r["report"]["surface"] = {"robust": robust, "checks": {} if robust else {"plateau": False}}
        r["report"]["wf_oos_pnl"] = 1.0 if robust else -1.0
        return r
    g = kn.build([st2("a", "NQ", "REJECTED", False), st2("a [ES]", "ES", "APPROVED", True), st2("a [GC]", "GC", "REJECTED", False)],
                 {}, __import__("pathlib").Path("/nonexistent"))
    assert g["templates"]["orb"] == {"NQ": {"tested": 1, "approved": 0}, "ES": {"tested": 1, "approved": 1}, "GC": {"tested": 1, "approved": 0}}
    mem = kn.memory_text(g)
    assert "ES 1 tested/1 passed" in mem and "NQ 1 tested/0 passed" in mem and "Results by market" in mem and "approved" not in mem


def test_dossier_uses_the_markets_micro_contract_and_point_value():
    rep = report(150, 250)
    rep["exits"] = {"best_stats": {"median_stop_points": 10.0}, "grid": []}
    rep["series"]["exit"] = {"stop_atr": 1.5, "rr": 2.0}
    d = build_dossier(rep, RULES, 2, 100, market="GC", micro="MGC", point_value=10.0)
    assert d["market"] == "GC" and d["micro"] == "MGC" and "(MGC)" in d["accounts"]["50k"]["text"]
    n = d["accounts"]["50k"]["recommended"]["micro_equiv"]
    assert abs(d["accounts"]["50k"]["risk"]["risk_per_trade"] - 10.0 * 10.0 * n) < 1e-6        # 10 points x $10 x micros


def test_build_market_makes_a_file_from_the_day_files_and_explains_failures(tmp_path):
    import gzip
    from ofo.markets import build_market
    HEADER = "ticker,exchange,session_end_date,window_start,open,high,low,close,volume,dollar_volume,transactions\n"

    def row(t, ts, price, vol):
        ns = int(pd.Timestamp(ts, tz="UTC").value)
        return f"{t},4,{str(ts)[:10]},{ns},{price},{price+1},{price-1},{price},{vol},0,1\n"
    for day in ("05", "06", "07", "08"):
        rows = [row("ESZ5", f"2025-11-{day} 15:0{m}", 6000.0 + m, 500) for m in range(3)]
        rows += [row("GCZ5", f"2025-11-{day} 15:0{m}", 2500.0 + m, 300) for m in range(3)]
        rows += [row("NQZ5", f"2025-11-{day} 15:0{m}", 21000.0, 400) for m in range(3)]
        with gzip.open(tmp_path / f"2025-11-{day}.csv.gz", "wt") as f:
            f.write(HEADER + "".join(rows))
    es = build_market(tmp_path, "ES", min_lead_days=3)
    gc = build_market(tmp_path, "GC", min_lead_days=3)
    assert es["chain"] == ["ESZ5"] and gc["chain"] == ["GCZ5"] and (tmp_path / "ES_1min.csv.gz").exists()
    assert set(discover(tmp_path)) == {"ES", "GC"}                      # the Lab finds them (and ignores its own output as day files)
    import pytest
    with pytest.raises(ValueError) as e:
        build_market(tmp_path, "SI", min_lead_days=3)                    # silver is not in the files
    assert "plan does not include" in str(e.value)
    with pytest.raises(ValueError):
        build_market(tmp_path / "empty", "ES")


def test_market_filter_decides_where_strategies_are_tested(tmp_path, bars_csv):
    lab = two_markets(tmp_path, bars_csv)
    assert lab.enabled_markets() == ["NQ", "ES"]                                         # default: every market with data
    st = lab.set_markets(["NQ"])
    assert lab.enabled_markets() == ["NQ"] and [m["enabled"] for m in st["markets"]] == [True, False]
    assert lab._add_pending_all(from_dict(SPEC), "paper") == 1
    assert [v["market"] for v in lab.pending.values()] == ["NQ"]                          # nothing queued for ES
    lab.set_markets(["NQ", "ES"])
    assert lab._add_pending_all(from_dict(SPEC), "paper") == 1                           # ES joins; NQ already queued
    assert sorted(v["market"] for v in lab.pending.values()) == ["ES", "NQ"]
    lab.set_markets(["ES"])                                                               # entries of a disabled market stay parked
    assert lab.autopilot_tick() is True and [j["market"] for j in lab.jobs.values()] == ["ES"]
    wait_all(lab)
    assert [v["market"] for v in lab.pending.values()] == ["NQ"]
    assert lab.autopilot_tick() is True                                                   # idle on ES: a classic family is seeded there
    last = list(lab.jobs.values())[-1]
    assert last["market"] == "ES" and last["name"].endswith("[ES]")                       # ...and the parked NQ entry did not start
    assert any(v["market"] == "NQ" and v["spec"].name == "ORB retest small" for v in lab.pending.values())
    wait_all(lab)
    assert lab.set_markets(["XX", "GC"])["markets"][0]["enabled"] is True                 # an invalid selection never leaves zero markets
    lab2 = two_markets(tmp_path, bars_csv)                                                # the choice survives a restart
    assert lab2.enabled_markets() == ["NQ"]
