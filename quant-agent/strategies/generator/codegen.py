"""Compiles a `StrategySpec` into a `CompiledStrategy` the backtesting
engine can run — this is the "generate executable code from the
specification" step (spec section 8/42), implemented as safe, data-driven
feature computation + expression evaluation rather than free-form code
generation, so it never needs to `exec()` untrusted Python.

Supported built-in feature builders (name pattern -> vectorized pandas
computation): `sma_<n>`, `ema_<n>`, `atr_<n>`, `rolling_std_<n>`,
`zscore_<n>`, `returns_<n>`, `rolling_rank_<n>`. Each (except `atr`, which
is always high/low/close) defaults to operating on `close`, or on another
already-computed column via `<base_col>:<kind>_<n>`, e.g.
`vol20:rolling_rank_60` ranks the `vol20` feature's current value against
its own trailing 60-bar history — the building block used for regime
filters ("is current volatility in the top quartile of the last N bars?").
Any other feature name is computed row-wise via `safe_eval` against the
raw OHLCV(+ custom) columns — useful for research-provided signals like an
order-flow-imbalance column already present in the input data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategies.schemas.strategy import StrategySpec
from .safe_eval import eval_expression, eval_signal_logic

_PATTERN = re.compile(r"^(?:([a-zA-Z_][a-zA-Z0-9_]*):)?(sma|ema|atr|rolling_std|zscore|returns|rolling_rank)_(\d+)$")


def _build_feature(df: pd.DataFrame, name: str, expression: str) -> pd.Series:
    m = _PATTERN.match(expression.strip())
    if m:
        base_col, kind, window_s = m.group(1) or "close", m.group(2), int(m.group(3))
        window = window_s
        series = df[base_col]
        if kind == "sma":
            return series.rolling(window).mean()
        if kind == "ema":
            return series.ewm(span=window, adjust=False).mean()
        if kind == "returns":
            return series.pct_change(window)
        if kind == "rolling_std":
            return series.pct_change().rolling(window).std() if base_col == "close" else series.rolling(window).std()
        if kind == "zscore":
            roll = series.rolling(window)
            return (series - roll.mean()) / roll.std()
        if kind == "rolling_rank":
            return series.rolling(window).apply(lambda w: (w.iloc[-1] > w).mean(), raw=False)
        if kind == "atr":
            prev_close = df["close"].shift(1)
            tr = pd.concat(
                [
                    df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            return tr.rolling(window).mean()

    # Fall back to row-wise safe evaluation against existing columns.
    def _row_eval(row: pd.Series) -> float:
        try:
            return float(eval_expression(expression, row.to_dict()))
        except Exception:
            return float("nan")

    return df.apply(_row_eval, axis=1)


@dataclass
class CompiledStrategy:
    spec: StrategySpec
    features_df: pd.DataFrame  # original OHLCV + computed feature columns

    def namespace_at(self, i: int) -> dict:
        row = self.features_df.iloc[i]
        ns = row.to_dict()
        ns.update(self.spec.parameters)
        return ns

    def entry_signal(self, i: int) -> bool:
        ns = self.namespace_at(i)
        if not eval_signal_logic(self.spec.signal_logic.all_of, self.spec.signal_logic.any_of, ns):
            return False
        return eval_signal_logic(self.spec.entry_logic.all_of, self.spec.entry_logic.any_of, ns)

    def exit_signal(self, i: int) -> bool:
        ns = self.namespace_at(i)
        if not (self.spec.exit_logic.all_of or self.spec.exit_logic.any_of):
            return False
        return eval_signal_logic(self.spec.exit_logic.all_of, self.spec.exit_logic.any_of, ns)

    def stop_loss_price(self, i: int, entry_price: float, side: int) -> float | None:
        if not self.spec.risk_model.stop_loss:
            return None
        distance = float(eval_expression(self.spec.risk_model.stop_loss, self.namespace_at(i)))
        return entry_price - side * abs(distance)

    def take_profit_price(self, i: int, entry_price: float, side: int) -> float | None:
        if not self.spec.risk_model.take_profit:
            return None
        distance = float(eval_expression(self.spec.risk_model.take_profit, self.namespace_at(i)))
        return entry_price + side * abs(distance)


def compile_strategy(spec: StrategySpec, bars: pd.DataFrame) -> CompiledStrategy:
    df = bars.copy()
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek
    for feature in spec.features:
        df[feature.name] = _build_feature(df, feature.name, feature.expression)
    # regime/session filter columns referenced in constraints are expected
    # to already exist on `bars` (produced by the regime engine) or be
    # defined as ordinary features above.
    return CompiledStrategy(spec=spec, features_df=df)
