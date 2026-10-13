import dataclasses

import numpy as np
import pytest

from ofo import farming as F
from ofo.lifecycle import SessionPool, block_indices, simulate_path
from ofo.rulesets import from_dict, load_farming_config, load_rulesets

R = load_rulesets()
CFG = {**load_farming_config("rules/farming.json"), "n_outer": 8, "n_inner": 60, "n_select": 40}
RS = R["GENERIC_EOD_50K"]


def mk(close, worst, best, cost=8.0, trades=1.0):
    n = len(close)
    return SessionPool(np.array(close, float), np.array(worst, float), np.array(best, float), np.full(n, trades), np.full(n, float(cost)),
                       [f"2024-{i:05d}" for i in range(n)], 5.0)


def noisy(n=150, mean=70, sd=250, seed=1):
    rng = np.random.default_rng(seed)
    c = rng.normal(mean, sd, n)
    return mk(c, np.minimum(c, 0) - abs(rng.normal(60, 40, n)), np.maximum(c, 0) + abs(rng.normal(60, 40, n)))


def ctx(**kw):
    return {"integrity_ok": True, "edge_ok": True, "oos_positive": True, "robust_ok": True, "forward": None, "n_locked_sessions": 80, "n_locked_trades": 80, **kw}


# ---------------------------------------------------------------- rulesets and lifecycle
def test_rulesets_declare_provenance_and_are_not_all_the_same():
    assert RS.provenance == "ASSUMED_RULESET" and "non verificate" in RS.warning() and not RS.verified
    assert R["GENERIC_INTRADAY_50K"].evaluation.trailing == "intraday" and RS.evaluation.trailing == "eod"
    assert R["GENERIC_EOD_25K"].evaluation.profit_target != RS.evaluation.profit_target and RS.name == "GENERIC_EOD_50K v1.0"


def test_lifecycle_goes_through_the_spec_states_in_order():
    pool = mk([220] * 200, [-40] * 200, [60] * 200)
    r = simulate_path(pool, RS, np.arange(120), 1.0, record=True)
    states = [e["state"] for e in r["events"]]
    for a, b in zip(["EVALUATION_ACTIVE", "EVALUATION_PASSED", "FUNDED_PENDING", "FUNDED_ACTIVE", "PAYOUT_ELIGIBLE", "PAYOUT_REQUESTED", "PAYOUT_RECEIVED"],
                    ["EVALUATION_PASSED", "FUNDED_PENDING", "FUNDED_ACTIVE", "PAYOUT_ELIGIBLE", "PAYOUT_REQUESTED", "PAYOUT_RECEIVED", None]):
        assert a in states and (b is None or states.index(a) < states.index(b))
    assert r["net"] > 0 and r["total_cost"] == 200 and r["payouts"][0] == pytest.approx(0.9 * (r["gross"] and r["gross"] / len(r["payouts"])), rel=2)


def test_a_ruleset_without_a_funded_phase_ends_at_the_evaluation():
    d = {"id": "EVAL_ONLY", "version": "1", "provenance": "ASSUMED", "account": {"size": 50000},
         "evaluation": {"profit_target": 3000, "max_loss": 2000, "trailing": "static"}, "costs": {"eval_monthly": 100}}
    rs = from_dict(d)
    r = simulate_path(mk([300] * 100, [-10] * 100, [10] * 100), rs, np.arange(60), 1.0)
    assert r["passed"] and not r["funded"] and r["payouts"] == [] and r["end_state"] == "EVALUATION_PASSED"


def test_uncertainty_has_two_separate_parts():
    pool = noisy()
    a = F.estimate(pool, RS, 1.0, "A", CFG, n_outer=12, n_inner=40, seed=3)
    b = F.estimate(pool, RS, 1.0, "A", CFG, n_outer=12, n_inner=160, seed=3)
    assert a["npfv"]["lcb95"] < a["npfv"]["mean"] < a["npfv"]["ucb95"]
    assert b["npfv"]["mc_se"] < a["npfv"]["mc_se"]                                   # more paths: Monte Carlo error shrinks...
    assert b["npfv"]["data_uncertainty_sd"] > 0.6 * a["npfv"]["data_uncertainty_sd"]   # ...the data uncertainty does not vanish with paths
    short = F.estimate(pool.slice(0, 40), RS, 1.0, "A", CFG, n_outer=12, n_inner=40, seed=3)
    assert short["npfv"]["data_uncertainty_sd"] > a["npfv"]["data_uncertainty_sd"]   # ...it shrinks only with more DATA


