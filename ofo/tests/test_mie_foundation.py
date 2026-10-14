"""MIE foundation: configuration, provenance vocabulary, session calendar (DST, short sessions, missing data) and the development-only data door."""
import numpy as np
import pandas as pd
import pytest

from ofo.mie import config as MC
from ofo.mie import schema as S
from ofo.mie import sessions as SS
from ofo.mie.guard import DevView, LockedAccessError
from test_research import synth

CFG = MC.load()


def test_configuration_is_versioned_and_complete():
    assert CFG["config_version"].startswith("1+") and CFG["markets"]["priority"] == ["NQ"]
    assert set(CFG["sessions"]) >= {"overnight", "premarket", "opening_range", "intraday", "close", "rth"}
    assert S.EVIDENCE == ("OBSERVED", "HYPOTHESIZED", "TESTED", "SUPPORTED", "INCONCLUSIVE", "CONTRADICTED") and "PROVEN" not in S.EVIDENCE
    assert S.RESEARCHER_ACCESS == ("dev", "owner", "external") and "locked_derived" not in S.RESEARCHER_ACCESS


def test_a_definition_has_a_stable_identity():
    a = {"event": "sweep_high", "level": "PDH", "k": 3}
    assert S.fingerprint(a) == S.fingerprint(dict(reversed(list(a.items())))) and S.fingerprint(a) != S.fingerprint({**a, "k": 4})


def test_an_observation_must_be_knowable_after_it_happened():
    ok = S.Observation("NQ", "swing_high", "2024-01-02 10:05", "2024-01-02 10:20", "2024-01-02 10:20", "v1")
    assert S.causal(ok)
    bad = S.Observation("NQ", "swing_high", "2024-01-02 10:05", "2024-01-02 10:00", "2024-01-02 10:20", "v1")        # available before it happened
    assert not S.causal(bad)


def test_sessions_are_segmented_in_local_time():
    day = pd.date_range("2024-01-09 00:00", "2024-01-09 23:59", freq="1min")
    seg = pd.Series(SS.segment_names(day, CFG), index=day)
    assert seg["2024-01-09 03:00"] == "overnight" and seg["2024-01-09 09:00"] == "premarket" and seg["2024-01-09 09:30"] == "opening_range"
    assert seg["2024-01-09 10:00"] == "intraday" and seg["2024-01-09 15:45"] == "close" and seg["2024-01-09 16:30"] == "post_cash" and seg["2024-01-09 19:00"] == "overnight"


def test_the_cash_open_stays_at_0930_local_across_a_dst_change():
    """2023-03-10 is EST (UTC-5), 2023-03-13 is EDT (UTC-4): the same 09:30 local bar is 14:30 and 13:30 UTC."""
    utc = pd.DatetimeIndex(["2023-03-10 14:30", "2023-03-13 13:30", "2023-03-13 14:30"])
    local = SS.to_local_naive(utc)
    assert [t.strftime("%H:%M") for t in local] == ["09:30", "09:30", "10:30"]
    seg = SS.segment_names(local, CFG)
    assert list(seg) == ["opening_range", "opening_range", "intraday"]


def test_short_sessions_and_missing_minutes_are_flagged_not_repaired():
    bars = synth(n_sessions=30, seed=3)
    day = bars["session"].unique()[10]
    bars = bars[~((bars["session"] == day) & (bars.index.hour >= 13))]                      # a half day: the feed stops at 13:00
    gap_day = bars["session"].unique()[5]
    idx = bars.index[(bars["session"] == gap_day)]
    bars = bars.drop(idx[200:210])                                                          # ten missing minutes inside a normal day
    fr = SS.session_frame(bars, CFG)
    assert bool(fr.loc[day, "early_close"])                                               # detected from the data, not from a holiday calendar
    assert int(fr.loc[gap_day, "missing_minutes"]) == 10 and len(bars[bars["session"] == gap_day]) == len(idx) - 10      # the gap is still a gap
    dq = SS.data_quality(bars, CFG)
    assert dq["missing_minutes_total"] >= 10 and dq["monotonic"] and dq["duplicate_timestamps"] == 0


def test_the_development_view_never_exposes_the_hidden_block():
    bars = synth(n_sessions=100, seed=4)
    days = sorted(pd.to_datetime(bars["session"]).unique())
    start, end = str(days[70])[:10], str(days[89])[:10]
    v = DevView(bars, start, end)
    assert pd.to_datetime(v.development["session"]).max() < pd.Timestamp(start)
    assert pd.to_datetime(v.forward["session"]).min() > pd.Timestamp(end)
    assert len(v.development) + len(v.forward) < len(bars)                                  # the epoch rows are in neither view
    with pytest.raises(LockedAccessError):
        v.window(str(days[60])[:10], str(days[75])[:10])                                      # overlaps the block: refused, not clipped
    with pytest.raises(LockedAccessError):
        v.window(start, end)
    assert len(v.window(str(days[10])[:10], str(days[20])[:10])) > 0 and not hasattr(v, "locked") and not hasattr(v, "hidden")
    assert v.data_version != DevView(bars.iloc[: len(bars) // 2], start, end).data_version
