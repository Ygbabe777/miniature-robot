"""Baseline strategy families. Each returns a target position in {-1,0,1} per bar,
computed ONLY from data up to and including that bar (the backtester shifts it)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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


def _minutes(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(index.hour * 60 + index.minute)


def orb(bars: pd.DataFrame, or_minutes: int) -> pd.Series:
    """Opening-range breakout on the US cash open (09:30 exchange time). The range is
    built from bars starting inside [09:30, 09:30+or_minutes); signals start after it and
    stop at 16:45. Long above the range high, short below the low, held until flat."""
    idx = bars.index
    tod = _minutes(idx)
    day = idx.normalize()
    in_or = (tod >= 570) & (tod < 570 + or_minutes)
    hi = bars["high"].where(in_or).groupby(day).transform("max")
    lo = bars["low"].where(in_or).groupby(day).transform("min")
    post = (tod >= 570 + or_minutes) & (tod < 16 * 60 + 45)
    c = bars["close"]
    sig = pd.Series(np.nan, index=idx)
    sig[~post] = 0.0
    sig[post & (c > hi)] = 1.0
    sig[post & (c < lo)] = -1.0
    return _hold(sig)


def vwap_dev(bars: pd.DataFrame, window: int, z: float) -> pd.Series:
    """Fade stretches away from the session VWAP, measured in rolling standard deviations
    of the deviation. Session VWAP resets with the trading session."""
    key = bars["session"] if "session" in bars.columns else bars.index.normalize()
    vol = bars["volume"].astype(float)
    vwap = (bars["close"] * vol).groupby(key).cumsum() / vol.groupby(key).cumsum()
    dev = bars["close"] - vwap
    score = dev / dev.rolling(window).std()
    sig = pd.Series(np.nan, index=bars.index)
    sig[score < -z] = 1.0
    sig[score > z] = -1.0
    sig[score.abs() < 0.25] = 0.0
    return _hold(sig)


@dataclass(frozen=True)
class Template:
    fn: Callable
    params: dict            # name -> (type, lo, hi): hard bounds a spec must respect
    valid: Callable = lambda p: True
    control: bool = False   # zero-edge controls are never offered to the LLM
    doc: str = ""


TEMPLATES = {
    "ema_cross": Template(ema_cross, {"fast": (int, 2, 60), "slow": (int, 5, 400)},
                          lambda p: p["fast"] < p["slow"], doc="trend: fast/slow EMA cross"),
    "breakout": Template(breakout, {"lookback": (int, 5, 300)},
                         doc="trend: Donchian channel breakout"),
    "mean_reversion": Template(mean_reversion, {"window": (int, 10, 300), "z": (float, 0.5, 4.0)},
                               doc="reversion: z-score of close vs rolling mean"),
    "orb": Template(orb, {"or_minutes": (int, 5, 120)},
                    doc="opening-range breakout at the 09:30 ET cash open"),
    "vwap_dev": Template(vwap_dev, {"window": (int, 10, 300), "z": (float, 0.5, 4.0)},
                         doc="reversion: deviation from session VWAP"),
    "random_control": Template(random_control, {"seed": (int, 0, 100_000)}, control=True,
                               doc="zero-edge control"),
}


GRIDS = {
    "ema_cross": (ema_cross, [{"fast": f, "slow": s} for f in (5, 9, 13, 21) for s in (30, 50, 100)]),
    "breakout": (breakout, [{"lookback": n} for n in (10, 20, 40, 80)]),
    "mean_reversion": (mean_reversion, [{"window": w, "z": z} for w in (20, 50) for z in (1.5, 2.0, 2.5)]),
}
TIMEFRAMES = ("5min", "15min", "30min", "60min")
