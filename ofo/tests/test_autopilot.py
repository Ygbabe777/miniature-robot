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
    assert 0 <= r["p_pass_lo"] <= r["p_pass"] <= r["p_pass_hi"] <= 1 and 0 <= r["p_fast_lo"] <= r["p_fast"] <= r["p_fast_hi"] <= 1
    assert a["risk"]["daily_stop"] == PRESETS["50k"].max_drawdown / 3
    assert "micro" in a["text"] and "Gestione del rischio" in a["text"] and "NON verificate" in d["caveat"]
    assert {t["micro_equiv"] for t in a["table"]} >= {1, 10, 20}
    en = build_dossier(report(150, 250), RULES, 2, 1747, "en")
    assert "Risk management" in en["accounts"]["50k"]["text"]


def test_dossier_says_no_safe_size_when_every_size_busts():
    d = build_dossier(report(-400, 500), RULES, 2, 1000)
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


# ---- autonomous (paper) deployment ----
import pandas as pd
from ofo import deploy as dep
from ofo.backtest import MNQ


def test_assess_retires_on_drawdown_or_divergence_and_confirms_good_runs():
    bad = [-300] * 6                                   # -1800 vs allowed 2000: past 80%
    assert dep.assess(bad, 100, 200, 2000)[0] == "retired"
    drift = [-60.0] * 45                               # small losses but far below the expected +100/day
    assert dep.assess(drift, 100, 150, 5000)[0] == "retired"
    ok = [110.0, 90.0] * 35
    assert dep.assess(ok, 100, 150, 2000)[0] == "confirmed"
    assert dep.assess([50.0] * 10, 100, 150, 2000)[0] == "live"
    assert dep.assess([], 100, 150, 2000) == ("live", "")


def test_nothing_deployed_without_a_safe_size():
    d = {"slug": "x", "name": "x", "template": "orb", "tf": "15min", "params": {}, "accounts": {"50k": {"recommended": None}}}
    assert dep.make_deployment(d, "2025-01-01", "now") is None


def test_approved_strategy_is_deployed_and_updated_without_any_click(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    lab.bars()
    first_cut = str(lab._sessions[-60])[:10]                       # pretend the strategy was approved 60 sessions ago
    lab.dossier_dir.mkdir(parents=True)
    d = {"slug": "bo", "name": "BO", "template": "breakout", "tf": "15min", "params": {"lookback": 20},
         "exits": {"chosen": {"stop_atr": 2.0, "rr": 2.0}}, "oos_mean": 50.0, "oos_std": 300.0,
         "accounts": {"50k": {"recommended": {"micro_equiv": 2}}}}
    (lab.dossier_dir / "bo.json").write_text(json.dumps(d))
    assert lab.deploy_ready() == 1 and lab.deploy_ready() == 0     # idempotent
    item = lab.deployments.get("bo")
    assert item["status"] == "live" and item["micro"] == 2
    item["cut"] = first_cut
    assert lab.forward_tick(force=True) is True
    fw = lab.deployments.get("bo")["forward"]
    assert fw["days"] > 30 and len(fw["equity"]) == fw["days"] and "eval_if_traded" in fw
    assert lab.state()["deployed"] in (0, 1)
    assert "trader" not in lab.working_agents()                    # finished: nobody stuck "working"
    assert dep.Store(lab.data / "deployments.json").get("bo")      # persisted


def test_refresh_data_without_keys_is_a_clean_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("MASSIVE_S3_ACCESS_KEY", raising=False)
    assert dep.refresh_data(tmp_path, str(tmp_path / "b.csv.gz"), "NQ") == {"ok": False, "why": "no_keys"}


def test_refresh_downloads_new_days_and_rebuilds(tmp_path):
    day = tmp_path / "us_futures/min/2025/01/2025-01-06.csv.gz"
    day.parent.mkdir(parents=True)
    day.write_bytes(b"x")
    calls = {}

    def download(prefix, a, b, out):
        calls["args"] = (prefix, str(a), out)
        (tmp_path / "us_futures/min/2025/01/2025-01-07.csv.gz").write_bytes(b"y")

    def build(paths, root):
        calls["n"] = len(paths)
        idx = pd.date_range("2025-01-06 09:30", periods=30, freq="1min")
        return pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}, index=idx)

    out = dep.refresh_data(tmp_path, str(tmp_path / "NQ_1min.csv.gz"), "NQ", today=__import__("datetime").date(2025, 1, 8),
                           download=download, build=build)
    assert out == {"ok": True, "new_files": 1, "rebuilt": True} and calls["args"][0] == "us_futures/min/2025/01/" and calls["n"] == 2
    assert (tmp_path / "NQ_1min.csv.gz").exists()


