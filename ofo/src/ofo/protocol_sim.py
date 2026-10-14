"""Synthetic simulation of the WHOLE evidence protocol, not of one formula in isolation.

One simulated hidden-block epoch: K preregistered finalists (possibly strongly correlated, autocorrelated, heavy-tailed) are judged on T_hold unseen
sessions with the official dsr_holdout; those that clear the PAPER_CANDIDATE line then run the preregistered sequential forward test on fresh sessions.
Reported: the probability that AT LEAST ONE finalist is RESEARCH_APPROVED / PAPER_CANDIDATE / FORWARD_CONFIRMED (family-wise rates) and the per-finalist rates.
Used by tests/test_protocol.py and scripts/dsr_audit.py. Nothing here reads real data."""
from __future__ import annotations

import math

import numpy as np

from . import evidence as EV
from .stats import probabilistic_sharpe

ANN = math.sqrt(252)


def draw(rng, shape, dist: str = "gauss", phi: float = 0.0) -> np.ndarray:
    """Unit-variance innovations of the requested family, with AR(1) serial correlation phi along the last axis."""
    if dist == "t3":
        z = rng.standard_t(3, shape) / math.sqrt(3)
    elif dist == "zero_infl":
        z = rng.standard_normal(shape) * (rng.random(shape) < 0.3) / math.sqrt(0.3)
    elif dist == "neg_skew":
        z = np.where(rng.random(shape) < 0.04, -6.0, 0.25) + 0.3 * rng.standard_normal(shape)
        z = (z - 0.0) / z.std()
    else:
        z = rng.standard_normal(shape)
    if phi:
        x = np.zeros_like(z)
        x[..., 0] = z[..., 0]
        for t in range(1, shape[-1]):
            x[..., t] = phi * x[..., t - 1] + z[..., t]
        z = x / math.sqrt(1 / (1 - phi ** 2))
    return z


def block(rng, reps: int, K: int, T: int, true_annual: float, rho: float, dist: str, phi: float) -> np.ndarray:
    """(reps, K, T) returns for K finalists sharing a common factor with correlation rho."""
    common = draw(rng, (reps, 1, T), dist, phi)
    own = draw(rng, (reps, K, T), dist, phi)
    x = math.sqrt(rho) * common + math.sqrt(1 - rho) * own
    if dist == "neg_skew":
        x = x - x.mean(axis=2, keepdims=True)                      # the null has zero true mean whatever the skew
    return x + true_annual / ANN


def simulate(seed: int, reps: int, *, K: int = 3, T_hold: int = 100, rho: float = 0.9, phi: float = 0.0, dist: str = "gauss", true_annual: float = 0.0,
             looks: tuple = (60, 120, 180, 250), alpha: float = 0.05, conf: float = 0.95, k_planned: int | None = None) -> dict:
    rng = np.random.default_rng(seed)
    kp = k_planned or K
    X = block(rng, reps, K, T_hold, true_annual, rho, dist, phi)
    any_app = any_pap = any_fwd = 0
    n_app = n_pap = n_fwd = 0
    for r in range(reps):
        cand, appr = [], []
        for k in range(K):
            x = X[r, k]
            d = EV.dsr_holdout(x, kp)["value"]
            if d is None:
                continue
            if d >= conf:
                appr.append(k)
            if (d >= 0.70 and probabilistic_sharpe(x, 0.0) >= 0.90 and x.sum() > 0) or d >= conf:
                cand.append(k)
        any_app += bool(appr); n_app += len(appr)
        any_pap += bool(cand); n_pap += len(cand)
        confirmed = 0
        for k in cand:
            f = block(rng, 1, 1, max(looks), true_annual, 0.0, dist, phi)[0, 0]
            confirmed += EV.forward_test(f, list(looks), alpha)["status"] == "CONFIRMED"
        any_fwd += bool(confirmed); n_fwd += confirmed
    return {"reps": reps, "K": K, "T_hold": T_hold, "rho": rho, "phi": phi, "dist": dist, "true_annual": true_annual,
            "P_any_approved": any_app / reps, "P_any_paper": any_pap / reps, "P_any_forward_confirmed": any_fwd / reps,
            "per_finalist": {"approved": n_app / (reps * K), "paper": n_pap / (reps * K), "forward_confirmed": n_fwd / (reps * K)}}
