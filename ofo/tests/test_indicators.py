import numpy as np
import pandas as pd
import pytest

from ofo.app.lab import Lab
from ofo.data import resample
from ofo.specs import from_dict
from ofo.strategies import INDICATOR_HELP, TEMPLATES, ema_cross, orb
from test_research import synth

PARAMS = {"ema_cross": {"fast": 5, "slow": 30}, "breakout": {"lookback": 20}, "mean_reversion": {"window": 30, "z": 1.5},
          "orb": {"or_minutes": 30}, "orb_retest": {"or_minutes": 30, "retest_bars": 6, "tol": 0.2}, "vwap_dev": {"window": 30, "z": 1.5},
          "sweep_reclaim": {"sweep_atr": 0.2, "reclaim_bars": 3, "level_set": 2, "ctx": 1}, "overnight_gap": {"gap_atr": 0.3, "mode": 0, "exit_min": 120},
          "regime_adaptive": {"er_window": 10, "er_thresh": 0.3, "lookback": 40, "z": 1.5}, "trend_pullback": {"ema_fast": 9, "ema_slow": 50, "pullback_z": 1.0, "ctx": 1}}


@pytest.fixture(scope="module")
def bars15():
    return resample(synth(n_sessions=25, seed=21), "15min")


def test_every_real_template_declares_its_indicators_and_a_help_text(bars15):
    for name, t in TEMPLATES.items():
        if t.control:
            assert t.indicators is None
            continue
        lines = t.indicators(bars15, **PARAMS[name])
        assert lines and all(isinstance(s, pd.Series) and len(s) == len(bars15) for s in lines.values()), name
        assert any(s.notna().any() for s in lines.values()), name
        assert {"it", "en"} <= set(INDICATOR_HELP[name]), name


def test_ema_lines_are_the_ones_the_signal_uses(bars15):
    f = TEMPLATES["ema_cross"].indicators(bars15, 5, 30)
    pos = ema_cross(bars15, 5, 30)
    ok = pos.index[30:]
    assert (np.sign(f["EMA 5"] - f["EMA 30"]).loc[ok] == pos.loc[ok]).all()


def test_opening_range_levels_match_what_the_signal_trades(bars15):
    lv = TEMPLATES["orb"].indicators(bars15, 30)
    hi, lo = lv["Opening range high (30m)"], lv["Opening range low (30m)"]
    pos = orb(bars15, 30)
    first_break = (bars15["close"] > hi) & hi.notna()
    assert first_break.any() and (pos[first_break] == 1).all()          # closing above the drawn high = long
    assert ((bars15["close"] < lo) & lo.notna()).any()
    tod = bars15.index.hour * 60 + bars15.index.minute
    assert hi[(tod < 600)].isna().all() and hi[tod >= 16 * 60 + 45].isna().all()   # levels only exist during the session


def test_bands_surround_the_mean(bars15):
    m = TEMPLATES["mean_reversion"].indicators(bars15, 30, 1.5)
    ok = m["Mean 30"].notna()
    assert (m["+1.5 sigma"][ok] >= m["Mean 30"][ok]).all() and (m["-1.5 sigma"][ok] <= m["Mean 30"][ok]).all()


def test_lab_indicator_endpoint_windowing_and_gaps(tmp_path):
    csv = tmp_path / "b.csv.gz"; synth(n_sessions=40, seed=8).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    spec = from_dict({"name": "ORB ind", "template": "orb", "ranges": {"or_minutes": [15, 30]}, "timeframes": ["15min"], "rationale": "t"})
    lp = lab.loop(); lp.all_timeframes = False                         # this test is about windowing, not about the timeframe search
    lp.process(spec)
    out = lab.indicators("ORB_ind")
    assert out["tf"] == "15min" and out["help"]["en"] and len(out["series"]) == 2
    pts = out["series"][0]["points"]
    assert any(v is None for _, v in pts)                              # gap markers between days
    assert all(pts[i][0] < pts[i + 1][0] for i in range(len(pts) - 1))
    mid = pts[len(pts) // 2][0]
    cut = lab.indicators("ORB_ind", mid, None)["series"][0]["points"]
    assert 0 < len(cut) < len(pts) and cut[0][0] >= mid
    assert lab.indicators("nope") is None
