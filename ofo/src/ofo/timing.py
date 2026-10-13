"""How many days does it take to pass a challenge? Answered for EVERY strategy, on a 25k and on a 50k account.

For each account the size is first chosen as the one that passes most often within the goal window (a week = 5 sessions)
without busting too often in that window; then, at that size, two views of the same daily P&L:

  * simulated: thousands of evaluation attempts built by block-bootstrapping the days -> the fastest pass seen, the median,
    the 10th-90th percentile of days to pass, and the share of attempts that pass within 1, 2, 3, 5, 10 days;
  * historical: an attempt started on every session of the data -> the fastest real window that reached the target
    (these windows overlap: they are NOT independent samples).

The prop rules set a floor (PropRules.min_days): no strategy can pass faster than that. For studies the Judge has not looked
at, the numbers use DEVELOPMENT data only and are in-sample (optimistic, labelled so); after the Judge's look the same is
computed on the unseen block."""
from __future__ import annotations

import numpy as np

from .prop import PropRules, _resample_blocks, simulate_account

SIZES = tuple(range(1, 13)) + (15, 20)        # micro-contract equivalents: EVERY size from 1 to 12, then 15 and 20 (not just 1 and 10)
MAX_BUST_WINDOW = 0.65                           # a size that busts more often than this inside the window is not offered
CHECKPOINTS = (1, 2, 3, 5, 10)


def _p_within(days: list[int], n: int, d: int) -> float:
    return sum(1 for x in days if x <= d) / n if n else 0.0


PRUDENT_BUST = 0.20                              # the prudent profile accepts at most this bust rate inside the window


def best_size(pnl, rules: PropRules, base_contracts: int, window: int = 5, n_sims: int = 600, block: int = 3, seed: int = 0,
              max_bust: float = MAX_BUST_WINDOW) -> int:
    """Micro size with the highest pass probability inside `window` sessions among those that do not bust more than `max_bust`."""
    x = np.asarray(pnl, dtype=float)
    best, best_p = SIZES[0], -1.0
    for n in SIZES:
        rng = np.random.default_rng(seed)
        y = x * (n / base_contracts)
        passed = busted = 0
        for _ in range(n_sims):
            o = simulate_account(_resample_blocks(y, window, block, rng), rules)
            passed += o.status == "passed"
            busted += o.status == "busted"
        if busted / n_sims <= max_bust and passed / n_sims > best_p:
            best, best_p = n, passed / n_sims
    return best


def pass_timing(pnl, rules: PropRules, base_contracts: int, n_micro: int, window: int = 5, horizon: int = 20,
                n_sims: int = 1500, block: int = 3, seed: int = 1) -> dict:
    x = np.asarray(pnl, dtype=float) * (n_micro / base_contracts)
    rng = np.random.default_rng(seed)
    days, busted = [], 0
    for _ in range(n_sims):
        o = simulate_account(_resample_blocks(x, horizon, block, rng), rules)
        if o.status == "passed":
            days.append(o.days)
        elif o.status == "busted":
            busted += 1
    hist = []
    n_win = max(0, len(x) - horizon + 1)
    for s in range(n_win):
        o = simulate_account(x[s:s + horizon], rules)
        if o.status == "passed":
            hist.append(o.days)
    pct = (lambda q: float(np.percentile(days, q))) if days else (lambda q: None)
    return {"n_micro": int(n_micro), "window_days": window, "horizon_days": horizon, "rules_min_days": rules.min_days,
            "p_pass_horizon": len(days) / n_sims, "p_bust_horizon": busted / n_sims,
            "min_days_seen": min(days) if days else None, "mean_days": float(np.mean(days)) if days else None,
            "p10_days": pct(10), "median_days": pct(50), "p90_days": pct(90),
            "p_pass_within": {str(d): _p_within(days, n_sims, d) for d in CHECKPOINTS},
            "hist_windows": n_win, "hist_min_days": min(hist) if hist else None,
            "hist_p_pass_within_window": _p_within(hist, n_win, window), "windows_independent": False}


def timing_report(pnl, accounts: dict[str, PropRules], base_contracts: int, window: int = 5, min_pass_rate: float = 0.30,
                  basis: str = "development") -> dict:
    out = {"basis": basis, "in_sample": basis == "development", "window_days": window, "min_pass_rate": min_pass_rate, "accounts": {}}
    for acct, rules in accounts.items():
        n = best_size(pnl, rules, base_contracts, window)                    # the size that passes most often inside the window (up to 65% bust)
        t = pass_timing(pnl, rules, base_contracts, n, window)
        t["within_window_ok"] = bool(t["p_pass_within"].get(str(window), 0.0) >= min_pass_rate)
        # two ways to attack the evaluation, on the same daily P&L: AGGRESSIVE = the size above (more risk, faster, more busts);
        # PRUDENT = the best size that busts at most PRUDENT_BUST of the attempts inside the window.
        n_pru = best_size(pnl, rules, base_contracts, window, max_bust=PRUDENT_BUST, seed=7)
        t["profiles"] = {"aggressive": {"n_micro": int(n), **{k: t[k] for k in ("p_pass_horizon", "p_bust_horizon", "mean_days", "median_days", "min_days_seen")},
                                        "p_pass_window": t["p_pass_within"].get(str(window))},
                         "prudent": None}
        tp = t if n_pru == n else pass_timing(pnl, rules, base_contracts, n_pru, window)
        t["profiles"]["prudent"] = {"n_micro": int(n_pru), **{k: tp[k] for k in ("p_pass_horizon", "p_bust_horizon", "mean_days", "median_days", "min_days_seen")},
                                    "p_pass_window": tp["p_pass_within"].get(str(window))}
        out["accounts"][acct] = t
    return out


def describe(t: dict, lang: str = "it") -> str:
    """One readable paragraph for the dossier / notes."""
    parts = []
    for acct, r in t["accounts"].items():
        w = str(t["window_days"])
        if r["min_days_seen"] is None:
            parts.append(f"{acct}: " + ("nessuna simulazione arriva al target" if lang == "it" else "no simulated attempt reaches the target"))
            continue
        if lang == "it":
            parts.append(f"{acct} con {r['n_micro']} micro: minimo {r['min_days_seen']} giorni (regole: non meno di {r['rules_min_days']}), "
                         f"mediana {r['median_days']:.0f}, 10°-90° percentile {r['p10_days']:.0f}-{r['p90_days']:.0f}; "
                         f"entro {t['window_days']} giorni passi nel {r['p_pass_within'][w]:.0%} dei tentativi")
        else:
            parts.append(f"{acct} with {r['n_micro']} micro: fastest {r['min_days_seen']} days (rules: not less than {r['rules_min_days']}), "
                         f"median {r['median_days']:.0f}, 10th-90th percentile {r['p10_days']:.0f}-{r['p90_days']:.0f}; "
                         f"within {t['window_days']} days you pass {r['p_pass_within'][w]:.0%} of attempts")
    head = "Giorni per passare la challenge: " if lang == "it" else "Days to pass the challenge: "
    return head + "; ".join(parts) + "."
