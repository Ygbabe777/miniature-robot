"""Numerical checks of the evidence arithmetic against INDEPENDENT references (a paper-formula re-implementation, numerical integration, Monte Carlo)."""
import math

import numpy as np
import pytest
from scipy import stats as st

from ofo import evidence as EV
from ofo.stats import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe

ANN = math.sqrt(252)


def psr_reference(x, b):
    """Bailey & Lopez de Prado (2012) eq. written out with plain numpy moments (no scipy.stats moments)."""
    x = np.asarray(x, float); T = len(x); m = x.mean(); s = x.std(ddof=1)
    sr = m / s
    z = (x - x.mean()) / x.std(ddof=0)
    g3, g4 = (z ** 3).mean(), (z ** 4).mean()
    arg = (sr - b) * math.sqrt(T - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return 0.5 * (1 + math.erf(arg / math.sqrt(2)))


def test_psr_and_dsr_match_the_paper_formula_in_per_session_units():
    rng = np.random.default_rng(1)
    for T in (60, 91, 250, 500):
        x = rng.standard_t(5, T) * 300 + 25
        assert probabilistic_sharpe(x, 0.0) == pytest.approx(psr_reference(x, 0.0), abs=1e-12)
        b = expected_max_sharpe(40, 0.002)                                 # variance (not sd) of per-session Sharpes
        assert deflated_sharpe(x, 40, 0.002) == pytest.approx(psr_reference(x, b), abs=1e-12)
    assert expected_max_sharpe(40, 0.004) == pytest.approx(2 * expected_max_sharpe(40, 0.001), rel=1e-12)    # sqrt(V): quadrupling V doubles the benchmark


def test_expected_max_approximation_agrees_with_numerical_integration_and_monte_carlo():
    assert EV.max_z_bldp(2) == pytest.approx(EV.max_z_exact(2), rel=0.09) and EV.max_z_bldp(2) < EV.max_z_exact(2)       # the paper's formula is lenient at small k
    for k in (5, 10, 30, 100, 1000):
        assert EV.max_z_bldp(k) == pytest.approx(EV.max_z_exact(k), rel=0.03)
        assert EV.max_z(k) == EV.max_z_exact(k)
    rng = np.random.default_rng(2)
    mc = rng.standard_normal((200_000, 10)).max(axis=1).mean()
    assert EV.max_z_exact(10) == pytest.approx(mc, abs=0.01)
    assert EV.max_z(1) == 0.0


def test_a_null_strategy_is_approved_at_most_about_alpha_of_the_time():
    """Calibration: P(PSR(0) >= 0.95 | true SR = 0) ~ 5%, and P(DSR_holdout >= 0.95 | K finalists) is far smaller."""
    rng = np.random.default_rng(3)
    X = rng.standard_normal((3000, 100))
    p1 = np.mean([probabilistic_sharpe(x, 0.0) >= 0.95 for x in X])
    assert 0.035 < p1 < 0.065
    p5 = np.mean([EV.dsr_holdout(x, 5)["value"] >= 0.95 for x in X])
    assert p5 < 0.02


def test_family_wise_error_over_k_finalists_on_one_block_is_controlled():
    """K independent null finalists on the SAME 100 sessions: P(at least one passes) must stay <= ~5% with the coherent benchmark."""
    rng = np.random.default_rng(4)
    K, T, reps = 5, 100, 1500
    hit = 0
    for _ in range(reps):
        X = rng.standard_normal((K, T))
        hit += any(EV.dsr_holdout(x, K)["value"] >= 0.95 for x in X)
    assert hit / reps < 0.06


def test_required_observed_sharpe_matches_a_brute_force_inversion_of_dsr():
    """Shift a fixed return sample until DSR_holdout hits 0.95: the Sharpe reached must equal the closed form (denominator ~ 1 for small Sharpe)."""
    rng = np.random.default_rng(5)
    for T, k in ((91, 1), (91, 5), (250, 3), (500, 10)):
        base = rng.standard_normal(T)
        lo, hi = -0.5, 1.5
        for _ in range(60):
            mid = (lo + hi) / 2
            if EV.dsr_holdout(base + mid, k, adjust=False, small_sample_t=False, z_margin=0.0)["value"] >= 0.95:
                hi = mid
            else:
                lo = mid
        sr_daily = (base + hi).mean() / (base + hi).std(ddof=1)
        assert sr_daily * ANN == pytest.approx(EV.required_observed_sharpe(T, k, 0.95), rel=0.06)


def test_published_table_values():
    """The numbers quoted in docs/DSR_AUDIT.md (K=1, DSR>=0.95, observed annual Sharpe) and the 80%-power requirement."""
    assert EV.required_observed_sharpe(91, 1) == pytest.approx(2.75, abs=0.02)
    assert EV.required_observed_sharpe(250, 1) == pytest.approx(1.65, abs=0.02)
    assert EV.required_true_sharpe(91, 1) == pytest.approx(4.16, abs=0.05)               # 80% power
    assert EV.required_true_sharpe(500, 1) == pytest.approx(1.77, abs=0.03)
    assert EV.sessions_needed(1.0, 1) == pytest.approx(1555, abs=15)                     # a true Sharpe of 1.0 needs ~6 years for 80% power
    assert EV.power(1.0, 91, 1) == pytest.approx(0.15, abs=0.02)


def test_power_formula_agrees_with_monte_carlo():
    rng = np.random.default_rng(6)
    T, true = 120, 1.5
    X = rng.standard_normal((4000, T)) + true / ANN                                       # unit variance -> annual Sharpe `true`
    mc = np.mean([probabilistic_sharpe(x, 0.0) >= 0.95 for x in X])
    assert EV.power(true, T, 1) == pytest.approx(mc, abs=0.05)


def test_holdout_benchmark_uses_the_blocks_own_variance_and_is_stricter_than_the_mixed_form():
    T, K = 91, 5
    mixed = expected_max_sharpe(K, 0.001)                      # V taken from a development sweep
    coherent = EV.sr0_holdout(T, K)
    assert coherent > mixed and coherent == pytest.approx(math.sqrt(1 / (T - 1)) * EV.max_z_exact(K), rel=1e-9)


def test_chronological_split_has_no_overlap_and_keeps_the_order():
    s = EV.propose_split(500)
    blocks = [s["dev"], s["embargo_1"], s["validation"], s["embargo_2"], s["holdout"]]
    assert blocks[0][0] == 0 and blocks[-1][1] == 500 and all(blocks[i][1] == blocks[i + 1][0] for i in range(4))
    assert s["sizes"] == {"dev": 296, "validation": 100, "holdout": 100, "embargo": 4}
    with pytest.raises(ValueError):
        EV.propose_split(200)


def test_sequential_forward_test_keeps_the_error_rate_under_repeated_looks_and_has_power():
    rng = np.random.default_rng(7)
    looks = [60, 120, 180, 250]
    null = np.mean([EV.forward_test(rng.standard_normal(250), looks)["status"] == "CONFIRMED" for _ in range(3000)])
    assert null <= 0.05
    good = np.mean([EV.forward_test(rng.standard_normal(250) + 2.0 / ANN, looks)["status"] == "CONFIRMED" for _ in range(1500)])
    assert good > 0.4
    assert EV.look_boundary(1, 0.05) == pytest.approx(1.645, abs=0.001) and EV.look_boundary(4) > EV.look_boundary(1)


def test_stages_are_separate_and_forward_data_alone_promotes():
    appr = {"verdict": "APPROVED", "prop": {"compatible": ["R50"]}, "reason": "ok"}
    pap = {"verdict": "PAPER_CANDIDATE", "prop": {"compatible": []}, "reason": "plausibile"}
    assert EV.stage_of(appr)["stage"] == "RESEARCH_APPROVED" and EV.stage_of(pap)["stage"] == "PAPER_CANDIDATE"
    assert EV.stage_of(pap, {"status": "CONFIRMED"})["stage"] == "FORWARD_CONFIRMED"            # confirmed but no compatible ruleset
    assert EV.stage_of(appr, {"status": "CONFIRMED"})["stage"] == "PROP_ELIGIBLE"
    assert EV.stage_of(appr, {"status": "CONFIRMED", "degraded": True})["stage"] == "RESEARCH_APPROVED"
    assert EV.stage_of({"verdict": "STATISTICALLY_REJECTED"}, {"status": "CONFIRMED"})["stage"] == "RESEARCH_REJECTED"
    assert EV.stage_of(appr, {"status": "PENDING"})["stage"] == "RESEARCH_APPROVED"


def test_the_official_bar_includes_the_conservative_margin():
    rng = np.random.default_rng(8)
    for T, k in ((91, 3), (250, 3)):
        base = rng.standard_normal(T)
        lo, hi = -0.5, 1.5
        for _ in range(60):
            mid = (lo + hi) / 2
            if EV.dsr_holdout(base + mid, k)["value"] >= 0.95:
                hi = mid
            else:
                lo = mid
        sr = (base + hi).mean() / (base + hi).std(ddof=1) * ANN
        assert sr == pytest.approx(EV.required_observed_sharpe(T, k, 0.95, margin=EV.Z_MARGIN), rel=0.10)
        assert sr > EV.required_observed_sharpe(T, k, 0.95)                               # strictly harder than the bare formula
