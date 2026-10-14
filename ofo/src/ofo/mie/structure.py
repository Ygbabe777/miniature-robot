"""Market Structure Engine: explicit, configurable, causal definitions.

SWING: bar i is a swing high if its high is strictly above the highs of the k bars before it and at least equal to those of the k bars after it (the FIRST of equal
highs wins; swing low mirrored). The swing is
CONFIRMED only when bar i+k has closed: `confirm_time = end of bar i+k`. Nothing labelled with a swing may be used before `confirm_time`.
STRUCTURE (labelled at confirmation, in confirmation order): HH/LH against the previous confirmed swing high, HL/LL against the previous confirmed
swing low. BOS: a close beyond the last confirmed swing in the direction of the trend; CHoCH: a close beyond the last confirmed swing AGAINST it.
LIQUIDITY around a level L known from `avail`: a BREACH is the first bar whose high (low) exceeds L by sweep_min_ticks; the outcome is classified only
after `reclaim_bars` bars (rejection / reclaim / acceptance / run) and stamped at the END of that window, so the classification is never early.
REGIME: efficiency ratio and ATR ratio on past bars only; multilabel; UNCERTAIN when there is not enough history or no label applies."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .observer import DURATION, atr


def swings(bars: pd.DataFrame, k: int, rule: str) -> pd.DataFrame:
    """Confirmed swing highs/lows. Columns: kind ('H'/'L'), price, event_time (bar start), confirm_time (end of bar i+k)."""
    h, l = bars["high"].to_numpy(), bars["low"].to_numpy()
    n = len(bars)
    rows = []
    for i in range(k, n - k):
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            rows.append(("H", float(h[i]), bars.index[i], bars.index[i + k] + DURATION[rule]))
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            rows.append(("L", float(l[i]), bars.index[i], bars.index[i + k] + DURATION[rule]))
    return pd.DataFrame(rows, columns=["kind", "price", "event_time", "confirm_time"]).sort_values("confirm_time", kind="stable").reset_index(drop=True)


def label_structure(sw: pd.DataFrame) -> pd.DataFrame:
    """HH/LH/HL/LL in CONFIRMATION order, so a label never depends on a swing that was not yet confirmed."""
    last = {"H": None, "L": None}
    lab = []
    for _, r in sw.iterrows():
        p = last[r["kind"]]
        if p is None:
            lab.append(None)
        elif r["kind"] == "H":
            lab.append("HH" if r["price"] > p else "LH" if r["price"] < p else "EH")
        else:
            lab.append("HL" if r["price"] > p else "LL" if r["price"] < p else "EL")
        last[r["kind"]] = r["price"]
    out = sw.copy()
    out["label"] = lab
    return out


def trend_state(labelled: pd.DataFrame, upto) -> str:
    """Trend as known at time `upto`: up (HH+HL), down (LH+LL), mixed, or unknown with fewer than two labelled swings of each side."""
    known = labelled[(labelled["confirm_time"] <= upto) & labelled["label"].notna()].tail(4)
    if len(known) < 2:
        return "unknown"
    ls = list(known["label"])
    up = sum(x in ("HH", "HL") for x in ls); dn = sum(x in ("LH", "LL") for x in ls)
    return "up" if up >= 3 and dn == 0 else "down" if dn >= 3 and up == 0 else "up" if up == len(ls) else "down" if dn == len(ls) else "mixed"


def structure_events(bars: pd.DataFrame, labelled: pd.DataFrame, rule: str) -> pd.DataFrame:
    """BOS / CHoCH / structural invalidation, detected at the CLOSE of the breaking bar using only swings confirmed before that bar."""
    sw = labelled.reset_index(drop=True)
    ev, j = [], 0
    ch, ends = bars["close"].to_numpy(), bars.index + DURATION[rule]
    known: list = []
    for i in range(len(bars)):
        while j < len(sw) and sw.loc[j, "confirm_time"] <= ends[i] - DURATION[rule]:       # swings confirmed at or before this bar's START are usable for its close
            known.append(sw.loc[j]); j += 1
        hs = [x for x in known if x["kind"] == "H"]; ls = [x for x in known if x["kind"] == "L"]
        if not hs or not ls:
            continue
        lh, ll = hs[-1], ls[-1]
        trend = trend_state(sw.iloc[:j], ends[i] - DURATION[rule])
        if ch[i] > lh["price"] and not lh.get("_used_up"):
            ev.append((ends[i], "BOS" if trend == "up" else "CHoCH" if trend == "down" else "break_up", "up", float(lh["price"]), ends[i]))
            lh["_used_up"] = True
        if ch[i] < ll["price"] and not ll.get("_used_dn"):
            ev.append((ends[i], "BOS" if trend == "down" else "CHoCH" if trend == "up" else "break_down", "down", float(ll["price"]), ends[i]))
            ll["_used_dn"] = True
    return pd.DataFrame(ev, columns=["event_time", "kind", "direction", "level", "available_at"])


def trading_range(labelled: pd.DataFrame, upto, atr_value: float, cfg: dict) -> dict | None:
    """A trading range as known at `upto`: the last N confirmed swings lie inside a band of range_band_atr * ATR and alternate without trending labels."""
    n = int(cfg["structure"]["range_min_pivots"])
    known = labelled[labelled["confirm_time"] <= upto].tail(n)
    if len(known) < n or not atr_value or np.isnan(atr_value):
        return None
    hi, lo = known[known["kind"] == "H"]["price"], known[known["kind"] == "L"]["price"]
    if len(hi) < 2 or len(lo) < 2:
        return None
    band = float(hi.max() - lo.min())
    if band <= cfg["structure"]["range_band_atr"] * atr_value * 3:
        return {"high": float(hi.max()), "low": float(lo.min()), "width": band, "since": str(known["confirm_time"].iloc[-1])}
    return None


def liquidity_events(bars: pd.DataFrame, level: float, avail, side: str, cfg: dict, rule: str = "5min", session_end=None) -> list[dict]:
    """Breaches of `level` (a 'high' level is breached upward, a 'low' level downward) after `avail`, and their classified outcome.
    outcome: rejection (never closed beyond), reclaim (closed beyond then back inside), acceptance (>= acceptance_closes consecutive closes beyond),
    run (kept going >= 1 ATR beyond without coming back). The classification time is the end of the observation window."""
    st = cfg["structure"]
    eps = st["sweep_min_ticks"] * st["tick_size"]
    w = bars[bars.index >= pd.Timestamp(avail)]
    if session_end is not None:
        w = w[w.index < pd.Timestamp(session_end)]
    if len(w) == 0:
        return []
    a = atr(bars, 20).reindex(w.index)
    hi, lo, cl = w["high"].to_numpy(), w["low"].to_numpy(), w["close"].to_numpy()
    up = side == "high"
    beyond = (hi > level + eps) if up else (lo < level - eps)
    idx = np.flatnonzero(beyond)
    if len(idx) == 0:
        return []
    i = int(idx[0])                                                   # first breach of the session; later ones are re-tests, studied as their own definition
    N = int(st["reclaim_bars"])
    seg = slice(i, min(len(w), i + N))
    closes_beyond = (cl[seg] > level) if up else (cl[seg] < level)
    t_breach = w.index[i] + DURATION[rule]
    t_end = w.index[min(len(w) - 1, i + N - 1)] + DURATION[rule]
    complete = (i + N) <= len(w)
    ext = ((hi[seg].max() - level) if up else (level - lo[seg].min()))
    a0 = float(a.iloc[i]) if not np.isnan(a.iloc[i]) else None
    run_len = 0; best = 0
    for c in closes_beyond:
        run_len = run_len + 1 if c else 0; best = max(best, run_len)
    if best >= st["acceptance_closes"]:
        outcome = "acceptance"
    elif not closes_beyond.any():
        outcome = "rejection"
    elif not closes_beyond[-1]:
        outcome = "reclaim"
    elif a0 and ext >= a0:
        outcome = "run"
    else:
        outcome = "acceptance" if closes_beyond.all() else "reclaim"
    return [{"kind": f"breach_{side}", "level": float(level), "breach_time": t_breach, "event_time": w.index[i], "available_at": t_breach, "atr": a0},
            {"kind": f"liquidity_{side}_{outcome}", "level": float(level), "breach_time": t_breach, "event_time": w.index[i], "available_at": t_end, "complete": bool(complete),
             "excursion": float(ext), "atr": a0, "outcome": outcome}]


def regime_series(bars: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Per-bar regime labels using past bars only. Columns: er, atr_ratio, labels (set as '|'-joined string), uncertain."""
    r = cfg["regime"]
    c = bars["close"]
    w = int(r["er_window"])
    net = (c - c.shift(w)).abs()
    path = c.diff().abs().rolling(w, min_periods=w).sum()
    er = net / path.replace(0, np.nan)
    fast, slow = atr(bars, int(r["atr_fast"])), atr(bars, int(r["atr_slow"]))
    ratio = fast / slow.replace(0, np.nan)
    labels = []
    known = np.arange(len(bars)) >= int(r["min_bars"])
    for e, q, ok in zip(er.to_numpy(), ratio.to_numpy(), known):
        ls = []
        if ok and not np.isnan(e):
            if e >= r["er_trend"]:
                ls.append("TRENDING")
            elif e <= r["er_range"]:
                ls.append("RANGING")
            else:
                ls.append("TRANSITION")
        if ok and not np.isnan(q):
            if q >= r["vol_expansion"]:
                ls.append("VOLATILITY_EXPANSION")
            elif q <= r["vol_compression"]:
                ls.append("VOLATILITY_COMPRESSION")
        labels.append("|".join(ls) if ls else "UNCERTAIN")
    out = pd.DataFrame({"er": er, "atr_ratio": ratio, "labels": labels}, index=bars.index)
    out["uncertain"] = out["labels"] == "UNCERTAIN"
    return out
