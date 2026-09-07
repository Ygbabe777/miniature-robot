"""DataProvider abstraction (spec section 31).

Strategies and the backtesting engine depend only on this interface, never
on a concrete data source, so swapping CSV -> Parquet -> a live broker feed
never requires touching strategy code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    """Returns OHLCV bars indexed by a UTC `DatetimeIndex`, columns
    `["open", "high", "low", "close", "volume"]`, sorted ascending.

    Implementations MUST NOT return any bar whose timestamp is after
    `end` — this is the primary look-ahead-bias guard used by the
    backtesting engine (spec section 10).
    """

    @abstractmethod
    def get_bars(self, market: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def data_version(self, market: str, timeframe: str) -> str:
        """A stable identifier for exactly which data was used (for experiment lineage)."""
        raise NotImplementedError


class DataQualityError(RuntimeError):
    pass


def validate_bars(df: pd.DataFrame, end: str) -> None:
    """Shared validation used by every concrete provider.

    Rejects data that would silently introduce look-ahead bias or
    timestamp errors (spec section 10 / 37).
    """
    if df.empty:
        return
    if not df.index.is_monotonic_increasing:
        raise DataQualityError("Bars are not sorted ascending by timestamp.")
    if df.index.has_duplicates:
        raise DataQualityError("Duplicate timestamps in bar data.")
    end_ts = pd.Timestamp(end)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    if df.index.max() > end_ts:
        raise DataQualityError(
            f"Data provider returned bars after requested end={end} — look-ahead bias risk."
        )
    missing = df[["open", "high", "low", "close", "volume"]].isna().any()
    if missing.any():
        raise DataQualityError(f"NaNs present in required columns: {missing[missing].index.tolist()}")
