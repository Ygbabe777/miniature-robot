"""What the Judge rejected, WHY, and what can honestly be done about it.

Three tools, none of which lowers a standard:

* `consistency`  - how steadily a strategy earns: share of winning days, longest losing streak, share of winning weeks. The owner favours
  steady winning days and a high pass rate, so these are reported for every study and used to put FAVOURITES first in the ranking.
* `near_miss`    - a study the Judge rejected ONLY because its deflated Sharpe fell short (everything else passed: unseen block profitable,
  pass rate, bust risk, compliance, first payout, costs, at least the minimum number of backtest trades), with a DSR still above a candidate
  line. It stays REJECTED. The owner may send it to PAPER trading: fresh forward days are real out-of-sample evidence that no amount of
  past data can replace. If the paper days confirm it, it is promoted; if not, it is retired. The Judge never bends; the evidence grows.
* `dsr_power`    - why a 90-day unseen block rejects good strategies: the Sharpe a strategy must show to reach a given DSR, by block length
  and number of trials. This is arithmetic, not opinion."""
from __future__ import annotations

import numpy as np
from scipy import stats as st

from .stats import expected_max_sharpe


def consistency(daily) -> dict:
    x = np.asarray(daily, dtype=float)
    act = x[x != 0]
    if len(act) == 0:
        return {"active_days": 0, "winning_days_pct": None, "max_losing_streak": 0, "winning_weeks_pct": None}
    streak = cur = 0
    for v in x:
        cur = cur + 1 if v < 0 else 0
        streak = max(streak, cur)
    weeks = [float(c.sum()) for c in np.array_split(x, max(1, len(x) // 5)) if len(c)]
    return {"active_days": int(len(act)), "winning_days_pct": float((act > 0).mean()), "max_losing_streak": int(streak),
            "winning_weeks_pct": float(np.mean([w > 0 for w in weeks])) if weeks else None}


def is_favorite(cons: dict, pass_rate_ok: bool, cfg: dict) -> bool:
    wd = cons.get("winning_days_pct")
    return bool(pass_rate_ok and wd is not None and wd >= float(cfg.get("min_winning_days", 0.50))
                and cons.get("max_losing_streak", 99) <= int(cfg.get("max_losing_streak", 7)))


def near_miss(report: dict, cfg: dict) -> dict | None:
    """None unless the ONLY thing missing is the deflated-Sharpe confidence, and it is not far from the bar."""
    lk = report.get("locked") or {}
    checks = lk.get("checks") or {}
    if lk.get("status") != "REJECTED" or not checks:
        return None                                            # the Judge must have looked (and not for a lack of sample)
    dsr = (lk.get("dsr") or {}).get("value")
    if dsr is None or dsr < float(cfg.get("min_dsr", 0.70)):
        return None
    if any(not ok for k, ok in checks.items() if k != "dsr") or checks.get("dsr", True):
        return None                                            # something else failed too, or DSR was not the problem
    bt = lk.get("backtest_trades") or {}
    if bt and not bt.get("ok", True):
        return None
    fr = report.get("farming") or {}
    best = fr.get("best_ruleset")
    gates = (((fr.get("by_ruleset") or {}).get(best) or {}).get("gates") or {}).get("gates") if best else None
    if gates:
        others = [g for g in gates if g["id"] <= 6 and g["id"] != 2]
        if any(g["status"] != "PASS" for g in others):
            return None
    if not lk.get("pnl", 0) > 0:
        return None
    return {"gate": "edge", "dsr": float(dsr), "needed": float((lk.get("dsr") or {}).get("needed", 0.95)),
            "why": "tutto il resto è passato: manca solo la confidenza statistica (Sharpe deflazionato) sul blocco nascosto"}


def dsr_power(n_days: int, n_trials: int, var_sr: float, target: float = 0.95) -> dict:
    """Smallest true daily Sharpe (and its annualised value) whose expected DSR reaches `target`, solved on the normal approximation:
    PSR = Phi((SR - SR0) * sqrt(n-1)), SR0 = expected max Sharpe of n_trials. Skew and kurtosis ignored: a floor, not a promise."""
    sr0 = expected_max_sharpe(n_trials, var_sr)
    z = float(st.norm.ppf(target))
    need = sr0 + z / np.sqrt(max(1, n_days - 1))
    return {"daily_sharpe": float(need), "annual_sharpe": float(need * np.sqrt(252)), "luck_benchmark_daily": float(sr0)}


def power_table(var_sr: float = 0.001) -> list[dict]:
    rows = []
    for days in (60, 90, 120, 250, 500):
        for n in (1, 10, 100, 1000):
            a, b = dsr_power(days, n, var_sr, 0.95), dsr_power(days, n, var_sr, 0.70)
            rows.append({"days": days, "trials": n, "annual_sharpe_for_95": round(a["annual_sharpe"], 2), "annual_sharpe_for_70": round(b["annual_sharpe"], 2)})
    return rows
