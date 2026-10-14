"""Market Observer: what the market looked like, session by session, from the bars alone and CAUSALLY.

Every level and statistic carries the moment from which it was KNOWABLE (`available_at`):
  prior-day high/low/close   known at the previous RTH close (16:00 of the previous session)
  overnight high/low         known at the cash open (09:30)
  opening gap                known at the first cash bar's close
  opening range              known at the end of the opening-range window
  VWAP                       the cumulative value at a bar is known at that bar's close
A missing input gives None / NaN, never an invented value. Delta, CVD, footprint, absorption and volume profile are NOT computed: OHLCV bars
cannot observe them and presenting an OHLCV proxy as order flow would be false data."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import Observation, fingerprint
from .sessions import hm, in_window, minute_of_day, session_labels

DURATION = {"1min": pd.Timedelta(minutes=1), "5min": pd.Timedelta(minutes=5), "15min": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1)}


def derive(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Higher timeframe from 1-minute bars. A derived bar is labelled with its START; it is complete (and knowable) at start + duration (`bar_end`)."""
    if rule == "1min":
        return bars
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "session" in bars.columns:
        agg["session"] = "last"
    out = bars.resample(rule, label="left", closed="left").agg(agg)
    return out.dropna(subset=["close"])


def bar_end(index: pd.DatetimeIndex, rule: str) -> pd.DatetimeIndex:
    return index + DURATION[rule]


def daily(bars: pd.DataFrame, rth: tuple[str, str] | None = None) -> pd.DataFrame:
    """Daily bars per futures session (full session, or only the RTH window). Built from the `session` label, never from the calendar date."""
    b = bars if rth is None else bars[in_window(bars.index, *rth)]
    lab = session_labels(bars).loc[b.index] if "session" not in b.columns else pd.to_datetime(b["session"])
    g = b.groupby(lab.values)
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(), "volume": g["volume"].sum(),
                         "available_at": g.apply(lambda x: x.index.max() + pd.Timedelta(minutes=1))})


def atr(bars: pd.DataFrame, window: int) -> pd.Series:
    pc = bars["close"].shift(1)
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - pc).abs(), (bars["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window).mean()


def vwap(bars: pd.DataFrame, start: str = "09:30") -> pd.Series:
    """Cumulative RTH VWAP per session from `start`. NaN where volume is missing or zero: a VWAP without volume is not computed."""
    lab = session_labels(bars)
    m = minute_of_day(bars.index) >= hm(start)
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    pv = (typical * bars["volume"]).where(m, 0.0)
    v = bars["volume"].where(m, 0.0)
    cpv = pv.groupby(lab.values).cumsum()
    cv = v.groupby(lab.values).cumsum()
    out = cpv / cv.where(cv > 0)
    out[~m] = np.nan
    out[bars["volume"].isna() | (bars["volume"] <= 0)] = np.nan
    return out


def session_context(bars: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One row per session with the structural levels and the opening context, each with its availability time. Rows lacking an input keep None."""
    s = cfg["sessions"]
    lab = session_labels(bars)
    ssn = sorted(pd.unique(lab.values))
    rows, prev = [], None
    for d in ssn:
        b = bars[lab.values == d]
        rth = b[in_window(b.index, *s["rth"])]
        on = b[in_window(b.index, s["overnight"][0], s["rth"][0]) & (minute_of_day(b.index) != hm(s["rth"][0]))]
        on = on[(minute_of_day(on.index) >= hm(s["overnight"][0])) | (minute_of_day(on.index) < hm(s["rth"][0]))]
        orw = b[in_window(b.index, *s["opening_range"])]
        row = {"session": pd.Timestamp(d), "n_bars": len(b), "has_rth": len(rth) > 0}
        row["pdh"], row["pdl"], row["pdc"], row["pd_available_at"] = (prev["high"], prev["low"], prev["close"], prev["end"]) if prev else (None, None, None, None)
        row["onh"] = float(on["high"].max()) if len(on) else None
        row["onl"] = float(on["low"].min()) if len(on) else None
        row["on_available_at"] = rth.index[0] if len(rth) else None                      # knowable at the cash open
        first = rth.iloc[0] if len(rth) and minute_of_day(rth.index[:1])[0] == hm(s["rth"][0]) else None
        row["open"] = float(first["open"]) if first is not None else None
        row["open_available_at"] = (rth.index[0] + pd.Timedelta(minutes=1)) if first is not None else None
        row["gap"] = (row["open"] - prev["close"]) if (first is not None and prev) else None
        row["gap_pct"] = (row["gap"] / prev["close"]) if row["gap"] is not None else None
        row["open_vs_on"] = (None if row["open"] is None or row["onh"] is None else "above" if row["open"] > row["onh"] else "below" if row["open"] < row["onl"] else "inside")
        row["open_vs_pd"] = (None if row["open"] is None or prev is None else "above" if row["open"] > prev["high"] else "below" if row["open"] < prev["low"] else "inside")
        full_or = len(orw) >= (hm(s["opening_range"][1]) - hm(s["opening_range"][0]))          # a partial opening range is NOT used
        row["orh"] = float(orw["high"].max()) if full_or else None
        row["orl"] = float(orw["low"].min()) if full_or else None
        row["or_available_at"] = (orw.index[-1] + pd.Timedelta(minutes=1)) if full_or else None
        row["rth_end"] = (rth.index[-1] + pd.Timedelta(minutes=1)) if len(rth) else None
        rows.append(row)
        prev = {"high": float(rth["high"].max()), "low": float(rth["low"].min()), "close": float(rth["close"].iloc[-1]),
                "end": rth.index[-1] + pd.Timedelta(minutes=1)} if len(rth) else None
    return pd.DataFrame(rows).set_index("session")


def observations(ctx: pd.DataFrame, market: str, data_version: str, cfg: dict) -> list[Observation]:
    """The context as provenance-carrying observations (one per level group, stamped with when it became knowable)."""
    out = []
    dv = data_version
    for d, r in ctx.iterrows():
        sd = str(d)[:10]

        def add(kind, avail, values, ev=None):
            if avail is None or pd.isna(avail) or any(v is None or (isinstance(v, (float, np.floating)) and np.isnan(v)) for v in values.values()):
                return
            t = str(avail)[:16]
            out.append(Observation(market, kind, str(ev or avail)[:16], t, t, dv, "CONFIRMED", sd, {k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in values.items()},
                                   fingerprint({"kind": kind, "cfg": cfg["config_version"]})))
        add("prior_day_levels", r["pd_available_at"], {"pdh": r["pdh"], "pdl": r["pdl"], "pdc": r["pdc"]})
        add("overnight_range", r["on_available_at"], {"onh": r["onh"], "onl": r["onl"]})
        add("opening_gap", r["open_available_at"], {"open": r["open"], "gap": r["gap"], "gap_pct": r["gap_pct"], "open_vs_on": r["open_vs_on"], "open_vs_pd": r["open_vs_pd"]})
        add("opening_range", r["or_available_at"], {"orh": r["orh"], "orl": r["orl"]})
    return out
