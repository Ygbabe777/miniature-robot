import numpy as np

from ofo.backtest import MNQ
from ofo.optimizer import run_study
from ofo.prop import PRESETS
from ofo.registry import TrialRegistry
from ofo.specs import from_dict
from ofo.timing import best_size, describe, pass_timing, timing_report
from test_research import synth

BOTH = {k: PRESETS[k] for k in ("25k", "50k")}


def test_a_steady_strategy_cannot_pass_faster_than_the_rules_allow():
    x = np.full(60, 800.0)                                      # +$800 a day at 2 micro
    t = pass_timing(x, PRESETS["50k"], 2, 4, window=5)          # 4 micro: +$1,600 a day: target $3,000 on day 2
    assert t["min_days_seen"] == 2 == t["rules_min_days"] and t["median_days"] == 2
    assert t["p_pass_within"]["1"] == 0.0 and t["p_pass_within"]["2"] == 1.0 and t["hist_min_days"] == 2
    slow = pass_timing(x, PRESETS["50k"], 2, 1, window=5)       # +$400 a day: 8 days
    assert slow["min_days_seen"] == 8 and slow["p_pass_within"]["5"] == 0.0 and slow["p_pass_within"]["10"] == 1.0
    small = pass_timing(x, PRESETS["25k"], 2, 2, window=5)      # 25k target $1,500: $800 a day -> 2 days
    assert small["min_days_seen"] == 2


def test_losing_or_flat_strategies_get_an_honest_none_not_a_made_up_speed():
    rng = np.random.default_rng(3)
    for x in (rng.normal(-120, 300, 200), np.zeros(100)):
        r = timing_report(x, BOTH, 2)
        for acct in ("25k", "50k"):
            a = r["accounts"][acct]
            assert a["p_pass_within"]["5"] <= 0.15 and not a["within_window_ok"]       # at most a lottery ticket, far from 30%
            if a["min_days_seen"] is None:
                assert a["median_days"] is None and a["p10_days"] is None
        assert "nessuna simulazione" in describe(r, "it") or "passi nel" in describe(r, "it")


def test_best_size_grows_with_the_need_to_be_fast_but_respects_the_bust_cap():
    rng = np.random.default_rng(1)
    x = rng.normal(120, 300, 250)
    n = best_size(x, PRESETS["50k"], 2)
    assert n in (1, 2, 3, 4, 5, 6, 8, 10, 20) and n >= 2         # 5 days to make $3,000: needs more than the base size
    lottery = rng.normal(0, 300, 250)
    assert best_size(lottery, PRESETS["50k"], 2) >= 1            # zero-edge: whatever it picks, passing is variance, not skill


def test_report_has_both_accounts_with_a_goal_flag_and_readable_text():
    x = np.random.default_rng(1).normal(100, 300, 250)
    r = timing_report(x, BOTH, 2, window=5, min_pass_rate=0.30)
    assert set(r["accounts"]) == {"25k", "50k"} and r["in_sample"] is True and r["window_days"] == 5
    for a in r["accounts"].values():
        assert isinstance(a["within_window_ok"], bool) and a["windows_independent"] is False
    it, en = describe(r, "it"), describe(r, "en")
    assert "Giorni per passare la challenge" in it and "25k con" in it and "50k con" in it and "Days to pass the challenge" in en
    oos = timing_report(x, BOTH, 2, basis="unseen_block")
    assert oos["in_sample"] is False


def test_every_study_reports_timing_for_25k_and_50k_even_a_rejected_one(tmp_path):
    bars = synth(n_sessions=260, seed=4)
    spec = from_dict({"template": "ema_cross", "ranges": {"fast": [5, 9], "slow": [30, 60]}, "timeframes": ["15min", "30min"], "rationale": "test"})
    rep = run_study(bars, spec, MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"), train_days=60, test_days=10)
    assert rep["locked"]["status"] != "APPROVED"
    t = rep["timing"]
    assert t["basis"] == "development" and t["in_sample"] is True and set(t["accounts"]) == {"25k", "50k"}
    assert all("min_days_seen" in a and "p_pass_within" in a for a in t["accounts"].values())
    assert "timing" not in rep["locked"]                                  # the unseen block was not touched


def test_a_judged_study_also_reports_timing_on_the_unseen_block_and_the_dossier_quotes_it(tmp_path):
    from ofo.dossier import build_dossier
    from test_autopilot import RULES, report
    bars = synth(n_sessions=520, drift_ar=0.9, seed=5)
    spec = from_dict({"name": "bo", "template": "breakout", "ranges": {"lookback": [10, 20, 30, 40, 60]},
                      "timeframes": ["15min", "30min"], "rationale": "MECCANISMO x EVIDENZA y QUANDO NON FUNZIONA z"})
    rep = run_study(bars, spec, MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"))
    lt = rep["locked"]["timing"]
    assert lt["basis"] == "unseen_block" and lt["in_sample"] is False and set(lt["accounts"]) == {"25k", "50k"}
    d = build_dossier(report(150, 250), RULES, 2, 100)
    for acct in ("25k", "50k"):
        e = d["accounts"][acct]
        assert "timing" in e and e["timing"]["min_days_seen"] is not None and "Giorni per passare la challenge" in e["text"]
