"""Baseline strategy families. Each returns a target position in {-1,0,1} per bar,
computed ONLY from data up to and including that bar (the backtester shifts it)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


def _hold(sig: pd.Series) -> pd.Series:
    return sig.ffill().fillna(0.0)


def ema_cross(bars: pd.DataFrame, fast: int, slow: int) -> pd.Series:
    if fast >= slow:
        return pd.Series(0.0, index=bars.index)
    c = bars["close"]
    pos = np.sign(c.ewm(span=fast, adjust=False).mean() - c.ewm(span=slow, adjust=False).mean())
    pos.iloc[:slow] = 0.0
    return pos


def breakout(bars: pd.DataFrame, lookback: int) -> pd.Series:
    up = bars["high"].rolling(lookback).max().shift(1)
    dn = bars["low"].rolling(lookback).min().shift(1)
    sig = pd.Series(np.nan, index=bars.index)
    sig[bars["close"] > up] = 1.0
    sig[bars["close"] < dn] = -1.0
    return _hold(sig)


def mean_reversion(bars: pd.DataFrame, window: int, z: float) -> pd.Series:
    c = bars["close"]
    score = (c - c.rolling(window).mean()) / c.rolling(window).std()
    sig = pd.Series(np.nan, index=bars.index)
    sig[score < -z] = 1.0
    sig[score > z] = -1.0
    sig[score.abs() < 0.25] = 0.0
    return _hold(sig)


def random_control(bars: pd.DataFrame, seed: int, block: int = 20) -> pd.Series:
    """Zero-edge control. If the pipeline approves this, the pipeline is broken."""
    rng = np.random.default_rng(seed)
    n = -(-len(bars) // block)
    return pd.Series(np.repeat(rng.choice([-1.0, 0.0, 1.0], n), block)[:len(bars)],
                     index=bars.index)


def flatten_overnight(pos: pd.Series, flat_from: str = "16:45", flat_to: str = "18:00") -> pd.Series:
    """Guarantee nothing is HELD at or after `flat_from` (exchange-local time).

    A target set at the close of bar t is held during bar t+1. For the held position to
    be zero on every bar that ends after `flat_from`, the target must already be zero on
    bars starting later than flat_from - 2*bar_length. This works for any bar size: with
    30/60-minute bars a naive time mask would leave a position open through the
    17:00-18:00 maintenance break. Prop firms require flat before the daily close."""
    step = pos.index.to_series().diff().median()
    limit = pd.Timedelta(hours=int(flat_from[:2]), minutes=int(flat_from[3:])) - 2 * step
    stop = pd.Timedelta(hours=int(flat_to[:2]), minutes=int(flat_to[3:]))
    tod = pd.to_timedelta(pos.index.hour, unit="h") + pd.to_timedelta(pos.index.minute, unit="m")
    return pos.where(~((tod > limit) & (tod < stop)), 0.0)


def _minutes(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(index.hour * 60 + index.minute)


def orb(bars: pd.DataFrame, or_minutes: int) -> pd.Series:
    """Opening-range breakout on the US cash open (09:30 exchange time). The range is
    built from bars starting inside [09:30, 09:30+or_minutes); signals start after it and
    stop at 16:45. Long above the range high, short below the low, held until flat."""
    idx = bars.index
    tod = _minutes(idx)
    day = idx.normalize()
    in_or = (tod >= 570) & (tod < 570 + or_minutes)
    hi = bars["high"].where(in_or).groupby(day).transform("max")
    lo = bars["low"].where(in_or).groupby(day).transform("min")
    post = (tod >= 570 + or_minutes) & (tod < 16 * 60 + 45)
    c = bars["close"]
    sig = pd.Series(np.nan, index=idx)
    sig[~post] = 0.0
    sig[post & (c > hi)] = 1.0
    sig[post & (c < lo)] = -1.0
    return _hold(sig)


def orb_retest(bars: pd.DataFrame, or_minutes: int, retest_bars: int, tol: float) -> pd.Series:
    """Opening-range breakout that waits for a RETEST before entering.

    1. Build the range from bars starting in [09:30, 09:30 + or_minutes).
    2. The first close beyond the range marks the breakout side.
    3. Within `retest_bars` bars the price must come back to the broken level (within
       `tol` x range) and CLOSE beyond it again: that bar is the entry, held until flat.
    4. No retest in time -> the setup expires and a new breakout can start another one.
    Fewer, more selective trades than the plain breakout. Causal: each bar sees only the past."""
    idx = bars.index
    n = len(bars)
    tod = _minutes(idx)
    in_or = (tod >= 570) & (tod < 570 + or_minutes)
    day = idx.normalize()
    hi = bars["high"].where(in_or).groupby(day).transform("max").to_numpy()
    lo = bars["low"].where(in_or).groupby(day).transform("min").to_numpy()
    post = (tod >= 570 + or_minutes) & (tod < 16 * 60 + 45)
    c, h, l = bars["close"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy()
    codes = pd.factorize(day)[0]
    pos = np.zeros(n)
    last_day, phase, side, brk, cur = -1, 0, 0, -1, 0.0
    for i in range(n):
        if codes[i] != last_day:
            last_day, phase, side, brk, cur = codes[i], 0, 0, -1, 0.0
        if not post[i] or np.isnan(hi[i]) or np.isnan(lo[i]):
            continue
        rng = hi[i] - lo[i]
        if phase in (1, 2) and i - brk > retest_bars:
            phase, side = 0, 0                                   # setup expired
        if phase in (1, 2):
            level = hi[i] if side == 1 else lo[i]
            touched = (l[i] <= level + tol * rng) if side == 1 else (h[i] >= level - tol * rng)
            beyond = (c[i] > level) if side == 1 else (c[i] < level)
            if (phase == 2 or touched) and beyond:
                cur, phase = float(side), 3                      # retest confirmed: enter
            elif touched:
                phase = 2                                        # touched, wait for a close beyond
        elif phase == 0:
            if c[i] > hi[i]:
                phase, side, brk = 1, 1, i
            elif c[i] < lo[i]:
                phase, side, brk = 1, -1, i
        pos[i] = cur
    return pd.Series(pos, index=idx)


def vwap_dev(bars: pd.DataFrame, window: int, z: float) -> pd.Series:
    """Fade stretches away from the session VWAP, measured in rolling standard deviations
    of the deviation. Session VWAP resets with the trading session."""
    key = bars["session"] if "session" in bars.columns else bars.index.normalize()
    vol = bars["volume"].astype(float)
    vwap = (bars["close"] * vol).groupby(key).cumsum() / vol.groupby(key).cumsum()
    dev = bars["close"] - vwap
    score = dev / dev.rolling(window).std()
    sig = pd.Series(np.nan, index=bars.index)
    sig[score < -z] = 1.0
    sig[score > z] = -1.0
    sig[score.abs() < 0.25] = 0.0
    return _hold(sig)




# ---------- point-in-time context and new families (sweep/reclaim, overnight inventory, regime, pullback) ----------
def _atr(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    c = bars["close"]
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - c.shift(1)).abs(), (bars["low"] - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=2).mean()


def context_15m(bars: pd.DataFrame, fn) -> pd.Series:
    """A higher-timeframe feature, available POINT-IN-TIME. `fn` is computed on 15-minute bars; each 15-minute bar's value is shifted
    by one bar (a bar is only known once it has closed) and carried forward to the execution bars. Execution bar t can only see
    15-minute bars that were complete before t. Causality is covered by the lookahead tests."""
    b15 = bars.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
    s = fn(b15).shift(1)
    return s.reindex(s.index.union(bars.index)).ffill().reindex(bars.index)


def _session_key(bars: pd.DataFrame):
    return bars["session"] if "session" in bars.columns else bars.index.normalize()


def _levels_prior_overnight(bars: pd.DataFrame):
    """Structural levels known before they are used: prior RTH day high/low (by trading session) and the overnight (Globex) range
    of the CURRENT session, final only from the 09:30 open. Returns four arrays aligned to the bars (NaN when not yet known)."""
    tod = _minutes(bars.index)
    key = _session_key(bars)
    rth = (tod >= 570) & (tod < 960)
    onite = (tod < 570) | (tod >= 1080)
    h, l = bars["high"], bars["low"]
    d = pd.DataFrame({"h": h.where(rth), "l": l.where(rth)}).groupby(key.values).agg({"h": "max", "l": "min"})
    prior_h = pd.Series(key.values, index=bars.index).map(d["h"].shift(1)).to_numpy()
    prior_l = pd.Series(key.values, index=bars.index).map(d["l"].shift(1)).to_numpy()
    o = pd.DataFrame({"h": h.where(onite), "l": l.where(onite)}).groupby(key.values).agg({"h": "max", "l": "min"})
    on_h = pd.Series(key.values, index=bars.index).map(o["h"]).to_numpy()
    on_l = pd.Series(key.values, index=bars.index).map(o["l"]).to_numpy()
    known = tod >= 570                                          # the overnight range is final only once the cash open starts
    on_h = np.where(known, on_h, np.nan); on_l = np.where(known, on_l, np.nan)
    return prior_h, prior_l, on_h, on_l


def sweep_reclaim(bars: pd.DataFrame, sweep_atr: float, reclaim_bars: int, level_set: int, ctx: int) -> pd.Series:
    """Liquidity sweep and reclaim. Price pokes beyond a structural level (prior-day high/low, overnight high/low) by at least
    `sweep_atr` x ATR (stops are taken), then CLOSES back inside within `reclaim_bars` bars: fade the sweep (short after a swept high,
    long after a swept low) until the session ends. level_set: 0 prior day, 1 overnight, 2 both. ctx=1 additionally requires the
    15-minute trend (close vs its EMA20, point-in-time) to agree with the fade."""
    n = len(bars)
    tod = _minutes(bars.index)
    ph, pl, oh, ol = _levels_prior_overnight(bars)
    hi_levels = ([ph] if level_set in (0, 2) else []) + ([oh] if level_set in (1, 2) else [])
    lo_levels = ([pl] if level_set in (0, 2) else []) + ([ol] if level_set in (1, 2) else [])
    atr = _atr(bars).shift(1).to_numpy()
    c, h, l = bars["close"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy()
    trend = context_15m(bars, lambda b: np.sign(b["close"] - b["close"].ewm(span=20, adjust=False).mean())).fillna(0.0).to_numpy() if ctx else None
    codes = pd.factorize(_session_key(bars))[0]
    post = (tod >= 570) & (tod < 16 * 60 + 45)
    pos = np.zeros(n)
    last, cur = -1, 0.0
    pend = []                                                   # (side, level, bar of the sweep)
    for i in range(n):
        if codes[i] != last:
            last, cur, pend = codes[i], 0.0, []
        if not post[i] or np.isnan(atr[i]):
            cur = 0.0 if not post[i] else cur
            pos[i] = cur
            continue
        pend = [p for p in pend if i - p[2] <= reclaim_bars]
        for lv in hi_levels:
            if not np.isnan(lv[i]) and h[i] > lv[i] + sweep_atr * atr[i]:
                pend.append((-1, lv[i], i))
        for lv in lo_levels:
            if not np.isnan(lv[i]) and l[i] < lv[i] - sweep_atr * atr[i]:
                pend.append((1, lv[i], i))
        for side, lvl, j in pend:
            back = (c[i] < lvl) if side == -1 else (c[i] > lvl)
            if back and i > j - 1 and (trend is None or trend[i] == side):
                cur = float(side)
                pend = []
                break
        pos[i] = cur
    return pd.Series(pos, index=bars.index)


def overnight_gap(bars: pd.DataFrame, gap_atr: float, mode: int, exit_min: int) -> pd.Series:
    """Overnight inventory into the cash open. The gap between the prior RTH close and today's first RTH price, in units of the
    average daily range of the previous 14 sessions. mode 0 fades a gap larger than `gap_atr` (inventory imbalance gets unwound),
    mode 1 follows it. The position is held `exit_min` minutes after 09:30, then flat."""
    tod = _minutes(bars.index)
    key = _session_key(bars)
    rth = (tod >= 570) & (tod < 960)
    d = pd.DataFrame({"o": bars["open"].where(rth), "c": bars["close"].where(rth), "h": bars["high"].where(rth), "l": bars["low"].where(rth)})
    g = d.groupby(key.values)
    first_open = g["o"].first(); last_close = g["c"].last()
    rng = (g["h"].max() - g["l"].min()).rolling(14, min_periods=5).mean().shift(1)
    gap = ((first_open - last_close.shift(1)) / rng)
    gs = pd.Series(key.values, index=bars.index).map(gap).to_numpy()
    active = (tod >= 570) & (tod < 570 + exit_min) & (tod < 16 * 60 + 45)
    side = np.where(gs > gap_atr, -1.0, np.where(gs < -gap_atr, 1.0, 0.0))
    if mode == 1:
        side = -side
    return pd.Series(np.where(active & ~np.isnan(gs), side, 0.0), index=bars.index)


def regime_adaptive(bars: pd.DataFrame, er_window: int, er_thresh: float, lookback: int, z: float) -> pd.Series:
    """Trade a different logic in each regime, measured point-in-time on 15-minute bars: when the Kaufman efficiency ratio over
    `er_window` 15-minute bars is at least `er_thresh` the market is directional (Donchian breakout over `lookback` bars), otherwise
    it is balanced (fade a `z`-score stretch from the rolling mean over `lookback` bars). Flat outside 09:30-16:45."""
    def er(b):
        c = b["close"]
        return (c - c.shift(er_window)).abs() / c.diff().abs().rolling(er_window).sum()
    ratio = context_15m(bars, er).to_numpy()
    trend_pos = breakout(bars, lookback).to_numpy()
    mr_pos = mean_reversion(bars, lookback, z).to_numpy()
    tod = _minutes(bars.index)
    pos = np.where(ratio >= er_thresh, trend_pos, mr_pos)
    pos = np.where(np.isnan(ratio), 0.0, pos)
    return pd.Series(np.where((tod >= 570) & (tod < 16 * 60 + 45), pos, 0.0), index=bars.index)


def trend_pullback(bars: pd.DataFrame, ema_fast: int, ema_slow: int, pullback_z: float, ctx: int) -> pd.Series:
    """Trend continuation on a pullback: with the fast EMA above the slow one (uptrend) buy when price stretches BELOW the fast EMA
    by `pullback_z` standard deviations (a dip inside the trend), exit when it returns to the EMA or the trend breaks; mirrored for
    downtrends. ctx=1 also requires the 15-minute EMA20 slope (point-in-time) to agree with the trend."""
    if ema_fast >= ema_slow:
        return pd.Series(0.0, index=bars.index)
    c = bars["close"]
    f, sl = c.ewm(span=ema_fast, adjust=False).mean(), c.ewm(span=ema_slow, adjust=False).mean()
    trend = np.sign(f - sl)
    dev = c - f
    score = dev / dev.rolling(max(10, ema_fast)).std()
    if ctx:
        t15 = context_15m(bars, lambda b: np.sign(b["close"].ewm(span=20, adjust=False).mean().diff(3))).fillna(0.0)
        trend = trend.where(trend == t15, 0.0)
    sig = pd.Series(np.nan, index=bars.index)
    sig[(trend > 0) & (score < -pullback_z)] = 1.0
    sig[(trend < 0) & (score > pullback_z)] = -1.0
    sig[(trend == 0) | ((trend > 0) & (score > 0)) | ((trend < 0) & (score < 0))] = 0.0
    sig.iloc[:ema_slow] = 0.0
    return _hold(sig)


# ---------- the indicators each template actually uses (drawn on the Trader chart) ----------
def _levels(bars: pd.DataFrame, or_minutes: int):
    idx = bars.index
    tod = _minutes(idx)
    day = idx.normalize()
    in_or = (tod >= 570) & (tod < 570 + or_minutes)
    hi = bars["high"].where(in_or).groupby(day).transform("max")
    lo = bars["low"].where(in_or).groupby(day).transform("min")
    post = (tod >= 570 + or_minutes) & (tod < 16 * 60 + 45)
    return hi.where(post), lo.where(post)


def ind_ema_cross(bars, fast, slow):
    c = bars["close"]
    return {f"EMA {fast}": c.ewm(span=fast, adjust=False).mean(), f"EMA {slow}": c.ewm(span=slow, adjust=False).mean()}


def ind_breakout(bars, lookback):
    return {f"Upper channel {lookback}": bars["high"].rolling(lookback).max().shift(1),
            f"Lower channel {lookback}": bars["low"].rolling(lookback).min().shift(1)}


def ind_mean_reversion(bars, window, z):
    c = bars["close"]; m = c.rolling(window).mean(); sd = c.rolling(window).std()
    return {f"Mean {window}": m, f"+{z} sigma": m + z * sd, f"-{z} sigma": m - z * sd}


def ind_orb(bars, or_minutes):
    hi, lo = _levels(bars, or_minutes)
    return {f"Opening range high ({or_minutes}m)": hi, f"Opening range low ({or_minutes}m)": lo}


def ind_orb_retest(bars, or_minutes, retest_bars, tol):
    hi, lo = _levels(bars, or_minutes)
    rng = hi - lo
    return {f"Opening range high ({or_minutes}m)": hi, f"Opening range low ({or_minutes}m)": lo,
            "Retest zone (upper)": hi + tol * rng, "Retest zone (lower)": lo - tol * rng}


def ind_vwap_dev(bars, window, z):
    key = bars["session"] if "session" in bars.columns else bars.index.normalize()
    vol = bars["volume"].astype(float)
    vwap = (bars["close"] * vol).groupby(key).cumsum() / vol.groupby(key).cumsum()
    sd = (bars["close"] - vwap).rolling(window).std()
    return {"Session VWAP": vwap, f"VWAP +{z} sigma": vwap + z * sd, f"VWAP -{z} sigma": vwap - z * sd}


def ind_sweep_reclaim(bars, sweep_atr, reclaim_bars, level_set, ctx):
    ph, pl, oh, ol = _levels_prior_overnight(bars)
    out = {}
    if level_set in (0, 2):
        out["Prior-day high"] = pd.Series(ph, index=bars.index); out["Prior-day low"] = pd.Series(pl, index=bars.index)
    if level_set in (1, 2):
        out["Overnight high"] = pd.Series(oh, index=bars.index); out["Overnight low"] = pd.Series(ol, index=bars.index)
    return out


def ind_overnight_gap(bars, gap_atr, mode, exit_min):
    tod = _minutes(bars.index)
    key = _session_key(bars)
    rth = (tod >= 570) & (tod < 960)
    last_close = bars["close"].where(rth).groupby(key.values).last().shift(1)
    return {"Prior RTH close": pd.Series(key.values, index=bars.index).map(last_close)}


def ind_regime_adaptive(bars, er_window, er_thresh, lookback, z):
    return {f"Channel high {lookback}": bars["high"].rolling(lookback).max().shift(1), f"Channel low {lookback}": bars["low"].rolling(lookback).min().shift(1),
            f"Mean {lookback}": bars["close"].rolling(lookback).mean()}


def ind_trend_pullback(bars, ema_fast, ema_slow, pullback_z, ctx):
    c = bars["close"]
    return {f"EMA {ema_fast}": c.ewm(span=ema_fast, adjust=False).mean(), f"EMA {ema_slow}": c.ewm(span=ema_slow, adjust=False).mean()}


INDICATOR_HELP = {
    "sweep_reclaim": {"it": "Livelli del giorno precedente e dell'overnight: se il prezzo li supera e richiude dentro, la strategia va contro lo sweep.",
                      "en": "Prior-day and overnight levels: when price pokes through and closes back inside, the strategy fades the sweep."},
    "overnight_gap": {"it": "Chiusura RTH precedente: il gap rispetto all'apertura delle 9:30 decide se andare contro (fade) o con il gap.",
                      "en": "Prior RTH close: the gap to the 09:30 open decides whether to fade or follow it."},
    "regime_adaptive": {"it": "Canale e media: breakout se il contesto a 15 minuti è direzionale, reversione allo z-score se è bilanciato.",
                        "en": "Channel and mean: breakout when the 15-minute context is directional, z-score fade when balanced."},
    "trend_pullback": {"it": "Le due EMA definiscono il trend; si compra il ritracciamento sotto la EMA veloce (vendita nel trend ribassista).",
                       "en": "The two EMAs define the trend; the dip below the fast EMA is bought (sold in a downtrend)."},
    "ema_cross": {"it": "Long quando l'EMA veloce è sopra quella lenta, short quando è sotto.",
                  "en": "Long when the fast EMA is above the slow one, short when below."},
    "breakout": {"it": "Long se il prezzo chiude sopra il canale alto, short se chiude sotto quello basso (canale di Donchian).",
                 "en": "Long when price closes above the upper channel, short below the lower one (Donchian channel)."},
    "mean_reversion": {"it": "Compra quando il prezzo scende sotto la banda bassa, vende sopra quella alta; esce vicino alla media.",
                       "en": "Buys when price falls below the lower band, sells above the upper one; exits near the mean."},
    "orb": {"it": "Il range dei primi minuti dopo le 9:30 (New York). Long sopra il massimo, short sotto il minimo.",
            "en": "The range of the first minutes after 9:30 New York time. Long above the high, short below the low."},
    "orb_retest": {"it": "Come l'ORB, ma entra solo dopo che il prezzo torna sul livello rotto (zona di retest) e richiude oltre.",
                   "en": "Like the ORB, but enters only after price comes back to the broken level (retest zone) and closes beyond it again."},
    "vwap_dev": {"it": "VWAP della sessione con bande: compra molto sotto il VWAP, vende molto sopra.",
                 "en": "Session VWAP with bands: buys far below the VWAP, sells far above."},
}


@dataclass(frozen=True)
class Template:
    fn: Callable
    params: dict            # name -> (type, lo, hi): hard bounds a spec must respect
    valid: Callable = lambda p: True
    control: bool = False   # zero-edge controls are never offered to the LLM
    doc: str = ""
    indicators: Callable | None = None   # params -> {name: Series}: what the strategy looks at
    family: str = ""       # research family (see rules/research.json)
    filters: tuple = ()     # parameters that switch an optional filter on (1) / off (0): the ablation study turns them off


TEMPLATES = {
    "ema_cross": Template(ema_cross, {"fast": (int, 2, 60), "slow": (int, 5, 400)},
                          lambda p: p["fast"] < p["slow"], doc="trend: fast/slow EMA cross", indicators=ind_ema_cross),
    "breakout": Template(breakout, {"lookback": (int, 5, 300)},
                         doc="trend: Donchian channel breakout", indicators=ind_breakout),
    "mean_reversion": Template(mean_reversion, {"window": (int, 10, 300), "z": (float, 0.5, 4.0)},
                               doc="reversion: z-score of close vs rolling mean", indicators=ind_mean_reversion),
    "orb": Template(orb, {"or_minutes": (int, 5, 120)},
                    doc="opening-range breakout at the 09:30 ET cash open", indicators=ind_orb),
    "orb_retest": Template(orb_retest, {"or_minutes": (int, 5, 120), "retest_bars": (int, 1, 30),
                                        "tol": (float, 0.0, 1.0)},
                           doc="opening-range breakout that enters only after a retest of the broken level", indicators=ind_orb_retest),
    "vwap_dev": Template(vwap_dev, {"window": (int, 10, 300), "z": (float, 0.5, 4.0)},
                         doc="reversion: deviation from session VWAP", indicators=ind_vwap_dev),
    "sweep_reclaim": Template(sweep_reclaim, {"sweep_atr": (float, 0.05, 1.5), "reclaim_bars": (int, 1, 10), "level_set": (int, 0, 2), "ctx": (int, 0, 1)},
                              doc="liquidity sweep of the prior-day / overnight high or low, then a close back inside: fade it", family="C", indicators=ind_sweep_reclaim, filters=("ctx",)),
    "overnight_gap": Template(overnight_gap, {"gap_atr": (float, 0.1, 3.0), "mode": (int, 0, 1), "exit_min": (int, 30, 360)},
                              doc="overnight inventory: fade (mode 0) or follow (mode 1) the gap between the prior RTH close and the 09:30 open", family="F", indicators=ind_overnight_gap),
    "regime_adaptive": Template(regime_adaptive, {"er_window": (int, 4, 40), "er_thresh": (float, 0.15, 0.6), "lookback": (int, 10, 200), "z": (float, 0.8, 3.0)},
                                doc="regime-adaptive: breakout when the 15-minute efficiency ratio is high, z-score fade when it is low", family="G", indicators=ind_regime_adaptive),
    "trend_pullback": Template(trend_pullback, {"ema_fast": (int, 5, 60), "ema_slow": (int, 30, 300), "pullback_z": (float, 0.3, 2.5), "ctx": (int, 0, 1)},
                               lambda p: p["ema_fast"] < p["ema_slow"], doc="trend continuation: buy the dip / sell the rally inside an EMA trend", family="E", indicators=ind_trend_pullback, filters=("ctx",)),
    "random_control": Template(random_control, {"seed": (int, 0, 100_000)}, control=True,
                               doc="zero-edge control"),
}


GRIDS = {
    "ema_cross": (ema_cross, [{"fast": f, "slow": s} for f in (5, 9, 13, 21) for s in (30, 50, 100)]),
    "breakout": (breakout, [{"lookback": n} for n in (10, 20, 40, 80)]),
    "mean_reversion": (mean_reversion, [{"window": w, "z": z} for w in (20, 50) for z in (1.5, 2.0, 2.5)]),
}
TIMEFRAMES = ("1min", "5min", "15min", "30min", "60min")      # every timeframe a spec may name; which ones the Optimizer runs is in rules/research.json


DIRECTIONS = ("both", "long", "short")


def directional(fn, direction: str | None):
    """The same strategy restricted to one side: 'long' keeps only the long positions, 'short' only the short ones."""
    if direction in (None, "both"):
        return fn

    def restricted(bars, **params):
        pos = fn(bars, **params)
        return pos.clip(lower=0.0) if direction == "long" else pos.clip(upper=0.0)
    restricted.__name__ = f"{getattr(fn, '__name__', 'strategy')}_{direction}_only"
    return restricted


def _hm_minutes(hm: str) -> int:
    return int(hm[:2]) * 60 + int(hm[3:5])


def scheduled(fn, schedule: dict | None):
    """The same strategy allowed to ENTER only inside a time window (exchange clock, [from, to) ) and at most `max_per_day` times per
    session. Entries that break a rule are removed together with the whole position they would have opened; exits are untouched.
    schedule = {"window": ["09:30", "11:00"] | None, "max_per_day": 2 | None}."""
    if not schedule or not (schedule.get("window") or schedule.get("max_per_day")):
        return fn
    w, cap = schedule.get("window"), schedule.get("max_per_day")
    lo, hi = (_hm_minutes(w[0]), _hm_minutes(w[1])) if w else (None, None)

    def restricted(bars, **params):
        pos = flatten_overnight(fn(bars, **params))          # a position cut by the daily flat is a new entry the next day: count it as one
        a = pos.to_numpy(dtype=float).copy()
        n, mins = len(a), _minutes(bars.index)
        sess = (bars["session"].astype(str).str[:10].to_numpy() if "session" in bars.columns
                else bars.index.normalize().strftime("%Y-%m-%d").to_numpy())
        counts: dict = {}
        i = 0
        while i < n:
            v = a[i]
            if v != 0 and (i == 0 or a[i - 1] != v):
                j = i
                while j + 1 < n and a[j + 1] == v:
                    j += 1
                ok = lo is None or lo <= mins[i] < hi
                if ok and cap is not None and counts.get(sess[i], 0) >= cap:
                    ok = False
                if ok:
                    counts[sess[i]] = counts.get(sess[i], 0) + 1
                else:
                    a[i:j + 1] = 0.0
                i = j + 1
            else:
                i += 1
        return pd.Series(a, index=pos.index)
    restricted.__name__ = f"{getattr(fn, '__name__', 'strategy')}_scheduled"
    return restricted


def version_fn(fn, direction: str | None, schedule: dict | None = None):
    """THE final version of a strategy: direction restriction, then the entry schedule. One place, used by every replay of it."""
    return scheduled(directional(fn, direction), schedule)
