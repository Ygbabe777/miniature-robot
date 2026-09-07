"""Bar normalization utilities (timezone alignment, session boundaries, rollover).

TODO: extend with futures contract-roll logic (back-adjustment /
ratio-adjustment) once real continuous-contract data is wired up via a
DataProvider other than SyntheticDataProvider.
"""
from __future__ import annotations

import pandas as pd


def align_to_utc(df: pd.DataFrame, source_tz: str) -> pd.DataFrame:
    if df.index.tzinfo is None:
        df = df.tz_localize(source_tz)
    return df.tz_convert("UTC")


def filter_session(df: pd.DataFrame, start_hour_utc: int, end_hour_utc: int) -> pd.DataFrame:
    return df[(df.index.hour >= start_hour_utc) & (df.index.hour < end_hour_utc)]
