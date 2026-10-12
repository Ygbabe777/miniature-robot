"""Funded-account economics: what is an account worth, and which size is best?

value of one attempt = P(pass) * E[payouts while funded] - fees paid.
Payout rules differ per firm and change often, so plans are DATA (rules/firms.json) and
the numbers there must be verified by you. The simulator never invents a missing fee."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .prop import PropRules, Trailing, _resample_blocks, simulate_account


@dataclass(frozen=True)
class PayoutRules:
    split: float = 0.9                   # trader's share of each payout
    min_winning_days: int = 5            # winning days needed since the last payout
    min_day_profit: float = 150.0        # a day counts as "winning" at/above this
    min_payout: float = 500.0
    max_payout: float | None = None
    buffer: float = 0.0                  # balance must stay above start_balance + buffer
    consistency_pct: float | None = None  # best day since last payout < pct of profit since


@dataclass(frozen=True)
class FundedPlan:
    firm: str
    plan: str
    size_k: int
    eval_rules: PropRules
    payout: PayoutRules
    eval_fee_monthly: float | None = None
    activation_fee: float | None = None
    max_contracts: int | None = None
    funded_rules: PropRules | None = None   # default: the evaluation rules without a target
    verified: bool = False
    sources: tuple = ()
    notes: str = ""

    def missing(self) -> list[str]:
        return [n for n in ("eval_fee_monthly", "activation_fee") if getattr(self, n) is None]


def simulate_funded(daily_pnl, plan: FundedPlan) -> dict:
    """Funded phase: trade until the account busts or the path ends, requesting a payout
    whenever the plan's conditions are met. Returns gross and trader-net payouts."""
    r = plan.funded_rules or replace(plan.eval_rules, profit_target=float("inf"))
    pr = plan.payout
    balance = r.start_balance
    peak, floor = balance, balance - r.max_drawdown
    since_profit = best_day = 0.0
    wins = n_pay = 0
    gross_total = 0.0
    status, days = "alive", 0
    for days, pnl in enumerate(daily_pnl, start=1):
        if r.daily_loss_limit is not None and pnl <= -r.daily_loss_limit:
            status = "busted"; break
        balance += pnl
        if balance <= floor:
            status = "busted"; break
        if r.trailing is Trailing.EOD and balance > peak:
            peak = balance
            floor = peak - r.max_drawdown
            if r.lock_at_start:
                floor = min(floor, r.start_balance)
        since_profit += pnl
        best_day = max(best_day, pnl)
        wins += pnl >= pr.min_day_profit
        if wins >= pr.min_winning_days and since_profit >= pr.min_payout:
            if pr.consistency_pct is None or best_day < pr.consistency_pct * since_profit:
                gross = min(since_profit, balance - (r.start_balance + pr.buffer),
                            pr.max_payout if pr.max_payout is not None else math.inf)
                if gross >= pr.min_payout:
                    balance -= gross
                    gross_total += gross
                    n_pay += 1
                    since_profit = best_day = 0.0
                    wins = 0
    return {"status": status, "days": days, "n_payouts": n_pay,
            "gross": gross_total, "net": gross_total * pr.split}


def _attempt(path_eval, path_funded, plan: FundedPlan) -> dict:
    ev = simulate_account(path_eval, plan.eval_rules)
    fees = math.ceil(max(ev.days, 1) / 21) * plan.eval_fee_monthly
    out = {"passed": ev.status == "passed", "net": 0.0, "paid": False}
    if out["passed"]:
        fees += plan.activation_fee
        f = simulate_funded(path_funded, plan)
        out.update(net=f["net"], paid=f["n_payouts"] > 0)
    out["fees"] = fees
    return out


def account_ev(daily_pnl, plan: FundedPlan, scale: float = 1.0, n_outer: int = 60, n_inner: int = 60,
               eval_horizon: int = 60, funded_horizon: int = 120, block: int = 5, seed: int = 0) -> dict:
    """Expected value of ONE attempt, with an interval that includes how little the
    history tells us (the history itself is re-drawn n_outer times)."""
    if plan.missing():
        raise ValueError(f"{plan.firm} {plan.plan}: fill in {plan.missing()} in rules/firms.json first")
    x = np.asarray(daily_pnl, dtype=float) * scale
    rng = np.random.default_rng(seed)
    evs, passes, paid, nets, fees = [], [], [], [], []
    for _ in range(n_outer):
        hist = _resample_blocks(x, len(x), block, rng)
        res = [_attempt(_resample_blocks(hist, eval_horizon, block, rng),
                        _resample_blocks(hist, funded_horizon, block, rng), plan) for _ in range(n_inner)]
        evs.append(np.mean([a["net"] - a["fees"] for a in res]))
        passes.append(np.mean([a["passed"] for a in res]))
        paid.append(np.mean([a["paid"] for a in res]))
        nets.append(np.mean([a["net"] for a in res]))
        fees.append(np.mean([a["fees"] for a in res]))
    evs = np.array(evs)
    p_pass = float(np.mean(passes))
    return {"ev": float(evs.mean()), "ev_ci95": (float(np.percentile(evs, 2.5)), float(np.percentile(evs, 97.5))),
            "p_ev_positive": float((evs > 0).mean()), "p_pass": p_pass,
            "p_payout": float(np.mean(paid)),
            "p_payout_given_pass": float(np.mean(paid) / p_pass) if p_pass > 0 else 0.0,
            "mean_net_payout": float(np.mean(nets)), "mean_fees": float(np.mean(fees)), "scale": scale}


def best_size(daily_pnl, plan: FundedPlan, base_contracts: int, scales=(0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4), **kw) -> list[dict]:
    """Scan position-size multipliers (relative to `base_contracts`) within the plan's
    contract cap, best expected value first. Bigger is not better: drawdown is fixed."""
    rows = []
    for s in scales:
        if plan.max_contracts is not None and base_contracts * s > plan.max_contracts:
            continue
        rows.append({**account_ev(daily_pnl, plan, scale=s, **kw), "contracts": base_contracts * s})
    return sorted(rows, key=lambda r: -r["ev"])


def load_plans(path: str | Path) -> list[FundedPlan]:
    plans = []
    for d in json.loads(Path(path).read_text())["plans"]:
        e = d["eval"]
        rules = PropRules(name=f"{d['firm']} {d['plan']}", start_balance=e["start_balance"],
                          profit_target=e["profit_target"], max_drawdown=e["max_drawdown"],
                          trailing=Trailing(e.get("trailing", "eod")), lock_at_start=e.get("lock_at_start", True),
                          daily_loss_limit=e.get("daily_loss_limit"), max_days=e.get("max_days"),
                          min_days=e.get("min_days") or 1, consistency_pct=e.get("consistency_pct"))
        p = {k: v for k, v in d.get("payout", {}).items() if v is not None}
        fees = d.get("fees", {})
        plans.append(FundedPlan(d["firm"], d["plan"], d["size_k"], rules, PayoutRules(**p),
                                fees.get("eval_fee_monthly"), fees.get("activation_fee"),
                                d.get("max_contracts"), None, d.get("verified", False),
                                tuple(d.get("sources", [])), d.get("notes", "")))
    return plans
