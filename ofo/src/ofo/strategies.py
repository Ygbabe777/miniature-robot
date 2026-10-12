"""Baseline strategy families. Each returns a target position in {-1,0,1} per bar,
computed ONLY from data up to and including that bar (the backtester shifts it)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _hold(sig: pd.Series) -> pd.Series:
    return sig.ffill().fillna(0.0)


def ema_cross(bars: pd.DataFrame, fast: int, slow: int) -> pd.Series:
    if fast >= slow:
        return pd.Series(0.0, index=bars.index)
    c = bars["close"]
    pos = np.sign(c.ewm(span=fast, adjust=False).mean() - c.ewm(span=slow, adjust=False).mean())
    pos.iloc[:slow] = 0.0
    return pos


def breakout(bars: pd.DataFrame, lookback: int) -> pd.Series:
    up = bars["high"].rolling(lookback).max().shift(1)
    dn = bars["low"].rolling(lookback).min().shift(1)
    sig = pd.Series(np.nan, index=bars.index)
    sig[bars["close"] > up] = 1.0
    sig[bars["close"] < dn] = -1.0
    return _hold(sig)


def mean_reversion(bars: pd.DataFrame, window: int, z: float) -> pd.Series:
    c = bars["close"]
    score = (c - c.rolling(window).mean()) / c.rolling(window).std()
    sig = pd.Series(np.nan, index=bars.index)
    sig[score < -z] = 1.0
    sig[score > z] = -1.0
    sig[score.abs() < 0.25] = 0.0
    return _hold(sig)


def random_control(bars: pd.DataFrame, seed: int, block: int = 20) -> pd.Series:
    """Zero-edge control. If the pipeline approves this, the pipeline is broken."""
    rng = np.random.default_rng(seed)
    n = -(-len(bars) // block)
    return pd.Series(np.repeat(rng.choice([-1.0, 0.0, 1.0], n), block)[:len(bars)],
                     index=bars.index)


def flatten_overnight(pos: pd.Series, flat_from: str = "16:45", flat_to: str = "18:00") -> pd.Series:
    """Guarantee nothing is HELD at or after `flat_from` (exchange-local time).

    A target set at the close of bar t is held during bar t+1. For the held position to
    be zero on every bar that ends after `flat_from`, the target must already be zero on
    bars starting later than flat_from - 2*bar_length. This works for any bar size: with
    30/60-minute bars a naive time mask would leave a position open through the
    17:00-18:00 maintenance break. Prop firms require flat before the daily close."""
    step = pos.index.to_series().diff().median()
    limit = pd.Timedelta(hours=int(flat_from[:2]), minutes=int(flat_from[3:])) - 2 * step
    stop = pd.Timedelta(hours=int(flat_to[:2]), minutes=int(flat_to[3:]))
    tod = pd.to_timedelta(pos.index.hour, unit="h") + pd.to_timedelta(pos.index.minute, unit="m")
    return pos.where(~((tod > limit) & (tod < stop)), 0.0)


GRIDS = {
    "ema_cross": (ema_cross, [{"fast": f, "slow": s} for f in (5, 9, 13, 21) for s in (30, 50, 100)]),
    "breakout": (breakout, [{"lookback": n} for n in (10, 20, 40, 80)]),
    "mean_reversion": (mean_reversion, [{"window": w, "z": z} for w in (20, 50) for z in (1.5, 2.0, 2.5)]),
}
TIMEFRAMES = ("5min", "15min", "30min", "60min")
