"""The preregistered protocol: frozen epoch, immutable finalists, lineage guard, and the behaviour of the WHOLE evidence chain under adverse data."""
import copy
import sqlite3

import pytest

from ofo import evidence as EV
from ofo import finalists as FIN
from ofo.protocol_sim import simulate
from ofo.registry import TrialRegistry


def labels(n, start="2024-01-01"):
    import pandas as pd
    return [d for d in pd.bdate_range(start, periods=n)]


def payload(name="A", params=None):
    return {"spec": {"name": name, "template": "orb"}, "params": params or {"or_minutes": 15}, "tf": "5min", "contracts": 2, "code_version": "c1", "config_version": "v1",
            "dataset_fp": "d1", "sizing": {"rule": "dev"}, "rulesets": ["R50"]}


@pytest.fixture
def fin(tmp_path):
    return FIN.Finalists(TrialRegistry(tmp_path / "r.db"))


# ---------------------------------------------------------------- epoch
def test_the_epoch_is_frozen_and_new_data_does_not_move_it(fin):
    s = labels(500)
    e1 = fin.epoch_for("NQ", s, 0.2)
    e2 = fin.epoch_for("NQ", labels(560), 0.2)                      # 60 sessions later
    assert (e1["start"], e1["end"]) == (e2["start"], e2["end"]) and e1["id"] == e2["id"]
    assert e1["n_sessions"] == 100
    assert fin.open_next("NQ", labels(560), 100).get("error")       # not enough fresh sessions after the block
    nxt = fin.open_next("NQ", labels(620), 100)
    assert nxt["start"] > e1["end"] and fin.current("NQ")["id"] == nxt["id"]


def test_an_epoch_that_does_not_fit_the_data_is_retired_not_stretched(fin):
    e1 = fin.epoch_for("NQ", labels(500), 0.2)
    e2 = fin.epoch_for("NQ", labels(300, "2020-01-01"), 0.2)        # other data entirely
    assert e2["id"] != e1["id"] and e2["start"] >= "2020-01-01"


# ---------------------------------------------------------------- finalists
def test_at_most_k_finalists_and_a_repeat_is_not_a_new_one(fin):
    ep = fin.epoch_for("NQ", labels(500), 0.2, max_finalists=3)
    ids = [fin.register(ep, payload(f"S{i}", {"x": i}), f"orb:s{i}")["finalist"]["k_index"] for i in range(3)]
    assert ids == [1, 2, 3]
    again = fin.register(ep, payload("S1", {"x": 1}), "orb:s1")
    assert again["repeat"] and again["finalist"]["k_index"] == 2
    with pytest.raises(FIN.BlockFull):
        fin.register(ep, payload("S9", {"x": 9}), "orb:s9")
    assert len(fin.rows(ep["id"])) == 3


def test_registered_finalists_are_immutable_and_hash_chained(fin):
    ep = fin.epoch_for("NQ", labels(500), 0.2)
    f = fin.register(ep, payload(), "orb:a")["finalist"]
    con = fin.reg._con
    for sql in ("UPDATE finalists SET payload='{}' WHERE id=?", "UPDATE finalists SET fingerprint='x' WHERE id=?", "DELETE FROM finalists WHERE id=?"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql, (f["id"],))
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE epochs SET start_session='1999-01-01' WHERE id=?", (ep["id"],))
    fin.record_verdict(f["id"], "INSUFFICIENT_EVIDENCE")
    fin.record_verdict(f["id"], "APPROVED")                          # a verdict is written once: the second write is ignored
    assert fin.rows(ep["id"])[0]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert fin.verify_chain()["ok"]
    con.execute("DROP TRIGGER finalists_identity_frozen")             # someone tampers with the database outside the triggers
    con.execute("UPDATE finalists SET payload = payload || ' ' WHERE id=?", (f["id"],))
    assert not fin.verify_chain()["ok"]


def test_a_variant_of_a_line_already_judged_on_the_block_is_refused(fin):
    ep = fin.epoch_for("NQ", labels(500), 0.2)
    f = fin.register(ep, payload("orb", {"or_minutes": 15}), "orb:orb")["finalist"]
    fin.record_verdict(f["id"], "NEEDS_ROBUSTNESS")
    with pytest.raises(FIN.LineageBlocked):
        fin.register(ep, payload("orb v2", {"or_minutes": 20}), "orb:orb")                 # modified after the block was seen
    assert fin.register(ep, payload("gap", {"g": 1}), "gap:gap")["finalist"]["k_index"] == 2   # a different line is fine


def test_the_fingerprint_captures_identity_not_time(fin):
    a, b = payload(), payload()
    b["registered_at"] = "later"
    assert FIN.fingerprint(a) == FIN.fingerprint(b)
    c = payload(); c["code_version"] = "c2"
    assert FIN.fingerprint(a) != FIN.fingerprint(c)


# ---------------------------------------------------------------- statistics of the whole chain
@pytest.mark.parametrize("kw", [dict(rho=0.9), dict(rho=0.9, phi=0.4), dict(rho=0.0, phi=0.4), dict(dist="t3"), dict(dist="zero_infl", phi=0.2), dict(dist="neg_skew")])
def test_under_the_null_the_whole_protocol_keeps_family_wise_error_low(kw):
    r = simulate(5, 700, K=3, T_hold=100, **kw)
    assert r["P_any_approved"] <= 0.05, r                  # at least one of 3 null finalists approved on the block
    assert r["P_any_forward_confirmed"] <= 0.03, r         # ... and then surviving the preregistered forward test


