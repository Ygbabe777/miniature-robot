"""CSV / Parquet backed DataProvider implementations."""
from __future__ import annotations

import hashlib
import os

import pandas as pd

from .base import DataProvider, validate_bars

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


class CSVDataProvider(DataProvider):
    """Reads `{root}/{market}_{timeframe}.csv` with a `timestamp` column."""

    def __init__(self, root: str):
        self.root = root

    def _path(self, market: str, timeframe: str) -> str:
        return os.path.join(self.root, f"{market}_{timeframe}.csv")

    def get_bars(self, market: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        path = self._path(market, timeframe)
        df = pd.read_csv(path, parse_dates=["timestamp"])
        df = df.set_index("timestamp").sort_index()
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        df = df.loc[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index <= pd.Timestamp(end, tz="UTC"))]
        df = df[REQUIRED_COLUMNS]
        validate_bars(df, end)
        return df

    def data_version(self, market: str, timeframe: str) -> str:
        path = self._path(market, timeframe)
        stat = os.stat(path)
        digest = hashlib.sha256(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:16]
        return f"csv:{market}:{timeframe}:{digest}"


class ParquetDataProvider(DataProvider):
    """Reads `{root}/{market}_{timeframe}.parquet` with a `timestamp` column."""

    def __init__(self, root: str):
        self.root = root

    def _path(self, market: str, timeframe: str) -> str:
        return os.path.join(self.root, f"{market}_{timeframe}.parquet")

    def get_bars(self, market: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        path = self._path(market, timeframe)
        df = pd.read_parquet(path)
        df = df.set_index("timestamp").sort_index()
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        df = df.loc[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index <= pd.Timestamp(end, tz="UTC"))]
        df = df[REQUIRED_COLUMNS]
        validate_bars(df, end)
        return df

    def data_version(self, market: str, timeframe: str) -> str:
        path = self._path(market, timeframe)
        stat = os.stat(path)
        digest = hashlib.sha256(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:16]
        return f"parquet:{market}:{timeframe}:{digest}"


class DatabaseDataProvider(DataProvider):
    """TODO: REQUIRES DATA — wire to a real historical-bar table once one
    exists (e.g. populated by a vendor ETL job). Not implemented here to
    avoid faking data-quality guarantees this class cannot actually make.
    """

    def get_bars(self, market: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("TODO: REQUIRES DATA — no historical bar table configured yet.")

    def data_version(self, market: str, timeframe: str) -> str:
        raise NotImplementedError("TODO: REQUIRES DATA")


class BrokerDataProvider(DataProvider):
    """TODO: REQUIRES API — requires a connected, authenticated broker/market
    data session (see execution/brokers). Not implemented here.
    """

    def get_bars(self, market: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("TODO: REQUIRES API — no live market data connection configured.")

    def data_version(self, market: str, timeframe: str) -> str:
        raise NotImplementedError("TODO: REQUIRES API")
