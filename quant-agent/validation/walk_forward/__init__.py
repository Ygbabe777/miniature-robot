"""Walk-forward validation (spec section 12).

Rolls a train/test window forward through the dataset and re-runs the
(fixed) strategy spec on each out-of-sample test slice, measuring
consistency, performance degradation, and how many windows were
profitable. Rejects strategies whose performance exists only in one
historical period.

LIMITATION (documented, not hidden): a full walk-forward implementation
re-optimizes parameters on each TRAIN window before evaluating on the
paired TEST window. Doing that generically requires a parameter-search
harness bounded by `MAX_PARAMETER_SEARCH_SIZE` that does not exist yet in
this MVP (`TODO: REQUIRES parameter search harness`). What this module
validates today is temporal *consistency of a fixed, already-chosen*
spec — still a meaningful and standard robustness check (a strategy whose
edge only appears in one slice of history is rejected either way), just
not a re-fit-per-window walk-forward in the strictest sense.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting.engine import BacktestEngine
from strategies.schemas.results import WalkForwardResult, WalkForwardWindow
from strategies.schemas.strategy import StrategySpec


def generate_windows(
    start: pd.Timestamp, end: pd.Timestamp, train_span: pd.DateOffset, test_span: pd.DateOffset
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    windows = []
    train_start = start
    while True:
        train_end = train_start + train_span
        test_start = train_end
        test_end = test_start + test_span
        if test_end > end:
            break
        windows.append((train_start, train_end, test_start, test_end))
        train_start = train_start + test_span
    return windows


def run_walk_forward(
    spec: StrategySpec,
    bars: pd.DataFrame,
    experiment_id: str,
    train_months: int = 12,
    test_months: int = 3,
    min_windows: int = 4,
) -> WalkForwardResult:
    start, end = bars.index.min(), bars.index.max()
    windows = generate_windows(
        start, end, pd.DateOffset(months=train_months), pd.DateOffset(months=test_months)
    )
    if len(windows) < min_windows:
        raise ValueError(
            f"Only {len(windows)} walk-forward windows available (need >= {min_windows}). "
            "Need a longer history for a meaningful walk-forward test."
        )

    engine = BacktestEngine()
    results: list[WalkForwardWindow] = []
    sharpes = []
    for train_start, train_end, test_start, test_end in windows:
        test_bars = bars.loc[(bars.index >= test_start) & (bars.index < test_end)]
        if len(test_bars) < 10:
            continue
        try:
            result = engine.run(spec, test_bars)
            sharpe = result.metrics.sharpe
            ret_pct = result.metrics.total_return_pct
        except ValueError:
            sharpe, ret_pct = 0.0, 0.0
        sharpes.append(sharpe)
        results.append(
            WalkForwardWindow(
                train_start=str(train_start.date()),
                train_end=str(train_end.date()),
                test_start=str(test_start.date()),
                test_end=str(test_end.date()),
                test_sharpe=sharpe,
                test_return_pct=ret_pct,
                profitable=ret_pct > 0,
                parameters_used=spec.parameters,
            )
        )

    pct_profitable = (sum(1 for w in results if w.profitable) / len(results) * 100) if results else 0.0
    stability = float(1.0 - min(1.0, np.std(sharpes) / (abs(np.mean(sharpes)) + 1e-6))) if sharpes else 0.0
    stability = max(0.0, stability)
    degradation = 0.0
    if len(sharpes) >= 2:
        first_half = np.mean(sharpes[: len(sharpes) // 2])
        second_half = np.mean(sharpes[len(sharpes) // 2 :])
        if abs(first_half) > 1e-9:
            degradation = (first_half - second_half) / abs(first_half) * 100

    passed = pct_profitable >= 60.0 and stability >= 0.3

    return WalkForwardResult(
        experiment_id=experiment_id,
        windows=results,
        pct_profitable_windows=pct_profitable,
        parameter_stability_score=stability,
        performance_degradation_pct=float(degradation),
        passed=passed,
    )
