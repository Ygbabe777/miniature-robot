"""Market-data loading and validation for futures OHLCV.

Supported CSV layouts:
  - ninjatrader : `yyyyMMdd HHmmss;open;high;low;close;volume` (no header, ';' sep)
  - firstrate   : `YYYY-MM-DD HH:MM:SS,open,high,low,close,volume` (no header)
  - databento   : header row with `ts_event,open,high,low,close,volume`
All timestamps are returned as tz-naive in the file's own timezone; record the
timezone with the dataset (NinjaTrader exports use the platform's local time).
"""
from __future__ import annotations

import pandas as pd

COLS = ["open", "high", "low", "close", "volume"]


def load_ohlcv(path: str, fmt: str) -> pd.DataFrame:
    if fmt == "ninjatrader":
        df = pd.read_csv(path, sep=";", header=None, names=["ts", *COLS])
        df["ts"] = pd.to_datetime(df["ts"], format="%Y%m%d %H%M%S")
    elif fmt == "firstrate":
        df = pd.read_csv(path, header=None, names=["ts", *COLS])
        df["ts"] = pd.to_datetime(df["ts"])
    elif fmt == "databento":
        df = pd.read_csv(path)
        df = df.rename(columns={"ts_event": "ts"})
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
    else:
        raise ValueError(f"unknown format: {fmt}")
    return df.set_index("ts").sort_index()[COLS]


def validate(df: pd.DataFrame) -> dict:
    """Report problems instead of silently fixing them."""
    bad_ohlc = ((df["high"] < df[["open", "close", "low"]].max(axis=1))
                | (df["low"] > df[["open", "close", "high"]].min(axis=1)))
    return {
        "rows": len(df),
        "start": df.index.min(),
        "end": df.index.max(),
        "duplicate_timestamps": int(df.index.duplicated().sum()),
        "inconsistent_ohlc_rows": int(bad_ohlc.sum()),
        "non_positive_prices": int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        "monotonic": bool(df.index.is_monotonic_increasing),
    }


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate bars, e.g. rule='5min', '15min', '1h'. Empty buckets are dropped."""
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["close"])


def split_blocked(df: pd.DataFrame, oos_fraction: float = 0.3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split. The tail is the LOCKED out-of-sample set: only the Judge
    may read it, and only once per candidate."""
    cut = int(len(df) * (1 - oos_fraction))
    return df.iloc[:cut], df.iloc[cut:]


def walk_forward_windows(index: pd.DatetimeIndex, train_days: int, test_days: int,
                         step_days: int | None = None):
    """Yield (train_slice, test_slice) as (start, end) timestamp pairs over trading days.

    Every test window is judged by parameters fitted ONLY on data before it. Stitching
    the test windows gives one out-of-sample series covering most of the sample.
    """
    days = index.normalize().unique().sort_values()
    step = step_days or test_days
    i = train_days
    while i + test_days <= len(days):
        yield ((days[i - train_days], days[i - 1]), (days[i], days[i + test_days - 1]))
        i += step
