"""Deterministic judge. No LLM discretion: numbers and a checklist decide."""
from __future__ import annotations

from dataclasses import dataclass, field

from .prop import PropRules, pass_probability
from .stats import deflated_sharpe


@dataclass
class Thresholds:
    min_days: int = 120
    min_dsr: float = 0.95
    min_p_pass_lower: float = 0.40      # lower bound of the 95% CI, not the point value
    max_p_bust: float = 0.40


@dataclass
class Verdict:
    approved: bool
    checks: dict = field(default_factory=dict)
    mc: dict = field(default_factory=dict)


def judge(oos_daily_pnl, rules: PropRules, n_trials: int, var_sr_trials: float,
          th: Thresholds | None = None) -> Verdict:
    """Judge strictly on OUT-OF-SAMPLE daily P&L that no optimiser has ever seen."""
    th = th or Thresholds()
    x = list(oos_daily_pnl)
    checks = {"enough_days": len(x) >= th.min_days}
    if not checks["enough_days"]:
        return Verdict(False, checks)
    checks["dsr"] = deflated_sharpe(x, n_trials, var_sr_trials) >= th.min_dsr
    mc = pass_probability(x, rules)
    checks["pass_prob_ci_lower"] = mc["p_pass_ci95"][0] >= th.min_p_pass_lower
    checks["bust_risk"] = mc["p_bust"] <= th.max_p_bust
    return Verdict(all(checks.values()), checks, mc)
