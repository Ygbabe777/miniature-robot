"""When to trade and how often: the Optimizer's two scheduling parameters, judged on DEVELOPMENT trades only.

* the entry WINDOW: the contiguous block of 30-minute slots (exchange clock) where the strategy earns most per trade, chosen with a
  t-statistic of the mean trade (so a tiny lucky slot cannot win) and a minimum number of trades;
* the number of trades per day: after the window, the cap N that keeps (almost) all of the profit (later trades of the day are often worse).

Both are selections among several options on the same data: they are counted as trials, labelled in-sample, and the real backtest of the
scheduled version must beat the unscheduled one before the schedule is applied. The locked block is never used here."""
from __future__ import annotations

import numpy as np

BUCKET = 30                 # minutes per slot
MIN_TRADES = 30             # a window must contain at least this many development trades
MIN_T_GAIN = 0.3            # and beat the whole session's t-statistic by this much
CAP_KEEP = 0.98             # smallest N keeping this share of the best cumulative P&L
MAX_CAP = 6


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _tstat(p: np.ndarray) -> float:
    if len(p) < 2:
        return 0.0
    sd = float(np.std(p, ddof=1))
    return float(np.mean(p) / (sd / np.sqrt(len(p)))) if sd > 0 else 0.0


def _minute(t: dict) -> int:
    return int((t["entry_t"] % 86400) // 60)


def by_time(trades: list[dict]) -> list[dict]:
    rows: dict[int, list[float]] = {}
    for t in trades:
        rows.setdefault((_minute(t) // BUCKET) * BUCKET, []).append(t["pnl"])
    return [{"from": _hm(k), "to": _hm(k + BUCKET), "n": len(v), "win_rate": float(np.mean(np.array(v) > 0)), "pnl": float(np.sum(v)),
             "avg": float(np.mean(v))} for k, v in sorted(rows.items())]


def by_count(trades: list[dict]) -> list[dict]:
    """Per ordinal of the trade within its session (1st, 2nd, ...) and the P&L that a cap of N trades a day would have kept."""
    seq: dict[str, int] = {}
    ords: dict[int, list[float]] = {}
    for t in sorted(trades, key=lambda x: x["entry_t"]):
        k = t.get("entry_session") or t.get("session")
        seq[k] = seq.get(k, 0) + 1
        ords.setdefault(min(seq[k], MAX_CAP), []).append(t["pnl"])
    cum, out = 0.0, []
    for o in sorted(ords):
        v = np.array(ords[o])
        cum += float(v.sum())
        out.append({"n_th": o if o < MAX_CAP else f"{MAX_CAP}+", "ordinal": o, "n": len(v), "win_rate": float((v > 0).mean()), "avg": float(v.mean()),
                    "pnl": float(v.sum()), "cum_pnl": cum})
    return out


def schedule_study(trades: list[dict]) -> dict:
    """Tables plus the recommendation. Never touches anything but the trades it is given (development trades)."""
    if not trades:
        return {"n_trades": 0, "by_time": [], "by_count": [], "window": None, "max_per_day": None, "n_scanned": 0, "n_caps": 0}
    slots = by_time(trades)
    m = np.array([_minute(t) for t in trades])
    p = np.array([t["pnl"] for t in trades])
    t_all, n_all, pnl_all = _tstat(p), len(p), float(p.sum())
    lo_k, hi_k = int(m.min() // BUCKET) * BUCKET, int(m.max() // BUCKET) * BUCKET
    ks = list(range(lo_k, hi_k + BUCKET, BUCKET))
    best, scanned = None, 0
    for i in range(len(ks)):
        for j in range(i, len(ks)):
            a, b = ks[i], ks[j] + BUCKET
            sel = (m >= a) & (m < b)
            scanned += 1
            n = int(sel.sum())
            if n < MIN_TRADES or n >= n_all:
                continue
            ps = p[sel]
            tt, tot = _tstat(ps), float(ps.sum())
            if tot > 0 and tt >= t_all + MIN_T_GAIN and (best is None or tt > best["t"]):
                best = {"from": _hm(a), "to": _hm(b), "n": n, "pnl": tot, "t": tt, "from_min": a, "to_min": b}
    keep = trades
    if best:
        keep = [t for t in trades if best["from_min"] <= _minute(t) < best["to_min"]]
    cnt = by_count(keep)
    cap = None
    if cnt:
        top = max(r["cum_pnl"] for r in cnt)
        if top > 0:
            n_best = next(r["ordinal"] for r in cnt if r["cum_pnl"] >= CAP_KEEP * top)
            if n_best < max(r["ordinal"] for r in cnt):
                cap = int(n_best)
    return {"n_trades": n_all, "pnl_all": pnl_all, "t_all": t_all, "by_time": slots, "by_count": cnt,
            "window": ({k: best[k] for k in ("from", "to", "n", "pnl", "t")} if best else None), "max_per_day": cap,
            "n_scanned": scanned, "n_caps": len(cnt), "min_trades": MIN_TRADES,
            "avg_trades_per_day": float(n_all / max(1, len({t.get("entry_session") for t in trades})))}
