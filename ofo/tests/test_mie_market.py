"""Market Observer / Structure Engine / Behavior Analyzer: known structures, no look-ahead, no invented data, honest statistics."""
import numpy as np
import pandas as pd
import pytest

from ofo.mie import behavior as BH
from ofo.mie import config as MC
from ofo.mie import observer as OB
from ofo.mie import questions as Q
from ofo.mie import structure as ST
from test_research import synth

CFG = MC.load()


def path_bars(points, leg=10, start="2024-01-02 10:00", freq="5min"):
    """5-minute bars following a piecewise-linear close path through `points` (each leg `leg` bars)."""
    closes = []
    for a, b in zip(points[:-1], points[1:]):
        closes += list(np.linspace(a, b, leg, endpoint=False))
    closes.append(points[-1])
    c = np.array(closes)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(start, periods=len(c), freq=freq)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.1, "low": np.minimum(o, c) - 0.1, "close": c, "volume": 100.0}, index=idx)


# ---------------------------------------------------------------- structure
def test_a_known_synthetic_structure_is_labelled_correctly():
    bars = path_bars([100, 110, 105, 115, 108, 120, 112, 125, 118])                    # HH/HL staircase then a pullback
    sw = ST.label_structure(ST.swings(bars, 2, "5min"))
    highs = sw[sw["kind"] == "H"]["label"].tolist()
    lows = sw[sw["kind"] == "L"]["label"].tolist()
    assert highs[1:] and all(x == "HH" for x in highs[1:]) and lows[1:] and all(x == "HL" for x in lows[1:])
    assert ST.trend_state(sw, sw["confirm_time"].max()) == "up"
    ev = ST.structure_events(bars, sw, "5min")
    assert (ev["kind"] == "BOS").any() and (ev[ev["kind"] == "BOS"]["direction"] == "up").all()
    # a break of the last higher low against the trend is a change of character
    bars2 = path_bars([100, 110, 105, 115, 108, 120, 112, 125, 104, 100])
    sw2 = ST.label_structure(ST.swings(bars2, 2, "5min"))
    ev2 = ST.structure_events(bars2, sw2, "5min")
    assert ((ev2["kind"] == "CHoCH") & (ev2["direction"] == "down")).any()


def test_swings_are_confirmed_only_after_k_bars_and_nothing_is_known_early():
    bars = path_bars([100, 110, 100, 110], leg=10)
    sw = ST.swings(bars, 3, "5min")
    for _, r in sw.iterrows():
        assert r["confirm_time"] == r["event_time"] + 4 * pd.Timedelta(minutes=5)           # k=3 bars later, at the END of that bar
        assert r["confirm_time"] > r["event_time"]


def test_prefix_consistency_no_lookahead_in_swings_structure_and_liquidity():
    rng = np.random.default_rng(5)
    c = 100 + np.cumsum(rng.normal(0, 1, 700))
    idx = pd.date_range("2024-01-02 09:30", periods=700, freq="5min")
    full = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.7, "low": c - 0.7, "close": c, "volume": 100.0}, index=idx)
    sw_full = ST.label_structure(ST.swings(full, 3, "5min"))
    ev_full = ST.structure_events(full, sw_full, "5min")
    for cut in (200, 350, 520):
        pre = full.iloc[:cut]
        end = pre.index[-1] + pd.Timedelta(minutes=5)
        sw_pre = ST.label_structure(ST.swings(pre, 3, "5min"))
        known_full = sw_full[sw_full["confirm_time"] <= end]
        assert list(zip(sw_pre["kind"], sw_pre["price"], sw_pre["confirm_time"], sw_pre["label"])) == list(zip(known_full["kind"], known_full["price"], known_full["confirm_time"], known_full["label"]))
        ev_pre = ST.structure_events(pre, sw_pre, "5min")
        ev_known = ev_full[ev_full["available_at"] <= end]
        assert list(zip(ev_pre["kind"], ev_pre["direction"], ev_pre["available_at"])) == list(zip(ev_known["kind"], ev_known["direction"], ev_known["available_at"]))
        lv = float(c[:60].max())
        lp = ST.liquidity_events(pre, lv, idx[60], "high", CFG)
        lf = ST.liquidity_events(full, lv, idx[60], "high", CFG)
        for a, b in zip(lp, lf):
            if a["kind"].startswith("breach"):
                assert a["available_at"] == b["available_at"]
            else:
                assert a["available_at"] == b["available_at"] or not a["complete"]            # classification is only issued when its window has closed


