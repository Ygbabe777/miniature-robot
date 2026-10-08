import numpy as np
import pandas as pd
import pytest

from src.config import FeaturesCfg
from src.features import (atr, atr_series, classify_regime, volume_profile, vwap)


def bars(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"]).assign(
        timestamp=pd.date_range("2026-10-07 09:30", periods=len(rows), freq="5min"))


def test_vwap_known_value():
    df = bars([(10, 12, 8, 10, 100), (10, 14, 10, 12, 300)])
    # tp1=10, tp2=12 -> (10*100+12*300)/400 = 11.5
    assert vwap(df) == pytest.approx(11.5)


def test_vwap_zero_volume_is_none():
    assert vwap(bars([(10, 11, 9, 10, 0)])) is None


def test_atr_wilder_known_values():
    # TR costante = 2 -> ATR = 2
    df = bars([(10, 11, 9, 10, 1)] * 20)
    assert atr(df, 14) == pytest.approx(2.0)
    s = atr_series(df, 14)
    assert np.isnan(s[12]) and not np.isnan(s[13])


def test_atr_needs_enough_bars():
    assert atr(bars([(10, 11, 9, 10, 1)] * 5), 14) is None


def test_atr_wilder_smoothing():
    rows = [(10, 11, 9, 10, 1)] * 14 + [(10, 20, 10, 15, 1)]  # ultimo TR = 10
    expected = (2.0 * 13 + 10.0) / 14
    assert atr(bars(rows), 14) == pytest.approx(expected)


def test_volume_profile_poc_and_value_area():
    # volume concentrato attorno a 105
    rows = [(105, 106, 104, 105, 1000)] * 10 + [(100, 101, 99, 100, 50), (110, 111, 109, 110, 50)]
    vp = volume_profile(bars(rows), bin_size=2.0, value_area_pct=0.7)
    assert vp is not None
    assert 104 <= vp.poc <= 106
    assert vp.val <= vp.poc <= vp.vah
    assert vp.vah - vp.val < 10


def test_volume_profile_value_area_pct_coverage():
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(200):
        c = float(rng.normal(100, 3))
        rows.append((c, c + 1, c - 1, c, int(rng.integers(100, 500))))
    narrow = volume_profile(bars(rows), 1.0, 0.5)
    wide = volume_profile(bars(rows), 1.0, 0.9)
    assert (wide.vah - wide.val) >= (narrow.vah - narrow.val)


def test_volume_profile_empty():
    assert volume_profile(bars([(1, 1, 1, 1, 0)])) is None


def test_hvn_lvn_bimodal():
    rows = [(100, 101, 99, 100, 1000)] * 10 + [(120, 121, 119, 120, 1000)] * 10 + [(110, 111, 109, 110, 20)] * 3
    vp = volume_profile(bars(rows), bin_size=1.0, value_area_pct=0.7, hvn_ratio=1.0, lvn_ratio=0.8)
    assert vp is not None
    assert any(abs(p - 110) < 3 for p in vp.lvn)


F = FeaturesCfg()


def regime(**kw):
    base = dict(spot=100.0, on_open=100.0, on_range=20.0, on_vwap=100.0, prev_val=95.0, prev_vah=105.0,
                atr_daily=100.0, atr_ratio=1.0, fcfg=F)
    base.update(kw)
    return classify_regime(**base)


def test_regime_trend_up_with_reasons():
    r = regime(spot=140.0, on_vwap=120.0)
    assert r.state == "TREND_UP" and r.reasons


def test_regime_trend_down():
    assert regime(spot=60.0, on_vwap=80.0).state == "TREND_DOWN"


def test_regime_balanced():
    r = regime()
    assert r.state == "BALANCED" and "value area" in " ".join(r.reasons)


def test_regime_high_and_low_vol():
    assert regime(atr_ratio=2.0).state == "HIGH_VOLATILITY"
    assert regime(on_range=150.0).state == "HIGH_VOLATILITY"
    assert regime(atr_ratio=0.4).state == "LOW_VOLATILITY"


def test_regime_unknown_when_missing():
    assert regime(atr_daily=None).state == "UNKNOWN"
