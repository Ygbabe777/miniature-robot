"""Prop-firm evaluation on BAR-LEVEL equity, with unrealised P&L and intraday drawdown rules.

prop.simulate_account sees one number per day (SIMPLIFIED model). Real firms measure the drawdown against equity that
moves all day, including the open position's unrealised profit and loss. This module replays an account bar by bar:

  * static   - the floor never moves: start_balance - max_drawdown
  * eod      - the floor trails the highest END-OF-SESSION balance (locks at the start balance if rules.lock_at_start)
  * intraday - the floor trails the highest equity reached AT ANY MOMENT, unrealised profit included (same lock rule):
               a trade that runs +$800 and comes back to flat has already raised the floor by $800
  * the daily loss limit is checked against the intraday low, so a violation while a position is open is a breach
  * the profit target and the consistency rule are evaluated at the end of the session

Inside a bar the order of highs and lows is unknown: it is resolved against the account (favourable extreme first, so
the trailing floor rises, then the adverse extreme). The result is therefore a conservative estimate. The arrays it
needs (per-bar net P&L, worst and best unrealised P&L) come from the execution ledger."""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from .prop import PropRules, Trailing, simulate_account

MODES = ("static", "eod", "intraday")


@dataclasses.dataclass
class BarArrays:
    pnl: np.ndarray            # net P&L of each bar (gross - costs)
    worst: np.ndarray          # worst unrealised P&L reached inside the bar while holding (vs previous close)
    best: np.ndarray           # best unrealised P&L reached inside the bar
    bounds: list               # [(start, end)] bar-index range of each session, in order
    sessions: list             # session label of each range

    def scaled(self, k: float) -> "BarArrays":
        return BarArrays(self.pnl * k, self.worst * k, self.best * k, self.bounds, self.sessions)

    def daily(self) -> np.ndarray:
        return np.array([self.pnl[a:b].sum() for a, b in self.bounds])


def arrays_from_ledger(bars: pd.DataFrame, led, only_from=None) -> BarArrays:
    key = (pd.to_datetime(bars["session"]) if "session" in bars.columns else pd.Series(bars.index.normalize(), index=bars.index))
    k = key.to_numpy()
    starts = np.flatnonzero(np.concatenate([[True], k[1:] != k[:-1]]))
    ends = np.concatenate([starts[1:], [len(k)]])
    keep = [(int(a), int(b), k[a]) for a, b in zip(starts, ends) if only_from is None or k[a] >= np.datetime64(only_from)]
    return BarArrays(led.pnl.to_numpy(), led.worst.to_numpy(), led.best.to_numpy(),
                     [(a, b) for a, b, _ in keep], [s for _, _, s in keep])


def simulate_intraday(arr: BarArrays, rules: PropRules, mode: str | None = None, start: int = 0,
                      max_sessions: int | None = None) -> dict:
    """One evaluation attempt starting at session index `start`. Returns
    {"status": passed|busted|timeout, "sessions", "final_balance", "mode", "model": "intraday", "breach": {...}|None}."""
    mode = mode or rules.trailing.value
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    dd, start_bal = rules.max_drawdown, rules.start_balance
    bal, floor = start_bal, start_bal - dd
    peak_all = peak_close = start_bal
    best_day, n = 0.0, 0
    stop = len(arr.bounds) if max_sessions is None else min(len(arr.bounds), start + max_sessions)
    for s in range(start, stop):
        a, b = arr.bounds[s]
        n += 1
        eq_close = bal + np.cumsum(arr.pnl[a:b])
        eq_prev = np.concatenate([[bal], eq_close[:-1]])
        worst_eq = np.minimum(eq_prev + arr.worst[a:b], eq_close)
        best_eq = np.maximum(eq_prev + arr.best[a:b], eq_close)
        if mode == "intraday":
            floors = np.maximum.accumulate(np.maximum(peak_all, best_eq)) - dd      # favourable extreme first
            if rules.lock_at_start:
                floors = np.minimum(floors, start_bal)
            floors = np.maximum(floors, floor)
        else:
            floors = np.full(len(eq_close), floor)
        trail_hit = worst_eq <= floors
        dl_hit = (bal - worst_eq) >= rules.daily_loss_limit if rules.daily_loss_limit is not None else np.zeros(len(eq_close), bool)
        hit = np.flatnonzero(trail_hit | dl_hit)
        if len(hit):
            i = int(hit[0])
            kind = "daily_loss_limit" if (dl_hit[i] and not trail_hit[i]) else f"{mode}_drawdown"
            open_pos = bool(arr.worst[a + i] != 0 or arr.best[a + i] != 0)
            return {"status": "busted", "sessions": n, "final_balance": float(worst_eq[i]), "mode": mode, "model": "intraday",
                    "breach": {"session": str(arr.sessions[s])[:10], "bar": a + i, "kind": kind, "equity": float(worst_eq[i]),
                               "floor": float(floors[i]), "while_position_open": open_pos,
                               "closed_below_floor": bool(eq_close[i] <= floors[i])}}
        day = float(eq_close[-1] - bal)
        best_day = max(best_day, day)
        bal = float(eq_close[-1])
        if mode == "intraday":
            peak_all = max(peak_all, float(best_eq.max()), bal)
            nf = peak_all - dd
            floor = max(floor, min(nf, start_bal) if rules.lock_at_start else nf)
        elif mode == "eod":
            peak_close = max(peak_close, bal)
            nf = peak_close - dd
            floor = max(floor, min(nf, start_bal) if rules.lock_at_start else nf)
        profit = bal - start_bal
        if profit >= rules.profit_target and n >= rules.min_days and (
                rules.consistency_pct is None or best_day < rules.consistency_pct * profit):
            return {"status": "passed", "sessions": n, "final_balance": bal, "mode": mode, "model": "intraday", "breach": None}
        if rules.max_days is not None and n >= rules.max_days:
            return {"status": "timeout", "sessions": n, "final_balance": bal, "mode": mode, "model": "intraday", "breach": None}
    return {"status": "timeout", "sessions": n, "final_balance": bal, "mode": mode, "model": "intraday", "breach": None}