def test_liquidity_outcomes_are_classified_after_the_window_not_at_the_breach():
    N = CFG["structure"]["reclaim_bars"]
    idx = pd.date_range("2024-01-02 10:00", periods=40, freq="5min")
    base = dict(volume=100.0)
    # flat at 99, then a spike above 100 that closes back inside (a wick): REJECTION
    c = np.full(40, 99.0); h = c + 0.3; l = c - 0.3
    h[10] = 101.0
    b = pd.DataFrame({"open": c, "high": h, "low": l, "close": c, **base}, index=idx)
    ev = ST.liquidity_events(b, 100.0, idx[0], "high", CFG)
    assert [e["kind"] for e in ev] == ["breach_high", "liquidity_high_rejection"]
    assert ev[0]["available_at"] == idx[10] + pd.Timedelta(minutes=5) and ev[1]["available_at"] == idx[10 + N - 1] + pd.Timedelta(minutes=5) > ev[0]["available_at"]
    # closes beyond for several bars: ACCEPTANCE
    c2 = c.copy(); c2[10:10 + N] = 100.8; h2 = c2 + 0.3; l2 = c2 - 0.3
    b2 = pd.DataFrame({"open": c2, "high": h2, "low": l2, "close": c2, **base}, index=idx)
    assert ST.liquidity_events(b2, 100.0, idx[0], "high", CFG)[1]["outcome"] == "acceptance"
    # close beyond then back inside: RECLAIM
    c3 = c.copy(); c3[10:12] = 100.6; h3 = c3 + 0.3; l3 = c3 - 0.3
    b3 = pd.DataFrame({"open": c3, "high": h3, "low": l3, "close": c3, **base}, index=idx)
    assert ST.liquidity_events(b3, 100.0, idx[0], "high", CFG)[1]["outcome"] == "reclaim"


def test_regime_can_be_uncertain_and_is_multilabel():
    up = pd.DataFrame({"open": np.arange(300.0), "high": np.arange(300.0) + 0.5, "low": np.arange(300.0) - 0.5, "close": np.arange(300.0) + 0.2, "volume": 1.0},
                      index=pd.date_range("2024-01-02", periods=300, freq="5min"))
    r = ST.regime_series(up, CFG)
    assert r["labels"].iloc[10] == "UNCERTAIN" and r["uncertain"].iloc[:100].all()          # not enough history: it says so
    assert "TRENDING" in r["labels"].iloc[-1].split("|")
    rw = synth(n_sessions=4, seed=2)
    rr = ST.regime_series(OB.derive(rw, "5min"), CFG)
    assert (rr["labels"].str.contains("RANGING|TRANSITION")).sum() > 0 and (rr["labels"] != "").all()


# ---------------------------------------------------------------- observer
def test_levels_gap_and_availability_order_on_a_known_day():
    bars = synth(n_sessions=6, seed=3)
    ctx = OB.session_context(bars, CFG)
    d0, d1 = ctx.index[1], ctx.index[2]
    prev = bars[(bars["session"] == ctx.index[1]) & (bars.index.hour * 60 + bars.index.minute >= 570) & (bars.index.hour * 60 + bars.index.minute < 960)]
    assert ctx.loc[d1, "pdh"] == pytest.approx(prev["high"].max()) and ctx.loc[d1, "pdl"] == pytest.approx(prev["low"].min()) and ctx.loc[d1, "pdc"] == pytest.approx(prev["close"].iloc[-1])
    assert ctx.loc[d1, "gap"] == pytest.approx(ctx.loc[d1, "open"] - ctx.loc[d1, "pdc"])
    r = ctx.loc[d1]
    assert r["pd_available_at"] <= r["on_available_at"] <= r["open_available_at"] <= r["or_available_at"]         # nothing is known before it can be
    assert pd.isna(ctx.iloc[0]["pdh"])                                                                           # the first session has no previous day


def test_missing_data_is_never_replaced_by_an_invented_value():
    bars = synth(n_sessions=5, seed=4)
    d = bars["session"].unique()[2]
    day = bars[bars["session"] == d]
    rth_open = day[(day.index.hour == 9) & (day.index.minute == 30)].index[0]
    bars = bars.drop(day[(day.index >= rth_open) & (day.index < rth_open + pd.Timedelta(minutes=10))].index)         # the first ten cash minutes are missing
    ctx = OB.session_context(bars, CFG)
    assert pd.isna(ctx.loc[d, "open"]) and pd.isna(ctx.loc[d, "gap"])                                                 # no cash-open bar: no open, no gap
    assert pd.isna(ctx.loc[d, "orh"]) and pd.isna(ctx.loc[d, "or_available_at"])                                      # a partial opening range is not used
    nov = synth(n_sessions=3, seed=5); nov["volume"] = np.nan
    assert OB.vwap(nov).isna().all()                                                                                # no volume, no VWAP
    obs = OB.observations(ctx, "NQ", "v1", CFG)
    assert not any(o.kind == "opening_gap" and o.session == str(d)[:10] for o in obs)                               # no observation for what could not be measured
    assert all(o.event_time <= o.available_at for o in obs)


def test_higher_timeframes_are_derived_from_the_bars_without_inventing_any():
    bars = synth(n_sessions=3, seed=6)
    b5, b1h = OB.derive(bars, "5min"), OB.derive(bars, "1h")
    assert len(b5) == len(bars) // 5 and b5["high"].iloc[0] == bars["high"].iloc[:5].max() and b5["volume"].iloc[0] == 500.0
    assert (OB.bar_end(b5.index, "5min")[0] - b5.index[0]) == pd.Timedelta(minutes=5)
    assert b1h["low"].iloc[0] == bars["low"].iloc[:60].min()
    dly = OB.daily(bars)
    assert len(dly) == 3 and (dly["available_at"] > dly.index).all()


