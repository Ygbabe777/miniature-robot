"""Approximate economics of an approved strategy per account size: payout probability, costs, several attempts.

Everything about fees and payout rules here is an ASSUMPTION, not any firm's real price list: prop firms change
fees and rules constantly and their sites disagree. The defaults are round, illustrative numbers; put the real ones
in rules/economics.json. The probabilities come from the same bootstrap as the Judge (out-of-sample days)."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

from .funded import FundedPlan, PayoutRules, account_ev
from .prop import PropRules

ASSUMED = {
    "50k": {"eval_fee_monthly": 100.0, "activation_fee": 100.0,
            "payout": {"split": 0.9, "min_winning_days": 5, "min_day_profit": 150.0, "min_payout": 500.0}},
    "25k": {"eval_fee_monthly": 60.0, "activation_fee": 80.0,
            "payout": {"split": 0.9, "min_winning_days": 5, "min_day_profit": 100.0, "min_payout": 250.0}},
}


def load_assumptions(path: str | Path | None) -> dict:
    out = json.loads(json.dumps(ASSUMED))
    if path and Path(path).exists():
        for k, v in json.loads(Path(path).read_text()).items():
            if k in out and isinstance(v, dict):
                out[k]["payout"].update(v.get("payout", {}))
                for f in ("eval_fee_monthly", "activation_fee"):
                    if v.get(f) is not None:
                        out[k][f] = float(v[f])
    return out


def account_economics(pnl, rules: PropRules, base_contracts: int, n_micro: int, assume: dict, seed: int = 0) -> dict:
    plan = FundedPlan("assumed", rules.name, int(rules.start_balance // 1000), rules, PayoutRules(**assume["payout"]),
                      assume["eval_fee_monthly"], assume["activation_fee"])
    r = account_ev(pnl, plan, scale=n_micro / base_contracts, n_outer=40, n_inner=40, seed=seed)
    return {"p_pass": r["p_pass"], "p_payout": r["p_payout"], "p_payout_given_pass": r["p_payout_given_pass"],
            "mean_fees": r["mean_fees"], "mean_net_payout": r["mean_net_payout"], "ev": r["ev"], "ev_ci95": r["ev_ci95"],
            "eval_fee_monthly": assume["eval_fee_monthly"], "activation_fee": assume["activation_fee"]}


def attempts_needed(p: float, target: float) -> int | None:
    if p <= 0:
        return None
    if p >= 1:
        return 1
    return max(1, math.ceil(math.log(1 - target) / math.log(1 - p)))


def multi_account(p_pass: float, p_lo: float, p_hi: float, p_payout: float, fees_per_attempt: float, n_max: int = 10,
                  activation: float = 0.0) -> dict:
    """Several attempts, one after another at different dates, so each is an independent draw. P(at least one) =
    1-(1-p)^N. Accounts opened TOGETHER and copying the same trades are strongly correlated and do not follow this."""
    rows = []
    for n in range(1, n_max + 1):
        used = (1 - (1 - p_pass) ** n) / p_pass if p_pass > 0 else float(n)       # expected attempts actually bought
        rows.append({"accounts": n, "p_pass_any": 1 - (1 - p_pass) ** n, "p_pass_any_lo": 1 - (1 - p_lo) ** n,
                     "p_pass_any_hi": 1 - (1 - p_hi) ** n, "p_payout_any": 1 - (1 - p_payout) ** n,
                     "expected_cost": fees_per_attempt * used + activation * (1 - (1 - p_pass) ** n)})
    return {"table": rows, "n_for_80": attempts_needed(p_pass, 0.8), "n_for_90": attempts_needed(p_pass, 0.9),
            "n_for_95": attempts_needed(p_pass, 0.95)}


def load_goal(path: str | Path | None) -> dict:
    """The owner's goal from rules/economics.json: {"goal": {"max_eval_days": 5, "min_p_fast_lower": 0.25}}."""
    if path and Path(path).exists():
        g = json.loads(Path(path).read_text()).get("goal") or {}
        return {k: g[k] for k in ("max_eval_days", "min_p_fast_lower") if k in g}
    return {}