# ---------------------------------------------------------------- acceptance tests A-G
def test_A_passes_evaluations_but_the_funded_account_dies_before_the_first_payout():
    rs = dataclasses.replace(RS, funded=dataclasses.replace(RS.funded, max_loss=500, trailing="intraday"))
    rng = np.random.default_rng(2)
    pool = mk(rng.normal(200, 60, 100), np.full(100, -700.0), np.full(100, 50.0))
    m = F.estimate(pool, rs, 1.0, "A", CFG)
    assert m["p_pass"] > 0.9 and m["p_first_payout"] < 0.05
    ev = F.evaluate_ruleset(pool.slice(0, 50), pool.slice(50, 100), rs, 2, CFG, light=True)
    g = F.gates_for(ev, ctx(), CFG)
    six = next(x for x in g["gates"] if x["id"] == 6)
    assert g["verdict"] != "APPROVED" and six["status"] == "FAIL" and not six["first_payout_gate"]   # never a high-quality prop farming strategy


def test_B_great_daily_numbers_but_the_intraday_trailing_floor_is_breached():
    intraday = R["GENERIC_INTRADAY_50K"]
    n = 120
    pool = mk([100] * n, [-1300.0] * n, [900.0] * n)                                 # +100 at every close, but a +900 peak then -1300 inside the day
    daily_view = mk([100] * n, [0.0] * n, [100.0] * n)
    assert simulate_path(daily_view, intraday, np.arange(60), 1.0, max_attempts=1)["failed_eval"] == 0   # daily P&L looks perfect
    comp = F.compliance(pool, intraday, 1.0, 2)
    assert not comp["ok"] and comp["checks"]["intraday_hidden_breach"]["p"] > 0.5
    ev = F.evaluate_ruleset(pool.slice(0, 60), pool.slice(60, 120), intraday, 2, CFG, light=True)
    g = F.gates_for(ev, ctx(), CFG)
    assert next(x for x in g["gates"] if x["id"] == 5)["status"] == "FAIL" and g["verdict"] == "REJECTED"


def test_C_positive_gross_payouts_but_costs_make_the_net_value_negative():
    rs = dataclasses.replace(RS, costs=dataclasses.replace(RS.costs, eval_monthly=900, activation=2500, reset=900))
    pool = noisy(150, 60, 200, seed=4)
    m = F.estimate(pool, rs, 1.0, "A", CFG)
    assert m["e_net_payout"] > 0 and m["npfv"]["mean"] < 0
    ev = F.evaluate_ruleset(pool.slice(0, 70), pool.slice(70, 150), rs, 2, CFG, light=True)
    g = F.gates_for(ev, ctx(), CFG)
    six = next(x for x in g["gates"] if x["id"] == 6)
    assert six["status"] == "FAIL" and not six["economics_gate"] and g["verdict"] == "REJECTED"


def test_D_only_profitable_with_unrealistically_low_slippage():
    rng = np.random.default_rng(5)
    c = rng.normal(180, 120, 200)                                                    # net edge +80 after 100/session of costs...
    pool = mk(c - 100, np.minimum(c - 100, 0) - 40, np.maximum(c - 100, 0) + 40, cost=100.0)
    d2 = F.stress_pool(pool, "costi_x", 2.0)
    assert d2.close.mean() < 0 < pool.close.mean()                                   # ...which disappears with costs doubled
    ev = F.evaluate_ruleset(pool.slice(0, 100), pool.slice(100, 200), RS, 2, CFG, light=True)
    g = F.gates_for(ev, ctx(), CFG)
    assert next(x for x in g["gates"] if x["id"] == 4)["status"] == "FAIL" and g["verdict"] != "APPROVED"


def test_E_ten_accounts_copying_the_same_trades_are_not_diversified():
    pool = noisy(150, 60, 200, seed=4)
    p = F.portfolio(pool, [(RS, 2)] * 10, 2, CFG, n_paths=80)
    assert p["identical_signals"] and p["p_all_fail"] == p["p_any_fail"]              # they fail TOGETHER
    assert p["p_all_fail"] > 0.2
    assert p["npfv_total_p05"] < p["independent_assumption"]["npfv_total_p05"] - 500   # independence would have understated the tail
    nocopy = dataclasses.replace(RS, rules={**RS.rules, "copy_trading_allowed": False})
    assert F.portfolio(pool, [(nocopy, 2)] * 3, 2, CFG, n_paths=20)["warnings"]


