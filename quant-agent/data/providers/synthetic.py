"""Synthetic OHLCV generator — FOR DEMOS / TESTS ONLY.

This provider fabricates a random-walk price series with an injected,
known, decaying autocorrelation structure so that `scripts/example_workflow.py`
can exercise the full pipeline (discovery -> ... -> paper trading) without
requiring a licensed market-data subscription. It must never be used to
justify a real trading decision, and every consumer of it must label
results as synthetic. This is enforced by prefixing `data_version()` with
`"SYNTHETIC:"` so any experiment record naming it is unambiguous.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import DataProvider, validate_bars

_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 60 * 24}


class SyntheticDataProvider(DataProvider):
    def __init__(self, seed: int = 42, autocorr: float = 0.05, annual_vol: float = 0.18):
        self.seed = seed
        self.autocorr = autocorr
        self.annual_vol = annual_vol

    def get_bars(self, market: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        minutes = _TIMEFRAME_MINUTES[timeframe]
        idx = pd.date_range(start=start, end=end, freq=f"{minutes}min", tz="UTC")
        idx = idx[(idx.hour >= 13) & (idx.hour < 20)]  # roughly RTH in UTC
        n = len(idx)
        if n == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=idx)

        rng = np.random.default_rng(abs(hash((self.seed, market, timeframe))) % (2**32))
        bar_vol = self.annual_vol / np.sqrt(252 * 6.5 * 60 / minutes)
        shocks = rng.normal(0, bar_vol, size=n)
        signal = rng.normal(0, 1, size=n)  # a proxy "order-flow imbalance" feature
        # Inject a small, genuinely-known predictive edge: the return realized
        # OVER THE NEXT BAR correlates with the CURRENT bar's signal (lagged
        # by one bar relative to `signal`) -- this is what a forward-return
        # regression (`agents.hypothesis_agent.test_hypothesis`) is expected
        # to (partially) recover. Using the *contemporaneous* signal here
        # would make the "edge" indistinguishable from noise under a
        # forward-return test, since real predictive signals must lead, not
        # coincide with, the return they explain.
        lagged_signal = np.roll(signal, 1)
        lagged_signal[0] = 0.0
        returns = self.autocorr * bar_vol * np.tanh(lagged_signal) + shocks
        price = 15000 * np.exp(np.cumsum(returns))

        close = price
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, bar_vol / 4, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, bar_vol / 4, n)))
        volume = rng.lognormal(mean=7, sigma=0.5, size=n)

        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume,
             "signal": signal},
            index=idx,
        )
        df = df[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index <= pd.Timestamp(end, tz="UTC"))]
        validate_bars(df[["open", "high", "low", "close", "volume"]], end)
        return df

    def data_version(self, market: str, timeframe: str) -> str:
        return f"SYNTHETIC:seed={self.seed}:autocorr={self.autocorr}:{market}:{timeframe}"
