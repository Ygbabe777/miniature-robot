import json

import numpy as np
import pandas as pd
import pytest

from ofo import deploy as dep
from ofo.backtest import MNQ
from ofo.prop import PRESETS
from test_research import synth


def deployment(bars, oos_mean=50.0, oos_std=300.0, back=80):
    days = sorted(pd.to_datetime(bars["session"]).unique())
    return {"slug": "bo", "name": "BO", "template": "breakout", "tf": "15min", "params": {"lookback": 20},
            "exit": {"stop_atr": 2.0, "rr": 2.0}, "account": "50k", "micro": 2, "cut": str(days[-back])[:10],
            "oos_mean": oos_mean, "oos_std": oos_std, "status": "live"}


def test_zero_valued_expectations_are_values_not_missing_data():
    assert dep._scaled(0.0, 3) == 0.0 and dep._scaled(None, 3) is None and dep._scaled(10.0, 0.5) == 5.0
    # expected mean exactly 0: a persistent loss is judged against 0 (it used to be skipped by `x and y`-style checks)
    status, why = dep.assess([-60.0] * 45, 0.0, 100.0, 50_000)
    assert status == "retired" and "z=" in why
    # an unknown deviation or a zero deviation cannot produce a z-score: no verdict from it
    assert dep.assess([-60.0] * 45, 0.0, None, 50_000)[0] == "live" and dep.assess([-60.0] * 45, 0.0, 0.0, 50_000)[0] == "live"


def test_forward_update_works_with_zero_expectations_and_without_crashing():
    bars = synth(n_sessions=200, seed=4)
    d = deployment(bars, oos_mean=0.0, oos_std=0.0)
    r = dep.forward_update(bars, d, MNQ, 2, PRESETS["50k"])
    assert r["forward"]["days"] == 79 or r["forward"]["days"] > 70 and r["status"] in ("live", "retired", "confirmed")


def test_signal_log_is_append_only_hash_chained_and_tamper_evident(tmp_path):
    log = dep.SignalLog(tmp_path / "s.jsonl")
    t = {"entry_t": 1, "exit_t": 2, "side": 1, "entry_px": 100.0, "exit_px": 101.0, "pnl": 10.0, "session": "2025-01-02",
         "entry_session": "2025-01-02", "reason": "target"}
    added = log.sync("x", [t], "fp1")
    assert [r["kind"] for r in added] == ["entry", "exit"] and log.verify()["ok"]
    assert log.sync("x", [t], "fp1") == []                                   # syncing again adds nothing
    first_bytes = (tmp_path / "s.jsonl").read_bytes()
    # the dataset was rebuilt and the same trade now computes differently: the history stays, a restatement is appended
    t2 = {**t, "pnl": 25.0, "exit_px": 103.5}
    added = log.sync("x", [t2], "fp2")
    assert [r["kind"] for r in added] == ["restatement"] and added[0]["recorded_pnl"] == 10.0 and added[0]["recomputed_pnl"] == 25.0
    assert (tmp_path / "s.jsonl").read_bytes().startswith(first_bytes)       # earlier lines byte-for-byte unchanged
    assert log.sync("x", [t2], "fp2") == []                                  # the same restatement is not repeated
    assert [e["pnl"] for e in log.exits()] == [10.0]                         # P&L of record = what was recorded
    reopened = dep.SignalLog(tmp_path / "s.jsonl")
    assert reopened.verify()["ok"] and len(reopened.records) == len(log.records)
    lines = (tmp_path / "s.jsonl").read_text().splitlines()
    forged = json.loads(lines[1]); forged["pnl"] = 9999.0
    (tmp_path / "s.jsonl").write_text("\n".join([lines[0], json.dumps(forged)] + lines[2:]) + "\n")
    bad = dep.SignalLog(tmp_path / "s.jsonl").verify()
    assert bad["ok"] is False and bad["first_bad_seq"] == 1                  # an edit is detected at the line it touched


def test_open_trades_are_recorded_at_entry_and_only_closed_trades_count_as_pnl(tmp_path):
    log = dep.SignalLog(tmp_path / "s.jsonl")
    open_t = {"entry_t": 5, "exit_t": 9, "side": -1, "entry_px": 100.0, "exit_px": 98.0, "pnl": 3.0, "session": "2025-01-03",
              "entry_session": "2025-01-03", "reason": "open"}
    assert [r["kind"] for r in log.sync("x", [open_t], None)] == ["entry"] and log.exits() == []
    closed = {**open_t, "reason": "target", "pnl": 7.0}
    assert [r["kind"] for r in log.sync("x", [closed], None)] == ["exit"] and log.exits()[0]["pnl"] == 7.0


