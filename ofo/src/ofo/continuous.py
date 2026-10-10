"""Build a back-adjusted continuous futures series from per-contract bars.

Roll rule: switch to the next contract on the first day its daily volume exceeds
the current contract's. Adjustment is ADDITIVE (price differences), so a P&L
computed as points * point_value on the continuous series stays correct. Absolute
price levels in the past are shifted; never use them as real historical prices.
"""
from __future__ import annotations

import pandas as pd


def _roll_day(a: pd.DataFrame, b: pd.DataFrame) -> pd.Timestamp:
    va = a["volume"].groupby(a.index.normalize()).sum()
    vb = b["volume"].groupby(b.index.normalize()).sum()
    both = pd.concat([va.rename("a"), vb.rename("b")], axis=1).dropna()
    crossed = both[both["b"] > both["a"]]
    if crossed.empty:
        return a.index.max().normalize() + pd.Timedelta(days=1)
    return crossed.index[0]


def build_continuous(contracts: list[pd.DataFrame]) -> pd.DataFrame:
    """contracts: OHLCV frames ordered by expiry (front first)."""
    if len(contracts) < 2:
        return contracts[0].copy()
    rolls, deltas = [], []
    prev_roll = pd.Timestamp.min
    for a, b in zip(contracts[:-1], contracts[1:]):
        roll = max(_roll_day(a, b), prev_roll)
        common = a.index.intersection(b.index)
        common = common[common < roll]
        if len(common) == 0:
            raise ValueError("contracts have no overlapping bars before the roll; cannot adjust")
        t = common[-1]
        deltas.append(float(b.loc[t, "close"] - a.loc[t, "close"]))
        rolls.append(roll)
        prev_roll = roll
    parts = []
    for i, df in enumerate(contracts):
        lo = rolls[i - 1] if i > 0 else pd.Timestamp.min
        hi = rolls[i] if i < len(rolls) else pd.Timestamp.max
        seg = df[(df.index >= lo) & (df.index < hi)].copy()
        adj = sum(deltas[i:])
        seg[["open", "high", "low", "close"]] += adj
        parts.append(seg)
    return pd.concat(parts).sort_index()
