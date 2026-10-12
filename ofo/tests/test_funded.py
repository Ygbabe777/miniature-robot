import numpy as np

from ofo.funded import FundedPlan, PayoutRules, account_ev, best_size, simulate_funded
from ofo.prop import PropRules


def plan(**kw):
    rules = PropRules("t", 50_000, 3_000, 2_000, min_days=1)
    pay = kw.pop("payout", PayoutRules(split=0.9, min_winning_days=3, min_day_profit=100, min_payout=500))
    return FundedPlan("F", "P", 50, rules, pay, eval_fee_monthly=100, activation_fee=50, max_contracts=10, **kw)


def test_payout_after_enough_winning_days_and_split():
    f = simulate_funded([300, 300, 300, 100], plan())
    assert f["n_payouts"] == 1 and f["gross"] == 900 and abs(f["net"] - 810) < 1e-9


def test_no_payout_if_account_busts_first():
    f = simulate_funded([300, 300, -2500], plan())
    assert f["status"] == "busted" and f["n_payouts"] == 0 and f["net"] == 0


def test_consistency_blocks_a_lopsided_payout():
    pay = PayoutRules(min_winning_days=3, min_day_profit=100, min_payout=500, consistency_pct=0.4)
    assert simulate_funded([1000, 150, 150], plan(payout=pay))["n_payouts"] == 0   # best day = 77% of profit
    assert simulate_funded([400, 400, 400], plan(payout=pay))["n_payouts"] == 1    # 33%


def test_buffer_keeps_money_in_the_account():
    pay = PayoutRules(min_winning_days=3, min_day_profit=100, min_payout=500, buffer=800)
    f = simulate_funded([300, 300, 300], plan(payout=pay))
    assert f["gross"] == 100 or f["n_payouts"] == 0     # only 900-800 = 100 is withdrawable < min_payout
    assert f["n_payouts"] == 0


def test_missing_fees_refuse_to_compute():
    p = FundedPlan("F", "P", 50, PropRules("t", 50_000, 3_000, 2_000), PayoutRules())
    try:
        account_ev(np.ones(50), p); assert False
    except ValueError as e:
        assert "eval_fee_monthly" in str(e)


def test_ev_orders_by_edge_and_size_is_bounded():
    """EV rises with edge. A zero-edge account is NOT necessarily negative: fees cap the
    downside while payouts are uncapped (an option), so only a clearly negative drift loses."""
    rng = np.random.default_rng(3)
    mk = lambda mu: rng.normal(mu, 350, 300)
    kw = dict(n_outer=20, n_inner=40)
    good, flat, bad = (account_ev(mk(mu), plan(), **kw)["ev"] for mu in (120, 0, -120))
    assert good > flat > bad
    assert bad < 0
    rows = best_size(mk(120), plan(), base_contracts=2, **kw)
    assert all(r["contracts"] <= 10 for r in rows)
