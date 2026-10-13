"""The whole life of a prop account, simulated session by session on intraday-aware session data.

States (spec): EVALUATION_ACTIVE -> EVALUATION_PASSED | EVALUATION_FAILED; EVALUATION_PASSED -> FUNDED_PENDING -> FUNDED_ACTIVE;
FUNDED_ACTIVE -> PAYOUT_ELIGIBLE -> PAYOUT_REQUESTED -> PAYOUT_RECEIVED -> FUNDED_ACTIVE; FUNDED_ACTIVE -> FUNDED_FAILED;
anything final -> ACCOUNT_CLOSED. The rules (drawdown model, targets, payout eligibility, caps, split, buffer, fees) come from a
Ruleset: nothing is hard-coded to one firm, and a ruleset without a funded phase simply ends at the evaluation.

What one session is (SessionPool): the day's net P&L at the close and the worst / best equity reached INSIDE the day relative to
the day's start (from the execution ledger's per-bar unrealised extremes), plus the day's trade count and costs.
Order inside a day is unknown with bar data, so it is resolved against the account: the favourable extreme comes first (it lifts a
trailing floor) and the adverse extreme after it. Breaches are therefore overestimated if anything, never hidden. The data
granularity (bar size) is reported as a limit of the simulation, and nothing is invented below it.
Targets and payouts are evaluated at the CLOSE of the session only."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

STATES = ("EVALUATION_ACTIVE", "EVALUATION_FAILED", "EVALUATION_PASSED", "FUNDED_PENDING", "FUNDED_ACTIVE", "PAYOUT_ELIGIBLE",
          "PAYOUT_REQUESTED", "PAYOUT_RECEIVED", "FUNDED_FAILED", "ACCOUNT_CLOSED")
POLICIES = {"A": "prelievo massimo consentito", "B": "prelievo parziale (50%) per tenere buffer",
            "C": "prelievo dinamico: solo se resta un cuscinetto pari al rischio di coda"}
MONTH = 21


@dataclass
class SessionPool:
    close: np.ndarray            # net P&L at the close of each session (base size, costs included)
    worst: np.ndarray            # worst equity inside the session vs the session start (<= min(0, close))
    best: np.ndarray             # best equity inside the session vs the session start (>= max(0, close))
    trades: np.ndarray           # trades entered in the session
    cost: np.ndarray             # commissions + slippage paid in the session (positive dollars, base size)
    sessions: list               # labels
    bar_minutes: float = 0.0     # data granularity: the limit of the intraday reconstruction

    def __len__(self) -> int:
        return len(self.close)

    def take(self, idx) -> "SessionPool":
        idx = np.asarray(idx)
        return SessionPool(self.close[idx], self.worst[idx], self.best[idx], self.trades[idx], self.cost[idx],
                           [self.sessions[i] for i in idx], self.bar_minutes)

    def slice(self, a: int, b: int) -> "SessionPool":
        return self.take(np.arange(a, b))


def pool_from_ledger(bars, led, only_from=None, only_before=None) -> SessionPool:
    """Session data from an execution ledger (ledger.run_ledger) over `bars`. only_from/only_before select a block of sessions."""
    import pandas as pd
    from .prop_intraday import arrays_from_ledger
    arr = arrays_from_ledger(bars, led, only_from=only_from)
    cl, wo, be, sess = [], [], [], []
    for (a, b), s in zip(arr.bounds, arr.sessions):
        if only_before is not None and s >= np.datetime64(only_before):
            continue
        eq = np.cumsum(arr.pnl[a:b])
        prev = np.concatenate([[0.0], eq[:-1]])
        wo.append(float(min(0.0, np.min(np.minimum(prev + arr.worst[a:b], eq)))))
        be.append(float(max(0.0, np.max(np.maximum(prev + arr.best[a:b], eq)))))
        cl.append(float(eq[-1])); sess.append(str(s)[:10])
    pos = {s: i for i, s in enumerate(sess)}
    tr = np.zeros(len(sess)); co = np.zeros(len(sess))
    for t in led.trades:
        i = pos.get(str(t["entry_session"])[:10])
        if i is not None:
            tr[i] += 1
    for f in led.fills:
        i = pos.get(str(f["session"])[:10])
        if i is not None:
            co[i] += f["commission"] + f["slippage"]
    idx = bars.index
    step = float(pd.Series(idx).diff().median() / pd.Timedelta(minutes=1)) if len(idx) > 2 else 0.0
    return SessionPool(np.array(cl), np.array(wo), np.array(be), tr, co, sess, step)


def block_indices(n: int, length: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Circular stationary-ish block bootstrap of session indices: preserves loss clustering and serial dependence within a block."""
    if n == 0:
        return np.zeros(0, int)
    nb = -(-length // block)
    starts = rng.integers(0, n, nb)
    return ((starts[:, None] + np.arange(block)[None, :]) % n).ravel()[:length]


def _risk_buffer(pool: SessionPool, scale: float) -> float:
    """Tail-risk cushion used by payout policy C: the average of the worst 5% sessions (intraday), at the trading size."""
    if len(pool) == 0:
        return 0.0
    w = np.sort(pool.worst)[: max(1, int(0.05 * len(pool)))]
    return float(-w.mean() * scale)


def simulate_path(pool: SessionPool, rs, idx, scale: float, policy: str = "A", max_attempts: int = 3, record: bool = False,
                  risk_buffer: float | None = None) -> dict:
    """One life of an account bought on day 0. `idx`: the sequence of session indices (the horizon is len(idx) sessions)."""
    ev, fu, po, cs = rs.evaluation, rs.funded, rs.payout, rs.costs
    close, worst, best = (pool.close * scale).tolist(), (pool.worst * scale).tolist(), (pool.best * scale).tolist()
    H = len(idx)
    events: list = []
    rb = _risk_buffer(pool, scale) if risk_buffer is None else risk_buffer

    def log(day, state, **kw):
        if record:
            events.append({"day": day, "state": state, **kw})

    phase, state = ev, "EVALUATION_ACTIVE"
    bal, peak, floor = ev.start_balance, ev.start_balance, ev.start_balance - ev.max_loss
    days, best_day, win_days, attempts = 0, 0.0, 0, 1
    cost, month = cs.eval_monthly, 0
    res = {"passed": False, "pass_day": None, "funded": False, "funded_day": None, "payouts": [], "gross": 0.0, "net": 0.0,
           "attempts": 1, "pass_days_all": [], "failed_eval": 0, "failed_funded": 0, "violations": [], "max_dd_use": 0.0, "closed_day": None,
           "first_payout_day": None, "end_state": state, "events": events}
    log(0, state, cost=cost)
    funded_life = 0
    res.update({"first_funded_failed": False, "first_funded_payout": False})
    pending: list = []                                           # payouts requested, to be received on a later day
    lowest = bal
    for d in range(H):
        for pd_, amt in [p for p in pending if p[0] <= d]:       # payouts whose delay has elapsed
            res["payouts"].append(amt); res["net"] += amt
            if res["first_payout_day"] is None:
                res["first_payout_day"] = d
            log(d, "PAYOUT_RECEIVED", net=amt)
        pending = [p for p in pending if p[0] > d]
        if state in ("ACCOUNT_CLOSED",):
            continue
        i = idx[d]
        c, w, b = close[i], worst[i], best[i]
        month += 1
        if month >= MONTH:
            month = 0
            cost += cs.eval_monthly if state == "EVALUATION_ACTIVE" else cs.funded_monthly
        days += 1
        # ---- drawdown check, intraday-aware (favourable extreme first, adverse extreme after) ----
        if phase.trailing == "intraday":
            hi = max(peak, bal + b)
            fl = hi - phase.max_loss
            fl = min(fl, phase.start_balance) if phase.lock_at_start else fl
            fl = max(floor, fl)
        else:
            fl = floor                                           # static / eod: the floor moves only at the close
        breach = None
        if bal + w <= fl:
            breach = "drawdown_" + phase.trailing
        elif phase.daily_loss_limit is not None and -w >= phase.daily_loss_limit:
            breach = "daily_loss_limit"
        lowest = min(lowest, bal + w)
        res["max_dd_use"] = max(res["max_dd_use"], max(0.0, (min(peak, fl + phase.max_loss) - (bal + w)) / phase.max_loss))   # share of the allowance used
        if breach:
            res["violations"].append({"day": d, "kind": breach, "state": state, "equity": bal + w, "floor": fl, "closed_above_floor": bool(bal + c > fl)})
            if state == "EVALUATION_ACTIVE":
                res["failed_eval"] += 1; log(d, "EVALUATION_FAILED", kind=breach)
            else:
                res["failed_funded"] += 1; log(d, "FUNDED_FAILED", kind=breach)
                if funded_life == 1:
                    res["first_funded_failed"] = True
            if attempts < max_attempts and d < H - 1:            # buy a new try: a reset of the same account
                attempts += 1; res["attempts"] = attempts
                cost += cs.reset or cs.eval_monthly
                phase, state = ev, "EVALUATION_ACTIVE"
                bal, peak, floor = ev.start_balance, ev.start_balance, ev.start_balance - ev.max_loss
                days, best_day, win_days, lowest = 0, 0.0, 0, bal
                log(d, state, reset_cost=cs.reset or cs.eval_monthly)
            else:
                state = "ACCOUNT_CLOSED"; res["closed_day"] = d; log(d, state)
            continue
        # ---- close of the session ----
        bal += c
        peak = max(peak, bal + 0.0) if phase.trailing != "intraday" else max(peak, bal, (bal - c) + b)
        nf = peak - phase.max_loss
        if phase.trailing != "static":
            floor = max(floor, min(nf, phase.start_balance) if phase.lock_at_start else nf)
        if state == "EVALUATION_ACTIVE":
            best_day = max(best_day, c)
            profit = bal - ev.start_balance
            if profit >= (ev.profit_target or 0) and days >= ev.min_days and (ev.consistency_pct is None or best_day < ev.consistency_pct * profit):
                res["passed"] = True
                if res["pass_day"] is None:
                    res["pass_day"] = d                          # the FIRST pass: later lives (after a failure) do not overwrite it
                log(d, "EVALUATION_PASSED", balance=bal)
                if fu is None or po is None:
                    state = "ACCOUNT_CLOSED"; res["closed_day"] = d; res["end_state"] = "EVALUATION_PASSED"; continue
                cost += cs.activation
                log(d, "FUNDED_PENDING", cost=cs.activation)
                phase, state = fu, "FUNDED_ACTIVE"; funded_life += 1
                bal, peak, floor = fu.start_balance, fu.start_balance, fu.start_balance - fu.max_loss
                days, win_days, lowest = 0, 0, bal
                res["funded"] = True
                if res["funded_day"] is None:
                    res["funded_day"] = d
                log(d, state)
        else:
            if c >= po.winning_day_min:
                win_days += 1
            avail = bal - fu.start_balance - po.buffer
            if win_days >= po.min_winning_days and avail >= po.min_payout:
                log(d, "PAYOUT_ELIGIBLE", balance=bal, winning_days=win_days)
                if policy == "A":
                    amt = avail
                elif policy == "B":
                    amt = 0.5 * avail
                else:
                    amt = min(avail, bal - floor - rb)           # keep a tail-risk cushion above the floor
                if po.cap is not None:
                    amt = min(amt, po.cap)
                if amt >= po.min_payout:
                    bal -= amt; win_days = 0
                    gross = amt
                    res["gross"] += gross
                    pending.append((d + po.delay_days, gross * po.profit_split))
                    log(d, "PAYOUT_REQUESTED", gross=gross, net=gross * po.profit_split)
                    if funded_life == 1:
                        res["first_funded_payout"] = True
                    if po.floor_after_payout == "rebase":
                        peak = bal; floor = max(fu.start_balance - fu.max_loss, bal - fu.max_loss)
    # payouts requested but not yet received inside the horizon are NOT counted (conservative)
    res["total_cost"] = cost
    if res["end_state"] == "EVALUATION_ACTIVE":
        res["end_state"] = state
    res["lifetime"] = (res["closed_day"] if res["closed_day"] is not None else H)
    res["npfv"] = res["net"] - cost
    return res
