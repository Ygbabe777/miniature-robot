"""Data quality checks beyond `providers.base.validate_bars` — gap detection,
outlier detection, stale-quote detection used by the live safety checks in
`risk/risk_manager.py`.
"""
from __future__ import annotations

import pandas as pd


def detect_gaps(df: pd.DataFrame, expected_freq: str, max_gap_multiplier: float = 3.0) -> list[tuple]:
    """Return (start, end) pairs where the gap between consecutive bars
    exceeds `max_gap_multiplier` times the expected bar spacing."""
    if len(df) < 2:
        return []
    expected = pd.Timedelta(expected_freq)
    deltas = df.index.to_series().diff().dropna()
    gaps = deltas[deltas > expected * max_gap_multiplier]
    return [(df.index[df.index.get_loc(ts) - 1], ts) for ts in gaps.index]


def is_stale(last_bar_timestamp: pd.Timestamp, now: pd.Timestamp, max_staleness: pd.Timedelta) -> bool:
    return (now - last_bar_timestamp) > max_staleness