# ---------------------------------------------------------------- behavior
def _events(bars, n, seed, sign=1, drift=0.0, mins=60):
    rng = np.random.default_rng(seed)
    sess = sorted(bars["session"].unique())
    rows = []
    for d in sess[: n]:
        day = bars[(bars["session"] == d) & (bars.index.hour == 11) & (bars.index.minute == int(rng.integers(0, 50)))]
        t = day.index[0]
        rows.append({"session": str(d)[:10], "available_at": t, "sign": sign})
        if drift:
            m = (bars.index >= t) & (bars.index < t + pd.Timedelta(minutes=mins))
            bars.loc[m, ["open", "high", "low", "close"]] += drift * sign * np.arange(1, m.sum() + 1)[:, None]
    return pd.DataFrame(rows)


def test_an_injected_edge_is_supported_and_pure_noise_is_not():
    bars = synth(n_sessions=130, seed=7).copy()
    ev = _events(bars, 120, 1, drift=0.15)
    res = BH.study(ev, bars, CFG, "injected", claim=+1)
    assert res["status"] == "SUPPORTED" and res["baseline"]["diff_ci95"][0] > 0 and res["distribution"]["mean_net"] > 0
    assert res["n_events"] >= 100 and res["scope"] == "development" and res["limits"]
    noise = 0
    for seed in range(12):
        b = synth(n_sessions=130, seed=100 + seed).copy()
        noise += BH.study(_events(b, 70, seed), b, CFG, "noise", claim=+1)["status"] == "SUPPORTED"
    assert noise <= 2                                                                    # false-positive rate of the status rule on pure noise


def test_the_study_reports_more_than_a_hit_rate():
    bars = synth(n_sessions=100, seed=8).copy()
    res = BH.study(_events(bars, 80, 2, drift=0.05), bars, CFG, "x", claim=+1)
    for k in ("quantiles", "skew", "kurtosis", "mean_ci95", "win_rate_ci95", "mfe_q", "mae_q"):
        assert k in res["distribution"]
    assert {"top10pct_share_of_gains", "best_session_share"} <= set(res["concentration"]) and "halves_agree_in_sign" in res["stability"] and "events_per_session" in res["dependence"]
    assert res["cost_sensitivity"]["x0"] > res["cost_sensitivity"]["x1"] > res["cost_sensitivity"]["x3"]
    assert res["baseline"]["n"] > res["n_events"]


def test_few_clustered_events_are_inconclusive_and_say_why():
    bars = synth(n_sessions=40, seed=9).copy()
    ev = _events(bars, 3, 3, drift=0.3)
    res = BH.study(ev, bars, CFG, "tiny", claim=+1)
    assert res["status"] == "INCONCLUSIVE" and any("pochi eventi" in x for x in res["limits"])
    desc = BH.study(_events(synth(n_sessions=40, seed=9).copy(), 30, 4), synth(n_sessions=40, seed=9), CFG, "descr", claim=None)
    assert desc["status"] == "OBSERVED"                                                  # a description is not a claim


def test_multiplicity_control_demotes_an_unremarkable_winner():
    studies = [{"status": "SUPPORTED", "p_value": 0.04, "limits": []}] + [{"status": "INCONCLUSIVE", "p_value": 0.9, "limits": []} for _ in range(19)]
    BH.apply_fdr(studies, 0.10)
    assert studies[0]["status"] == "INCONCLUSIVE" and studies[0]["q_value"] == pytest.approx(0.8) and "falsi positivi" in studies[0]["limits"][-1]
    alone = [{"status": "SUPPORTED", "p_value": 0.01, "limits": []}]
    assert BH.apply_fdr(alone, 0.10)[0]["status"] == "SUPPORTED"


def test_the_question_catalogue_produces_causal_events_on_real_shaped_data():
    bars = synth(n_sessions=60, seed=10)
    ctx = OB.session_context(bars, CFG)
    qs = Q.standard_questions(CFG)
    assert len(qs) >= 10 and len({q["definition"]["id"] for q in qs}) == len(qs)
    any_events = 0
    for q in qs:
        ev = Q.events_for(q, bars, ctx, CFG)
        any_events += len(ev)
        if len(ev):
            assert (pd.to_datetime(ev["available_at"]) >= pd.to_datetime(ev["session"]) - pd.Timedelta(days=1)).all()
            if "breach_time" in ev:
                assert (pd.to_datetime(ev["available_at"]) >= pd.to_datetime(ev["breach_time"])).all()
    assert any_events > 0
    assert Q.range_return_rate(bars, ctx)["status"] == "OBSERVED" and Q.day_types(bars, ctx)["status"] == "OBSERVED"