def test_refreshing_data_cannot_rewrite_the_paper_history(tmp_path):
    bars = synth(n_sessions=200, seed=4)
    d = deployment(bars)
    log = dep.SignalLog(tmp_path / "s.jsonl")
    r1 = dep.forward_update(bars, d, MNQ, 2, PRESETS["50k"], log)
    before = [x["hash"] for x in log.records]
    exits_before = {e["id"]: e["pnl"] for e in log.exits()}
    assert before and exits_before and r1["forward"]["days"] > 50 and r1["forward"]["signal_log"]
    key = pd.to_datetime(bars["session"])
    # (a) the continuous contract is back-adjusted after a roll: every OLDER price moves by a constant. P&L cannot change.
    shifted = bars.copy()
    old = np.asarray(key <= pd.Timestamp(d["cut"]) + pd.Timedelta(days=40))
    for c in ("open", "high", "low", "close"):
        shifted.loc[old, c] = shifted.loc[old, c] + 37.5
    r2 = dep.forward_update(shifted, d, MNQ, 2, PRESETS["50k"], dep.SignalLog(tmp_path / "s.jsonl"))
    assert abs(r2["forward"]["pnl"] - r1["forward"]["pnl"]) < 1e-6
    # (b) the vendor corrects past bars: the recomputed trades differ. History must not change.
    mask = np.asarray(key > pd.Timestamp(d["cut"]))
    alt = bars.copy()
    wob = np.cumsum(np.random.default_rng(0).normal(0, 0.8, mask.sum()))
    for c in ("open", "high", "low", "close"):
        alt.loc[mask, c] = alt.loc[mask, c].to_numpy() + wob
    r3 = dep.forward_update(alt, d, MNQ, 2, PRESETS["50k"], dep.SignalLog(tmp_path / "s.jsonl"))
    after = dep.SignalLog(tmp_path / "s.jsonl")
    assert [x["hash"] for x in after.records][:len(before)] == before                   # nothing already written changed
    assert {e["id"]: e["pnl"] for e in after.exits() if e["id"] in exits_before} == exits_before
    assert {x["kind"] for x in after.records} <= {"entry", "exit", "restatement", "late_trade"} and after.verify()["ok"]
    assert any(x["kind"] in ("late_trade", "restatement") for x in after.records)          # the disagreement is on record, not applied
    late = [x for x in after.records if x["kind"] == "late_trade"]
    assert all(x["id"] not in {e["id"] for e in after.exits()} for x in late)           # and never counted as P&L


def test_lab_deployment_writes_the_chained_log_and_reports_its_health(tmp_path):
    from test_autopilot import make
    from test_lab import bars_csv  # noqa: F401
    import test_lab
    csv = tmp_path / "bars.csv.gz"
    synth(n_sessions=200, seed=3).to_csv(csv)
    lab = make(tmp_path, str(csv))
    lab.bars()
    cut = str(lab._sessions[-60])[:10]
    lab.dossier_dir.mkdir(parents=True)
    d = {"slug": "bo", "name": "BO", "template": "breakout", "tf": "15min", "params": {"lookback": 20},
         "exits": {"chosen": {"stop_atr": 2.0, "rr": 2.0}}, "oos_mean": 0.0, "oos_std": 300.0,
         "accounts": {"50k": {"recommended": {"micro_equiv": 2}}}}
    (lab.dossier_dir / "bo.json").write_text(json.dumps(d))
    lab.deploy_ready()
    lab.deployments.get("bo")["cut"] = cut
    assert lab.forward_tick(force=True)
    log = dep.SignalLog(lab.data / "signals" / "bo.jsonl")
    assert log.records and log.verify()["ok"] and lab.deployments.get("bo")["forward"]["signal_log"] is True
    n = len(log.records)
    lab.forward_tick(force=True)                                                # a second pass must not duplicate anything
    assert len(dep.SignalLog(lab.data / "signals" / "bo.jsonl").records) == n
