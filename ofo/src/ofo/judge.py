"""Deterministic judge. No LLM discretion: numbers and a checklist decide."""
from __future__ import annotations

from dataclasses import dataclass, field

from .prop import PropRules, pass_probability_ci
from .stats import deflated_sharpe


@dataclass
class Thresholds:
    min_days: int = 120
    min_dsr: float = 0.95
    min_pass_rate: float = 0.30         # a challenge worth considering passes at least this often (the owner's rule: 30-35%)
    eval_days: int = 5                  # ... within this many sessions, at the best size that does not bust too often
    base_contracts: int = 2             # size the daily P&L series was produced with
    max_p_bust: float = 0.40            # at the base size, over a long horizon


APPROVED = "APPROVED"
REJECTED = "REJECTED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


@dataclass
class Verdict:
    status: str
    checks: dict = field(default_factory=dict)
    mc: dict = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.status == APPROVED


def judge(oos_daily_pnl, rules: PropRules, n_trials: int, var_sr_trials: float,
          th: Thresholds | None = None) -> Verdict:
    """Judge strictly on OUT-OF-SAMPLE daily P&L that no optimiser has ever seen."""
    th = th or Thresholds()
    x = list(oos_daily_pnl)
    checks = {"enough_days": len(x) >= th.min_days}
    if not checks["enough_days"]:
        return Verdict(INSUFFICIENT, checks)
    checks["dsr"] = bool(deflated_sharpe(x, n_trials, var_sr_trials) >= th.min_dsr)
    mc = pass_probability_ci(x, rules)                       # base size, long horizon: the interval shown to the owner + bust risk
    # PASS RATE: the owner's business rule. A strategy is worth considering when, at the best size that does not bust too
    # often, it passes the challenge within eval_days at least min_pass_rate of the time. Same function the dossier uses.
    from .dossier import choose, size_table
    best = choose(size_table(x, rules, th.base_contracts, th.eval_days))["pick"]
    mc["best_size"] = None if best is None else {k: best[k] for k in ("micro_equiv", "p_fast", "p_fast_lo", "p_fast_hi", "p_bust_fast")}
    checks["pass_rate"] = bool(best is not None and best["p_fast"] >= th.min_pass_rate)
    checks["bust_risk"] = bool(mc["p_bust"] <= th.max_p_bust)
    return Verdict(APPROVED if all(checks.values()) else REJECTED, checks, mc)