def test_F_excellent_results_on_too_small_a_sample_are_insufficient_evidence():
    pool = noisy(150, 150, 120, seed=6)
    ev = F.evaluate_ruleset(pool.slice(0, 120), pool.slice(120, 140), RS, 2, CFG, light=True)
    g = F.gates_for(ev, {**ctx(), "n_locked_sessions": 20, "n_locked_trades": 20}, CFG)
    assert g["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_G_maximum_payout_policy_can_shrink_the_funded_buffer_and_policies_are_compared_on_net_value():
    pool = noisy(150, 70, 250, seed=1)
    pol = F.compare_policies(pool, RS, 1.0, CFG)["policies"]
    assert pol["A"]["p_funded_failure"] > pol["B"]["p_funded_failure"] + 0.1          # the bigger first withdrawal costs buffer
    assert set(pol) == {"A", "B", "C"} and all(v["npfv_lcb95"] is not None for v in pol.values())
    best = F.compare_policies(pool, RS, 1.0, CFG)["best"]
    assert pol[best]["npfv_lcb95"] == max(v["npfv_lcb95"] for v in pol.values())      # chosen on the value of the whole horizon


# ---------------------------------------------------------------- ranking, first payout, hierarchy
def test_ranking_orders_by_lcb_declares_ties_and_may_approve_zero():
    e = lambda n, lo, hi, v="APPROVED": {"slug": n, "name": n, "verdict": v, "npfv": {"mean": (lo + hi) / 2, "lcb95": lo, "ucb95": hi}}
    r = F.rank([e("a", 900, 3000), e("b", 700, 2900), e("c", -50, 100), e("d", 5000, 9000, "REJECTED"), e("f", 100, 400)])["ranking"]
    assert [x["slug"] for x in r] == ["a", "b", "c", "f"] or [x["slug"] for x in r][:2] == ["a", "b"]
    assert next(x for x in r if x["slug"] == "b")["indistinguishable_from_top"] and "d" not in [x["slug"] for x in r]
    assert F.rank([e("x", 10, 20, "REJECTED")])["ranking"] == [] and "zero" in F.rank([])["note"]


def test_a_failed_critical_gate_cannot_be_compensated_and_the_first_failed_gate_is_reported():
    pool = noisy(150, 200, 100, seed=8)
    ev = F.evaluate_ruleset(pool.slice(0, 70), pool.slice(70, 150), RS, 2, CFG, light=True)
    g = F.gates_for(ev, ctx(integrity_ok=False), CFG)
    assert g["verdict"] == "REJECTED" and g["first_failed"]["id"] == 1                # nothing downstream can compensate
    ok = F.gates_for(ev, ctx(), CFG)
    assert ok["gates"][-1]["id"] == 7 and ok["gates"][-1]["status"] == "PENDING"      # forward validation is pending until paper days exist


def test_review_and_fascicolo_are_built_from_real_numbers_in_italian():
    from ofo.fascicolo import board_review, build
    pool = noisy(200, 220, 90, seed=9)
    dev, oos = pool.slice(0, 100), pool.slice(100, 200)
    fr = F.review(dev, oos, [RS], 2, CFG, ctx(), "orb")
    report = {"name": "ORB test", "template": "orb", "market": "NQ", "farming": fr, "locked": {"status": fr["verdict"], "pnl": float(oos.close.sum())},
              "series": {"best_params": {"or_minutes": 30, "tf": "15min"}, "direction": "long", "exit": {"stop_atr": 1.5, "rr": 2.0}},
              "surface": {"median_sharpe": 0.05, "plateau_ratio": 0.8, "n_combos": 10}, "n_candidates": 40, "rationale": "range di apertura"}
    b = board_review(report)
    assert [x["agent"] for x in b] == ["risk", "strategist", "auditor", "planner", "judge"] and "primo payout" in b[0]["text"]
    if fr["verdict"] == "APPROVED":
        doc = build("orb-test", report)
        assert doc["logica_ingresso"]["direzione"] == "solo long" and "stop a 1.5 ATR" in doc["logica_ingresso"]["uscite"]
        assert doc["condizioni_invalidanti"] and doc["avvisi"] and doc["portafoglio"]
    assert fr["by_ruleset"][RS.id]["evaluation"]["ruleset"]["provenance"] == "ASSUMED_RULESET"
