"""Deterministic judge. No LLM discretion: numbers and a checklist decide."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .prop import PropRules, pass_probability_ci
from .stats import deflated_sharpe


@dataclass
class Thresholds:
    min_days: int = 120
    min_dsr: float = 0.95
    min_dsr_candidate: float = 0.70     # not an approval: the line above which a rejection for DSR alone is reported as a NEAR MISS (see nearmiss.py)
    min_pass_rate: float = 0.30         # a challenge worth considering passes at least this often (the owner's rule: 30-35%)
    eval_days: int = 5                  # ... within this many sessions, at the best size that does not bust too often
    base_contracts: int = 2             # size the daily P&L series was produced with
    max_p_bust: float = 0.40            # at the base size, over a long horizon
    min_trades: int = 15                # a sample needs at least this many trades, whatever the days
    min_days_floor: int = 80            # ... and never fewer sessions than this
    min_trades_per_day: float = 0.2     # fewer days than min_days are fine when the strategy trades at least this often: one trade a week (the owner trades several strategies per account and has ~2 years of data)


APPROVED = "APPROVED"
REJECTED = "REJECTED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass
class Verdict:
    status: str
    checks: dict = field(default_factory=dict)
    mc: dict = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.status == APPROVED


def judge(oos_daily_pnl, rules: PropRules, n_trials: int, var_sr_trials: float,
          th: Thresholds | None = None, n_trades: int | None = None, dsr_value: float | None = None, dsr_n: int | None = None,
          dsr_var: float | None = None) -> Verdict:
    """Judge strictly on OUT-OF-SAMPLE daily P&L that no optimiser has ever seen."""
    th = th or Thresholds()
    x = list(oos_daily_pnl)
    if any(not np.isfinite(v) for v in x):                   # NaN/inf is an accounting failure, not evidence about the strategy
        return Verdict(EXECUTION_FAILED, {"finite": False}, {"sample": {"days": len(x), "non_finite": int(sum(1 for v in x if not np.isfinite(v)))}})
    days = len(x)
    trades = int(n_trades) if n_trades is not None else int(sum(1 for v in x if v != 0))      # no trade count given: active days (a lower bound)
    tpd = trades / days if days else 0.0
    long_enough = days >= th.min_days
    frequent = days >= th.min_days_floor and trades >= th.min_trades and tpd >= th.min_trades_per_day
    # FEW DAYS ARE NOT A DEFECT when the strategy trades at least once a day on average: the evidence lives in the trades, and the
    # bootstrap intervals below still carry the uncertainty. Few days AND sporadic trades (< 1 every two days) ARE a defect: too little to judge.
    checks = {"enough_days": bool(long_enough or frequent)}
    sample = {"days": days, "n_trades": trades, "trades_per_day": tpd, "days_waived": bool(frequent and not long_enough),
              "sporadic": bool(not long_enough and tpd < th.min_trades_per_day)}
    if not checks["enough_days"]:
        return Verdict(INSUFFICIENT, checks, {"sample": sample})
    # dsr_value: the OFFICIAL Deflated Sharpe computed by the caller (dsr_holdout, evidence.py). Without it the legacy sweep-based formula is used.
    dsr = float(dsr_value) if dsr_value is not None else float(deflated_sharpe(x, n_trials, var_sr_trials))
    checks["dsr"] = bool(dsr >= th.min_dsr)
    mc = pass_probability_ci(x, rules)                       # base size, long horizon: the interval shown to the owner + bust risk
    # PASS RATE: the owner's business rule. A strategy is worth considering when, at the best size that does not bust too
    # often, it passes the challenge within eval_days at least min_pass_rate of the time. Same function the dossier uses.
    from .dossier import choose, size_table
    best = choose(size_table(x, rules, th.base_contracts, th.eval_days))["pick"]
    mc["sample"] = sample
    mc["dsr"] = {"value": dsr, "n_trials": int(dsr_n if dsr_n is not None else n_trials), "var_sr": float(dsr_var if dsr_var is not None else var_sr_trials), "needed": th.min_dsr, "candidate_needed": th.min_dsr_candidate}
    mc["best_size"] = None if best is None else {k: best[k] for k in ("micro_equiv", "p_fast", "p_fast_lo", "p_fast_hi", "p_bust_fast")}
    checks["pass_rate"] = bool(best is not None and best["p_fast"] >= th.min_pass_rate)
    checks["bust_risk"] = bool(mc["p_bust"] <= th.max_p_bust)
    return Verdict(APPROVED if all(checks.values()) else REJECTED, checks, mc)