def test_idle_lab_seeds_starters_then_brainstorms_and_starts_testing_by_itself(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    assert not lab.pending and lab.autopilot_state()["phase"] == "waiting"
    assert lab.autopilot_tick() is True                    # nothing queued: a classic family is seeded and started
    assert any(j["status"] in ("queued", "running", "done", "error") for j in lab.jobs.values())
    assert len(lab.pending) >= 1                           # the other starters wait their turn
    wait_jobs(lab)
    assert lab.autopilot_state()["phase"] in ("waiting", "paused")


def test_brainstorm_asks_the_researcher_with_the_memory_and_is_rate_limited(tmp_path, bars_csv):
    from test_knowledge import Fake
    lab = make(tmp_path, bars_csv, llm={"researcher": Fake({"strategies": [SPEC], "concepts": ["opening range"]})})
    assert lab.brainstorm(now=10_000.0) == 1
    assert list(lab.pending.values())[0]["origin"] == "brainstorm"
    assert lab.brainstorm(now=10_100.0) == 0               # at most one request every 20 minutes
    assert lab.working_agents() == set()


def test_a_huge_proposal_does_not_block_the_small_ones_behind_it(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    big = from_dict(SPEC)                                              # 16 candidates
    small = from_dict({**SPEC, "name": "Tiny", "ranges": {"or_minutes": [15], "retest_bars": [3], "tol": [0.0]},
                       "timeframes": ["15min"]})                       # 1 candidate
    lab._add_pending(big, "paper"); lab._add_pending(small, "paper")
    lab.ap["max_trials_per_day"] = 5
    assert lab.autopilot_tick() is True
    wait_jobs(lab)
    assert [j["name"] for j in lab.jobs.values()] == ["Tiny"] and [v["spec"].name for v in lab.pending.values()] == [big.name]
    assert lab.autopilot_tick() is False and lab.autopilot_state()["paused"] == "day"


def test_old_autopilot_caps_are_raised_on_upgrade(tmp_path, bars_csv):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "autopilot.json").write_text(json.dumps({"enabled": True, "max_trials_per_day": 4000, "max_trials_total": 60000}))
    lab = make(tmp_path, bars_csv)
    assert lab.ap["max_trials_per_day"] == 30000 and lab.ap["max_trials_total"] == 300000


def test_multi_account_probabilities_and_attempts_needed():
    from ofo import economics as eco
    m = eco.multi_account(0.2, 0.1, 0.3, 0.1, fees_per_attempt=100.0, n_max=5)
    assert abs(m["table"][0]["p_pass_any"] - 0.2) < 1e-9 and abs(m["table"][1]["p_pass_any"] - 0.36) < 1e-9
    assert m["table"][4]["p_pass_any_lo"] < m["table"][4]["p_pass_any"] < m["table"][4]["p_pass_any_hi"]
    assert eco.attempts_needed(0.2, 0.8) == 8 and eco.attempts_needed(0.5, 0.9) == 4 and eco.attempts_needed(0.0, 0.9) is None
    assert m["table"][4]["expected_cost"] < 500.0                      # you stop buying accounts once one passes
    assert abs(m["table"][0]["expected_cost"] - 100.0) < 1e-6


def test_dossier_has_payout_probability_costs_multi_account_and_50k_vs_25k():
    d = build_dossier(report(150, 250), RULES, 2, 1000)
    a = d["accounts"]["50k"]
    e = a["economics"]
    assert 0 <= e["p_payout"] <= e["p_pass"] + 1e-9 <= 1.0001 and e["mean_fees"] > 0 and e["eval_fee_monthly"] == 100.0
    assert len(a["multi"]["table"]) == 10 and "Probabilità di arrivare a un payout" in a["text"] and "NON sono indipendenti" in a["text"]
    assert d["comparison"] and ("50k" in d["comparison"] and "25k" in d["comparison"])
    custom = build_dossier(report(150, 250), RULES, 2, 1000, economics={"50k": {"eval_fee_monthly": 7.0, "activation_fee": 0.0,
                           "payout": {"split": 0.9, "min_winning_days": 5, "min_day_profit": 150.0, "min_payout": 500.0}}})
    assert custom["accounts"]["50k"]["economics"]["eval_fee_monthly"] == 7.0 and "economics" not in custom["accounts"]["25k"]
    assert custom["comparison"] is None                                  # no comparison unless both accounts have assumptions


def test_dossier_answers_whether_the_evaluation_can_be_passed_within_a_week():
    d = build_dossier(report(150, 250), RULES, 2, 1000)
    a = d["accounts"]["50k"]
    assert d["goal"] == {"max_eval_days": 5, "min_pass_rate": 0.30, "strong_pass_rate": 0.35} and d["fast_ok"] is True
    assert "entro 5 giorni" in a["text"] and "DA CONSIDERARE" in a["text"] and a["pass_class"] in ("strong", "considered") and "Costo per passare" in a["text"]
    assert a["economics"]["cost_per_pass"] > 0 and "50k" in d["comparison"] and "Costo atteso per un passaggio" in d["comparison"]
    slow = build_dossier(report(150, 250), RULES, 2, 1000, goal={"max_eval_days": 1, "min_pass_rate": 0.99})
    assert slow["fast_ok"] is False and "DA SCARTARE" in slow["headline"] and "DA SCARTARE" in slow["accounts"]["50k"]["text"]


def test_dossier_answers_how_many_accounts_which_size_and_what_it_costs():
    d = build_dossier(report(40, 300), RULES, 2, 1000)
    a, b = d["accounts"]["25k"], d["accounts"]["50k"]
    for x in (a, b):
        p = x["recommended"]["p_fast"]
        tb = x["multi"]["table"]
        assert abs(tb[0]["p_pass_any"] - p) < 1e-9 and abs(tb[1]["p_pass_any"] - (1 - (1 - p) ** 2)) < 1e-9     # 2nd attempt: cumulative
        assert abs(tb[2]["p_at_attempt"] - p * (1 - p) ** 2) < 1e-9 and tb[3]["cost_if_all_fail"] == 4 * x["economics"]["eval_fee_monthly"]
        assert x["multi"]["n_for_99"] >= x["multi"]["n_for_95"] >= x["multi"]["n_for_90"] >= 1
        assert "QUANTI CONTI" in x["text"] and "al 99%" in x["text"] and "spendi $" in x["text"]
    t = d["attempts_table"]
    assert len(t) == 10 and t[0]["p25_cum"] == a["multi"]["table"][0]["p_pass_any"] and t[1]["cost50_if_all_fail"] == 2 * b["economics"]["eval_fee_monthly"]
    assert d["comparison"].startswith("25k O 50k?") and ("più probabile con il 25k" in d["comparison"] or "più probabile con il 50k" in d["comparison"])
    assert "spesa massima" in d["comparison"] and "Costo atteso per un passaggio" in d["comparison"]
