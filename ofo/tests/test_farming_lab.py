import json

import numpy as np
import pandas as pd

from ofo.app.lab import Lab
from ofo.backtest import MNQ
from ofo.optimizer import farming_for_study
from ofo.storage import load_study, save_study
from test_research import synth


def _seed_study(lab, name="Old breakout", status="REJECTED"):
    bars = lab.bars()
    days = sorted(pd.Series(bars["session"].astype(str).str[:10]).unique())
    cut = days[int(len(days) * 0.6)]
    rep = {"name": name, "template": "breakout", "rationale": "x", "source": "t", "n_candidates": 10, "market": lab.primary,
           "surface": {"robust": True, "median_sharpe": 0.1, "plateau_ratio": 0.9, "n_combos": 10}, "quality": {"ok": True},
           "wf_oos_pnl": 100.0, "surface_detrended": {"robust": True},
           "locked": {"status": status, "checks": {"enough_days": True, "dsr": True}, "pnl": 500.0}}
    ser = {"cut": cut, "best_params": {"lookback": 20, "tf": "15min"}, "exit": None, "direction": "both"}
    return save_study(lab.work, {**rep, "series": ser}), cut


def test_a_study_the_judge_already_looked_at_is_reviewed_with_the_new_criterion_without_touching_oos_numbers(tmp_path):
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=260, drift_ar=0.9, seed=3).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    slug, cut = _seed_study(lab)
    assert lab.farming_pending() == [slug]
    before = load_study(lab.work, slug)["report"]["locked"]["pnl"]
    out = lab.farming_review(slug)
    d = load_study(lab.work, slug)
    lk = d["report"]["locked"]
    assert out["verdict"] in ("APPROVED", "REJECTED", "INSUFFICIENT_EVIDENCE") and lk["legacy_status"] == "REJECTED"
    assert lk["pnl"] == before and lk["reviewed_with"] == "prop_farming_v1"                  # OOS numbers untouched, old verdict kept
    assert d["report"]["farming"]["by_ruleset"] and lab.farming_pending() == []
    assert any(l.startswith("farming_review:") for l in [str(x) for x in lab.registry.data.get("locked_looks_labels", [])]) or lab.registry.data["locked_looks"] >= 1
    r = lab.ranking()
    assert r["evaluated"] == 1 and "note" in r


def test_decisions_are_the_owners_and_never_send_orders(tmp_path):
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=40, seed=3).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    assert lab.decide("x", "bogus")["error"]
    assert lab.decide("x", "live", "provo")["ok"] and lab.decisions()["x"]["decision"] == "live"
    ev = [e for e in lab.events.tail() if e["type"] == "decision"]
    assert "nessun ordine inviato" in ev[-1]["message"]
