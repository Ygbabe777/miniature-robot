"""Session calendar for futures, from the DATA (not from an assumed calendar).

Bars are tz-naive in New York time (`data.py` converts UTC feeds), so the cash open is 09:30 on both sides of a DST change: segmentation works on
local minute-of-day. A futures session runs 18:00 ET (previous evening) to 17:00 ET; its label is the `session` column when present, otherwise the
date of its end. Holidays and half days are not assumed: they are DETECTED from the bars (missing or short sessions are flagged, never filled)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def hm(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:])


def minute_of_day(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(index.hour * 60 + index.minute)


def in_window(index: pd.DatetimeIndex, start: str, end: str) -> np.ndarray:
    """Local-time window [start, end); wraps over midnight (18:00 -> 08:30)."""
    m, a, b = minute_of_day(index), hm(start), hm(end)
    return (m >= a) | (m < b) if a > b else (m >= a) & (m < b)


def segment_names(index: pd.DatetimeIndex, cfg: dict) -> np.ndarray:
    """One label per bar: overnight / premarket / opening_range / intraday / close / post_cash (cash_open and rth are overlays, not labels)."""
    s = cfg["sessions"]
    out = np.full(len(index), "other", dtype=object)
    for name in ("overnight", "premarket", "opening_range", "intraday", "close", "post_cash"):
        out[in_window(index, *s[name])] = name
    return out


def session_labels(bars: pd.DataFrame) -> pd.Series:
    if "session" in bars.columns:
        return pd.to_datetime(bars["session"])
    ts = bars.index
    d = ts.normalize() + pd.to_timedelta((minute_of_day(ts) >= hm("18:00")).astype(int), unit="D")
    return pd.Series(d, index=ts)


def session_frame(bars: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One row per session: bars, missing minutes, first/last bar, and honest flags. Nothing is repaired."""
    lab = session_labels(bars)
    g = bars.groupby(lab.values)
    fr = pd.DataFrame({"n_bars": g.size(), "first": g.apply(lambda x: x.index.min()), "last": g.apply(lambda x: x.index.max())})
    fr["span_min"] = ((fr["last"] - fr["first"]).dt.total_seconds() // 60 + 1).astype(int)
    fr["missing_minutes"] = (fr["span_min"] - fr["n_bars"]).clip(lower=0)
    med = float(fr["n_bars"].median()) if len(fr) else 0.0
    fr["short"] = fr["n_bars"] < 0.6 * med                                        # a holiday / half day shows up as a short session
    cutoff = hm(cfg["sessions"]["short_session_last_bar_before"])
    fr["early_close"] = [(l.hour * 60 + l.minute) < cutoff for l in fr["last"]]
    fr["has_rth"] = [bool(in_window(bars.index[lab.values == s], *cfg["sessions"]["rth"]).any()) for s in fr.index]
    return fr


def data_quality(bars: pd.DataFrame, cfg: dict) -> dict:
    fr = session_frame(bars, cfg)
    return {"sessions": int(len(fr)), "bars": int(len(bars)), "missing_minutes_total": int(fr["missing_minutes"].sum()),
            "short_sessions": [str(d)[:10] for d in fr.index[fr["short"]]][:20], "early_close_sessions": [str(d)[:10] for d in fr.index[fr["early_close"]]][:20],
            "sessions_without_rth": [str(d)[:10] for d in fr.index[~fr["has_rth"]]][:20],
            "duplicate_timestamps": int(bars.index.duplicated().sum()), "monotonic": bool(bars.index.is_monotonic_increasing),
            "first": str(bars.index.min()), "last": str(bars.index.max())}


def to_local_naive(utc_index: pd.DatetimeIndex, tz: str = "America/New_York") -> pd.DatetimeIndex:
    """UTC timestamps -> tz-naive local time, DST-correct (09:30 local is 13:30 UTC in summer and 14:30 UTC in winter)."""
    return pd.DatetimeIndex(utc_index).tz_localize("UTC").tz_convert(tz).tz_localize(None)