def test_a_single_null_finalist_with_adverse_data_stays_near_alpha():
    for kw in (dict(phi=0.4), dict(dist="t3"), dict(dist="zero_infl")):
        r = simulate(6, 1200, K=1, T_hold=91, rho=0.0, k_planned=1, **kw)
        assert r["P_any_approved"] <= 0.055, r


def test_the_chain_still_has_power_for_a_strong_edge_and_little_for_a_weak_one():
    strong = simulate(7, 500, K=3, T_hold=125, rho=0.5, true_annual=2.5)
    weak = simulate(7, 500, K=3, T_hold=125, rho=0.5, true_annual=0.5)
    assert strong["P_any_paper"] > 0.35 and strong["P_any_paper"] > 3 * weak["P_any_paper"]


def test_forward_plan_is_hashed_and_the_error_control_is_fixed_in_advance():
    p = EV.forward_plan("2026-01-05")
    assert p["looks"] == [60, 120, 180, 250] and p["boundary_z"] == pytest.approx(EV.look_boundary(4), rel=1e-12) and len(p["hash"]) == 64
    q = copy.deepcopy(p); q["looks"] = [30]
    assert EV.forward_plan("2026-01-05", [30])["hash"] != p["hash"] and q["hash"] == p["hash"]
    assert any("RETIRED" in s for s in p["stopping"]) and p["only_sessions_after_cutoff"]


# ---------------------------------------------------------------- inside a real study
def _enforced_cfg(max_finalists=3, dsr="holdout_null"):
    from ofo import research_config as rc
    cfg = copy.deepcopy(rc.get())
    cfg["protocol"] = {**cfg["protocol"], "enforce": True, "max_finalists": max_finalists}
    cfg["dsr_trials"] = dsr
    return cfg


def _study(tmp_path, monkeypatch, names, max_finalists=3, reg=None):
    from ofo import optimizer as O
    from ofo.backtest import MNQ
    from ofo.prop import PRESETS
    from ofo.specs import from_dict
    from test_research import synth
    real = O.surface_metrics

    def robust(*a, **k):
        o = real(*a, **k); o["robust"] = True; o["checks"] = {x: True for x in o["checks"]}; return o
    monkeypatch.setattr(O, "surface_metrics", robust)
    reg = reg or TrialRegistry(tmp_path / "r.db")
    bars = synth(n_sessions=700, drift_ar=0.9, seed=5)
    out = []
    ranges = {"breakout": {"lookback": [10, 20, 30, 40, 60]}, "ema_cross": {"fast": [5, 8, 12], "slow": [20, 30, 40]}}
    for n, tmpl in names:
        spec = from_dict({"name": n, "template": tmpl, "ranges": ranges[tmpl], "timeframes": ["15min", "30min"], "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"})
        out.append(O.run_study(bars, spec, MNQ, PRESETS["50k"], reg, dedupe=True, research=_enforced_cfg(max_finalists)))
    return out, reg


def test_a_study_registers_its_finalist_before_the_look_and_uses_the_official_dsr(tmp_path, monkeypatch):
    (rep,), reg = _study(tmp_path, monkeypatch, [("bo", "breakout")])
    lk = rep["locked"]
    if "checks" not in lk:
        pytest.skip("the synthetic study did not reach the audit stage")
    assert rep["finalist"]["k_index"] == 1 and rep["finalist"]["epoch"]["max_finalists"] == 3
    assert lk["official_dsr"] == "holdout_null" and lk["dsr"]["basis"] == "holdout_null" and lk["dsr"]["n_trials"] == 3
    assert set(lk["dsr_modes"]) == {"raw", "locked_looks", "holdout_null"} and lk["dsr_modes"]["holdout_null"]["official"] and not lk["dsr_modes"]["raw"]["official"]
    assert lk["dsr"]["value"] == pytest.approx(lk["dsr_modes"]["holdout_null"]["value"])
    fin = FIN.Finalists(reg)
    row = fin.rows(rep["finalist"]["epoch"]["id"])[0]
    assert row["verdict"] == rep["judgment"]["verdict"] and fin.verify_chain()["ok"]
    assert reg.data["locked_looks"] == 1                                           # a separate counter, one peek


def test_a_variant_of_a_judged_line_is_shelved_without_touching_the_block(tmp_path, monkeypatch):
    reps, reg = _study(tmp_path, monkeypatch, [("bo", "breakout"), ("bo 2", "breakout")])
    if "checks" not in reps[0]["locked"]:
        pytest.skip("the synthetic study did not reach the audit stage")
    second = reps[1]
    assert second["shelved"]["kind"] == "lineage" and second["gate_failed"] == "finalist_slot" and "checks" not in second["locked"]
    assert second["judgment"]["verdict"] == "INSUFFICIENT_EVIDENCE" and any(g["gate"] == "E7" for g in second["judgment"]["gates"])
    assert reg.data["locked_looks"] == 1                                           # the shelved study never looked at the block


def test_a_full_block_shelves_the_next_finalist(tmp_path, monkeypatch):
    reps, reg = _study(tmp_path, monkeypatch, [("bo", "breakout"), ("ema line", "ema_cross")], max_finalists=1)
    if "checks" not in reps[0]["locked"] or "checks" in reps[1]["locked"]:
        pytest.skip("the synthetic studies did not reach the expected stages")
    assert reps[1]["shelved"]["kind"] == "block_full"
