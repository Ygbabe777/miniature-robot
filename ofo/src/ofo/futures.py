"""Massive CME futures minute files -> one back-adjusted continuous series per root.

Input files: one gz CSV per day containing every CME ticker, columns
  ticker,exchange,session_end_date,window_start,open,high,low,close,volume,dollar_volume,transactions
Outright contracts look like NQZ5; spreads (NQZ5-NQH6) and TAS (NQTZ5) are dropped.
The ticker has a single year digit, so the decade is resolved from the first day a
ticker appears in the data (valid while contracts are listed < 10 years ahead).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .continuous import build_continuous

MONTHS = "FGHJKMNQUVXZ"
COLS = ["ticker", "session_end_date", "window_start", "open", "high", "low", "close", "volume"]


def _outright(root: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(root)}[{MONTHS}]\d$")


def read_day(path: str | Path, root: str, tz: str = "America/New_York") -> pd.DataFrame:
    df = pd.read_csv(path, usecols=COLS)
    df = df[df["ticker"].str.match(_outright(root))]
    if df.empty:
        return df.assign(ts=pd.NaT).iloc[0:0]
    df = df.copy()
    df["ts"] = (pd.to_datetime(df["window_start"], unit="ns", utc=True)
                .dt.tz_convert(tz).dt.tz_localize(None))
    return df.drop(columns="window_start")


def expiry_key(ticker: str, first_seen: pd.Timestamp) -> int:
    """Sortable (year*12+month) with the decade inferred from first appearance."""
    month = MONTHS.index(ticker[-2]) + 1
    digit = int(ticker[-1])
    year = first_seen.year + ((digit - first_seen.year) % 10)
    return year * 12 + month


def build_root(paths, root: str) -> pd.DataFrame:
    """Return the continuous 1-minute series (OHLCV + `session`) for `root`."""
    frames = [read_day(p, root) for p in sorted(paths)]
    frames = [f for f in frames if len(f)]
    if not frames:
        raise ValueError(f"no outright {root} contracts found")
    allrows = pd.concat(frames, ignore_index=True)
    first = allrows.groupby("ticker")["ts"].min()
    order = sorted(first.index, key=lambda t: expiry_key(t, first[t]))
    contracts = []
    for t in order:
        c = allrows[allrows["ticker"] == t].drop_duplicates("ts").set_index("ts").sort_index()
        contracts.append(c[["open", "high", "low", "close", "volume", "session_end_date"]])
    cont = build_continuous(contracts)
    return cont.rename(columns={"session_end_date": "session"})