def historical_attempts(arr: BarArrays, rules: PropRules, mode: str, horizon: int = 30) -> dict | None:
    """Start an evaluation on every session of the data (windows overlap, so they are NOT independent samples) and compare
    the intraday result with the simplified daily simulator on the same windows."""
    n = len(arr.bounds)
    if n < 10:
        return None
    daily = arr.daily()
    daily_rules = dataclasses.replace(rules, trailing=Trailing.EOD if mode == "intraday" else Trailing(mode))
    out = {"passed": 0, "busted": 0, "timeout": 0, "d_passed": 0, "d_busted": 0, "bust_only_intraday": 0, "pass_lost": 0, "open_breach": 0}
    total = 0
    for s in range(0, n):
        if s + horizon > n:                                  # a window that cannot run its full length may be censored
            r = simulate_intraday(arr, rules, mode, s, horizon)
            if r["status"] == "timeout":
                continue
        else:
            r = simulate_intraday(arr, rules, mode, s, horizon)
        d = simulate_account(daily[s:s + horizon], daily_rules)
        total += 1
        out[r["status"]] += 1
        out["d_passed"] += d.status == "passed"
        out["d_busted"] += d.status == "busted"
        out["bust_only_intraday"] += (r["status"] == "busted" and d.status != "busted")
        out["pass_lost"] += (d.status == "passed" and r["status"] != "passed")
        out["open_breach"] += bool(r["breach"] and r["breach"]["while_position_open"])
    if not total:
        return None
    f = lambda k: out[k] / total
    return {"mode": mode, "model": "intraday", "horizon_sessions": horizon, "n_windows": total, "windows_independent": False,
            "p_pass": f("passed"), "p_bust": f("busted"), "p_timeout": f("timeout"),
            "daily_model_p_pass": f("d_passed"), "daily_model_p_bust": f("d_busted"),
            "p_bust_only_intraday": f("bust_only_intraday"), "p_pass_lost_to_intraday": f("pass_lost"),
            "p_bust_with_position_open": f("open_breach")}


def intraday_report(arr: BarArrays, rules: PropRules, horizon: int = 30, scale: float = 1.0) -> dict:
    """All three drawdown rules on the same bars. `scale` rescales the position (micro contracts) linearly."""
    a = arr if scale == 1.0 else arr.scaled(scale)
    return {m: historical_attempts(a, rules, m, horizon) for m in MODES}


def describe(rep: dict, rules_mode: str, lang: str = "it") -> str:
    r = (rep or {}).get(rules_mode)
    if not r:
        return ""
    if lang == "it":
        return (f" Simulazione INTRADAY (drawdown con profitto non realizzato, regola {r['mode']}) sulle finestre storiche del blocco mai "
                f"visto: conto bruciato {r['p_bust']:.0%}, valutazione superata {r['p_pass']:.0%}; il simulatore giornaliero semplificato "
                f"direbbe {r['daily_model_p_bust']:.0%} / {r['daily_model_p_pass']:.0%}. Bruciato SOLO per il drawdown intraday: "
                f"{r['p_bust_only_intraday']:.0%} delle finestre ({r['n_windows']} finestre sovrapposte, non indipendenti).")
    return (f" INTRADAY simulation (drawdown including unrealised profit, rule {r['mode']}) on the historical windows of the unseen "
            f"block: account busted {r['p_bust']:.0%}, evaluation passed {r['p_pass']:.0%}; the simplified daily simulator would say "
            f"{r['daily_model_p_bust']:.0%} / {r['daily_model_p_pass']:.0%}. Busted ONLY by the intraday drawdown: "
            f"{r['p_bust_only_intraday']:.0%} of windows ({r['n_windows']} overlapping, not independent).")
