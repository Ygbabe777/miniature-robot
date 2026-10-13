"""Execution ledger: an event-driven account of every signal, fill, cost and P&L.

The vectorised engine (backtest.run_backtest + exits.apply_exits) is fast but compact. This module replays the same
rules bar by bar, one order at a time, and writes down what happened:

    signal (target position decided at the CLOSE of bar t)  ->  fill at that close (+ slippage, + commission)
    open trade: stop / target checked from the NEXT bar (a fill never happens on the bar that created it)
    stop touched in a bar -> filled at the stop (or at the open if the bar gapped through it); stop wins ties
    target touched -> filled at the target; after any exit the strategy stays flat until its own signal changes

It is deliberately written independently of the vectorised code: `reconcile` compares both bar by bar, so a
timing or cost bug in either one shows up as a number, not as a better-looking backtest. extract_trades and the paper
trading ledger use this module as the single source of truth for entries, exits and trade P&L.

Accounting convention (identical in both engines): slippage and commission are charged on the bar where the fill
happens (the target changes at that bar's close); the position earns the NEXT bar's price move."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import Contract
from .exits import ATR_N, atr


class AccountingError(RuntimeError):
    """The two independent engines disagree: no verdict may be built on such numbers."""


@dataclass
class Ledger:
    fills: list = field(default_factory=list)       # one dict per order fill
    trades: list = field(default_factory=list)      # closed round trips
    pnl: pd.Series | None = None                    # net P&L per bar (gross - costs), same index as the bars
    open_trade: dict | None = None                  # position still open on the last bar (not in `trades`)
    worst: pd.Series | None = None                  # per bar: worst unrealised P&L reached while holding (vs previous close)
    best: pd.Series | None = None                   # per bar: best unrealised P&L reached while holding
    costs: dict = field(default_factory=dict)


def _seconds(ts) -> int:
    return int((ts - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1))


def run_ledger(bars: pd.DataFrame, position, contract: Contract, contracts: int = 1, slippage_ticks: float = 1.0,
               exit: dict | None = None, atr_n: int = ATR_N) -> Ledger:
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(c)
    sig = np.nan_to_num(np.asarray(position, dtype=float))
    a = atr(bars, atr_n) if exit else None
    stop_atr = exit["stop_atr"] if exit else None
    rr = exit.get("rr") if exit else None
    pv = contract.point_value * contracts
    comm = contract.commission_per_side * contracts
    slip_cost = slippage_ticks * contract.tick_value * contracts       # dollars per unit per fill
    slip_px = slippage_ticks * contract.tick_size
    idx = bars.index
    sess = (bars["session"].astype(str).str[:10].to_numpy() if "session" in bars.columns
            else idx.normalize().strftime("%Y-%m-%d").to_numpy())
    led = Ledger()
    pnl = np.zeros(n)
    worst, best = np.zeros(n), np.zeros(n)
    held, entry, blocked = 0.0, None, 0.0
    tot_comm = tot_slip = 0.0

    def record_fill(t: int, units: float, ref: float, reason: str) -> float:
        nonlocal tot_comm, tot_slip
        q = abs(units)
        cost = q * (comm + slip_cost)
        tot_comm += q * comm
        tot_slip += q * slip_cost
        side = 1 if units > 0 else -1
        led.fills.append({"bar": t, "t": _seconds(idx[t]), "session": str(sess[t]), "side": side, "qty": q * contracts,
                          "ref_price": float(ref), "price": float(ref + side * slip_px),   # a buy fills higher, a sell lower
                          "commission": q * comm, "slippage": q * slip_cost, "reason": reason})
        return cost

    def close_trade(t: int, px: float, reason: str) -> None:
        nonlocal entry
        side, ei = entry["side"], entry["bar"]
        gross = side * (px - entry["px"]) * pv
        net = gross - 2 * (comm + slip_cost)                           # an entry fill and an exit fill, one unit each
        led.trades.append({"entry_t": _seconds(idx[ei]), "exit_t": _seconds(idx[t]), "side": int(side),
                           "entry_px": float(entry["px"]), "exit_px": float(px), "pnl": float(net),
                           "session": str(sess[t]), "entry_session": str(sess[ei]), "reason": reason,
                           "stop": entry["stop"], "target": entry["tgt"]})
        entry = None

    for t in range(n):
        cost = 0.0
        gross = 0.0
        hit_px, hit_reason = None, None
        if held != 0.0 and t > 0:
            if entry is not None and entry["stop"] is not None:
                side = held
                if side > 0:
                    hs, ht = l[t] <= entry["stop"], entry["tgt"] is not None and h[t] >= entry["tgt"]
                else:
                    hs, ht = h[t] >= entry["stop"], entry["tgt"] is not None and l[t] <= entry["tgt"]
                if hs:                                                  # stop first on ties; a gap fills at the open
                    hit_px = min(entry["stop"], o[t]) if side > 0 else max(entry["stop"], o[t])
                    hit_reason = "stop"
                elif ht:
                    hit_px, hit_reason = entry["tgt"], "target"
            end = hit_px if hit_px is not None else c[t]
            gross = held * (end - c[t - 1]) * pv
            # intrabar path of the open position, for intraday drawdown rules. Adverse extreme = the bar's low (long) or
            # high (short); favourable = the opposite one. A stop closes the path at the stop (it cannot go further against
            # us); a target closes it at the target. Unknown ordering inside a bar is resolved against the account.
            adv, fav = (l[t], h[t]) if held > 0 else (h[t], l[t])
            if hit_reason == "stop":
                adv = end
                if entry is not None and entry["tgt"] is not None:
                    fav = min(fav, entry["tgt"]) if held > 0 else max(fav, entry["tgt"])
            elif hit_reason == "target":
                fav = end
            worst[t] = min(held * (adv - c[t - 1]) * pv, gross)
            best[t] = max(held * (fav - c[t - 1]) * pv, gross)
        cur = held
        if hit_px is not None:
            cost += record_fill(t, -held, hit_px, hit_reason)
            close_trade(t, hit_px, hit_reason)
            blocked, cur = held, 0.0
        target = sig[t]
        if blocked != 0.0:
            if target == blocked:
                target = 0.0                                            # stopped out: flat until the signal changes
            else:
                blocked = 0.0
        if target != cur:
            if cur != 0.0:                                              # close (or flip) at this bar's close
                cost += record_fill(t, -cur, c[t], "signal_exit")
                close_trade(t, c[t], "signal")
            if target != 0.0:
                cost += record_fill(t, target, c[t], "entry")
                risk = stop_atr * a[t] if exit else float("nan")
                if exit and np.isfinite(risk) and risk > 0:
                    stop = c[t] - target * risk
                    tgt = c[t] + target * rr * risk if rr is not None else None
                else:
                    stop = tgt = None
                entry = {"side": target, "bar": t, "px": c[t], "stop": stop, "tgt": tgt}
        pnl[t] = gross - cost
        held = target
    if entry is not None:
        led.open_trade = {"side": int(entry["side"]), "entry_t": _seconds(idx[entry["bar"]]), "entry_px": float(entry["px"]),
                          "entry_session": str(sess[entry["bar"]]), "last_px": float(c[-1])}
    led.pnl = pd.Series(pnl, index=bars.index)
    led.worst = pd.Series(worst, index=bars.index)
    led.best = pd.Series(best, index=bars.index)
    led.costs = {"commission": tot_comm, "slippage": tot_slip}
    return led


def reconcile(bars: pd.DataFrame, position, contract: Contract, contracts: int = 1, slippage_ticks: float = 1.0,
              exit: dict | None = None, atr_n: int = ATR_N, tolerance: float = 1e-6) -> dict:
    """Vectorised engine vs ledger, bar by bar. Returns {"ok", "max_abs_diff", "total_diff", "n_fills", "n_trades"}."""
    from .backtest import run_backtest
    from .exits import apply_exits
    pos = pd.Series(np.asarray(position, dtype=float), index=bars.index).fillna(0.0)
    xpx = None
    if exit:
        pos, xpx, _ = apply_exits(bars, pos, exit["stop_atr"], exit.get("rr"), atr_n)
    vec = run_backtest(bars, pos, contract, contracts, slippage_ticks, exit_px=xpx)["pnl"]
    led = run_ledger(bars, position, contract, contracts, slippage_ticks, exit, atr_n)
    d = (vec - led.pnl).abs()
    scale = max(1.0, float(vec.abs().max()))
    return {"ok": bool(d.max() <= tolerance * scale), "max_abs_diff": float(d.max()),
            "total_diff": float(vec.sum() - led.pnl.sum()), "n_fills": len(led.fills), "n_trades": len(led.trades)}


def run_ledger_next_open(bars: pd.DataFrame, position, contract: Contract, contracts: int = 1, slippage_ticks: float = 1.0,
                         exit: dict | None = None, atr_n: int = ATR_N) -> Ledger:
    """The same strategy with a more realistic execution: a signal decided at the CLOSE of bar t is filled at the OPEN
    of bar t+1 (a market order cannot trade at the close that created it). The position then lives through bar t+1,
    stop and target included. Used as a sensitivity check, not as the main engine: if an edge disappears here it
    was living on the same-close fill assumption."""
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(c)
    sig = np.nan_to_num(np.asarray(position, dtype=float))
    a = atr(bars, atr_n) if exit else None
    pv = contract.point_value * contracts
    comm = contract.commission_per_side * contracts
    slip_cost = slippage_ticks * contract.tick_value * contracts
    idx = bars.index
    sess = (bars["session"].astype(str).str[:10].to_numpy() if "session" in bars.columns
            else idx.normalize().strftime("%Y-%m-%d").to_numpy())
    led = Ledger()
    pnl = np.zeros(n)
    held, entry, blocked, pending = 0.0, None, 0.0, 0.0
    for t in range(n):
        cost = gross = 0.0
        # 1) the order decided at the previous close is filled at this bar's open
        if t > 0 and pending != held:
            if held != 0.0:
                cost += abs(held) * (comm + slip_cost)
                gross += held * (o[t] - c[t - 1]) * pv                      # the old position earned up to this open
                led.trades.append({"entry_t": _seconds(idx[entry["bar"]]), "exit_t": _seconds(idx[t]), "side": int(entry["side"]),
                                   "entry_px": float(entry["px"]), "exit_px": float(o[t]),
                                   "pnl": float(entry["side"] * (o[t] - entry["px"]) * pv - 2 * (comm + slip_cost)),
                                   "session": str(sess[t]), "entry_session": str(sess[entry["bar"]]), "reason": "signal"})
                entry = None
            if pending != 0.0:
                cost += abs(pending) * (comm + slip_cost)
                risk = exit["stop_atr"] * a[t - 1] if exit else float("nan")
                if exit and np.isfinite(risk) and risk > 0:
                    stop = o[t] - pending * risk
                    tgt = o[t] + pending * exit["rr"] * risk if exit.get("rr") is not None else None
                else:
                    stop = tgt = None
                entry = {"side": pending, "bar": t, "px": o[t], "stop": stop, "tgt": tgt}
            fresh = pending != 0.0
            held = pending
            ref = o[t]
        else:
            fresh, ref = False, c[t - 1] if t > 0 else c[t]
        # 2) live through this bar
        if held != 0.0:
            hit_px, hit_reason = None, None
            if entry is not None and entry["stop"] is not None:
                if held > 0:
                    hs, ht = l[t] <= entry["stop"], entry["tgt"] is not None and h[t] >= entry["tgt"]
                else:
                    hs, ht = h[t] >= entry["stop"], entry["tgt"] is not None and l[t] <= entry["tgt"]
                if hs:                                                      # (a position filled at this open cannot gap past its stop)
                    hit_px, hit_reason = (min(entry["stop"], o[t]) if held > 0 else max(entry["stop"], o[t])), "stop"
                elif ht:
                    hit_px, hit_reason = entry["tgt"], "target"
            end = hit_px if hit_px is not None else c[t]
            gross += held * (end - ref) * pv
            if hit_px is not None:
                cost += abs(held) * (comm + slip_cost)
                side = entry["side"]
                led.trades.append({"entry_t": _seconds(idx[entry["bar"]]), "exit_t": _seconds(idx[t]), "side": int(side),
                                   "entry_px": float(entry["px"]), "exit_px": float(hit_px),
                                   "pnl": float(side * (hit_px - entry["px"]) * pv - 2 * (comm + slip_cost)),
                                   "session": str(sess[t]), "entry_session": str(sess[entry["bar"]]),
                                   "reason": hit_reason})
                blocked, held, entry = side, 0.0, None
        # 3) decide what to hold from the next open
        target = sig[t]
        if blocked != 0.0:
            if target == blocked:
                target = 0.0
            else:
                blocked = 0.0
        pending = target
        pnl[t] = gross - cost
    led.pnl = pd.Series(pnl, index=bars.index)
    return led
