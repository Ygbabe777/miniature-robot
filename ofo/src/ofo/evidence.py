"""Statistical evidence arithmetic for OFO: what a finite sample of unseen sessions can and cannot prove.

Everything here is a closed form or a deterministic procedure that tests/test_evidence.py checks against independent references
(a re-implementation from the papers, numerical integration, Monte Carlo). Nothing in this module reads the locked block by itself,
and nothing in it is tuned by looking at how many strategies get approved.

Units. A *Sharpe* below is PER SESSION (daily) unless the name says `annual`; annual = daily * sqrt(252). `T` is the number of sessions.

1. PSR (Bailey & Lopez de Prado, 2012):  PSR(SR*) = Phi( (SR - SR*) sqrt(T-1) / sqrt(1 - g3*SR + (g4-1)/4 * SR^2) )
   g3 = sample skewness, g4 = sample (non-excess) kurtosis. This is the statistic in stats.probabilistic_sharpe.
2. Selection benchmark (same paper, DSR):  SR0 = sqrt(V) * ( (1-g) Phi^-1(1-1/N) + g Phi^-1(1-1/(N e)) ),  g = Euler-Mascheroni.
   V = variance (NOT standard deviation) of the Sharpe estimates among the N trials; N = number of INDEPENDENT trials.
3. Two different questions hide behind "N":
   (a) the DEVELOPMENT sweep: the candidate was chosen as the best of N_dev configurations on the same sample -> selection bias; the
       right V is the dispersion of the sweep's Sharpe estimates (and N should be the effective number of independent configurations);
   (b) the HOLDOUT: K finalists are shown to the same hidden sessions -> family-wise error over K tests. Each finalist's Sharpe estimate
       has, under H0 (true SR = 0), variance V0 = (1 + SR^2 (g4-1)/4 ...)/(T-1) ~ 1/(T-1). THAT is the V that belongs with K.
   Mixing V from (a) with N from (b) is internally inconsistent; `dsr_holdout` implements (b) coherently.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from scipy import stats as st

from .stats import EULER_GAMMA, expected_max_sharpe, probabilistic_sharpe

ANN = math.sqrt(252)


# ------------------------------------------------------------------ building blocks
def max_z_bldp(k: float) -> float:
    """Bailey-Lopez de Prado closed-form approximation of E[max of k independent standard normals] (the one in stats.expected_max_sharpe).
    It UNDER-estimates the true expectation by ~8% at k=2 and ~1-3% for k >= 5: slightly lenient where the number of finalists is small."""
    if k <= 1:
        return 0.0
    g = EULER_GAMMA
    return float((1 - g) * st.norm.ppf(1 - 1 / k) + g * st.norm.ppf(1 - 1 / (k * math.e)))


@lru_cache(maxsize=4096)
def _max_z_exact(k: float) -> float:
    """E[max of k iid N(0,1)] by numerical integration (k may be fractional for an effective count)."""
    from scipy import integrate
    f = lambda x: k * x * st.norm.pdf(x) * st.norm.cdf(x) ** (k - 1)
    return float(integrate.quad(f, -12, 12, limit=200)[0])


def max_z_exact(k: float) -> float:
    return _max_z_exact(round(float(k), 6))


def max_z(k: float) -> float:
    """The luck benchmark in standard-deviation units used by every function below: the exact expectation (k <= 1 -> 0)."""
    return 0.0 if k <= 1 else max_z_exact(k)


def null_sd(T: int, sr: float = 0.0, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Standard deviation of a per-session Sharpe ESTIMATE over T sessions (Mertens). ~ 1/sqrt(T-1) for a null strategy."""
    return math.sqrt(max(1e-12, (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (T - 1)))


def sr0_holdout(T: int, k_eff: float, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Selection benchmark for K_eff finalists tested on ONE hidden block of T sessions (daily units)."""
    return null_sd(T, 0.0, skew, kurt) * max_z(k_eff)


def autocorr_eta(x, q: int | None = None) -> float:
    """Lo (2002) / Newey-West variance inflation of a Sharpe estimate under serial correlation: eta = 1 + 2 sum_k (1 - k/(q+1)) rho_k, with
    Bartlett weights and q = floor(T^(1/3)). Never below 1: negative autocorrelation is NOT allowed to relax the test (conservative)."""
    a = np.asarray(x, dtype=float)
    T = len(a)
    if T < 8:
        return 1.0
    q = int(T ** (1 / 3)) if q is None else q
    d = a - a.mean()
    den = float((d ** 2).sum())
    if den == 0:
        return 1.0
    eta = 1.0
    for k in range(1, q + 1):
        rho = float((d[k:] * d[:-k]).sum() / den)
        eta += 2 * (1 - k / (q + 1)) * rho
    return max(1.0, eta)


Z_MARGIN = 0.30      # conservative margin (z units) added after the stress simulations in docs/DSR_AUDIT.md; it is NOT tuned on any real block


def dsr_holdout(x, k_eff: float, adjust: bool = True, small_sample_t: bool = True, z_margin: float | None = None) -> dict:
    """Coherent DSR on the hidden block: PSR of the observed per-session Sharpe against the luck benchmark of K finalists, whose estimate
    variance under H0 is that of THIS block (1/(T-1)), not the development sweep's.

    Conservative corrections (verified by simulation in tests/test_evidence.py and docs/DSR_AUDIT.md):
    * `adjust`: the standard error is inflated by sqrt(eta) for serial correlation (Lo 2002); without it AR(1) 0.2 returns reach ~6% false positives
      at K=3 and ~14% at AR(1) 0.4;
    * `small_sample_t`: the normal cdf is replaced by Student-t with T-1 degrees of freedom (fat tails of the estimator at T ~ 100).
    K should be the number of PREREGISTERED finalists (the planned maximum), not a running count: correlated finalists only make the test
    more conservative than the independence assumption."""
    a = np.asarray(x, dtype=float)
    T = len(a)
    if T < 4 or not np.all(np.isfinite(a)) or float(a.std(ddof=1)) == 0.0:
        return {"value": None, "T": T, "k_eff": k_eff, "sr0_daily": None, "sr0_annual": None, "eta": None}
    skew, kurt = float(st.skew(a)), float(st.kurtosis(a, fisher=False))
    b = sr0_holdout(T, k_eff, skew, kurt)
    sr = float(a.mean() / a.std(ddof=1))
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if den <= 0:
        return {"value": 0.0, "T": T, "k_eff": float(k_eff), "sr0_daily": b, "sr0_annual": b * ANN, "eta": None}
    eta = autocorr_eta(a) if adjust else 1.0
    z = (sr - b) * math.sqrt(T - 1) / math.sqrt(den * eta) - (Z_MARGIN if z_margin is None else z_margin)
    value = float(st.t.cdf(z, T - 1)) if small_sample_t else float(st.norm.cdf(z))
    return {"value": value, "T": T, "k_eff": float(k_eff), "sr0_daily": b, "sr0_annual": b * ANN, "eta": float(eta), "z": float(z)}


# ------------------------------------------------------------------ requirements and power (normal approximation, denominator ~ 1)
def required_observed_sharpe(T: int, k_eff: float = 1.0, conf: float = 0.95, annual: bool = True, margin: float = 0.0) -> float:
    """Smallest OBSERVED Sharpe that reaches DSR >= conf on T sessions with K_eff finalists:  (max_z(K) + z_conf + margin) / sqrt(T-1).
    margin=Z_MARGIN gives the bar of the official dsr_holdout (normal approximation, no autocorrelation inflation)."""
    d = (max_z(k_eff) + st.norm.ppf(conf) + margin) / math.sqrt(T - 1)
    return d * ANN if annual else d


def required_true_sharpe(T: int, k_eff: float = 1.0, conf: float = 0.95, power: float = 0.80, annual: bool = True) -> float:
    """TRUE Sharpe a strategy needs so that it passes with probability `power` (observed-estimate noise is 1/sqrt(T-1)).
    The 50%-power value equals required_observed_sharpe: a strategy with exactly that true Sharpe is approved half of the time."""
    d = (max_z(k_eff) + st.norm.ppf(conf) + st.norm.ppf(power)) / math.sqrt(T - 1)
    return d * ANN if annual else d


def power(true_annual: float, T: int, k_eff: float = 1.0, conf: float = 0.95) -> float:
    """P(pass | true annual Sharpe) under the normal approximation."""
    mu = true_annual / ANN * math.sqrt(T - 1)
    return float(1 - st.norm.cdf(max_z(k_eff) + st.norm.ppf(conf) - mu))


def sessions_needed(true_annual: float, k_eff: float = 1.0, conf: float = 0.95, power_: float = 0.80) -> int:
    """Sessions of unseen data to approve a strategy of that true annual Sharpe with probability `power_`."""
    if true_annual <= 0:
        return 10 ** 9
    z = max_z(k_eff) + st.norm.ppf(conf) + st.norm.ppf(power_)
    return int(math.ceil((z * ANN / true_annual) ** 2)) + 1


# ------------------------------------------------------------------ chronological split
def propose_split(n_sessions: int, embargo: int = 2, hold_frac: float = 0.20, val_frac: float = 0.20) -> dict:
    """Chronological DEV | VALIDATION | HOLDOUT with an embargo between blocks (intraday strategies end flat, so the only leak is indicator
    warm-up and regime persistence: `embargo` sessions are dropped on each side of a boundary). DEV contains the nested walk-forward.
    VALIDATION chooses the <= 2-3 finalists (its use is counted); only they ever see the HOLDOUT, which is read once per finalist."""
    hold = int(round(hold_frac * n_sessions))
    val = int(round(val_frac * n_sessions))
    dev = n_sessions - hold - val - 2 * embargo
    if dev < 120:
        raise ValueError("too few sessions for three separate blocks")
    b1, b2 = dev, dev + embargo
    return {"n": n_sessions, "dev": [0, dev], "embargo_1": [dev, dev + embargo], "validation": [b2, b2 + val], "embargo_2": [b2 + val, b2 + val + embargo],
            "holdout": [b2 + val + embargo, n_sessions], "sizes": {"dev": dev, "validation": val, "holdout": hold, "embargo": 2 * embargo}}


# ------------------------------------------------------------------ prospective evidence
def look_boundary(n_looks: int, alpha: float = 0.05) -> float:
    """z boundary per look for `n_looks` PRE-REGISTERED looks at the forward data, Sidak-adjusted so that P(any look crosses | H0) <= alpha
    (conservative: the looks are positively correlated, so the true error is lower)."""
    return float(st.norm.ppf((1 - alpha) ** (1 / n_looks)))


def forward_plan(cutoff: str, looks: list[int] | None = None, alpha: float = 0.05) -> dict:
    """The PREREGISTERED forward protocol, written once when a strategy goes to paper and hashed so that it cannot be edited afterwards.
    Verification dates are session counts after `cutoff`; the stopping rules and the error-control method are part of the plan."""
    import hashlib
    import json
    looks = list(looks or [60, 120, 180, 250])
    plan = {"cutoff": str(cutoff)[:10], "looks": looks, "alpha": alpha, "method": "sidak_over_looks_with_autocorr_adjustment",
            "boundary_z": look_boundary(len(looks), alpha), "max_sessions": max(looks),
            "stopping": ["first look with z >= boundary -> FORWARD_CONFIRMED (stop)", "reaching max_sessions without crossing -> NOT_CONFIRMED (stop)",
                         "paper drawdown >= 80% of the allowed drawdown, or z < -2.5 after 40 sessions -> RETIRED (stop, deploy.assess)"],
            "only_sessions_after_cutoff": True}
    plan["hash"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan


def forward_test(forward_pnl, look_at: list[int], alpha: float = 0.05, cutoff: str | None = None, adjust: bool = True) -> dict:
    """Sequential test on FORWARD sessions only (those after a registered cutoff date; nothing already seen is ever added back).
    At each pre-registered look (session counts in `look_at`) z = SR_hat * sqrt(T-1) / sqrt(eta) (eta: serial-correlation inflation, never < 1)
    is compared with the Sidak boundary. Stops at the first crossing. `degraded`: the last look's Sharpe is not positive."""
    x = np.asarray(forward_pnl, dtype=float)
    bound = look_boundary(len(look_at), alpha)
    rows = []
    for T in look_at:
        if len(x) < T:
            break
        a = x[:T]
        sd = float(a.std(ddof=1))
        eta = autocorr_eta(a) if adjust else 1.0
        z = float(a.mean() / sd * math.sqrt(T - 1) / math.sqrt(eta)) if sd > 0 else 0.0
        rows.append({"T": T, "z": z, "boundary": bound, "eta": float(eta), "annual_sharpe": float(a.mean() / sd * ANN) if sd > 0 else 0.0})
        if z >= bound:
            return {"status": "CONFIRMED", "looks": rows, "boundary": bound, "cutoff": cutoff, "degraded": False, "sessions": int(len(x))}
    status = "PENDING" if len(x) < max(look_at) else "NOT_CONFIRMED"
    return {"status": status, "looks": rows, "boundary": bound, "sessions": int(len(x)), "cutoff": cutoff, "degraded": bool(rows and rows[-1]["annual_sharpe"] <= 0)}


# ------------------------------------------------------------------ the three DSR formulations, shown side by side
def dsr_modes(x, *, var_sr: float, n_raw: int, locked_looks: int, k_planned: int, n_eff: float | None = None) -> dict:
    """Raw, locked_looks and dsr_holdout, each with formula, N, variance used, benchmark and interpretation. The OFFICIAL one is `holdout_null`;
    the other two are diagnostics that make the difference visible."""
    from .stats import deflated_sharpe
    a = np.asarray(x, dtype=float)
    ok = len(a) >= 4 and bool(np.all(np.isfinite(a))) and float(a.std(ddof=1)) > 0
    sd_raw = lambda n: expected_max_sharpe(n, var_sr) * ANN
    out = {}
    out["raw"] = {"official": False, "formula": "DSR = PSR(SR_oos ; SR0), SR0 = sqrt(V_sweep)*maxZ(N_raw)", "N": int(n_raw), "V": float(var_sr), "V_meaning": "varianza degli Sharpe per sessione dello sweep di sviluppo",
                  "sr0_annual": sd_raw(int(n_raw)), "value": float(deflated_sharpe(a, int(n_raw), var_sr)) if ok else None,
                  "interpretation": "N = tutti i tentativi mai registrati. Conservativo, ma mescola la selezione sullo sviluppo con il test sul blocco."}
    out["locked_looks"] = {"official": False, "formula": "DSR = PSR(SR_oos ; SR0), SR0 = sqrt(V_sweep)*maxZ(N_looks)", "N": int(locked_looks) + 1, "V": float(var_sr), "V_meaning": "varianza dello sweep di sviluppo",
                           "sr0_annual": sd_raw(int(locked_looks) + 1), "value": float(deflated_sharpe(a, int(locked_looks) + 1, var_sr)) if ok else None,
                           "interpretation": "N = accessi al blocco, ma V è quella dello sweep: sottostima la fortuna sul blocco (V del blocco è ~1/(T-1)). Più indulgente di raw e della formulazione coerente."}
    h = dsr_holdout(a, k_planned)
    out["holdout_null"] = {"official": True, "formula": "DSR = t_{T-1}( (SR_oos - SR0)*sqrt(T-1)/sqrt(den*eta) - margin ), SR0 = sqrt(1/(T-1))*maxZ(K)", "N": int(k_planned),
                           "V": (1 / (len(a) - 1)) if len(a) > 1 else None, "V_meaning": "varianza dello stimatore dello Sharpe SUL BLOCCO sotto H0 (1/(T-1)); eta = inflazione per autocorrelazione; margin = %.2f z" % Z_MARGIN,
                           "sr0_annual": h.get("sr0_annual"), "value": h["value"], "eta": h.get("eta"),
                           "interpretation": "K = finalisti PREREGISTRATI (massimo del protocollo), non un contatore corrente. Coerente: controlla l'errore family-wise sul blocco, anche con finalisti correlati."}
    if n_eff is not None:
        out["raw"]["n_effective_dev"] = float(n_eff)
    return out


# ------------------------------------------------------------------ stages (what each level of evidence means)
STAGES = ("RESEARCH_REJECTED", "RESEARCH_INCONCLUSIVE", "PAPER_CANDIDATE", "RESEARCH_APPROVED", "FORWARD_CONFIRMED", "PROP_ELIGIBLE")


def stage_of(judgment: dict | None, forward: dict | None = None) -> dict:
    """Separate, ordered levels of evidence from the Judge's verdict plus FORWARD data (never the other way round):

    RESEARCH_APPROVED  APPROVED by the Judge: credible on the hidden block after costs, prop-compatible. A research result, not tradable.
    PAPER_CANDIDATE    plausible but unproven: goes to forward (paper) observation. Not an approval.
    FORWARD_CONFIRMED  forward-only sessions pass the pre-registered sequential test AND did not degrade vs the hidden block.
    PROP_ELIGIBLE      FORWARD_CONFIRMED and at least one ruleset COMPATIBLE: the owner may decide to trade it by hand.
    """
    if not judgment:
        return {"stage": "RESEARCH_INCONCLUSIVE", "why": "nessun giudizio"}
    v = judgment.get("verdict")
    prop_ok = bool((judgment.get("prop") or {}).get("compatible"))
    fw = (forward or {}).get("status")
    if v in ("STATISTICALLY_REJECTED", "EXECUTION_FAILED"):
        return {"stage": "RESEARCH_REJECTED" if v == "STATISTICALLY_REJECTED" else "RESEARCH_INCONCLUSIVE", "why": judgment.get("reason")}
    if v in ("APPROVED", "PAPER_CANDIDATE", "EDGE_VALID_PROP_INCOMPATIBLE"):
        base = "RESEARCH_APPROVED" if v in ("APPROVED", "EDGE_VALID_PROP_INCOMPATIBLE") else "PAPER_CANDIDATE"
        if fw == "CONFIRMED" and not (forward or {}).get("degraded"):
            return {"stage": "PROP_ELIGIBLE" if prop_ok else "FORWARD_CONFIRMED", "why": "confermata sui soli giorni in avanti" + ("" if prop_ok else "; nessun ruleset compatibile")}
        return {"stage": base, "why": judgment.get("reason")}
    return {"stage": "RESEARCH_INCONCLUSIVE", "why": judgment.get("reason")}
