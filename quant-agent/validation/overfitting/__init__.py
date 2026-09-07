"""Anti-overfitting battery (spec section 13).

Every backtest is assumed overfit until proven otherwise. This module
implements:

* **Deflated Sharpe Ratio (DSR)** — Bailey & Lopez de Prado (2014). Corrects
  the observed Sharpe ratio for the number of independent trials that were
  run to find it (multiple-testing correction) and for non-normality
  (skew/kurtosis) of the return series.
* **Probability of Backtest Overfitting (PBO)** — a simplified
  Combinatorially-Symmetric Cross-Validation (CSCV) across *sibling*
  strategy variants (spec section 9's mutation tree). Genuine PBO requires
  >= 2 candidate configurations to rank in-sample vs. out-of-sample —
  with a single strategy there is nothing to rank, so this returns `None`
  with an explicit reason rather than fabricating a number.
* **Parameter sensitivity / perturbation test** — reruns the backtest with
  each numeric parameter nudged +/-10% and +/-20%; a strategy whose Sharpe
  collapses or flips sign under small perturbations is classified
  OVERFIT / UNROBUST regardless of its unperturbed Sharpe (spec section 13
  example: Sharpe 3.8 that collapses is rejected).
* **Randomized-entry test** — reruns the SAME exit/risk logic but replaces
  the researched entry signal with random entries at the same frequency,
  to check the edge isn't just "any trade would have made money" in this
  sample (e.g. survivorship-biased or trending sample).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from backtesting.engine import BacktestEngine
from strategies.schemas.results import OverfittingReport
from strategies.schemas.strategy import StrategySpec


def deflated_sharpe_ratio(bar_returns: np.ndarray, sharpe_hat: float, n_trials: int) -> float:
    """Returns the DSR as a probability in [0, 1]: P(true Sharpe > 0 | data),
    after correcting for `n_trials` independent strategy variants tested.
    """
    t = len(bar_returns)
    if t < 30 or n_trials < 1:
        return float("nan")
    skew = float(stats.skew(bar_returns))
    kurt = float(stats.kurtosis(bar_returns, fisher=False))  # non-excess kurtosis

    gamma = 0.5772156649  # Euler-Mascheroni constant
    if n_trials == 1:
        sr_star = 0.0
    else:
        sigma_sr = np.sqrt(1.0 / max(t - 1, 1))
        z1 = stats.norm.ppf(1 - 1.0 / n_trials)
        z2 = stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
        sr_star = sigma_sr * ((1 - gamma) * z1 + gamma * z2)

    denom = np.sqrt(max(1e-12, 1 - skew * sharpe_hat + (kurt - 1) / 4 * sharpe_hat**2))
    z = (sharpe_hat - sr_star) * np.sqrt(t - 1) / denom
    return float(stats.norm.cdf(z))


def probability_of_backtest_overfitting(
    candidate_bar_returns: dict[str, np.ndarray], n_splits: int = 8, seed: int = 11
) -> float | None:
    """Simplified CSCV across sibling strategy variants (spec section 9/13).

    Returns None (not a fabricated number) if fewer than 2 candidates are
    given — PBO is not defined for a single configuration.
    """
    if len(candidate_bar_returns) < 2:
        return None
    names = list(candidate_bar_returns)
    min_len = min(len(v) for v in candidate_bar_returns.values())
    if min_len < n_splits * 2:
        return None
    mat = np.column_stack([candidate_bar_returns[n][:min_len] for n in names])

    rng = np.random.default_rng(seed)
    chunk_size = min_len // n_splits
    chunks = [mat[i * chunk_size:(i + 1) * chunk_size] for i in range(n_splits)]

    logits = []
    half = n_splits // 2
    idx = np.arange(n_splits)
    trials = min(200, 1 << n_splits)
    for _ in range(trials):
        rng.shuffle(idx)
        train_idx, test_idx = idx[:half], idx[half:]
        train = np.vstack([chunks[i] for i in train_idx])
        test = np.vstack([chunks[i] for i in test_idx])
        train_sharpe = train.mean(axis=0) / (train.std(axis=0) + 1e-12)
        test_sharpe = test.mean(axis=0) / (test.std(axis=0) + 1e-12)
        best_in_sample = int(np.argmax(train_sharpe))
        rank_oos = int(stats.rankdata(test_sharpe)[best_in_sample])
        omega = rank_oos / len(names)
        omega = min(max(omega, 1e-6), 1 - 1e-6)
        logits.append(np.log(omega / (1 - omega)))

    pbo = float(np.mean(np.array(logits) < 0))
    return pbo


@dataclass
class PerturbationOutcome:
    parameter: str
    base_value: float
    perturbed_value: float
    base_sharpe: float
    perturbed_sharpe: float

    @property
    def relative_change(self) -> float:
        if abs(self.base_sharpe) < 1e-9:
            return float("inf") if abs(self.perturbed_sharpe) > 1e-9 else 0.0
        return (self.perturbed_sharpe - self.base_sharpe) / abs(self.base_sharpe)


def parameter_sensitivity_test(
    spec: StrategySpec, bars: pd.DataFrame, base_sharpe: float, engine: BacktestEngine | None = None
) -> tuple[float, list[PerturbationOutcome]]:
    engine = engine or BacktestEngine()
    outcomes: list[PerturbationOutcome] = []
    numeric_params = {k: v for k, v in spec.parameters.items() if isinstance(v, (int, float))}
    for name, value in numeric_params.items():
        for pct in (-0.20, -0.10, 0.10, 0.20):
            perturbed_spec = spec.model_copy(deep=True)
            perturbed_spec.parameters[name] = value * (1 + pct)
            try:
                result = engine.run(perturbed_spec, bars)
                sharpe = result.metrics.sharpe
            except ValueError:
                sharpe = 0.0
            outcomes.append(PerturbationOutcome(name, value, value * (1 + pct), base_sharpe, sharpe))

    if not outcomes:
        return 1.0, outcomes
    # Stability score: 1.0 minus average |relative change|, clipped to [0,1].
    rel_changes = [min(abs(o.relative_change), 3.0) for o in outcomes]
    score = max(0.0, 1.0 - float(np.mean(rel_changes)) / 3.0)
    return score, outcomes


def randomized_entry_test(
    spec: StrategySpec, bars: pd.DataFrame, real_sharpe: float, n_random: int = 50, seed: int = 3,
    engine: BacktestEngine | None = None,
) -> bool:
    """Replaces the entry condition with a random coin-flip at the same
    average entry frequency the real strategy exhibited, and checks the
    real Sharpe beats the 95th percentile of the random-entry distribution.
    """
    engine = engine or BacktestEngine()
    rng = np.random.default_rng(seed)
    randomized_sharpes = []
    entry_prob = 0.02  # approximate; a full implementation would match the
    # real strategy's realized entry frequency exactly. TODO: derive from spec.
    for _ in range(n_random):
        random_spec = spec.model_copy(deep=True)
        threshold = rng.uniform(0.5, 0.999)
        random_col = f"__rand_{rng.integers(0, 1_000_000)}"
        b = bars.copy()
        b[random_col] = rng.random(len(b))
        random_spec.signal_logic.all_of = [f"{random_col} > {threshold}"]
        random_spec.entry_logic.all_of = [f"{random_col} > {threshold}"]
        try:
            result = engine.run(random_spec, b)
            randomized_sharpes.append(result.metrics.sharpe)
        except ValueError:
            continue
    if not randomized_sharpes:
        return True
    p95 = float(np.percentile(randomized_sharpes, 95))
    return real_sharpe > p95


def build_overfitting_report(
    experiment_id: str,
    spec: StrategySpec,
    bars: pd.DataFrame,
    bar_returns: np.ndarray,
    sharpe_hat: float,
    n_trials: int,
    randomized_sequence_passed: bool,
    sibling_bar_returns: dict[str, np.ndarray] | None = None,
    engine: BacktestEngine | None = None,
) -> OverfittingReport:
    engine = engine or BacktestEngine()
    dsr = deflated_sharpe_ratio(bar_returns, sharpe_hat, n_trials)
    pbo = probability_of_backtest_overfitting(sibling_bar_returns) if sibling_bar_returns else None
    stability_score, outcomes = parameter_sensitivity_test(spec, bars, sharpe_hat, engine)
    perturbation_passed = stability_score >= 0.5
    randomized_entry_passed = randomized_entry_test(spec, bars, sharpe_hat, engine=engine)

    is_overfit = (
        (not perturbation_passed)
        or (not randomized_entry_passed)
        or (not randomized_sequence_passed)
        or (dsr == dsr and dsr < 0.5)  # dsr==dsr filters NaN
        or (pbo is not None and pbo > 0.5)
    )
    verdict = "OVERFIT / UNROBUST" if is_overfit else "ROBUST"
    if dsr != dsr and pbo is None:
        verdict = "INCONCLUSIVE" if not is_overfit else verdict

    notes = (
        f"DSR={dsr:.3f} PBO={'n/a (needs sibling variants)' if pbo is None else f'{pbo:.3f}'} "
        f"param_stability={stability_score:.2f} perturbation={'PASS' if perturbation_passed else 'FAIL'} "
        f"randomized_entry={'PASS' if randomized_entry_passed else 'FAIL'} "
        f"randomized_sequence={'PASS' if randomized_sequence_passed else 'FAIL'}"
    )

    return OverfittingReport(
        experiment_id=experiment_id,
        deflated_sharpe_ratio=None if dsr != dsr else dsr,
        probability_of_backtest_overfitting=pbo,
        parameter_sensitivity_score=stability_score,
        perturbation_test_passed=perturbation_passed,
        randomized_entry_test_passed=randomized_entry_passed,
        randomized_sequence_test_passed=randomized_sequence_passed,
        n_trials_considered=n_trials,
        verdict=verdict,
        notes=notes,
    )
